"""Chat schemas (doc 09 §2.2). Message text is re-identified for the owner at read time."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.assistant.proposals import ProposalCard
from leave_assistant.application.chat.service import ConversationView, MessageView, RunView


class ProposalCardOut(ApiModel):
    """The confirmation card (doc 09 §4 ProposalCard): rendered from the stored proposal for its owner."""

    proposal_id: UUID
    kind: Literal["submit", "modify", "cancel", "approve", "reject"]
    status: Literal["pending", "confirmed", "executed", "rejected", "expired", "failed"]
    payload_hash: str
    expires_at: datetime
    summary: dict[str, Any]
    actions: list[Literal["confirm", "reject", "edit"]]
    outcome: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_card(cls, card: ProposalCard) -> ProposalCardOut:
        return cls(
            proposal_id=card.proposal_id,
            kind=card.kind.value,
            status=card.status.value,
            payload_hash=card.payload_hash,
            expires_at=card.expires_at,
            summary=card.summary,
            actions=list(card.actions),  # type: ignore[arg-type]
            outcome=card.outcome,
        )


class ConversationOut(ApiModel):
    id: UUID
    title: str
    created_at: datetime
    last_activity_at: datetime
    pending_proposal: ProposalCardOut | None = None  # M7: the open confirmation card, if any

    @classmethod
    def from_view(cls, view: ConversationView) -> ConversationOut:
        c = view.conversation
        return cls(
            id=c.id,
            title=view.title,
            created_at=c.created_at,
            last_activity_at=c.last_activity_at,
            pending_proposal=ProposalCardOut.from_card(view.pending) if view.pending else None,
        )


class CitationItem(ApiModel):
    n: int
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    version_no: int
    title: str
    heading_path: list[str]
    page: int | None


class MessageOut(ApiModel):
    id: UUID
    role: str
    text: str
    citations: list[CitationItem]
    outcome: str | None
    created_at: datetime
    card: ProposalCardOut | None = None  # M7: the card this message carried, with its current status

    @classmethod
    def from_view(cls, view: MessageView) -> MessageOut:
        m = view.message
        return cls(
            id=m.id,
            role=m.role.value,
            text=view.text,
            citations=[CitationItem(**c) for c in m.citations],
            outcome=m.outcome,
            created_at=m.created_at,
            card=ProposalCardOut.from_card(view.card) if view.card else None,
        )


class ResumeEditsIn(ApiModel):
    """Structured edits only (dates, leave type): the proposal is re-prepared deterministically."""

    start_date: date | None = None
    end_date: date | None = None
    leave_type: str | None = Field(None, max_length=32, pattern=r"^[a-z_]{2,32}$")


class ResumeIn(ApiModel):
    proposal_id: UUID
    decision: Literal["confirm", "reject", "edit"]
    payload_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    edits: ResumeEditsIn | None = None


class ToolTraceItem(ApiModel):
    name: str
    status: str
    duration_ms: int
    reason: str | None = None


class RunProposalOut(ApiModel):
    id: UUID
    kind: str
    status: str


class RunOut(ApiModel):
    """Redacted run history (doc 04 §7): stages, tool trace, provider and timings — no prompts, no arguments."""

    id: UUID
    turn: int
    intent: str | None
    route: str | None
    outcome: str
    language: str | None
    provider: str | None
    model: str | None
    failovers: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    stages: list[str]
    tool_trace: list[ToolTraceItem]
    proposals: list[RunProposalOut]
    created_at: datetime

    @classmethod
    def from_view(cls, view: RunView) -> RunOut:
        r = view.run
        return cls(
            id=r.id,
            turn=r.turn,
            intent=r.intent,
            route=r.route,
            outcome=r.outcome,
            language=r.language,
            provider=r.provider_used,
            model=r.model_used,
            failovers=r.failovers,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            latency_ms=r.latency_ms,
            stages=[str(s) for s in r.details.get("stages", [])],
            tool_trace=[
                ToolTraceItem(
                    name=str(t.get("name")),
                    status=str(t.get("status")),
                    duration_ms=int(t.get("duration_ms") or 0),
                    reason=t.get("reason"),
                )
                for t in r.tool_trace
            ],
            proposals=[
                RunProposalOut(id=p.id, kind=p.kind.value, status=p.status.value) for p in view.proposals
            ],
            created_at=r.created_at,
        )


class SendMessageIn(ApiModel):
    text: str = Field(min_length=1, max_length=2000)


def sse_examples() -> dict[str, Any]:
    return {
        "text/event-stream": {
            "schema": {"type": "string"},
            "example": 'event: token\ndata: {"text": "Κάθε εργαζόμενος δικαιούται"}\n\n',
        }
    }


def resume_examples() -> dict[str, Any]:
    return {
        "text/event-stream": {
            "schema": {"type": "string"},
            "example": 'event: status\ndata: {"stage": "executing"}\n\nevent: done\ndata: {"outcome": "executed"}\n\n',
        }
    }
