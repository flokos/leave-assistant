"""Audit service (doc 08 §10): entries are written in the caller's transaction, never fire-and-forget."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from enum import Enum
from typing import Any
from uuid import UUID

from leave_assistant.application.audit.entry import ActorKind, AuditEntry, Outcome
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.request_context import current_request_id
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Principal

_FORBIDDEN_DETAIL_KEYS = frozenset({"password", "temporary_password", "token", "access_token", "secret"})


def json_safe(value: Any) -> Any:
    """Make details JSON-serialisable and strip secrets defensively (details never carry PII beyond IDs)."""
    if isinstance(value, Mapping):
        return {
            str(k): json_safe(v) for k, v in value.items() if str(k).lower() not in _FORBIDDEN_DETAIL_KEYS
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def actor_kind_for(principal: Principal | None) -> ActorKind:
    if principal is None:
        return ActorKind.SYSTEM
    if principal.is_automation:
        return ActorKind.AUTOMATION
    return ActorKind.USER


class AuditService:
    def __init__(self, clock: Clock, ids: IdGenerator) -> None:
        self._clock = clock
        self._ids = ids

    async def record(
        self,
        uow: UnitOfWork,
        principal: Principal | None,
        action: str,
        *,
        resource_type: str,
        resource_id: UUID | str | None = None,
        outcome: Outcome = Outcome.SUCCESS,
        details: Mapping[str, Any] | None = None,
        actor_kind: ActorKind | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            id=self._ids.new_id(),
            tenant_id=uow.context.tenant_id if uow.context else None,
            occurred_at=self._clock.now(),
            actor_kind=actor_kind or actor_kind_for(principal),
            actor_id=principal.user_id if principal else "system",
            actor_roles=tuple(sorted(r.value for r in principal.roles)) if principal else (),
            action=action,
            resource_type=resource_type,
            resource_id=None if resource_id is None else str(resource_id),
            outcome=outcome,
            request_id=current_request_id(),
            details=json_safe(dict(details or {})),
        )
        await uow.audit.add(entry)
        return entry

    async def record_denial(
        self, uow_factory: UnitOfWorkFactory, principal: Principal, action: str, resource: str | None
    ) -> None:
        """Denials are committed in their own transaction: the denied request has no transaction to join."""
        resource_type, _, resource_id = (resource or "action").partition(":")
        async with uow_factory(TenantContext.for_principal(principal)) as uow:
            await self.record(
                uow,
                principal,
                "authz.denied",
                resource_type=resource_type or "action",
                resource_id=resource_id or None,
                outcome=Outcome.DENIED,
                details={"action": action},
            )
            await uow.commit()
