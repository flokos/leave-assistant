"""Proposals and pending interrupts (doc 07 §4.3/§4.6): business values live here, in the database — the graph state
only carries the proposal id (doc 04 §2 invariant 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from leave_assistant.domain.agent.proposals import ProposalKind, ProposalStatus


@dataclass(slots=True)
class ActionProposal:
    id: UUID  # doubles as the idempotency key of the execution
    tenant_id: UUID
    conversation_id: UUID
    employee_id: UUID  # who prepared it — the only principal who may confirm it
    kind: ProposalKind
    payload: dict[str, Any]  # validated, real values (dates, leave type, request/step ids, comment)
    payload_hash: str
    status: ProposalStatus
    expires_at: datetime
    created_at: datetime
    run_id: UUID | None = None  # the run that prepared it
    claimed_by_run: UUID | None = None
    claimed_at: datetime | None = None
    decided_at: datetime | None = None
    executed_request_id: UUID | None = None
    outcome: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PendingInterrupt:
    """Mirror of the open LangGraph interrupt of a conversation (one at a time) for the UI and cleanup."""

    conversation_id: UUID
    tenant_id: UUID
    proposal_id: UUID
    expires_at: datetime
    created_at: datetime
