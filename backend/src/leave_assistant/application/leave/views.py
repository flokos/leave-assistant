"""Read models for requests and the need-to-know masking of doc 08 §5.2 / Q-09."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from leave_assistant.domain.leave.request import ApprovalStep, LeaveRequest, RequestEventRecord
from leave_assistant.domain.leave.types import LeaveType
from leave_assistant.domain.tenancy import Employee, Principal, Role


def reason_visible(principal: Principal, request: LeaveRequest, leave_type: LeaveType) -> bool:
    """Owners and HR see reasons; managers only for non-sick leave; everybody else never."""
    if principal.employee_id == request.employee_id or Role.HR_ADMIN in principal.roles:
        return True
    if Role.MANAGER in principal.roles and not leave_type.is_sick:
        return True
    return False


def leave_type_visible(principal: Principal, request: LeaveRequest) -> bool:
    """Owners, HR and managers see the leave type; company administrators see absence only (Q-09)."""
    return principal.employee_id == request.employee_id or principal.has_any(Role.HR_ADMIN, Role.MANAGER)


@dataclass(frozen=True, slots=True)
class RequestDetail:
    request: LeaveRequest
    leave_type: LeaveType
    employee: Employee
    steps: list[ApprovalStep]
    events: list[RequestEventRecord]
    reason_visible: bool
    leave_type_visible: bool = True
    supporting_count: int = 0  # M5: visible to the owner and HR; managers/administrators always see 0

    @property
    def reason(self) -> str | None:
        return self.request.reason if self.reason_visible else None


@dataclass(frozen=True, slots=True)
class PendingItem:
    step: ApprovalStep
    request: LeaveRequest
    requester: Employee
    leave_type: LeaveType
    fallback: bool  # an HR administrator deciding a manager step
    reason_visible: bool
    supporting_count: int = 0  # M5: HR only


@dataclass(frozen=True, slots=True)
class AbsenceRow:
    employee_id: UUID
    display_name: str
    department_id: UUID
    start_date: date
    end_date: date
    status: str
    leave_type_code: str | None  # None for viewers who may only see absence (company administrators)


@dataclass(frozen=True, slots=True)
class TeamRequestRow:
    request: LeaveRequest
    employee: Employee
    leave_type: LeaveType
    reason_visible: bool


@dataclass(frozen=True, slots=True)
class BalanceView:
    leave_type: LeaveType
    period_year: int
    balance_days: object
    pending_days: object
    available_days: object
