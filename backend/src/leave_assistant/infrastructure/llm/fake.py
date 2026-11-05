"""FakeChatModel (doc 04 §12): deterministic responses for tests, scenario runs and offline development.

Behaviour per output schema (by field names): intent → keyword classifier; query → the last user turn; answer +
citations → an extractive answer from the first `<document id="n">` block, citing it; summary → the last turns
truncated. Scripts keyed by prompt id override the defaults; faults (`ProviderError`) can be queued.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any, TypeVar

from pydantic import BaseModel

from leave_assistant.application.llm.ports import ModelRole, Prompt, Usage, prompt_text
from leave_assistant.domain.agent.types import ProviderError

T = TypeVar("T", bound=BaseModel)
DOCUMENT = re.compile(r'<document id="(\d+)" title="([^"]*)" section="([^"]*)">\n?(.*?)\n?</document>', re.S)
TARGET = re.compile(r"Answer in (Greek|English)")
GREEK_LETTERS = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
LATIN_LETTERS = re.compile(r"[A-Za-z]")
USER = re.compile(r"<user>\n?(.*?)\n?</user>", re.S)
BUSINESS = {
    "balance": ("υπόλοιπο", "απομένουν", "balance", "how many days do i have", "days left"),
    "history": ("ιστορικό", "history", "my requests"),
    "availability": ("ποιος λείπει", "availability", "who is away", "who is off"),
    "request": ("θέλω άδεια", "θέλω", "request leave", "book", "i want to take"),
    "cancel": ("ακύρωσ", "cancel"),
    "approval_action": ("έγκρισ", "approve", "εκκρεμεί για έγκριση"),
}
SMALLTALK = ("γεια", "καλημέρα", "ευχαριστώ", "hello", "hi ", "thanks", "thank you")


def _first_sentences(text: str, limit: int = 2) -> str:
    body = "\n".join(line for line in text.splitlines() if not line.startswith(("#", "|")))
    sentences = re.split(r"(?<=[.;!?])\s+", " ".join(body.split()))
    return " ".join(sentences[:limit]).strip()


class FakeChatModel:
    def __init__(
        self,
        name: str = "fake",
        *,
        script: dict[str, list[Any]] | None = None,
        fast_model: str = "fake-fast",
        answer_model: str = "fake-answer",
    ) -> None:
        self._name = name
        self._models = {ModelRole.FAST: fast_model, ModelRole.ANSWER: answer_model}
        self.script: dict[str, deque[Any]] = defaultdict(
            deque, {k: deque(v) for k, v in (script or {}).items()}
        )
        self.faults: deque[ProviderError] = deque()
        self.calls: list[tuple[str, ModelRole, str]] = []

    @property
    def name(self) -> str:
        return self._name

    def model_for(self, role: ModelRole) -> str:
        return self._models[role]

    def fail_next(self, *errors: ProviderError) -> None:
        self.faults.extend(errors)

    async def structured(
        self, prompt: Prompt, schema: type[T], *, role: ModelRole, deadline_seconds: float
    ) -> tuple[T, Usage]:
        text = prompt_text(prompt.messages)
        self.calls.append((prompt.id, role, text))
        if self.faults:
            raise self.faults.popleft()
        key = prompt.id.split("@", 1)[0]
        if self.script.get(key):
            item = self.script[key].popleft()
            if isinstance(item, Exception):
                raise item
            value = item if isinstance(item, BaseModel) else schema.model_validate(item)
            return value, Usage(len(text) // 4, 20)  # type: ignore[return-value]
        return schema.model_validate(self._default(schema, text)), Usage(len(text) // 4, 40)

    def _default(self, schema: type[BaseModel], text: str) -> dict[str, Any]:
        fields = set(schema.model_fields)
        users = USER.findall(text)
        last_user = users[-1].strip() if users else ""
        if "intent" in fields:
            folded = last_user.casefold()
            for intent, needles in BUSINESS.items():
                if any(re.search(rf"(?<!\w){re.escape(n)}", folded) for n in needles):
                    return {"intent": intent, "confidence": 0.9}
            if any(folded.startswith(s.strip()) for s in SMALLTALK) and len(folded) < 40:
                return {"intent": "smalltalk", "confidence": 0.9}
            return {"intent": "policy_question", "confidence": 0.9}
        if "query" in fields:
            return {"query": last_user}
        if "answer" in fields:
            documents = DOCUMENT.findall(text)
            if not documents:
                return {"answer": "", "citations": [], "confidence": "low"}
            n, title, section, body = documents[0]
            target = TARGET.search(text)
            greek_source = len(GREEK_LETTERS.findall(body)) >= len(LATIN_LETTERS.findall(body))
            if (
                target and target.group(1) == "Greek" and not greek_source
            ):  # a model answers in the user's language
                return {
                    "answer": f"Σύμφωνα με την πηγή [{n}], η απάντηση βρίσκεται στην ενότητα «{section}».",
                    "citations": [int(n)],
                    "confidence": "medium",
                }
            if target and target.group(1) == "English" and greek_source:
                return {
                    "answer": f"According to source [{n}], the answer is in the section “{section}”.",
                    "citations": [int(n)],
                    "confidence": "medium",
                }
            return {"answer": f"{_first_sentences(body)} [{n}]", "citations": [int(n)], "confidence": "high"}
        if "summary" in fields:
            return {"summary": " ".join(text.split())[-600:]}
        raise ValueError(f"no fake behaviour for {schema.__name__}")
