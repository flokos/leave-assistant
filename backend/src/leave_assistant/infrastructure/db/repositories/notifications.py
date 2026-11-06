"""Notification records and the integration-event outbox."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.notifications.models import EmailStatus, IntegrationEvent, Notification
from leave_assistant.infrastructure.db.models import IntegrationEventRow, NotificationRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _to_domain(r: NotificationRow) -> Notification:
    return Notification(
        id=r.id,
        tenant_id=r.tenant_id,
        recipient_employee_id=r.recipient_employee_id,
        type=r.type,
        params=dict(r.params),
        channels=tuple(r.channels),
        created_at=r.created_at,
        read_at=r.read_at,
        email_status=EmailStatus(r.email_status),
        email_attempts=r.email_attempts,
        dedupe_key=r.dedupe_key,
    )


class SqlNotificationRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[NotificationRow]:
        return select(NotificationRow).where(NotificationRow.tenant_id == self._tenant)

    async def add(self, notification: Notification) -> None:
        self._s.add(
            NotificationRow(
                id=notification.id,
                tenant_id=notification.tenant_id,
                recipient_employee_id=notification.recipient_employee_id,
                type=notification.type,
                params=notification.params,
                channels=list(notification.channels),
                read_at=notification.read_at,
                email_status=notification.email_status.value,
                email_attempts=notification.email_attempts,
                dedupe_key=notification.dedupe_key,
                created_at=notification.created_at,
            )
        )
        await flush_or_conflict(self._s)

    async def get(self, notification_id: UUID) -> Notification | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(NotificationRow.id == notification_id))
        ).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list(
        self,
        *,
        recipient_employee_id: UUID,
        unread_only: bool,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> builtins.list[Notification]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(NotificationRow.recipient_employee_id == recipient_employee_id)
            .order_by(NotificationRow.created_at.desc(), NotificationRow.id.desc())
            .limit(limit)
        )
        if unread_only:
            stmt = stmt.where(NotificationRow.read_at.is_(None))
        if after is not None:
            stmt = stmt.where(tuple_(NotificationRow.created_at, NotificationRow.id) < after)
        return [_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def update(self, notification: Notification) -> None:
        row = (await self._s.execute(self._base().where(NotificationRow.id == notification.id))).scalar_one()
        row.read_at = notification.read_at
        row.email_status = notification.email_status.value
        row.email_attempts = notification.email_attempts
        await flush_or_conflict(self._s)

    async def exists_dedupe(self, dedupe_key: str) -> bool:
        stmt = select(NotificationRow.id).where(
            NotificationRow.tenant_id == self._tenant, NotificationRow.dedupe_key == dedupe_key
        )
        return (await self._s.execute(stmt.limit(1))).scalar_one_or_none() is not None

    async def mark_all_read(self, recipient_employee_id: UUID, read_at: datetime) -> int:
        result = await self._s.execute(
            update(NotificationRow)
            .where(
                NotificationRow.tenant_id == self._tenant,
                NotificationRow.recipient_employee_id == recipient_employee_id,
                NotificationRow.read_at.is_(None),
            )
            .values(read_at=read_at)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class SqlIntegrationEventRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, event: IntegrationEvent) -> None:
        self._s.add(
            IntegrationEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                type=event.type,
                payload=event.payload,
                created_at=event.created_at,
                delivered_at=event.delivered_at,
                attempts=event.attempts,
                dead=event.dead,
            )
        )
        await flush_or_conflict(self._s)

    async def lease(
        self, *, now: datetime, until: datetime, limit: int, max_attempts: int
    ) -> builtins.list[IntegrationEvent]:
        live = (
            IntegrationEventRow.tenant_id == self._tenant,
            IntegrationEventRow.delivered_at.is_(None),
            IntegrationEventRow.dead.is_(False),
            or_(IntegrationEventRow.leased_until.is_(None), IntegrationEventRow.leased_until < now),
        )
        # events that keep failing stop being offered (dead letter, counted in /platform/health)
        await self._s.execute(
            update(IntegrationEventRow)
            .where(*live, IntegrationEventRow.attempts >= max_attempts)
            .values(dead=True)
        )
        rows = (
            (
                await self._s.execute(
                    select(IntegrationEventRow)
                    .where(*live)
                    .order_by(IntegrationEventRow.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.leased_until = until
            row.attempts = row.attempts + 1
        await flush_or_conflict(self._s)
        return [_event_to_domain(r) for r in rows]

    async def ack(self, ids: Sequence[UUID], now: datetime) -> int:
        if not ids:
            return 0
        result = await self._s.execute(
            update(IntegrationEventRow)
            .where(
                IntegrationEventRow.tenant_id == self._tenant,
                IntegrationEventRow.id.in_(list(ids)),
                IntegrationEventRow.delivered_at.is_(None),
            )
            .values(delivered_at=now, leased_until=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def mark_dead(self, ids: Sequence[UUID]) -> int:
        if not ids:
            return 0
        result = await self._s.execute(
            update(IntegrationEventRow)
            .where(
                IntegrationEventRow.tenant_id == self._tenant,
                IntegrationEventRow.id.in_(list(ids)),
                IntegrationEventRow.delivered_at.is_(None),
            )
            .values(dead=True, leased_until=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def counts(self) -> dict[str, int]:
        row = (
            await self._s.execute(
                select(
                    func.count().filter(IntegrationEventRow.dead.is_(True)),
                    func.count().filter(
                        IntegrationEventRow.delivered_at.is_(None), IntegrationEventRow.dead.is_(False)
                    ),
                ).where(IntegrationEventRow.tenant_id == self._tenant)
            )
        ).one()
        return {"dead": int(row[0]), "undelivered": int(row[1])}

    async def purge_delivered(self, before: datetime) -> int:
        result = await self._s.execute(
            delete(IntegrationEventRow).where(
                IntegrationEventRow.tenant_id == self._tenant,
                IntegrationEventRow.delivered_at.is_not(None),
                IntegrationEventRow.delivered_at < before,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)


def _event_to_domain(r: IntegrationEventRow) -> IntegrationEvent:
    return IntegrationEvent(
        id=r.id,
        tenant_id=r.tenant_id,
        type=r.type,
        payload=dict(r.payload),
        created_at=r.created_at,
        delivered_at=r.delivered_at,
        attempts=r.attempts,
        dead=r.dead,
        leased_until=r.leased_until,
    )
