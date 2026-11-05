"""Orchestrator + Knowledge subgraph (doc 04 §3–4, ADR-0007) for M6. Business intents get a templated reply until the
M7 tool gateway exists; the Approval subgraph arrives with M7.

    START → ingress ─blocked→ compose
              └→ classify ─policy_question→ [knowledge: condense? → retrieve → assess ─→ generate ⇄ verify] → compose
                         ├─business→ business_reply → compose
                         ├─smalltalk/out_of_scope→ scope_reply → compose
                         └─ambiguous→ clarify → compose → END
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy

from leave_assistant.agent import prompts
from leave_assistant.agent.state import AgentState, AnswerOutput, IntentOutput, QueryOutput, SummaryOutput
from leave_assistant.agent.templates import template
from leave_assistant.application.chat.context import RunContext
from leave_assistant.application.llm.ports import ModelRole, Prompt, PromptMessage, TokenCounter
from leave_assistant.application.llm.router import ProviderRouter
from leave_assistant.application.ports import LanguageDetector
from leave_assistant.application.retrieval.context import EvidencePolicy, build_context, sufficient
from leave_assistant.application.retrieval.service import RetrievalService
from leave_assistant.application.security.scan import InjectionScanService
from leave_assistant.domain.agent.budget import estimate
from leave_assistant.domain.agent.citations import MARKER, attach_declared, renumber, split_grouped, verify
from leave_assistant.domain.agent.streaming import PLACEHOLDER
from leave_assistant.domain.agent.types import (
    BUSINESS_INTENTS,
    CLARIFY_CONFIDENCE,
    AssistantAnswer,
    CitationRef,
    Intent,
    Outcome,
)
from leave_assistant.domain.privacy.types import LlmSafeText

MEDIUM_INPUT_RISK = 0.3
MAX_GENERATIONS = 2
# deterministic pre-router (doc 04 §3): clear business wording never needs an LLM call
PRE_ROUTER: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    (
        Intent.BALANCE,
        re.compile(r"υπόλοιπ|μου απομένουν|my balance|days (do i have )?left|remaining days", re.I),
    ),
    (Intent.HISTORY, re.compile(r"ιστορικό|οι αιτήσεις μου|my (leave )?history|my requests", re.I)),
    (Intent.CANCEL, re.compile(r"ακύρωσ\w* (την |τη )?αίτηση|cancel (my )?request", re.I)),
    (
        Intent.APPROVAL_ACTION,
        re.compile(r"εκκρεμεί για έγκριση|ενέκρινε|approve (request|#)|pending approvals", re.I),
    ),
    (Intent.AVAILABILITY, re.compile(r"ποιος λείπει|ποιοι λείπουν|who is (off|away|absent)", re.I)),
)


@dataclass(frozen=True, slots=True)
class AgentDeps:
    router: ProviderRouter
    retrieval: RetrievalService
    security: InjectionScanService
    language: LanguageDetector
    counter: TokenCounter
    evidence: EvidencePolicy


def _status(runtime: Runtime[RunContext], stage: str) -> None:
    runtime.context.metrics.stages.append(stage)
    runtime.stream_writer({"stage": stage})


def _turns(messages: list[dict[str, str]]) -> str:
    return "\n".join(f"<{m['role']}>{m['content']}</{m['role']}>" for m in messages)


def _answer(
    text: str, outcome: Outcome, refs: tuple[CitationRef, ...] = (), confidence: str | None = None
) -> dict[str, Any]:
    return {
        "text": text,
        "outcome": outcome.value,
        "confidence": confidence,
        "citations": [_ref_json(r) for r in refs],
    }


def _ref_json(ref: CitationRef) -> dict[str, Any]:
    data = asdict(ref)
    return {
        **data,
        "chunk_id": str(ref.chunk_id),
        "document_id": str(ref.document_id),
        "version_id": str(ref.version_id),
        "heading_path": list(ref.heading_path),
    }


def ref_from_json(data: dict[str, Any]) -> CitationRef:
    return CitationRef(
        n=int(data["n"]),
        chunk_id=UUID(data["chunk_id"]),
        document_id=UUID(data["document_id"]),
        version_id=UUID(data["version_id"]),
        version_no=int(data["version_no"]),
        title=str(data["title"]),
        heading_path=tuple(data["heading_path"]),
        page=data.get("page"),
    )


def answer_from_state(data: dict[str, Any]) -> AssistantAnswer:
    return AssistantAnswer(
        text=str(data["text"]),
        outcome=Outcome(data["outcome"]),
        citations=tuple(ref_from_json(r) for r in data.get("citations", [])),
        confidence=data.get("confidence"),
    )


class AgentGraph:
    def __init__(self, deps: AgentDeps) -> None:
        self.d = deps

    # --- helpers -----------------------------------------------------------------------------------------------
    def _tokens(self, text: str) -> int:
        return estimate(self.d.counter.count(text))

    def _history(self, state: AgentState) -> list[dict[str, str]]:
        """Messages before the current user turn that are not covered by the summary."""
        messages = state.get("messages", [])[:-1]
        return messages[state.get("summarised", 0) :]

    async def _call[T: (IntentOutput, QueryOutput, AnswerOutput, SummaryOutput)](
        self,
        runtime: Runtime[RunContext],
        name: str,
        system: LlmSafeText,
        user: LlmSafeText,
        schema: type[T],
        role: ModelRole,
    ) -> T:
        prompt = Prompt(
            id=prompts.load(name).ref,
            messages=(PromptMessage("system", system), PromptMessage("user", user)),
            max_output_tokens=runtime.context.budgets.max_output,
        )
        return await self.d.router.structured(
            prompt, schema, role=role, record=runtime.context.metrics.record()
        )

    # --- orchestrator nodes ----------------------------------------------------------------------------------
    async def ingress(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        """Deterministic (doc 04 §3): language, input screen, history budget. Pseudonymisation already happened in
        the application layer before the message entered the graph (it is what gets checkpointed)."""
        text = state["messages"][-1]["content"]
        language = self.d.language.detect(text) or runtime.context.locale
        screen = await asyncio.to_thread(  # embeds with e5: never on the event loop
            self.d.security.screen_input, text, block_threshold=runtime.context.block_threshold
        )
        runtime.context.metrics.details["input_risk_score"] = screen.score
        update: dict[str, Any] = {
            "language": language,
            "input_risk": "high"
            if screen.blocked
            else ("medium" if screen.score >= MEDIUM_INPUT_RISK else "low"),
            "attempts": 0,
            "feedback": "",
            "draft": {},
            "context": "",
            "refs": [],
            "sources": [],
            "answer": {},
        }
        if screen.blocked:
            runtime.context.metrics.details["blocked_families"] = list(screen.families)
            update["route"] = "blocked"
            update["answer"] = _answer(template("blocked", language), Outcome.BLOCKED)
            return update
        history = self._history(state)
        if self._tokens(_turns(history)) > runtime.context.budgets.history:
            update.update(await self._summarise(state, runtime, history))
        return update

    async def _summarise(
        self, state: AgentState, runtime: Runtime[RunContext], history: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Older turns → rolling summary; the last turns that fit the window stay verbatim (doc 04 §8)."""
        _status(runtime, "summarising")
        keep: list[dict[str, str]] = []
        for message in reversed(history):
            if self._tokens(_turns([message, *keep])) > runtime.context.budgets.history // 2:
                break
            keep.insert(0, message)
        older = history[: len(history) - len(keep)]
        previous = state.get("summary", "")
        user = LlmSafeText(
            (f"<summary>{previous}</summary>\n" if previous else "")
            + _turns(older)[: runtime.context.budgets.history * 4]
        )
        result = await self._call(
            runtime, "summarise", prompts.static("summarise"), user, SummaryOutput, ModelRole.FAST
        )
        return {"summary": result.summary, "summarised": state.get("summarised", 0) + len(older)}

    async def classify(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        _status(runtime, "classifying")
        text = state["messages"][-1]["content"]
        for intent, pattern in PRE_ROUTER:
            if pattern.search(text):
                return {"intent": intent.value, "confidence": 1.0, "route": self._route(intent, 1.0)}
        user = LlmSafeText(f"<user>{text}</user>")
        result = await self._call(
            runtime, "classify", prompts.static("classify"), user, IntentOutput, ModelRole.FAST
        )
        return {
            "intent": result.intent.value,
            "confidence": result.confidence,
            "route": self._route(result.intent, result.confidence),
        }

    @staticmethod
    def _route(intent: Intent, confidence: float) -> str:
        if confidence < CLARIFY_CONFIDENCE or intent is Intent.AMBIGUOUS:
            return "clarify"
        if intent is Intent.POLICY_QUESTION:
            return "knowledge"
        if intent in BUSINESS_INTENTS:
            return "business"
        if intent is Intent.SMALLTALK:
            return "smalltalk"
        return "scope"

    async def templated(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        route = state.get("route", "scope")
        name = {"business": "business", "smalltalk": "smalltalk", "clarify": "clarify"}.get(route, "scope")
        outcome = Outcome.CLARIFY if route == "clarify" else Outcome.SCOPE
        return {"answer": _answer(template(name, state.get("language")), outcome)}

    async def compose(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        """No LLM call (doc 04 §3): the answer object is final; it joins the history for later turns."""
        answer = state.get("answer") or _answer(
            template("insufficient_evidence", state.get("language")), Outcome.INSUFFICIENT_EVIDENCE
        )
        return {"answer": answer, "messages": [{"role": "assistant", "content": answer["text"]}]}

    # --- knowledge subgraph ------------------------------------------------------------------------------------
    async def condense(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        text = state["messages"][-1]["content"]
        history = self._history(state)
        if not history and not state.get("summary"):
            return {"query": text[:500]}
        _status(runtime, "condensing")
        summary = state.get("summary", "")
        user = LlmSafeText(
            (f"<summary>{summary}</summary>\n" if summary else "")
            + _turns([*history[-4:], state["messages"][-1]])
        )
        result = await self._call(
            runtime, "condense", prompts.static("condense"), user, QueryOutput, ModelRole.FAST
        )
        return {"query": (result.query.strip() or text)[:500]}

    async def retrieve(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        _status(runtime, "retrieving")
        hits = await self.d.retrieval.retrieve(runtime.context.principal, state["query"])
        bundle = build_context(hits, self.d.counter, self.d.evidence)
        runtime.context.metrics.details.update(
            {"candidates": len(hits), "top_score": round(bundle.top_score, 4), "evidence": bundle.evidence}
        )
        return {
            "context": str(bundle.text),
            "refs": [_ref_json(r) for r in bundle.refs],
            "sources": sorted(bundle.source_version_ids),
            "evidence_ok": sufficient(bundle, self.d.evidence),
        }

    async def generate(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        _status(runtime, "generating")
        language = state.get("language") or runtime.context.locale
        system = LlmSafeText(
            prompts.system_prompt(language, hardened=state.get("input_risk") == "medium")
            + "\n\n"
            + prompts.static("generate")
        )
        summary = state.get("summary", "")
        feedback = state.get("feedback", "")
        user = LlmSafeText(
            (f"<summary>{summary}</summary>\n" if summary else "")
            + _turns(self._history(state)[-4:])
            + f"\n<documents>\n{state['context']}\n</documents>\n<user>{state['messages'][-1]['content']}</user>"
            + (
                f"\n<correction>Your previous answer was rejected: {feedback}. Cite only the numbered documents.</correction>"
                if feedback
                else ""
            )
        )
        result = await self._call(runtime, "generate", system, user, AnswerOutput, ModelRole.ANSWER)
        return {"draft": result.model_dump(), "attempts": state.get("attempts", 0) + 1}

    async def verify_citations(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        _status(runtime, "verifying")
        draft = state["draft"]
        refs = [ref_from_json(r) for r in state.get("refs", [])]
        declared = list(draft.get("citations", []))
        answer = split_grouped(str(draft["answer"]))
        draft = {**draft, "answer": attach_declared(answer, declared, len(refs))}
        check = verify(str(draft["answer"]), declared, len(refs))
        language = state.get("language") or runtime.context.locale
        written = self._answer_language(str(draft["answer"]))
        if check.ok and written is not None and written != language:
            # sources may be in another language; the answer never is (doc 11 S03/S04)
            check = replace(
                check,
                ok=False,
                problems=(*check.problems, f"answer not in {prompts.LANGUAGE_NAMES.get(language, language)}"),
            )
        if not check.ok:
            runtime.context.metrics.details.setdefault("citation_problems", []).append(list(check.problems))
            if state.get("attempts", 0) < MAX_GENERATIONS and str(draft["answer"]).strip():
                return {"feedback": "; ".join(check.problems)}
            return {
                "answer": _answer(
                    template("insufficient_evidence", state.get("language")), Outcome.INSUFFICIENT_EVIDENCE
                ),
                "feedback": "",
            }
        ordered = [refs[n - 1] for n in check.cited]
        renumbered = tuple(
            CitationRef(
                i, r.chunk_id, r.document_id, r.version_id, r.version_no, r.title, r.heading_path, r.page
            )
            for i, r in enumerate(ordered, start=1)
        )
        text = renumber(str(draft["answer"]), check.cited)
        return {
            "answer": _answer(text, Outcome.ANSWERED, renumbered, str(draft.get("confidence") or "medium")),
            "feedback": "",
        }

    async def insufficient(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        return {
            "answer": _answer(
                template("insufficient_evidence", state.get("language")), Outcome.INSUFFICIENT_EVIDENCE
            )
        }

    def _answer_language(self, text: str) -> str | None:
        plain = PLACEHOLDER.sub(" ", MARKER.sub(" ", text))
        return self.d.language.detect(plain) if plain.strip() else None

    # --- edges -------------------------------------------------------------------------------------------------
    @staticmethod
    def after_ingress(state: AgentState) -> Literal["classify", "compose"]:
        return "compose" if state.get("route") == "blocked" else "classify"

    @staticmethod
    def after_classify(state: AgentState) -> Literal["knowledge", "templated"]:
        return "knowledge" if state.get("route") == "knowledge" else "templated"

    @staticmethod
    def after_assess(state: AgentState) -> Literal["generate", "insufficient"]:
        return "generate" if state.get("evidence_ok") else "insufficient"

    @staticmethod
    def after_verify(state: AgentState) -> Literal["generate", "done"]:
        return "generate" if state.get("feedback") else "done"

    # --- build ---------------------------------------------------------------------------------------------------
    def knowledge(self) -> Any:
        retry = RetryPolicy(max_attempts=2)
        graph = StateGraph(AgentState, context_schema=RunContext)
        graph.add_node("condense", self.condense)
        graph.add_node("retrieve", self.retrieve, retry_policy=retry)
        graph.add_node("generate", self.generate)
        graph.add_node("verify", self.verify_citations)
        graph.add_node("insufficient", self.insufficient)
        graph.add_edge(START, "condense")
        graph.add_edge("condense", "retrieve")
        graph.add_conditional_edges("retrieve", self.after_assess)
        graph.add_edge("generate", "verify")
        graph.add_conditional_edges("verify", self.after_verify, {"generate": "generate", "done": END})
        graph.add_edge("insufficient", END)
        return graph.compile()

    def build(self, checkpointer: Any) -> Any:
        graph = StateGraph(AgentState, context_schema=RunContext)
        graph.add_node("ingress", self.ingress)
        graph.add_node("classify", self.classify)
        graph.add_node("knowledge", self.knowledge())
        graph.add_node("templated", self.templated)
        graph.add_node("compose", self.compose)
        graph.add_edge(START, "ingress")
        graph.add_conditional_edges("ingress", self.after_ingress)
        graph.add_conditional_edges("classify", self.after_classify)
        graph.add_edge("knowledge", "compose")
        graph.add_edge("templated", "compose")
        graph.add_edge("compose", END)
        return graph.compile(checkpointer=checkpointer)
