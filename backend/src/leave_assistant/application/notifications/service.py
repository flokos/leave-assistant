"""Notification records + outbox events (FR-NOTIF-01/02, ADR-0019): written inside the business transaction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from leave_assistant.application.authz.policy import Action, ResourceRef, require
from leave_assistant.application.errors import NotFoundError
from leave_assistant.application.notifications.models import IntegrationEvent, Notification
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Principal

DEFAULT_CHANNELS = ("in_app", "email")


class NotificationService:
    def __init__(self, uow_factory: UnitOfWorkFactory, ids: IdGenerator, clock: Clock) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    async def notify(
        self,
        uow: UnitOfWork,
        recipients: Iterable[UUID],
        type: str,
        params: dict[str, Any],
        *,
        dedupe_key: str | None = None,
        channels: tuple[str, ...] = DEFAULT_CHANNELS,
    ) -> list[Notification]:
        tenant_id = uow.context.tenant_id if uow.context else None
        if tenant_id is None:
            raise RuntimeError("notifications need a tenant context")
        created: list[Notification] = []
        now = self._clock.now()
        for recipient in dict.fromkeys(recipients):  # distinct, order-preserving
            key = f"{dedupe_key}:{recipient}" if dedupe_key else None
            if key is not None and await uow.notifications.exists_dedupe(key):
                continue  # already notified: repeated triggers (reminders, retries) are harmless
            notification = Notification(
                id=self._ids.new_id(),
                tenant_id=tenant_id,
                recipient_employee_id=recipient,
                type=type,
                params=dict(params),
                channels=channels,
                created_at=now,
                dedupe_key=key,
            )
            await uow.notifications.add(notification)
            await uow.integration_events.add(
                IntegrationEvent(
                    id=self._ids.new_id(),
                    tenant_id=tenant_id,
                    type="notification.created",
                    payload={
                        "notification_id": str(notification.id),
                        "tenant_id": str(tenant_id),
                        "recipient_employee_id": str(recipient),
                        "notification_type": type,
                    },
                    created_at=now,
                )
            )
            created.append(notification)
        return created

    def _own(self, principal: Principal) -> ResourceRef:
        return ResourceRef(
            "notification", tenant_id=principal.tenant_id, owner_employee_id=principal.employee_id
        )

    async def list(
        self, principal: Principal, *, unread_only: bool, limit: int | None, cursor: str | None
    ) -> Page[Notification]:
        require(principal, Action.MANAGE_OWN_NOTIFICATIONS, self._own(principal))
        assert principal.employee_id is not None
        size = clamp_limit(limit)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            rows = await uow.notifications.list(
                recipient_employee_id=principal.employee_id,
                unread_only=unread_only,
                limit=size + 1,
                after=decode_cursor(cursor),
            )
        return build_page(rows, size, lambda n: (n.created_at, n.id))

    async def mark_read(self, principal: Principal, notification_id: UUID) -> Notification:
        require(principal, Action.MANAGE_OWN_NOTIFICATIONS, self._own(principal))
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            notification = await uow.notifications.get(notification_id)
            if notification is None or notification.recipient_employee_id != principal.employee_id:
                raise NotFoundError("notification not found")
            if notification.read_at is None:
                notification.read_at = self._clock.now()
                await uow.notifications.update(notification)
                await uow.commit()
        return notification

    async def mark_all_read(self, principal: Principal) -> int:
        require(principal, Action.MANAGE_OWN_NOTIFICATIONS, self._own(principal))
        assert principal.employee_id is not None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            count = await uow.notifications.mark_all_read(principal.employee_id, self._clock.now())
            await uow.commit()
        return count
