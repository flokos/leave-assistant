"""Leave request aggregate, approval steps and immutable events (doc 07 §4.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.types import StepRole


class StepStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ActorKind(StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    HR = "hr"
    SYSTEM = "system"
    AGENT = "agent"


class DecisionChannel(StrEnum):
    UI = "ui"
    AGENT = "agent"


@dataclass(slots=True)
class LeaveRequest:
    id: UUID
    tenant_id: UUID
    request_number: str
    employee_id: UUID
    leave_type_id: UUID
    start_date: date
    end_date: date
    working_days: Decimal
    status: RequestStatus
    reason: str | None
    submitted_at: datetime | None
    decided_at: datetime | None
    cancelled_at: datetime | None
    modification_of_request_id: UUID | None
    idempotency_key: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    def overlaps(self, start: date, end: date) -> bool:
        return self.start_date <= end and start <= self.end_date


@dataclass(slots=True)
class ApprovalStep:
    id: UUID
    tenant_id: UUID
    request_id: UUID
    step_no: int
    approver_role: StepRole
    designated_employee_id: UUID | None
    status: StepStatus
    decided_by_employee_id: UUID | None
    decided_at: datetime | None
    comment: str | None
    via: DecisionChannel | None
    fallback: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RequestEventRecord:
    id: UUID
    tenant_id: UUID
    request_id: UUID
    event_type: str
    from_status: RequestStatus | None
    to_status: RequestStatus
    actor_employee_id: UUID | None
    actor_kind: ActorKind
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
