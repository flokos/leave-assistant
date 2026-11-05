"""Leave schemas (doc 09 §4). Day quantities are exposed as numbers (multiples of 0.5, exact in JSON)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.leave.views import BalanceView, RequestDetail
from leave_assistant.domain.leave.ledger import LedgerEntry
from leave_assistant.domain.leave.request import ApprovalStep, LeaveRequest, RequestEventRecord
from leave_assistant.domain.leave.types import LeaveType
from leave_assistant.domain.leave.validation import ValidationReport
from leave_assistant.domain.leave.working_days import ExcludedDate, WorkingDays


def days(value: Decimal | int | float | object) -> float:
    return float(value)  # type: ignore[arg-type]


class LeaveTypeOut(ApiModel):
    id: UUID
    code: str
    name_el: str
    name_en: str
    balance_bearing: bool
    allow_retroactive_days: int
    requires_document_above_days: int | None
    active: bool

    @classmethod
    def from_domain(cls, t: LeaveType) -> LeaveTypeOut:
        return cls(
            id=t.id,
            code=t.code,
            name_el=t.name_el,
            name_en=t.name_en,
            balance_bearing=t.balance_bearing,
            allow_retroactive_days=t.allow_retroactive_days,
            requires_document_above_days=t.requires_document_above_days,
            active=t.active,
        )


class ExcludedDateOut(ApiModel):
    date: date
    reason: str
    name: str | None

    @classmethod
    def from_domain(cls, e: ExcludedDate) -> ExcludedDateOut:
        return cls(date=e.day, reason=e.reason, name=e.name)


class WorkingDaysOut(ApiModel):
    working_days: int
    excluded_dates: list[ExcludedDateOut]

    @classmethod
    def from_domain(cls, w: WorkingDays) -> WorkingDaysOut:
        return cls(working_days=w.count, excluded_dates=[ExcludedDateOut.from_domain(e) for e in w.excluded])


class PeriodOut(ApiModel):
    year: int
    start_date: date
    end_date: date
    working_days: int


class BalanceSnapshotOut(ApiModel):
    balance_days: float
    pending_days: float
    available_days: float
    after_days: float


class ViolationOut(ApiModel):
    code: str
    detail: str
    params: dict[str, Any]


class ValidationReportOut(ApiModel):
    valid: bool
    working_days: int
    excluded_dates: list[ExcludedDateOut]
    periods: list[PeriodOut]
    balance: BalanceSnapshotOut | None
    approval_chain: list[str]
    auto_acknowledged: bool
    violations: list[ViolationOut]
    warnings: list[ViolationOut]

    @classmethod
    def from_domain(cls, r: ValidationReport) -> ValidationReportOut:
        return cls(
            valid=r.valid,
            working_days=r.working_days,
            excluded_dates=[ExcludedDateOut.from_domain(e) for e in r.excluded_dates],
            periods=[
                PeriodOut(year=p.year, start_date=p.start, end_date=p.end, working_days=p.working_days)
                for p in r.periods
            ],
            balance=BalanceSnapshotOut(
                balance_days=days(r.balance.balance_days),
                pending_days=days(r.balance.pending_days),
                available_days=days(r.balance.available_days),
                after_days=days(r.balance.after_days),
            )
            if r.balance
            else None,
            approval_chain=[s.value for s in r.approval_chain],
            auto_acknowledged=r.auto_acknowledged,
            violations=[ViolationOut(code=v.code, detail=v.detail, params=v.params) for v in r.violations],
            warnings=[ViolationOut(code=v.code, detail=v.detail, params=v.params) for v in r.warnings],
        )


class BalanceOut(ApiModel):
    leave_type: str
    period_year: int
    balance_days: float
    pending_days: float
    available_days: float

    @classmethod
    def from_view(cls, v: BalanceView) -> BalanceOut:
        return cls(
            leave_type=v.leave_type.code,
            period_year=v.period_year,
            balance_days=days(v.balance_days),
            pending_days=days(v.pending_days),
            available_days=days(v.available_days),
        )


class LedgerEntryOut(ApiModel):
    id: UUID
    leave_type_id: UUID
    period_year: int
    entry_type: str
    quantity_days: float
    effective_date: date
    request_id: UUID | None
    reason: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, e: LedgerEntry) -> LedgerEntryOut:
        return cls(
            id=e.id,
            leave_type_id=e.leave_type_id,
            period_year=e.period_year,
            entry_type=e.entry_type.value,
            quantity_days=days(e.quantity_days),
            effective_date=e.effective_date,
            request_id=e.request_id,
            reason=e.reason,
            created_at=e.created_at,
        )


class StepOut(ApiModel):
    id: UUID
    step_no: int
    approver_role: str
    designated_employee_id: UUID | None
    status: str
    decided_by_employee_id: UUID | None
    decided_at: datetime | None
    comment: str | None
    via: str | None
    fallback: bool
    version: int

    @classmethod
    def from_domain(cls, s: ApprovalStep) -> StepOut:
        return cls(
            id=s.id,
            step_no=s.step_no,
            approver_role=s.approver_role.value,
            designated_employee_id=s.designated_employee_id,
            status=s.status.value,
            decided_by_employee_id=s.decided_by_employee_id,
            decided_at=s.decided_at,
            comment=s.comment,
            via=s.via.value if s.via else None,
            fallback=s.fallback,
            version=s.version,
        )


class EventOut(ApiModel):
    id: UUID
    event_type: str
    from_status: str | None
    to_status: str
    actor_employee_id: UUID | None
    actor_kind: str
    occurred_at: datetime
    payload: dict[str, Any]

    @classmethod
    def from_domain(cls, e: RequestEventRecord) -> EventOut:
        return cls(
            id=e.id,
            event_type=e.event_type,
            from_status=e.from_status.value if e.from_status else None,
            to_status=e.to_status.value,
            actor_employee_id=e.actor_employee_id,
            actor_kind=e.actor_kind.value,
            occurred_at=e.occurred_at,
            payload=e.payload,
        )


class LeaveRequestSummary(ApiModel):
    id: UUID
    request_number: str
    employee_id: UUID
    leave_type: str | None = Field(
        description="null when the viewer may only see the absence (company administrators)"
    )
    start_date: date
    end_date: date
    working_days: float
    status: str
    submitted_at: datetime | None
    decided_at: datetime | None
    cancelled_at: datetime | None
    modification_of_request_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, r: LeaveRequest, leave_type: str | None) -> LeaveRequestSummary:
        return cls(
            id=r.id,
            request_number=r.request_number,
            employee_id=r.employee_id,
            leave_type=leave_type,
            start_date=r.start_date,
            end_date=r.end_date,
            working_days=days(r.working_days),
            status=r.status.value,
            submitted_at=r.submitted_at,
            decided_at=r.decided_at,
            cancelled_at=r.cancelled_at,
            modification_of_request_id=r.modification_of_request_id,
            version=r.version,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )


class LeaveRequestOut(LeaveRequestSummary):
    reason: str | None
    steps: list[StepOut]
    events: list[EventOut]

    @classmethod
    def from_detail(cls, d: RequestDetail) -> LeaveRequestOut:
        summary = LeaveRequestSummary.from_domain(
            d.request, d.leave_type.code if d.leave_type_visible else None
        )
        return cls(
            **summary.model_dump(),
            reason=d.reason,
            steps=[StepOut.from_domain(s) for s in d.steps],
            events=[EventOut.from_domain(e) for e in d.events],
        )


class SubmitRequestIn(ApiModel):
    leave_type: str = Field(max_length=32, examples=["annual"])
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=1000)


class ModifyRequestIn(ApiModel):
    start_date: date
    end_date: date
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class CancelRequestIn(ApiModel):
    version: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=1000)
