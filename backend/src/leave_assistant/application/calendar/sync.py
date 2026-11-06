"""Calendar synchronisation outbox (doc 10 §4, M8 decisions 1–2).

Business transactions write `calendar_sync_jobs` rows and enqueue a `calendar.sync` queue job (same transaction);
the worker runs `handle_job`, which owns the state machine. Provider calls happen outside any transaction: the
job is marked `running` and committed first, so a crash mid-call is recovered by the queue's stale-job reclaim.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, ResourceRef, require
from leave_assistant.application.calendar.errors import CalendarConnectionExpired, SyncNotRetryable
from leave_assistant.application.calendar.models import (
    JOB_CALENDAR_SYNC,
    CalendarConnection,
    CalendarEventRecord,
    CalendarProviderError,
    CalendarSyncJob,
    CalendarTarget,
)
from leave_assistant.application.calendar.ports import CalendarProvider, EventExists
from leave_assistant.application.errors import NotFoundError
from leave_assistant.application.jobs.models import Job
from leave_assistant.application.jobs.service import JobService
from leave_assistant.application.notifications.models import IntegrationEvent
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import (
    Clock,
    FileCipher,
    IdGenerator,
    SyncJobFilter,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.calendar.model import (
    ConnectionKind,
    ConnectionStatus,
    ErrorClass,
    EventPayload,
    EventStatus,
    SyncOperation,
    SyncStatus,
    build_payload,
    check_transition,
    event_id,
    idempotency_key,
    next_delay,
    outcome_for,
    stored_hash,
)
from leave_assistant.domain.leave.request import LeaveRequest
from leave_assistant.domain.tenancy import Principal, Role

SYSTEM_USER = "calendar.sync"
LIVE_STATUSES = frozenset({ConnectionStatus.ACTIVE, ConnectionStatus.EXPIRED})
MAX_ERROR_CHARS = 500


def system_context(tenant_id: UUID) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id=SYSTEM_USER, roles=("system",))


def connection_aad(connection_id: UUID) -> bytes:
    return b"calendar-connection:" + connection_id.bytes


@dataclass(frozen=True, slots=True)
class SyncJobView:
    job: CalendarSyncJob
    request_number: str
    connection_kind: ConnectionKind | None


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    job_id: UUID
    status: SyncStatus
    detail: str = ""


class CalendarSyncService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        notifications: NotificationService,
        jobs: JobService,
        provider: CalendarProvider,
        cipher: FileCipher,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._notifications = notifications
        self._jobs = jobs
        self._provider = provider
        self._cipher = cipher

    # -- hooks called inside the business transactions ---------------------------------------------------------
    async def request_approved(
        self, uow: UnitOfWork, request: LeaveRequest
    ) -> builtins.list[CalendarSyncJob]:
        return await self._enqueue_for(uow, request, SyncOperation.CREATE)

    async def request_withdrawn(
        self, uow: UnitOfWork, request: LeaveRequest
    ) -> builtins.list[CalendarSyncJob]:
        return await self._enqueue_for(uow, request, SyncOperation.DELETE)

    async def _connections_for(
        self, uow: UnitOfWork, request: LeaveRequest
    ) -> builtins.list[CalendarConnection]:
        found = [
            await uow.calendar_connections.find_live(ConnectionKind.COMPANY_SHARED, None),
            await uow.calendar_connections.find_live(ConnectionKind.PERSONAL, request.employee_id),
        ]
        return [c for c in found if c is not None and c.status in LIVE_STATUSES]

    async def payload_for(
        self, uow: UnitOfWork, connection: CalendarConnection, request: LeaveRequest
    ) -> EventPayload:
        employee = await uow.employees.get(request.employee_id)
        tenant = await uow.tenants.get(request.tenant_id)
        name = f"{employee.first_name} {employee.last_name}" if employee else ""
        locale = tenant.default_locale.value if tenant else "en"
        return build_payload(
            kind=connection.kind,
            tenant_id=request.tenant_id,
            request_id=request.id,
            display_name=name,
            locale=locale,
            start=request.start_date,
            end=request.end_date,
        )

    async def _enqueue_for(
        self, uow: UnitOfWork, request: LeaveRequest, operation: SyncOperation
    ) -> builtins.list[CalendarSyncJob]:
        created: list[CalendarSyncJob] = []
        for connection in await self._connections_for(uow, request):
            if operation is SyncOperation.DELETE:
                record = await uow.calendar_events.get(connection.id, request.id)
                pending_create = await uow.calendar_jobs.latest_for_request(request.id, connection.id)
                if record is None and pending_create is None:
                    continue  # nothing was (or will be) written in this calendar
                digest = record.payload_hash if record else event_id(request.id, connection.kind)
            else:
                digest = (await self.payload_for(uow, connection, request)).hash
            job = await self.add_job(uow, connection, request.id, operation, digest)
            if job is not None:
                created.append(job)
        return created

    async def add_job(
        self,
        uow: UnitOfWork,
        connection: CalendarConnection,
        request_id: UUID,
        operation: SyncOperation,
        digest: str,
        *,
        nonce: str = "",
    ) -> CalendarSyncJob | None:
        """One job per (request, connection, operation, payload); `nonce` distinguishes deliberate repeats
        (reconciliation re-creating an event deleted by hand). Returns `None` for a duplicate."""
        key = idempotency_key(request_id, connection.id, operation, f"{digest}:{nonce}" if nonce else digest)
        if await uow.calendar_jobs.get_by_key(key) is not None:
            return None
        now = self._clock.now()
        parked = connection.status is not ConnectionStatus.ACTIVE
        job = CalendarSyncJob(
            id=self._ids.new_id(),
            tenant_id=connection.tenant_id,
            request_id=request_id,
            connection_id=connection.id,
            operation=operation,
            status=SyncStatus.PARKED if parked else SyncStatus.PENDING,
            idempotency_key=key,
            payload_hash=digest,
            created_at=now,
            updated_at=now,
            next_attempt_at=None if parked else now,
            error_class=ErrorClass.AUTH_EXPIRED if parked else None,
        )
        await uow.calendar_jobs.add(job)
        if not parked:
            await self._kick(uow, job)
        return job

    async def _kick(self, uow: UnitOfWork, job: CalendarSyncJob, run_after: datetime | None = None) -> None:
        await self._jobs.enqueue(
            uow,
            type=JOB_CALENDAR_SYNC,
            payload={"tenant_id": str(job.tenant_id), "sync_job_id": str(job.id)},
            tenant_id=job.tenant_id,
            dedupe_key=f"{JOB_CALENDAR_SYNC}:{job.id}:{job.attempts}",
            max_attempts=1,  # retries are this service's backoff, not the queue's
            run_after=run_after,
        )

    # -- worker -------------------------------------------------------------------------------------------------
    async def handle_job(self, job: Job) -> object:
        tenant_id = UUID(str(job.payload["tenant_id"]))
        outcome = await self.run(tenant_id, UUID(str(job.payload["sync_job_id"])))
        return {"sync_job_id": str(outcome.job_id), "status": outcome.status.value, "detail": outcome.detail}

    async def run(self, tenant_id: UUID, sync_job_id: UUID) -> SyncOutcome:
        ctx = system_context(tenant_id)
        async with self._uow(ctx) as uow:
            job = await uow.calendar_jobs.lock(sync_job_id)
            if job is None:
                return SyncOutcome(sync_job_id, SyncStatus.RUNNING, "locked or missing")
            if job.status not in (SyncStatus.PENDING, SyncStatus.RUNNING):
                return SyncOutcome(job.id, job.status, "not runnable")
            connection = await uow.calendar_connections.get(job.connection_id)
            if connection is None or connection.status is ConnectionStatus.REVOKED:
                await self._finish(uow, job, SyncStatus.DEAD, ErrorClass.PERMANENT, "connection revoked")
                await uow.commit()
                return SyncOutcome(job.id, SyncStatus.DEAD, "connection revoked")
            if connection.status is ConnectionStatus.EXPIRED:
                await self._finish(uow, job, SyncStatus.PARKED, ErrorClass.AUTH_EXPIRED, "connection expired")
                await uow.commit()
                return SyncOutcome(job.id, SyncStatus.PARKED, "connection expired")
            request = await uow.leave_requests.get(job.request_id)
            if request is None:
                await self._finish(uow, job, SyncStatus.DEAD, ErrorClass.PERMANENT, "request not found")
                await uow.commit()
                return SyncOutcome(job.id, SyncStatus.DEAD, "request not found")
            payload = await self.payload_for(uow, connection, request)
            record = await uow.calendar_events.get(connection.id, request.id)
            if job.status is SyncStatus.PENDING:
                check_transition(job.status, SyncStatus.RUNNING)
            job.status = SyncStatus.RUNNING
            job.attempts += 1
            job.updated_at = self._clock.now()
            await uow.calendar_jobs.update(job)
            await uow.commit()
        target = self.target(connection)
        try:
            external_id = await self._call(target, job.operation, payload, record)
        except CalendarProviderError as exc:
            return await self._failed(tenant_id, job, connection, exc)
        return await self._succeeded(tenant_id, job, connection, payload, external_id)

    def target(self, connection: CalendarConnection) -> CalendarTarget:
        token = ""
        if connection.refresh_token_encrypted and connection.refresh_token_dek:
            token = self._cipher.decrypt(
                connection.refresh_token_encrypted,
                connection.refresh_token_dek,
                aad=connection_aad(connection.id),
            ).decode()
        return CalendarTarget(
            tenant_id=connection.tenant_id,
            connection_id=connection.id,
            calendar_id=connection.external_calendar_id or "primary",
            refresh_token=token,
        )

    async def _call(
        self,
        target: CalendarTarget,
        operation: SyncOperation,
        payload: EventPayload,
        record: CalendarEventRecord | None,
    ) -> str:
        """Idempotent provider operations (doc 10 §4.2); returns the external event id."""
        event = record.external_event_id if record else payload.event_id
        if operation is SyncOperation.DELETE:
            await self._provider.delete(target, event)  # 404/410 → already gone → success
            return event
        body = payload.wire_body()
        if operation is SyncOperation.UPDATE:
            if await self._provider.patch(target, event, body) is None:  # 404 → missing → create
                await self._insert(target, body)
            return event
        await self._insert(target, body)
        return payload.event_id

    async def _insert(self, target: CalendarTarget, body: dict[str, Any]) -> None:
        try:
            await self._provider.insert(target, body)
        except EventExists:  # 409: compare the stored hash, patch only if the content differs
            current = await self._provider.get(target, str(body["id"]))
            if current is None or stored_hash(current.body) != stored_hash(body):
                await self._provider.patch(target, str(body["id"]), body)

    async def _succeeded(
        self,
        tenant_id: UUID,
        job: CalendarSyncJob,
        connection: CalendarConnection,
        payload: EventPayload,
        external_id: str,
    ) -> SyncOutcome:
        now = self._clock.now()
        async with self._uow(system_context(tenant_id)) as uow:
            current = await uow.calendar_jobs.get(job.id)
            if current is None:
                return SyncOutcome(job.id, SyncStatus.SUCCEEDED, "job vanished")
            await self._finish(uow, current, SyncStatus.SUCCEEDED, None, None)
            status = EventStatus.DELETED if job.operation is SyncOperation.DELETE else EventStatus.PRESENT
            record = await uow.calendar_events.get(connection.id, job.request_id)
            if record is None:
                await uow.calendar_events.add(
                    CalendarEventRecord(
                        id=self._ids.new_id(),
                        tenant_id=tenant_id,
                        request_id=job.request_id,
                        connection_id=connection.id,
                        external_event_id=external_id,
                        payload_hash=payload.hash,
                        status=status,
                        created_at=now,
                        updated_at=now,
                        last_verified_at=now,
                    )
                )
            else:
                record.external_event_id = external_id
                if job.operation is not SyncOperation.DELETE:
                    record.payload_hash = payload.hash
                record.status = status
                record.updated_at = now
                record.last_verified_at = now
                await uow.calendar_events.update(record)
            conn = await uow.calendar_connections.get(connection.id)
            if conn is not None and conn.status is ConnectionStatus.ACTIVE:
                conn.last_success_at = now
                conn.last_error = None
                conn.updated_at = now
                await uow.calendar_connections.update(conn)
            await uow.commit()
        return SyncOutcome(job.id, SyncStatus.SUCCEEDED)

    async def _failed(
        self,
        tenant_id: UUID,
        job: CalendarSyncJob,
        connection: CalendarConnection,
        exc: CalendarProviderError,
    ) -> SyncOutcome:
        now = self._clock.now()
        message = str(exc)[:MAX_ERROR_CHARS]
        async with self._uow(system_context(tenant_id)) as uow:
            current = await uow.calendar_jobs.get(job.id)
            if current is None:
                return SyncOutcome(job.id, SyncStatus.DEAD, "job vanished")
            if exc.error_class is ErrorClass.UNKNOWN:
                current.unknown_errors += 1
            target = outcome_for(exc.error_class, current.attempts, current.unknown_errors)
            await self._finish(uow, current, target, exc.error_class, message)
            if target is SyncStatus.PENDING:
                due = now + timedelta(seconds=next_delay(current.attempts, retry_after=exc.retry_after))
                current.next_attempt_at = due
                await uow.calendar_jobs.update(current)
                await self._kick(uow, current, run_after=due)
            elif target is SyncStatus.PARKED:
                await self._expire(uow, connection.id, message)
            else:
                await self._dead_letter(uow, current)
            await uow.commit()
        return SyncOutcome(job.id, target, message)

    async def _finish(
        self,
        uow: UnitOfWork,
        job: CalendarSyncJob,
        target: SyncStatus,
        error_class: ErrorClass | None,
        message: str | None,
    ) -> None:
        if job.status is not target:
            check_transition(job.status, target)
        now = self._clock.now()
        job.status = target
        job.error_class = error_class
        job.last_error = message
        job.updated_at = now
        if target in (SyncStatus.SUCCEEDED, SyncStatus.DEAD):
            job.finished_at = now
            job.next_attempt_at = None
        if target is SyncStatus.PARKED:
            job.next_attempt_at = None
        await uow.calendar_jobs.update(job)

    async def _expire(self, uow: UnitOfWork, connection_id: UUID, message: str) -> None:
        """AUTH_EXPIRED: the connection stops; its authoriser is told to re-authorise (doc 10 §2)."""
        connection = await uow.calendar_connections.get(connection_id)
        if connection is None or connection.status is not ConnectionStatus.ACTIVE:
            return
        now = self._clock.now()
        connection.status = ConnectionStatus.EXPIRED
        connection.last_error = message
        connection.updated_at = now
        await uow.calendar_connections.update(connection)
        await self._notifications.notify(
            uow,
            [connection.authorised_by_employee_id],
            "calendar.connection_expired",
            {"connection_id": str(connection.id), "kind": connection.kind.value},
            dedupe_key=f"calendar.connection_expired:{connection.id}:{connection.version}",
        )
        await self._event(uow, "calendar.connection_expired", {"connection_id": str(connection.id)})

    async def _dead_letter(self, uow: UnitOfWork, job: CalendarSyncJob) -> None:
        request = await uow.leave_requests.get(job.request_id)
        params = {
            "sync_job_id": str(job.id),
            "request_id": str(job.request_id),
            "request_number": request.request_number if request else "",
            "operation": job.operation.value,
            "error_class": job.error_class.value if job.error_class else None,
        }
        admins = [e.id for e in await uow.employees.list_active(role=Role.COMPANY_ADMIN)]
        if admins:
            await self._notifications.notify(
                uow,
                admins,
                "calendar.sync_failed",
                params,
                dedupe_key=f"calendar.sync_failed:{job.id}:{job.attempts}",
            )
        await self._event(uow, "calendar.sync_failed", {"sync_job_id": str(job.id)})

    async def _event(self, uow: UnitOfWork, type: str, payload: dict[str, Any]) -> None:
        tenant_id = uow.context.tenant_id if uow.context else None
        assert tenant_id is not None
        await uow.integration_events.add(
            IntegrationEvent(
                id=self._ids.new_id(),
                tenant_id=tenant_id,
                type=type,
                payload=payload,
                created_at=self._clock.now(),
            )
        )

    # -- resumption, retries, sweep -----------------------------------------------------------------------------
    async def resume_parked(self, uow: UnitOfWork, connection_id: UUID) -> int:
        """Re-authorisation: parked jobs go back to pending and are run again."""
        resumed = 0
        for job in await uow.calendar_jobs.list_parked(connection_id):
            check_transition(job.status, SyncStatus.PENDING)
            job.status = SyncStatus.PENDING
            job.error_class = None
            job.next_attempt_at = self._clock.now()
            job.updated_at = self._clock.now()
            await uow.calendar_jobs.update(job)
            await self._kick(uow, job)
            resumed += 1
        return resumed

    async def retry(self, principal: Principal, sync_job_id: UUID) -> CalendarSyncJob:
        """Manual retry (C): dead → pending with a fresh attempt budget; parked stays parked until re-authorised."""
        require(principal, Action.MANAGE_CALENDAR_COMPANY, ResourceRef("calendar_sync_job", sync_job_id))
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            job = await uow.calendar_jobs.get(sync_job_id)
            if job is None:
                raise NotFoundError("sync job not found")
            if job.status is not SyncStatus.DEAD:
                raise SyncNotRetryable("only failed sync jobs can be retried", status=job.status.value)
            connection = await uow.calendar_connections.get(job.connection_id)
            if connection is None or connection.status is not ConnectionStatus.ACTIVE:
                raise CalendarConnectionExpired(
                    "the calendar connection is not active; re-authorise it first"
                )
            check_transition(job.status, SyncStatus.PENDING)
            job.status = SyncStatus.PENDING
            job.attempts = 0
            job.unknown_errors = 0
            job.error_class = None
            job.finished_at = None
            job.next_attempt_at = self._clock.now()
            job.updated_at = self._clock.now()
            await uow.calendar_jobs.update(job)
            await self._kick(uow, job)
            await self._audit.record(
                uow, principal, "calendar.sync.retried", resource_type="calendar_sync_job", resource_id=job.id
            )
            await uow.commit()
        return job

    async def sweep(self, tenant_id: UUID, *, limit: int = 200) -> int:
        """Re-enqueue due pending jobs (n8n W5 every 15 min); the queue dedupe makes it idempotent."""
        async with self._uow(system_context(tenant_id)) as uow:
            due = await uow.calendar_jobs.list_due(self._clock.now(), limit)
            for job in due:
                await self._kick(uow, job)
            await uow.commit()
        return len(due)

    async def list_jobs(
        self, principal: Principal, *, status: str | None, request_id: UUID | None, limit: int = 100
    ) -> builtins.list[CalendarSyncJob]:
        require(
            principal,
            Action.READ_CALENDAR_SYNC,
            ResourceRef(
                "calendar_sync_job", tenant_id=principal.tenant_id, owner_employee_id=principal.employee_id
            ),
        )
        sees_all = bool(principal.roles & {Role.HR_ADMIN, Role.COMPANY_ADMIN})
        filters = SyncJobFilter(
            status=status, request_id=request_id, employee_id=None if sees_all else principal.employee_id
        )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.calendar_jobs.list(filters=filters, limit=min(max(limit, 1), 200))

    async def list_views(
        self, principal: Principal, *, status: str | None, request_id: UUID | None, limit: int = 100
    ) -> builtins.list[SyncJobView]:
        """The sync history with request numbers and connection kinds (doc 10 §7)."""
        jobs = await self.list_jobs(principal, status=status, request_id=request_id, limit=limit)
        numbers: dict[UUID, str] = {}
        kinds: dict[UUID, ConnectionKind] = {}
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            for job in jobs:
                if job.request_id not in numbers:
                    request = await uow.leave_requests.get(job.request_id)
                    numbers[job.request_id] = request.request_number if request else ""
                if job.connection_id not in kinds:
                    connection = await uow.calendar_connections.get(job.connection_id)
                    if connection is not None:
                        kinds[job.connection_id] = connection.kind
        return [SyncJobView(j, numbers.get(j.request_id, ""), kinds.get(j.connection_id)) for j in jobs]
