"""Agent value types (doc 04 §2): intents, answers, citations, run outcomes, provider error taxonomy (doc 04 §9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class Intent(StrEnum):
    POLICY_QUESTION = "policy_question"
    BALANCE = "balance"
    HISTORY = "history"
    AVAILABILITY = "availability"
    REQUEST = "request"
    MODIFY = "modify"
    CANCEL = "cancel"
    APPROVAL_ACTION = "approval_action"
    SMALLTALK = "smalltalk"
    OUT_OF_SCOPE = "out_of_scope"
    AMBIGUOUS = "ambiguous"


BUSINESS_INTENTS: frozenset[Intent] = frozenset(
    {
        Intent.BALANCE,
        Intent.HISTORY,
        Intent.AVAILABILITY,
        Intent.REQUEST,
        Intent.MODIFY,
        Intent.CANCEL,
        Intent.APPROVAL_ACTION,
    }
)
CLARIFY_CONFIDENCE = 0.6


class Outcome(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CLARIFY = "clarify"
    SCOPE = "scope"
    BLOCKED = "blocked"
    ERROR = "error"
    # M7: business operations
    INTERRUPTED = "interrupted"  # a confirmation card is waiting for the user
    EXECUTED = "executed"
    REJECTED = "rejected"  # the user declined the proposal (or it was superseded / expired)
    FAILED = "failed"  # the confirmed operation broke a business rule at execution time
    # M10 (R-36): the verifier rejected every draft or the plan loop hit its cap; the text is the deterministic fallback
    FALLBACK = "fallback"


class ProviderErrorKind(StrEnum):
    TRANSIENT = "transient"  # 429, 5xx, timeouts, network → retry, then fail over
    INVALID_OUTPUT = "invalid_output"  # schema mismatch → one repair on the same provider
    CONTENT_BLOCKED = "content_blocked"  # provider safety block → no retry
    FATAL = "fatal"  # bad key, unknown model → fail fast, provider marked down


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, provider: str, model: str, detail: str = "") -> None:
        super().__init__(f"{provider}/{model}: {kind.value}: {detail}"[:500])
        self.kind = kind
        self.provider = provider
        self.model = model
        self.detail = detail[:500]


class AllProvidersFailed(Exception):
    """Every provider in the policy failed for this call (or is down). The chat answers with a safe error."""

    def __init__(self, errors: list[ProviderError]) -> None:
        super().__init__("; ".join(str(e) for e in errors)[:800] or "no provider available")
        self.errors = errors


class BudgetExceeded(Exception):
    """A prompt exceeded the hard per-call input budget (doc 04 §8) — a bug in the builder, never retried."""


@dataclass(frozen=True, slots=True)
class CitationRef:
    """Citation `n` of an answer, mapped to a retrieved chunk (doc 05 §6.5)."""

    n: int
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    version_no: int
    title: str
    heading_path: tuple[str, ...]
    page: int | None


@dataclass(frozen=True, slots=True)
class AssistantAnswer:
    """The composed answer (pseudonymised text) — re-identified by the API layer for the principal."""

    text: str
    outcome: Outcome
    citations: tuple[CitationRef, ...] = field(default_factory=tuple)
    confidence: str | None = None
