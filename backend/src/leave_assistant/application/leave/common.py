"""Helpers shared by the leave services."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from leave_assistant.application.errors import AuthorizationError, InvalidInputError, NotFoundError
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWork
from leave_assistant.application.request_context import current_origin
from leave_assistant.domain.leave.request import ActorKind, LeaveRequest, RequestEventRecord
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.types import LeaveType
from leave_assistant.domain.tenancy import Employee, Principal, Role, Tenant


def tenant_user(principal: Principal) -> tuple[UUID, UUID]:
    if principal.tenant_id is None or principal.employee_id is None:
        raise AuthorizationError("tenant context required", action="leave")
    return principal.tenant_id, principal.employee_id


def actor_kind(principal: Principal | None) -> ActorKind:
    if principal is None:
        return ActorKind.SYSTEM
    if Role.HR_ADMIN in principal.roles:
        return ActorKind.HR
    if Role.MANAGER in principal.roles:
        return ActorKind.MANAGER
    return ActorKind.EMPLOYEE


async def load_tenant(uow: UnitOfWork, tenant_id: UUID) -> Tenant:
    tenant = await uow.tenants.get(tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found")
    return tenant


async def load_employee(uow: UnitOfWork, employee_id: UUID) -> Employee:
    employee = await uow.employees.get(employee_id)
    if employee is None:
        raise NotFoundError("employee not found")
    return employee


async def load_leave_type(uow: UnitOfWork, code: str, *, field: str = "leave_type") -> LeaveType:
    leave_type = await uow.leave_types.get_by_code(code.strip().lower())
    if leave_type is None or not leave_type.active:
        raise InvalidInputError(
            f"unknown or inactive leave type {code!r}",
            errors=[{"loc": ["body", field], "msg": "unknown leave type"}],
        )
    return leave_type


class EventWriter:
    def __init__(self, ids: IdGenerator, clock: Clock) -> None:
        self._ids = ids
        self._clock = clock

    async def record(
        self,
        uow: UnitOfWork,
        request: LeaveRequest,
        event_type: str,
        *,
        from_status: RequestStatus | None,
        to_status: RequestStatus,
        principal: Principal | None,
        payload: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> RequestEventRecord:
        event = RequestEventRecord(
            id=self._ids.new_id(),
            tenant_id=request.tenant_id,
            request_id=request.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor_employee_id=principal.employee_id if principal else None,
            actor_kind=actor_kind(principal),
            occurred_at=at or self._clock.now(),
            payload={**dict(payload or {}), **(current_origin() or {})},
        )
        await uow.request_events.add(event)
        return event
