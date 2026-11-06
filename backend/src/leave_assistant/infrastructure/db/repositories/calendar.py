"""Calendar connections, the sync outbox, event mapping, reconciliation items, the mock store and OAuth states."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.calendar.models import (
    CalendarConnection,
    CalendarEventRecord,
    CalendarSyncJob,
    OAuthState,
    ReconciliationItem,
)
from leave_assistant.application.ports import SyncJobFilter
from leave_assistant.domain.calendar.model import (
    ConnectionKind,
    ConnectionStatus,
    ErrorClass,
    EventStatus,
    Finding,
    ItemStatus,
    SyncOperation,
    SyncStatus,
)
from leave_assistant.infrastructure.db.models import (
    CalendarConnectionRow,
    CalendarEventRow,
    CalendarReconciliationItemRow,
    CalendarSyncJobRow,
    LeaveRequestRow,
    MockCalendarEventRow,
    OAuthStateRow,
)
from leave_assistant.infrastructure.db.session import flush_or_conflict

CONNECTION_FIELDS = (
    "external_calendar_id",
    "refresh_token_encrypted",
    "refresh_token_dek",
    "last_success_at",
    "last_error",
    "updated_at",
)
JOB_FIELDS = (
    "attempts",
    "unknown_errors",
    "next_attempt_at",
    "last_error",
    "payload_hash",
    "updated_at",
    "finished_at",
)


def _connection(r: CalendarConnectionRow) -> CalendarConnection:
    return CalendarConnection(
        id=r.id,
        tenant_id=r.tenant_id,
        kind=ConnectionKind(r.kind),
        employee_id=r.employee_id,
        provider=r.provider,
        status=ConnectionStatus(r.status),
        authorised_by_employee_id=r.authorised_by_employee_id,
        created_at=r.created_at,
        updated_at=r.updated_at,
        external_calendar_id=r.external_calendar_id,
        refresh_token_encrypted=r.refresh_token_encrypted,
        refresh_token_dek=r.refresh_token_dek,
        scopes=tuple(r.scopes or ()),
        last_success_at=r.last_success_at,
        last_error=r.last_error,
        version=r.version,
    )


def _job(r: CalendarSyncJobRow) -> CalendarSyncJob:
    return CalendarSyncJob(
        id=r.id,
        tenant_id=r.tenant_id,
        request_id=r.request_id,
        connection_id=r.connection_id,
        operation=SyncOperation(r.operation),
        status=SyncStatus(r.status),
        idempotency_key=r.idempotency_key,
        payload_hash=r.payload_hash,
        created_at=r.created_at,
        updated_at=r.updated_at,
        attempts=r.attempts,
        unknown_errors=r.unknown_errors,
        next_attempt_at=r.next_attempt_at,
        error_class=ErrorClass(r.error_class) if r.error_class else None,
        last_error=r.last_error,
        finished_at=r.finished_at,
    )


def _event(r: CalendarEventRow) -> CalendarEventRecord:
    return CalendarEventRecord(
        id=r.id,
        tenant_id=r.tenant_id,
        request_id=r.request_id,
        connection_id=r.connection_id,
        external_event_id=r.external_event_id,
        payload_hash=r.payload_hash,
        status=EventStatus(r.status),
        created_at=r.created_at,
        updated_at=r.updated_at,
        last_verified_at=r.last_verified_at,
    )


def _item(r: CalendarReconciliationItemRow) -> ReconciliationItem:
    return ReconciliationItem(
        id=r.id,
        tenant_id=r.tenant_id,
        connection_id=r.connection_id,
        run_id=r.run_id,
        run_at=r.run_at,
        finding=Finding(r.finding),
        status=ItemStatus(r.status),
        request_id=r.request_id,
        external_event_id=r.external_event_id,
        action=r.action,
        details=dict(r.details or {}),
        resolved_by_employee_id=r.resolved_by_employee_id,
        resolved_at=r.resolved_at,
    )


class _Tenanted:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id


class SqlCalendarConnectionRepository(_Tenanted):
    def _base(self) -> Select[CalendarConnectionRow]:
        return select(CalendarConnectionRow).where(CalendarConnectionRow.tenant_id == self._tenant)

    async def add(self, connection: CalendarConnection) -> None:
        self._s.add(
            CalendarConnectionRow(
                id=connection.id,
                tenant_id=connection.tenant_id,
                kind=connection.kind.value,
                employee_id=connection.employee_id,
                provider=connection.provider,
                status=connection.status.value,
                authorised_by_employee_id=connection.authorised_by_employee_id,
                scopes=list(connection.scopes),
                created_at=connection.created_at,
                version=connection.version,
                **{f: getattr(connection, f) for f in CONNECTION_FIELDS if f != "updated_at"},
                updated_at=connection.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def get(self, connection_id: UUID) -> CalendarConnection | None:
        row = (
            await self._s.execute(self._base().where(CalendarConnectionRow.id == connection_id))
        ).scalar_one_or_none()
        return _connection(row) if row else None

    async def find_live(self, kind: ConnectionKind, employee_id: UUID | None) -> CalendarConnection | None:
        stmt = self._base().where(
            CalendarConnectionRow.kind == kind.value,
            CalendarConnectionRow.status != ConnectionStatus.REVOKED.value,
        )
        if kind is ConnectionKind.PERSONAL:
            stmt = stmt.where(CalendarConnectionRow.employee_id == employee_id)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _connection(row) if row else None

    async def list_live(self) -> builtins.list[CalendarConnection]:
        stmt = (
            self._base()
            .where(CalendarConnectionRow.status != ConnectionStatus.REVOKED.value)
            .order_by(CalendarConnectionRow.created_at)
        )
        return [_connection(r) for r in (await self._s.execute(stmt)).scalars()]

    async def update(self, connection: CalendarConnection) -> None:
        row = (
            await self._s.execute(self._base().where(CalendarConnectionRow.id == connection.id))
        ).scalar_one()
        for f in CONNECTION_FIELDS:
            setattr(row, f, getattr(connection, f))
        row.status = connection.status.value
        row.scopes = list(connection.scopes)
        row.authorised_by_employee_id = connection.authorised_by_employee_id
        row.version = row.version + 1
        connection.version = row.version
        await flush_or_conflict(self._s)


class SqlCalendarSyncJobRepository(_Tenanted):
    def _base(self) -> Select[CalendarSyncJobRow]:
        return select(CalendarSyncJobRow).where(CalendarSyncJobRow.tenant_id == self._tenant)

    async def add(self, job: CalendarSyncJob) -> None:
        self._s.add(
            CalendarSyncJobRow(
                id=job.id,
                tenant_id=job.tenant_id,
                request_id=job.request_id,
                connection_id=job.connection_id,
                operation=job.operation.value,
                status=job.status.value,
                idempotency_key=job.idempotency_key,
                created_at=job.created_at,
                error_class=job.error_class.value if job.error_class else None,
                **{f: getattr(job, f) for f in JOB_FIELDS},
            )
        )
        await flush_or_conflict(self._s)

    async def get(self, job_id: UUID) -> CalendarSyncJob | None:
        row = (
            await self._s.execute(self._base().where(CalendarSyncJobRow.id == job_id))
        ).scalar_one_or_none()
        return _job(row) if row else None

    async def get_by_key(self, idempotency_key: str) -> CalendarSyncJob | None:
        stmt = self._base().where(CalendarSyncJobRow.idempotency_key == idempotency_key)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _job(row) if row else None

    async def lock(self, job_id: UUID) -> CalendarSyncJob | None:
        stmt = (
            self._base()
            .where(CalendarSyncJobRow.id == job_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _job(row) if row else None

    async def list(self, *, filters: SyncJobFilter, limit: int) -> builtins.list[CalendarSyncJob]:
        stmt = self._base().order_by(CalendarSyncJobRow.created_at.desc(), CalendarSyncJobRow.id.desc())
        if filters.status is not None:
            stmt = stmt.where(CalendarSyncJobRow.status == filters.status)
        if filters.request_id is not None:
            stmt = stmt.where(CalendarSyncJobRow.request_id == filters.request_id)
        if filters.employee_id is not None:
            stmt = stmt.where(
                CalendarSyncJobRow.request_id.in_(
                    select(LeaveRequestRow.id).where(LeaveRequestRow.employee_id == filters.employee_id)
                )
            )
        return [_job(r) for r in (await self._s.execute(stmt.limit(limit))).scalars()]

    async def list_parked(self, connection_id: UUID) -> builtins.list[CalendarSyncJob]:
        stmt = self._base().where(
            CalendarSyncJobRow.connection_id == connection_id,
            CalendarSyncJobRow.status == SyncStatus.PARKED.value,
        )
        return [
            _job(r) for r in (await self._s.execute(stmt.order_by(CalendarSyncJobRow.created_at))).scalars()
        ]

    async def list_due(self, now: datetime, limit: int) -> builtins.list[CalendarSyncJob]:
        stmt = (
            self._base()
            .where(
                CalendarSyncJobRow.status == SyncStatus.PENDING.value,
                (CalendarSyncJobRow.next_attempt_at.is_(None)) | (CalendarSyncJobRow.next_attempt_at <= now),
            )
            .order_by(CalendarSyncJobRow.created_at)
            .limit(limit)
        )
        return [_job(r) for r in (await self._s.execute(stmt)).scalars()]

    async def latest_for_request(self, request_id: UUID, connection_id: UUID) -> CalendarSyncJob | None:
        stmt = (
            self._base()
            .where(
                CalendarSyncJobRow.request_id == request_id, CalendarSyncJobRow.connection_id == connection_id
            )
            .order_by(CalendarSyncJobRow.created_at.desc(), CalendarSyncJobRow.id.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _job(row) if row else None

    async def update(self, job: CalendarSyncJob) -> None:
        row = (await self._s.execute(self._base().where(CalendarSyncJobRow.id == job.id))).scalar_one()
        for f in JOB_FIELDS:
            setattr(row, f, getattr(job, f))
        row.status = job.status.value
        row.error_class = job.error_class.value if job.error_class else None
        await flush_or_conflict(self._s)

    async def counts(self) -> dict[str, int]:
        stmt = (
            select(CalendarSyncJobRow.status, func.count())
            .where(CalendarSyncJobRow.tenant_id == self._tenant)
            .group_by(CalendarSyncJobRow.status)
        )
        return {str(status): int(n) for status, n in (await self._s.execute(stmt)).all()}


class SqlCalendarEventRepository(_Tenanted):
    def _base(self) -> Select[CalendarEventRow]:
        return select(CalendarEventRow).where(CalendarEventRow.tenant_id == self._tenant)

    async def get(self, connection_id: UUID, request_id: UUID) -> CalendarEventRecord | None:
        stmt = self._base().where(
            CalendarEventRow.connection_id == connection_id, CalendarEventRow.request_id == request_id
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _event(row) if row else None

    async def add(self, record: CalendarEventRecord) -> None:
        self._s.add(
            CalendarEventRow(
                id=record.id,
                tenant_id=record.tenant_id,
                request_id=record.request_id,
                connection_id=record.connection_id,
                external_event_id=record.external_event_id,
                payload_hash=record.payload_hash,
                status=record.status.value,
                last_verified_at=record.last_verified_at,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, record: CalendarEventRecord) -> None:
        row = (await self._s.execute(self._base().where(CalendarEventRow.id == record.id))).scalar_one()
        row.external_event_id = record.external_event_id
        row.payload_hash = record.payload_hash
        row.status = record.status.value
        row.last_verified_at = record.last_verified_at
        row.updated_at = record.updated_at
        await flush_or_conflict(self._s)

    async def list_for_connection(self, connection_id: UUID) -> builtins.list[CalendarEventRecord]:
        stmt = self._base().where(CalendarEventRow.connection_id == connection_id)
        return [_event(r) for r in (await self._s.execute(stmt)).scalars()]

    async def mark_orphaned(self, connection_id: UUID, now: datetime) -> int:
        result = await self._s.execute(
            update(CalendarEventRow)
            .where(
                CalendarEventRow.tenant_id == self._tenant,
                CalendarEventRow.connection_id == connection_id,
                CalendarEventRow.status == EventStatus.PRESENT.value,
            )
            .values(status=EventStatus.ORPHANED.value, updated_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class SqlReconciliationItemRepository(_Tenanted):
    def _base(self) -> Select[CalendarReconciliationItemRow]:
        return select(CalendarReconciliationItemRow).where(
            CalendarReconciliationItemRow.tenant_id == self._tenant
        )

    async def add(self, item: ReconciliationItem) -> None:
        self._s.add(
            CalendarReconciliationItemRow(
                id=item.id,
                tenant_id=item.tenant_id,
                connection_id=item.connection_id,
                run_id=item.run_id,
                run_at=item.run_at,
                finding=item.finding.value,
                status=item.status.value,
                request_id=item.request_id,
                external_event_id=item.external_event_id,
                action=item.action,
                details=item.details,
                resolved_by_employee_id=item.resolved_by_employee_id,
                resolved_at=item.resolved_at,
            )
        )
        await flush_or_conflict(self._s)

    async def get(self, item_id: UUID) -> ReconciliationItem | None:
        stmt = self._base().where(CalendarReconciliationItemRow.id == item_id)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _item(row) if row else None

    async def update(self, item: ReconciliationItem) -> None:
        row = (
            await self._s.execute(self._base().where(CalendarReconciliationItemRow.id == item.id))
        ).scalar_one()
        row.status = item.status.value
        row.action = item.action
        row.details = item.details
        row.external_event_id = item.external_event_id
        row.resolved_by_employee_id = item.resolved_by_employee_id
        row.resolved_at = item.resolved_at
        await flush_or_conflict(self._s)

    async def latest_run(self) -> builtins.list[ReconciliationItem]:
        latest = (
            select(func.max(CalendarReconciliationItemRow.run_at))
            .where(CalendarReconciliationItemRow.tenant_id == self._tenant)
            .scalar_subquery()
        )
        stmt = (
            self._base()
            .where(CalendarReconciliationItemRow.run_at == latest)
            .order_by(CalendarReconciliationItemRow.finding, CalendarReconciliationItemRow.id)
        )
        return [_item(r) for r in (await self._s.execute(stmt)).scalars()]


class SqlMockCalendarStore(_Tenanted):
    def _where(self, calendar_id: str, event_id: str) -> Any:
        return (
            (MockCalendarEventRow.tenant_id == self._tenant)
            & (MockCalendarEventRow.calendar_id == calendar_id)
            & (MockCalendarEventRow.event_id == event_id)
        )

    async def get(self, calendar_id: str, event_id: str) -> dict[str, Any] | None:
        stmt = select(MockCalendarEventRow.body).where(self._where(calendar_id, event_id))
        body = (await self._s.execute(stmt)).scalar_one_or_none()
        return dict(body) if body is not None else None

    async def put(self, calendar_id: str, event_id: str, body: dict[str, Any], now: datetime) -> bool:
        stmt = (
            insert(MockCalendarEventRow)
            .values(
                tenant_id=self._tenant,
                calendar_id=calendar_id,
                event_id=event_id,
                body=body,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing()
            .returning(MockCalendarEventRow.event_id)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none() is not None

    async def replace(self, calendar_id: str, event_id: str, body: dict[str, Any], now: datetime) -> bool:
        result = await self._s.execute(
            update(MockCalendarEventRow)
            .where(self._where(calendar_id, event_id))
            .values(body=body, updated_at=now)
        )
        return bool(getattr(result, "rowcount", 0))

    async def remove(self, calendar_id: str, event_id: str) -> bool:
        result = await self._s.execute(delete(MockCalendarEventRow).where(self._where(calendar_id, event_id)))
        return bool(getattr(result, "rowcount", 0))

    async def list(self, calendar_id: str) -> builtins.list[dict[str, Any]]:
        stmt = (
            select(MockCalendarEventRow.body)
            .where(
                MockCalendarEventRow.tenant_id == self._tenant,
                MockCalendarEventRow.calendar_id == calendar_id,
            )
            .order_by(MockCalendarEventRow.event_id)
        )
        return [dict(b) for b in (await self._s.execute(stmt)).scalars()]


def _state(r: OAuthStateRow) -> OAuthState:
    return OAuthState(
        nonce_hash=r.nonce_hash,
        tenant_id=r.tenant_id,
        user_id=r.user_id,
        employee_id=r.employee_id,
        kind=ConnectionKind(r.kind),
        provider=r.provider,
        roles=tuple(r.roles or ()),
        expires_at=r.expires_at,
        created_at=r.created_at,
        used_at=r.used_at,
    )


class SqlOAuthStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, state: OAuthState) -> None:
        self._s.add(
            OAuthStateRow(
                nonce_hash=state.nonce_hash,
                tenant_id=state.tenant_id,
                user_id=state.user_id,
                employee_id=state.employee_id,
                kind=state.kind.value,
                provider=state.provider,
                roles=list(state.roles),
                expires_at=state.expires_at,
                created_at=state.created_at,
            )
        )
        await flush_or_conflict(self._s)

    async def consume(self, nonce_hash: str, now: datetime) -> OAuthState | None:
        stmt = (
            update(OAuthStateRow)
            .where(
                OAuthStateRow.nonce_hash == nonce_hash,
                OAuthStateRow.used_at.is_(None),
                OAuthStateRow.expires_at > now,
            )
            .values(used_at=now)
            .returning(OAuthStateRow)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _state(row) if row else None

    async def purge(self, before: datetime) -> int:
        result = await self._s.execute(delete(OAuthStateRow).where(OAuthStateRow.expires_at < before))
        return int(getattr(result, "rowcount", 0) or 0)
