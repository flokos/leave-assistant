"""Team views (doc 09 §2.4) with the need-to-know rules of Q-09: managers see their department, HR the tenant,
company administrators absence only."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from leave_assistant.application.authz.policy import Action, ResourceRef, require
from leave_assistant.application.leave.common import tenant_user
from leave_assistant.application.leave.views import AbsenceRow, TeamRequestRow, reason_visible
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import RequestFilter, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.tenancy import Employee, Principal, Role

ABSENT_STATUSES = frozenset(
    {RequestStatus.APPROVED, RequestStatus.ACTIVE, RequestStatus.MODIFICATION_PENDING}
)


class TeamService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow = uow_factory

    async def _scope(
        self, uow: UnitOfWork, principal: Principal, action: Action, department_id: UUID | None = None
    ) -> tuple[bool, ResourceRef | None]:
        """Returns (tenant_wide, department resource). HR/company admins are tenant-wide; managers department-scoped
        — asking for another department (M7 tool argument) is a denial, decided by the same policy."""
        tenant_id, _ = tenant_user(principal)
        if principal.has_any(Role.HR_ADMIN, Role.COMPANY_ADMIN):
            require(principal, action, ResourceRef("team", tenant_id=tenant_id))
            return True, None
        target = department_id or principal.department_id
        department = await uow.departments.get(target) if target else None
        resource = ResourceRef(
            "department",
            target,
            tenant_id=tenant_id,
            department_id=target,
            department_manager_employee_id=department.manager_employee_id if department else None,
        )
        require(principal, action, resource)
        return False, resource

    async def employees(self, principal: Principal) -> list[Employee]:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            tenant_wide, _ = await self._scope(uow, principal, Action.READ_TEAM_REQUESTS)
            return await uow.employees.list_active(
                department_id=None if tenant_wide else principal.department_id
            )

    async def availability(
        self, principal: Principal, *, start: date, end: date, department_id: UUID | None = None
    ) -> list[AbsenceRow]:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            tenant_wide, _ = await self._scope(uow, principal, Action.READ_TEAM_AVAILABILITY, department_id)
            requests = await uow.leave_requests.list_in_window(start, end, statuses=ABSENT_STATUSES)
            employees = await uow.employees.get_many([r.employee_id for r in requests])
            types = {t.id: t for t in await uow.leave_types.list(include_inactive=True)}
            show_type = principal.has_any(Role.MANAGER, Role.HR_ADMIN)
            rows = []
            for request in requests:
                employee = employees.get(request.employee_id)
                if employee is None or (
                    not tenant_wide and employee.department_id != principal.department_id
                ):
                    continue
                if department_id is not None and employee.department_id != department_id:
                    continue
                leave_type = types.get(request.leave_type_id)
                rows.append(
                    AbsenceRow(
                        employee_id=employee.id,
                        display_name=employee.display_name,
                        department_id=employee.department_id,
                        start_date=max(request.start_date, start),
                        end_date=min(request.end_date, end),
                        status=request.status.value,
                        leave_type_code=leave_type.code if (show_type and leave_type) else None,
                    )
                )
            return sorted(rows, key=lambda r: (r.start_date, r.display_name))

    async def requests(
        self,
        principal: Principal,
        *,
        statuses: frozenset[RequestStatus] | None,
        limit: int | None,
        cursor: str | None,
    ) -> Page[TeamRequestRow]:
        size = clamp_limit(limit)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            if Role.COMPANY_ADMIN in principal.roles and not principal.has_any(Role.HR_ADMIN, Role.MANAGER):
                require(
                    principal, Action.READ_TEAM_LEAVE_TYPE
                )  # company administrators see absence only (denied here)
            tenant_wide, _ = await self._scope(uow, principal, Action.READ_TEAM_REQUESTS)
            rows = await uow.leave_requests.list(
                filters=RequestFilter(
                    department_id=None if tenant_wide else principal.department_id, statuses=statuses
                ),
                limit=size + 1,
                after=decode_cursor(cursor),
            )
            employees = await uow.employees.get_many([r.employee_id for r in rows])
            types = {t.id: t for t in await uow.leave_types.list(include_inactive=True)}
            items = [
                TeamRequestRow(
                    r,
                    employees[r.employee_id],
                    types[r.leave_type_id],
                    reason_visible(principal, r, types[r.leave_type_id]),
                )
                for r in rows
                if r.employee_id in employees and r.leave_type_id in types
            ]
        return build_page(items, size, lambda row: (row.request.created_at, row.request.id))
