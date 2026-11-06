"""Orchestrator + Knowledge, Business Operations and Approval subgraphs (doc 04 §3–6, ADR-0007/0009).

    START → ingress ─blocked→ compose
              └→ classify ─policy_question→ [knowledge: condense? → retrieve → assess → generate ⇄ verify] → compose
                         ├─business→ [business: plan ⇄ tool_gateway → verify] ─proposal→ [approval] → compose
                         ├─smalltalk/out_of_scope→ scope_reply → compose
                         └─ambiguous→ clarify → compose → END

    approval: render → await_confirmation (interrupt) → validate ─confirm→ execute → END
                                                               ├─reject→ record_rejection → END
                                                               └─edit→ re-prepare → render (loop)

Every subgraph declares its output keys (the M6 history-duplication bug, worklog M7). The model chooses tools; the
gateway and the application services decide; only a human confirmation of a stored proposal executes anything.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any, Literal
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, interrupt

from leave_assistant.agent import prompts
from leave_assistant.agent.renderers import fallback, outcome_text, proposal_text
from leave_assistant.agent.state import (
    AgentState,
    AnswerOutput,
    ApprovalOutput,
    BusinessOutput,
    IntentOutput,
    KnowledgeOutput,
    QueryOutput,
    SummaryOutput,
)
from leave_assistant.agent.templates import template
from leave_assistant.application.assistant.gateway import ToolContext, ToolGateway
from leave_assistant.application.assistant.proposals import ExecutionInterrupted, ProposalService
from leave_assistant.application.assistant.tools import ToolName, allowed_tools, spec_for
from leave_assistant.application.chat.context import RunContext
from leave_assistant.application.errors import NotFoundError
from leave_assistant.application.llm.ports import ModelRole, Prompt, PromptMessage, TokenCounter, ToolCall
from leave_assistant.application.llm.router import ProviderRouter
from leave_assistant.application.ports import LanguageDetector
from leave_assistant.application.retrieval.context import EvidencePolicy, build_context, sufficient
from leave_assistant.application.retrieval.service import RetrievalService
from leave_assistant.application.security.scan import InjectionScanService
from leave_assistant.domain.agent.budget import estimate
from leave_assistant.domain.agent.citations import (
    MARKER,
    URL,
    attach_declared,
    markers_in,
    renumber,
    split_grouped,
    strip_markdown,
    verify,
)
from leave_assistant.domain.agent.grounding import numbers_in, ungrounded_numbers
from leave_assistant.domain.agent.proposals import (
    EDITABLE,
    ConfirmationDecision,
    ProposalKind,
    ProposalStatus,
)
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
MAX_PLAN_ITERATIONS = 6  # doc 04 §5
DOCUMENT_ID = re.compile(r'<document id="(\d+)"')
PROPOSAL_KINDS = {
    ToolName.PREPARE_LEAVE_REQUEST.value: ProposalKind.SUBMIT,
    ToolName.PREPARE_MODIFICATION.value: ProposalKind.MODIFY,
    ToolName.PREPARE_CANCELLATION.value: ProposalKind.CANCEL,
}
# deterministic pre-router (doc 04 §3): clear business wording never needs an LLM call
PRE_ROUTER: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    (  # first: a request may also ask for the balance (the brief's headline question) — the action decides
        Intent.REQUEST,
        re.compile(
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(working\s+)?days?\s+(off|of\s+(annual\s+)?leave)\b"
            r"|θέλω\s+(\d+|μία|μια|δύο|τρεις|τέσσερις|πέντε|έξι|επτά|εφτά|οκτώ|εννέα|δέκα)\s+(εργάσιμες\s+)?(μέρες|ημέρες)"
            r"|θέλω\s+(να\s+πάρω\s+)?άδεια\s+(από|στις|την)\b",
            re.I,
        ),
    ),
    (
        Intent.BALANCE,
        re.compile(
            r"υπόλοιπ|μου απομένουν|my balance|days (do i have )?left|remaining days|days do i have remaining",
            re.I,
        ),
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
    tools: ToolGateway | None = None  # M7 business operations
    proposals: ProposalService | None = None


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


ACTION_INTENTS = frozenset(
    {Intent.REQUEST.value, Intent.MODIFY.value, Intent.CANCEL.value, Intent.APPROVAL_ACTION.value}
)
# "shall I prepare it?" — the card is that question (live Mistral, M7); a clarifying question is still fine
PERMISSION_QUESTION = re.compile(
    r"\b(shall|should|can|may) i (prepare|submit|book|create|go ahead)|\b(do you want|would you like) me to"
    r"|θέλετε να (το |την |τις )?(ετοιμάσω|υποβάλω|καταχωρήσω)|θέλεις να (το |την )?(ετοιμάσω|υποβάλω)"
    r"|να (το |την )?(ετοιμάσω|υποβάλω)\s*[;?]",
    re.IGNORECASE,
)
# claims of an action in a plain answer (EL/EN, folded case): only the card and the confirmed execution may say so
ACTION_CLAIM = re.compile(
    r"\b(i have|i've) (prepared|submitted|approved|cancell?ed|rejected)\b|\bon the card\b|\bthe card\b"
    r"|\bwas (submitted|approved|cancell?ed)\b|ετοίμασα|ετοιμάσα|στην κάρτα|υποβλήθηκε|εγκρίθηκε|ακυρώθηκε|υπέβαλα",
    re.IGNORECASE,
)


def _number(value: Any) -> str | None:
    found = numbers_in(str(value)) if value is not None else set()
    return next(iter(found), None)


def _shift_documents(text: str, offset: int) -> str:
    def shift(match: re.Match[str]) -> str:
        return f'<document id="{int(match.group(1)) + offset}"'

    return DOCUMENT_ID.sub(shift, text)


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
            "tool_steps": [],
            "pending_calls": [],
            "placeholders": {},
            "iterations": 0,
            "proposal_id": "",
            "proposal_kind": "",
            "proposal_round": 0,
            "decision": {},
            "execution": {},
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
        return {
            "answer": answer,
            "messages": [{"id": f"t{state.get('turn', 0)}a", "role": "assistant", "content": answer["text"]}],
        }

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
        answer = split_grouped(strip_markdown(str(draft["answer"])))
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

    # --- business operations subgraph (doc 04 §5) --------------------------------------------------------------
    def _tool_context(self, state: AgentState, runtime: Runtime[RunContext]) -> ToolContext:
        context = runtime.context
        return ToolContext(
            principal=context.principal,
            conversation_id=context.thread_id,
            run_id=context.run_id or context.thread_id,
            today=context.today or date.today(),
            language=state.get("language") or context.locale,
            read_only=state.get("input_risk") == "medium",
        )

    def _tool_results(self, state: AgentState, budget: int) -> str:
        """This turn's results, newest first until `budget` is used (older ones are reduced to a stub)."""
        blocks: list[str] = []
        used = 0
        for step in reversed(state.get("tool_steps", [])):
            if step["name"] == ToolName.SEARCH_POLICIES.value and step["status"] == "ok":
                body = str(step["result"].get("documents", ""))
            else:
                body = json.dumps(step["result"], ensure_ascii=False, separators=(",", ":"))
            block = f'<tool_result n="{step["n"]}" name="{step["name"]}" status="{step["status"]}">\n{body}\n</tool_result>'
            cost = self._tokens(block)
            if used + cost > budget:
                blocks.append(
                    f'<tool_result n="{step["n"]}" name="{step["name"]}" status="{step["status"]}">'
                    "(older result omitted: over the budget)</tool_result>"
                )
                continue
            used += cost
            blocks.append(block)
        return "\n".join(reversed(blocks))

    def _plan_user(self, state: AgentState, runtime: Runtime[RunContext], *, fixed: int) -> LlmSafeText:
        """Fits the 3,500-token total (doc 04 §8): `fixed` = system prompt + bound tool schemas; the rest is shared by
        the recent history (≤ its budget, oldest turns dropped first) and the turn's tool results (newest first)."""
        budgets = runtime.context.budgets
        today = runtime.context.today or date.today()
        summary = state.get("summary", "")
        feedback = state.get("feedback", "")
        head = f"<context>Today is {today.isoformat()} ({today.strftime('%A')}).</context>\n" + (
            f"<summary>{summary}</summary>\n" if summary else ""
        )
        user = f"\n<user>{state['messages'][-1]['content']}</user>"
        correction = (
            f"\n<correction>Your previous answer was rejected: {feedback}. Use only facts from the tool results "
            "and answer again.</correction>"
            if feedback
            else ""
        )
        room = budgets.total - fixed - self._tokens(head + user + correction) - 60  # tags and rounding
        history = self._history(state)[-4:]
        while history and self._tokens(_turns(history)) > min(budgets.history, max(room // 2, 0)):
            history = history[1:]
        room -= self._tokens(_turns(history))
        results = self._tool_results(state, max(min(budgets.tool_results, room), 0))
        return LlmSafeText(
            head
            + _turns(history)
            + user
            + (f"\n<tool_results>\n{results}\n</tool_results>" if results else "")
            + correction
        )

    async def plan(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        _status(runtime, "planning")
        iterations = state.get("iterations", 0)
        language = state.get("language") or runtime.context.locale
        if iterations >= MAX_PLAN_ITERATIONS:
            return {
                "pending_calls": [],
                "answer": _answer(fallback(state.get("tool_steps", []), language), Outcome.ANSWERED),
            }
        principal = runtime.context.principal
        bound = allowed_tools(
            principal.roles, read_only=state.get("input_risk") == "medium", intent=state.get("intent")
        )
        tools = [spec_for(t) for t in bound]
        system = LlmSafeText(
            prompts.business_prompt(language, hardened=state.get("input_risk") == "medium")
            + "\n\n"
            + prompts.static("plan")
        )
        fixed = self._tokens(system) + self.d.router.count_tools(tools)
        prompt = Prompt(
            id=prompts.load("plan").ref,
            messages=(
                PromptMessage("system", system),
                PromptMessage("user", self._plan_user(state, runtime, fixed=fixed)),
            ),
            max_output_tokens=runtime.context.budgets.max_output,
        )
        step = await self.d.router.tool_step(
            prompt, tools, role=ModelRole.ANSWER, record=runtime.context.metrics.record()
        )
        if step.calls:
            return {
                "iterations": iterations + 1,
                "pending_calls": [
                    {"call_id": f"c{iterations + 1}{i}", "name": c.name, "arguments": c.arguments}
                    for i, c in enumerate(step.calls, start=1)
                ],
            }
        return {
            "iterations": iterations + 1,
            "pending_calls": [],
            "draft": {"answer": step.text},
            "attempts": state.get("attempts", 0) + 1,
        }

    async def tool_gateway(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        assert self.d.tools is not None, "business operations need the tool gateway"
        ctx = self._tool_context(state, runtime)
        steps = list(state.get("tool_steps", []))
        placeholders = dict(state.get("placeholders", {}))
        refs = list(state.get("refs", []))
        sources = list(state.get("sources", []))
        update: dict[str, Any] = {}
        for call in state.get("pending_calls", []):
            runtime.stream_writer({"tool": call["name"], "status": "started"})
            outcome = await self.d.tools.call(ctx, ToolCall(call["name"], call["arguments"]), call["call_id"])
            runtime.stream_writer({"tool": call["name"], "status": outcome.status})
            runtime.context.metrics.details.setdefault("tool_trace", []).append(outcome.trace())
            result = outcome.result
            if outcome.refs:  # number this turn's documents continuously across searches
                offset = len(refs)
                result = {**result, "documents": _shift_documents(str(result.get("documents", "")), offset)}
                refs += [_ref_json(replace(r, n=r.n + offset)) for r in outcome.refs]
                sources = sorted({*sources, *outcome.source_version_ids})
            steps.append(
                {
                    "n": len(steps) + 1,
                    "call_id": call["call_id"],
                    "name": call["name"],
                    "status": outcome.status,
                    "result": result,
                }
            )
            placeholders.update(outcome.placeholders)
            if outcome.proposal_id is not None:
                kind = PROPOSAL_KINDS.get(call["name"])
                if kind is None:  # prepare_approval_decision
                    kind = (
                        ProposalKind.APPROVE
                        if call["arguments"].get("decision") == "approve"
                        else ProposalKind.REJECT
                    )
                update = {"proposal_id": str(outcome.proposal_id), "proposal_kind": kind.value}
                break  # one proposal per turn: the user decides before anything else happens
        return {
            "tool_steps": steps,
            "placeholders": placeholders,
            "refs": refs,
            "sources": sources,
            "pending_calls": [],
            **update,
        }

    def _grounding_sources(self, state: AgentState, runtime: Runtime[RunContext]) -> list[str]:
        today = runtime.context.today or date.today()
        texts = [json.dumps(step["result"], ensure_ascii=False) for step in state.get("tool_steps", [])]
        texts.append(state["messages"][-1]["content"])
        texts.append(today.isoformat())
        texts.extend(m["content"] for m in self._history(state)[-4:])
        return texts

    async def verify_business(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        """Deterministic grounding (decision 5): numbers from tool results, valid citations, user language."""
        _status(runtime, "verifying")
        language = state.get("language") or runtime.context.locale
        text = split_grouped(strip_markdown(str(state.get("draft", {}).get("answer", "")))).strip()
        refs = [ref_from_json(r) for r in state.get("refs", [])]
        problems: list[str] = []
        if not text:
            problems.append("empty answer")
        cited = markers_in(text)
        if cited and (not refs or max(cited) > len(refs) or min(cited) < 1):
            problems.append("it cites sources that were not provided")
        if URL.search(text):
            problems.append("it contains a link")
        ungrounded = ungrounded_numbers(text, self._grounding_sources(state, runtime))
        if ungrounded:
            problems.append(f"these numbers are not in the tool results: {', '.join(sorted(ungrounded))}")
        written = self._answer_language(text) if text else None
        if written is not None and written != language:
            problems.append(f"answer not in {prompts.LANGUAGE_NAMES.get(language, language)}")
        if text and ACTION_CLAIM.search(text):
            # no proposal reached this check (a proposal goes to the card, not here): any claim of one is false
            problems.append("it claims a prepared card or an executed action, but none was prepared")
        if (
            text
            and state.get("intent") in ACTION_INTENTS
            and (not text.rstrip().endswith(("?", ";")) or PERMISSION_QUESTION.search(text))
            and not any(step["name"].startswith("prepare_") for step in state.get("tool_steps", []))
        ):
            problems.append(
                "the user asked to act: call the matching prepare_ tool (it validates and shows the card), "
                "or ask one short question if details are missing"
            )
        balance = self._stated_balance(state)
        if text and balance is not None and balance not in numbers_in(text):
            problems.append(f"it does not state the available days from get_my_balance ({balance})")
        if problems:
            runtime.context.metrics.details.setdefault("grounding_problems", []).append(problems)
            if state.get("attempts", 0) < MAX_GENERATIONS:
                return {"feedback": "; ".join(problems)}
            return {
                "feedback": "",
                "answer": _answer(fallback(state.get("tool_steps", []), language), Outcome.ANSWERED),
            }
        ordered = [refs[n - 1] for n in cited]
        renumbered = tuple(replace(r, n=i) for i, r in enumerate(ordered, start=1))
        return {
            "feedback": "",
            "answer": _answer(renumber(text, cited) if cited else text, Outcome.ANSWERED, renumbered),
        }

    @staticmethod
    def _stated_balance(state: AgentState) -> str | None:
        """For a balance question answered from get_my_balance: the available days the answer must state (annual
        leave unless the call asked for another type). A small model said "no days left" with 15 available (M7)."""
        if state.get("intent") != Intent.BALANCE.value:
            return None
        for step in reversed(state.get("tool_steps", [])):
            if step["name"] == ToolName.GET_MY_BALANCE.value and step["status"] == "ok":
                rows = step["result"].get("balances", [])
                row = next(
                    (r for r in rows if r.get("leave_type") == "annual"), rows[0] if len(rows) == 1 else None
                )
                return _number(row.get("available_days")) if row else None
        return None

    @staticmethod
    def after_plan(state: AgentState) -> Literal["tool_gateway", "verify", "done"]:
        if state.get("pending_calls"):
            return "tool_gateway"
        if state.get("answer"):
            return "done"
        return "verify"

    @staticmethod
    def after_gateway(state: AgentState) -> Literal["plan", "done"]:
        return "done" if state.get("proposal_id") else "plan"

    @staticmethod
    def after_verify_business(state: AgentState) -> Literal["plan", "done"]:
        return "plan" if state.get("feedback") else "done"

    # --- approval subgraph (doc 04 §6) -------------------------------------------------------------------------
    async def render(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        _status(runtime, "awaiting_confirmation")
        language = state.get("language") or runtime.context.locale
        steps = state.get("tool_steps", [])
        prepared = next(
            (s for s in reversed(steps) if s.get("status") == "ok" and s["result"].get("prepared")), None
        )
        text = proposal_text(
            state.get("proposal_kind", ""), prepared["result"] if prepared else {}, steps, language
        )
        round_ = state.get("proposal_round", 0) + 1
        return {
            "proposal_round": round_,
            "messages": [{"id": f"t{state.get('turn', 0)}p{round_}", "role": "assistant", "content": text}],
            "answer": _answer(text, Outcome.INTERRUPTED),
        }

    async def await_confirmation(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        # nothing before interrupt(): the node re-runs from the top when it is resumed
        decision = interrupt(
            {
                "proposal_id": state.get("proposal_id"),
                "kind": state.get("proposal_kind"),
                "text": state.get("answer", {}).get("text", ""),
            }
        )
        return {"decision": dict(decision) if isinstance(decision, dict) else {"decision": str(decision)}}

    async def validate_decision(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        """Defence in depth: the API checked ownership, expiry and hash; the graph checks them again against the
        database row and the trusted RunContext principal — never against state."""
        assert self.d.proposals is not None
        language = state.get("language") or runtime.context.locale
        decision = dict(state.get("decision", {}))
        verdict = str(decision.get("decision", ""))
        try:
            proposal = await self.d.proposals.get(
                runtime.context.principal, UUID(str(state.get("proposal_id")))
            )
        except NotFoundError, ValueError:
            proposal = None
        stop: str | None = None
        if proposal is None or decision.get("proposal_id") != str(proposal.id):
            stop = "mismatch"
        elif decision.get("payload_hash") != proposal.payload_hash:
            stop = "mismatch"
        elif proposal.status is not ProposalStatus.PENDING:
            stop = "expired" if proposal.status is ProposalStatus.EXPIRED else "mismatch"
        elif verdict not in {d.value for d in ConfirmationDecision}:
            stop = "mismatch"
        elif verdict == ConfirmationDecision.EDIT.value and proposal.kind not in EDITABLE:
            stop = "mismatch"
        if stop is not None:
            outcome = Outcome.REJECTED
            return {
                "decision": {**decision, "verdict": "stop"},
                "execution": {"status": stop},
                "answer": _answer(template(stop, language), outcome),
            }
        return {"decision": {**decision, "verdict": verdict}}

    async def execute(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        assert self.d.proposals is not None
        _status(runtime, "executing")
        language = state.get("language") or runtime.context.locale
        result = await self.d.proposals.execute(
            runtime.context.principal,
            UUID(str(state.get("proposal_id"))),
            run_id=runtime.context.run_id or runtime.context.thread_id,
        )
        execution = {"status": result.status.value, "outcome": result.outcome, "replayed": result.replayed}
        outcome = Outcome.EXECUTED if result.status is ProposalStatus.EXECUTED else Outcome.FAILED
        return {
            "execution": execution,
            "answer": _answer(outcome_text(state.get("proposal_kind", ""), execution, language), outcome),
        }

    async def record_rejection(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        assert self.d.proposals is not None
        language = state.get("language") or runtime.context.locale
        await self.d.proposals.reject(
            runtime.context.principal, UUID(str(state.get("proposal_id"))), reason="user"
        )
        return {
            "execution": {"status": "rejected"},
            "answer": _answer(template("rejected", language), Outcome.REJECTED),
        }

    async def edit(self, state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        """Edits are structured (dates, leave type): the proposal is re-prepared deterministically — no model call
        — and the user sees a new card."""
        assert self.d.proposals is not None and self.d.tools is not None
        language = state.get("language") or runtime.context.locale
        principal = runtime.context.principal
        old = await self.d.proposals.get(principal, UUID(str(state.get("proposal_id"))))
        edits = dict(state.get("decision", {}).get("edits") or {})
        payload = old.payload
        start = edits.get("start_date") or payload.get("start_date")
        end = edits.get("end_date") or (payload.get("end_date") if not edits.get("start_date") else None)
        if old.kind is ProposalKind.SUBMIT:
            name = ToolName.PREPARE_LEAVE_REQUEST.value
            arguments: dict[str, Any] = {
                "leave_type": edits.get("leave_type") or payload.get("leave_type"),
                "start_date": start,
            }
            if payload.get("reason"):
                arguments["reason"] = payload["reason"]
        else:
            name = ToolName.PREPARE_MODIFICATION.value
            arguments = {"request_number": payload.get("request_number"), "start_date": start}
        if end:
            arguments["end_date"] = end
        else:
            arguments["working_days"] = int(edits.get("working_days") or payload.get("working_days") or 1)
        await self.d.proposals.reject(principal, old.id, reason="edited")
        round_ = state.get("proposal_round", 0)
        outcome = await self.d.tools.call(
            self._tool_context(state, runtime), ToolCall(name, arguments), f"e{round_}"
        )
        runtime.context.metrics.details.setdefault("tool_trace", []).append(outcome.trace())
        steps = [
            *state.get("tool_steps", []),
            {
                "n": len(state.get("tool_steps", [])) + 1,
                "call_id": f"e{round_}",
                "name": name,
                "status": outcome.status,
                "result": outcome.result,
            },
        ]
        if outcome.proposal_id is None:
            return {
                "tool_steps": steps,
                "proposal_id": "",
                "execution": {"status": "edit_invalid"},
                "answer": _answer(fallback(steps, language), Outcome.FAILED),
            }
        return {
            "tool_steps": steps,
            "proposal_id": str(outcome.proposal_id),
            "proposal_kind": old.kind.value,
            "decision": {},
            "answer": {},
        }

    @staticmethod
    def after_validate(state: AgentState) -> Literal["execute", "record_rejection", "edit", "done"]:
        verdict = state.get("decision", {}).get("verdict")
        if verdict == ConfirmationDecision.CONFIRM.value:
            return "execute"
        if verdict == ConfirmationDecision.REJECT.value:
            return "record_rejection"
        if verdict == ConfirmationDecision.EDIT.value:
            return "edit"
        return "done"

    @staticmethod
    def after_edit(state: AgentState) -> Literal["render", "done"]:
        return "render" if state.get("proposal_id") and not state.get("answer") else "done"

    # --- edges -------------------------------------------------------------------------------------------------
    @staticmethod
    def after_ingress(state: AgentState) -> Literal["classify", "compose"]:
        return "compose" if state.get("route") == "blocked" else "classify"

    def after_classify(self, state: AgentState) -> Literal["knowledge", "business", "templated"]:
        route = state.get("route")
        if route == "knowledge":
            return "knowledge"
        if route == "business" and self.d.tools is not None:
            return "business"
        return "templated"

    @staticmethod
    def after_business(state: AgentState) -> Literal["approval", "compose"]:
        return "approval" if state.get("proposal_id") else "compose"

    @staticmethod
    def after_assess(state: AgentState) -> Literal["generate", "insufficient"]:
        return "generate" if state.get("evidence_ok") else "insufficient"

    @staticmethod
    def after_verify(state: AgentState) -> Literal["generate", "done"]:
        return "generate" if state.get("feedback") else "done"

    # --- build ---------------------------------------------------------------------------------------------------
    def knowledge(self) -> Any:
        retry = RetryPolicy(max_attempts=2)
        graph = StateGraph(AgentState, context_schema=RunContext, output_schema=KnowledgeOutput)
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

    def business(self) -> Any:
        graph = StateGraph(AgentState, context_schema=RunContext, output_schema=BusinessOutput)
        graph.add_node("plan", self.plan)
        graph.add_node("tool_gateway", self.tool_gateway)
        graph.add_node("verify", self.verify_business)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges(
            "plan", self.after_plan, {"tool_gateway": "tool_gateway", "verify": "verify", "done": END}
        )
        graph.add_conditional_edges("tool_gateway", self.after_gateway, {"plan": "plan", "done": END})
        graph.add_conditional_edges("verify", self.after_verify_business, {"plan": "plan", "done": END})
        return graph.compile()

    def approval(self) -> Any:
        graph = StateGraph(AgentState, context_schema=RunContext, output_schema=ApprovalOutput)
        graph.add_node("render", self.render)
        graph.add_node("await_confirmation", self.await_confirmation)
        graph.add_node("validate", self.validate_decision)
        # a non-business failure after the claim may be retried: the claim and the request event keep it single
        graph.add_node(
            "execute", self.execute, retry_policy=RetryPolicy(max_attempts=3, retry_on=ExecutionInterrupted)
        )
        graph.add_node("record_rejection", self.record_rejection)
        graph.add_node("edit", self.edit)
        graph.add_edge(START, "render")
        graph.add_edge("render", "await_confirmation")
        graph.add_edge("await_confirmation", "validate")
        graph.add_conditional_edges(
            "validate",
            self.after_validate,
            {"execute": "execute", "record_rejection": "record_rejection", "edit": "edit", "done": END},
        )
        graph.add_edge("execute", END)
        graph.add_edge("record_rejection", END)
        graph.add_conditional_edges("edit", self.after_edit, {"render": "render", "done": END})
        return graph.compile()

    def build(self, checkpointer: Any) -> Any:
        graph = StateGraph(AgentState, context_schema=RunContext)
        graph.add_node("ingress", self.ingress)
        graph.add_node("classify", self.classify)
        graph.add_node("knowledge", self.knowledge())
        graph.add_node("business", self.business())
        graph.add_node("approval", self.approval())
        graph.add_node("templated", self.templated)
        graph.add_node("compose", self.compose)
        graph.add_edge(START, "ingress")
        graph.add_conditional_edges("ingress", self.after_ingress)
        graph.add_conditional_edges("classify", self.after_classify)
        graph.add_edge("knowledge", "compose")
        graph.add_conditional_edges("business", self.after_business)
        graph.add_edge("approval", "compose")
        graph.add_edge("templated", "compose")
        graph.add_edge("compose", END)
        return graph.compile(checkpointer=checkpointer)
