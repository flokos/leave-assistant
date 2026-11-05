from __future__ import annotations

from datetime import date
from uuid import UUID

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.api.schemas.leave import LeaveRequestSummary
from leave_assistant.application.leave.views import AbsenceRow, TeamRequestRow
from leave_assistant.domain.tenancy import Employee


class TeamEmployeeOut(ApiModel):
    id: UUID
    employee_number: str
    first_name: str
    last_name: str
    department_id: UUID

    @classmethod
    def from_domain(cls, e: Employee) -> TeamEmployeeOut:
        return cls(
            id=e.id,
            employee_number=e.employee_number,
            first_name=e.first_name,
            last_name=e.last_name,
            department_id=e.department_id,
        )


class AbsenceOut(ApiModel):
    employee_id: UUID
    display_name: str
    department_id: UUID
    start_date: date
    end_date: date
    status: str
    leave_type: str | None

    @classmethod
    def from_view(cls, r: AbsenceRow) -> AbsenceOut:
        return cls(
            employee_id=r.employee_id,
            display_name=r.display_name,
            department_id=r.department_id,
            start_date=r.start_date,
            end_date=r.end_date,
            status=r.status,
            leave_type=r.leave_type_code,
        )


class TeamRequestOut(ApiModel):
    request: LeaveRequestSummary
    employee_display_name: str
    reason: str | None

    @classmethod
    def from_view(cls, row: TeamRequestRow) -> TeamRequestOut:
        return cls(
            request=LeaveRequestSummary.from_domain(row.request, row.leave_type.code),
            employee_display_name=row.employee.display_name,
            reason=row.request.reason if row.reason_visible else None,
        )
