"""Notification records and the integration-event outbox."""

from __future__ import annotations

import builtins
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select, tuple_, update
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
