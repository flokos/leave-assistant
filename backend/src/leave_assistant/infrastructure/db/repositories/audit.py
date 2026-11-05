from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.audit.entry import ActorKind, AuditEntry, Outcome
from leave_assistant.application.ports import AuditFilter
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.infrastructure.db.models import AuditLogRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _to_domain(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        tenant_id=row.tenant_id,
        occurred_at=row.occurred_at,
        actor_kind=ActorKind(row.actor_kind),
        actor_id=row.actor_id,
        actor_roles=tuple(row.actor_roles),
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        outcome=Outcome(row.outcome),
        request_id=row.request_id,
        details=dict(row.details),
        thread_id=row.thread_id,
    )


class SqlAuditRepository:
    """INSERT + SELECT only (grants enforce it). Visibility is decided by the RLS policies (doc 07 §4.8)."""

    def __init__(self, session: AsyncSession, context: TenantContext | None) -> None:
        self._s = session
        self._ctx = context

    async def add(self, entry: AuditEntry) -> None:
        self._s.add(
            AuditLogRow(
                id=entry.id,
                tenant_id=entry.tenant_id,
                occurred_at=entry.occurred_at,
                actor_kind=entry.actor_kind.value,
                actor_id=entry.actor_id,
                actor_roles=list(entry.actor_roles),
                action=entry.action,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                outcome=entry.outcome.value,
                request_id=entry.request_id,
                thread_id=entry.thread_id,
                details=entry.details,
            )
        )
        await flush_or_conflict(self._s)

    async def list(
        self, *, filters: AuditFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[AuditEntry]:
        stmt = (
            select(AuditLogRow).order_by(AuditLogRow.occurred_at.desc(), AuditLogRow.id.desc()).limit(limit)
        )
        if filters.platform_only:
            stmt = stmt.where(AuditLogRow.tenant_id.is_(None))
        elif self._ctx is not None and self._ctx.tenant_id is not None:
            stmt = stmt.where(AuditLogRow.tenant_id == self._ctx.tenant_id)
        if filters.action:
            stmt = stmt.where(AuditLogRow.action == filters.action)
        if filters.occurred_from:
            stmt = stmt.where(AuditLogRow.occurred_at >= filters.occurred_from)
        if filters.occurred_to:
            stmt = stmt.where(AuditLogRow.occurred_at < filters.occurred_to)
        if after is not None:
            stmt = stmt.where(tuple_(AuditLogRow.occurred_at, AuditLogRow.id) < after)
        return [_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]
