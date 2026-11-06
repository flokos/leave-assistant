"""Reconciliation (doc 10 §5, M8 decision 5): expected events vs the app's events in each calendar.

`missing` → create and `mismatched` → update are enqueued automatically; an `orphaned` event is deleted only when
its request is known and was withdrawn, rejected or cancelled, otherwise it is flagged. `unknown_external_id` (the
event is right but the mapping row is lost) is flagged for `relink`. Every manual action is audited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, ResourceRef, require
from leave_assistant.application.calendar.errors import CalendarNotConnected
from leave_assistant.application.calendar.models import (
    CalendarConnection,
    CalendarEventRecord,
    CalendarProviderError,
    ReconciliationItem,
)
from leave_assistant.application.calendar.ports import CalendarProvider
from leave_assistant.application.calendar.sync import CalendarSyncService, system_context
from leave_assistant.application.errors import ConflictError, NotFoundError
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.calendar.model import (
    APP_MARKER,
    ConnectionKind,
    ConnectionStatus,
    EventStatus,
    Finding,
    ItemStatus,
    SyncOperation,
    event_id,
    stored_hash,
)
from leave_assistant.domain.leave.request import LeaveRequest
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.tenancy import Principal

WINDOW_BEFORE = timedelta(days=30)
WINDOW_AFTER = timedelta(days=365)
EXPECTED = frozenset(
    {
        RequestStatus.APPROVED,
        RequestStatus.ACTIVE,
        RequestStatus.COMPLETED,
        RequestStatus.MODIFICATION_PENDING,
    }
)
REMOVED = frozenset({RequestStatus.CANCELLED, RequestStatus.WITHDRAWN, RequestStatus.REJECTED})
ITEM_ACTIONS = ("relink", "ignore", "delete-orphan")


@dataclass(slots=True)
class ReconciliationReport:
    run_id: UUID | None
    run_at: datetime | None
    items: list[ReconciliationItem] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # connection id → classified provider error

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            out[item.finding.value] = out.get(item.finding.value, 0) + 1
        return out


class ReconciliationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        provider: CalendarProvider,
        sync: CalendarSyncService,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._provider = provider
        self._sync = sync

    # -- runs ----------------------------------------------------------------------------------------------------
    async def run_for(self, principal: Principal) -> ReconciliationReport:
        require(
            principal, Action.MANAGE_CALENDAR_COMPANY, ResourceRef("calendar", tenant_id=principal.tenant_id)
        )
        assert principal.tenant_id is not None
        report = await self.run(principal.tenant_id)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await self._audit.record(
                uow,
                principal,
                "calendar.reconciliation.run",
                resource_type="calendar",
                resource_id=report.run_id,
                details={"findings": report.counts(), "errors": len(report.errors)},
            )
            await uow.commit()
        return report

    async def run_all(self) -> dict[str, Any]:
        async with self._uow(
            TenantContext(tenant_id=None, user_id="calendar.reconcile", platform_admin=True)
        ) as uow:
            tenants = await uow.tenants.list(limit=500, after=None)
        totals: dict[str, int] = {}
        for tenant in tenants:
            report = await self.run(tenant.id)
            for finding, n in report.counts().items():
                totals[finding] = totals.get(finding, 0) + n
        return {"tenants": len(tenants), "findings": totals}

    async def run(self, tenant_id: UUID) -> ReconciliationReport:
        now = self._clock.now()
        run_id = self._ids.new_id()
        report = ReconciliationReport(run_id, now)
        async with self._uow(system_context(tenant_id)) as uow:
            connections = [
                c for c in await uow.calendar_connections.list_live() if c.status is ConnectionStatus.ACTIVE
            ]
            requests = await uow.leave_requests.list_in_window(
                (now - WINDOW_BEFORE).date(), (now + WINDOW_AFTER).date(), statuses=EXPECTED | REMOVED
            )
        for connection in connections:
            try:
                report.items.extend(await self._connection(tenant_id, connection, requests, run_id, now))
            except CalendarProviderError as exc:
                report.errors[str(connection.id)] = exc.error_class.value
        return report

    async def _connection(
        self,
        tenant_id: UUID,
        connection: CalendarConnection,
        requests: list[LeaveRequest],
        run_id: UUID,
        now: datetime,
    ) -> list[ReconciliationItem]:
        mine = [
            r
            for r in requests
            if connection.kind is ConnectionKind.COMPANY_SHARED or r.employee_id == connection.employee_id
        ]
        by_id = {r.id: r for r in mine}
        target = self._sync.target(connection)
        start = datetime.combine((now - WINDOW_BEFORE).date(), time.min, tzinfo=now.tzinfo)
        events = await self._provider.list_app_events(target, time_min=start, time_max=now + WINDOW_AFTER)
        remote = {
            e.event_id: e
            for e in events
            if e.private.get("app") == APP_MARKER and e.private.get("tenant_id") in (None, str(tenant_id))
        }
        items: list[ReconciliationItem] = []
        async with self._uow(system_context(tenant_id)) as uow:
            mapped = {m.request_id: m for m in await uow.calendar_events.list_for_connection(connection.id)}

            async def item(
                finding: Finding, request_id: UUID | None, external: str | None, **kw: Any
            ) -> None:
                items.append(
                    ReconciliationItem(
                        id=self._ids.new_id(),
                        tenant_id=tenant_id,
                        connection_id=connection.id,
                        run_id=run_id,
                        run_at=now,
                        finding=finding,
                        status=kw.pop("status", ItemStatus.OPEN),
                        request_id=request_id,
                        external_event_id=external,
                        action=kw.pop("action", None),
                        details=kw,
                    )
                )

            seen: set[str] = set()
            for request in mine:
                if request.status not in EXPECTED:
                    continue
                eid = event_id(request.id, connection.kind)
                seen.add(eid)
                expected = (await self._sync.payload_for(uow, connection, request)).hash
                event = remote.get(eid)
                if event is None:
                    await self._sync.add_job(
                        uow, connection, request.id, SyncOperation.CREATE, expected, nonce=str(run_id)
                    )
                    await item(
                        Finding.MISSING, request.id, eid, status=ItemStatus.RESOLVED, action="enqueued_create"
                    )
                elif stored_hash(event.body) != expected:
                    await self._sync.add_job(
                        uow, connection, request.id, SyncOperation.UPDATE, expected, nonce=str(run_id)
                    )
                    await item(
                        Finding.MISMATCHED,
                        request.id,
                        eid,
                        status=ItemStatus.RESOLVED,
                        action="enqueued_update",
                    )
                elif request.id not in mapped:
                    await item(Finding.UNKNOWN_EXTERNAL_ID, request.id, eid)
            for eid, event in remote.items():
                if eid in seen:
                    continue
                raw = event.private.get("leave_request_id")
                request_id = _uuid(raw)
                known = by_id.get(request_id) if request_id else None
                if known is None and request_id is not None:
                    known = await uow.leave_requests.get(request_id)
                if known is not None and known.status in REMOVED:
                    record = mapped.get(known.id)
                    digest = record.payload_hash if record else eid
                    await self._sync.add_job(
                        uow, connection, known.id, SyncOperation.DELETE, digest, nonce=str(run_id)
                    )
                    await item(
                        Finding.ORPHANED,
                        known.id,
                        eid,
                        status=ItemStatus.RESOLVED,
                        action="enqueued_delete",
                        request_status=known.status.value,
                    )
                else:
                    await item(
                        Finding.ORPHANED,
                        known.id if known else None,
                        eid,
                        request_status=known.status.value if known else None,
                    )
            for entry in items:
                await uow.calendar_items.add(entry)
            await uow.commit()
        return items

    # -- report and manual actions ------------------------------------------------------------------------------
    async def latest(self, principal: Principal) -> ReconciliationReport:
        require(
            principal, Action.MANAGE_CALENDAR_COMPANY, ResourceRef("calendar", tenant_id=principal.tenant_id)
        )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            items = await uow.calendar_items.latest_run()
        if not items:
            return ReconciliationReport(None, None)
        return ReconciliationReport(items[0].run_id, items[0].run_at, items)

    async def act(self, principal: Principal, item_id: UUID, action: str) -> ReconciliationItem:
        require(
            principal, Action.MANAGE_CALENDAR_COMPANY, ResourceRef("calendar", tenant_id=principal.tenant_id)
        )
        if action not in ITEM_ACTIONS:
            raise NotFoundError("unknown reconciliation action")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            item = await uow.calendar_items.get(item_id)
            if item is None:
                raise NotFoundError("reconciliation item not found")
            if item.status is not ItemStatus.OPEN:
                raise ConflictError("the item was already handled", status=item.status.value)
            connection = await uow.calendar_connections.get(item.connection_id)
            if connection is None or connection.status is not ConnectionStatus.ACTIVE:
                raise CalendarNotConnected("the calendar connection is not active")
        if action == "relink" and (
            item.finding is not Finding.UNKNOWN_EXTERNAL_ID or item.request_id is None
        ):
            raise ConflictError("only lost mappings can be relinked")
        if action == "delete-orphan":
            if item.finding is not Finding.ORPHANED or not item.external_event_id:
                raise ConflictError("only orphaned events can be deleted")
            await self._provider.delete(self._sync.target(connection), item.external_event_id)
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            current = await uow.calendar_items.get(item_id)
            assert current is not None
            if action == "relink":
                await self._relink(uow, connection, current, now)
            current.status = ItemStatus.IGNORED if action == "ignore" else ItemStatus.RESOLVED
            current.action = {"relink": "relinked", "ignore": "ignored", "delete-orphan": "deleted"}[action]
            current.resolved_by_employee_id = principal.employee_id
            current.resolved_at = now
            await uow.calendar_items.update(current)
            await self._audit.record(
                uow,
                principal,
                f"calendar.reconciliation.{current.action}",
                resource_type="calendar_reconciliation_item",
                resource_id=current.id,
                details={"finding": current.finding.value, "external_event_id": current.external_event_id},
            )
            await uow.commit()
        return current

    async def _relink(
        self, uow: UnitOfWork, connection: CalendarConnection, item: ReconciliationItem, now: datetime
    ) -> None:
        assert item.request_id is not None and item.external_event_id is not None
        request = await uow.leave_requests.get(item.request_id)
        if request is None:
            raise NotFoundError("request not found")
        digest = (await self._sync.payload_for(uow, connection, request)).hash
        record = await uow.calendar_events.get(connection.id, item.request_id)
        if record is None:
            await uow.calendar_events.add(
                CalendarEventRecord(
                    id=self._ids.new_id(),
                    tenant_id=item.tenant_id,
                    request_id=item.request_id,
                    connection_id=connection.id,
                    external_event_id=item.external_event_id,
                    payload_hash=digest,
                    status=EventStatus.PRESENT,
                    created_at=now,
                    updated_at=now,
                    last_verified_at=now,
                )
            )
        else:
            record.external_event_id = item.external_event_id
            record.payload_hash = digest
            record.status = EventStatus.PRESENT
            record.updated_at = now
            record.last_verified_at = now
            await uow.calendar_events.update(record)


def _uuid(raw: str | None) -> UUID | None:
    try:
        return UUID(raw) if raw else None
    except ValueError:
        return None


__all__ = ["ITEM_ACTIONS", "ReconciliationReport", "ReconciliationService"]
