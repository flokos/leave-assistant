"""JobService: the queue's business rules (doc 03 §4.5). Handlers live in the worker package."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.errors import ConflictError, NotFoundError
from leave_assistant.application.jobs.models import Job, JobStatus
from leave_assistant.application.ports import Clock, IdGenerator, JobFilter, UnitOfWork, UnitOfWorkFactory
from leave_assistant.domain.tenancy import Principal


class JobService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        *,
        max_attempts: int = 3,
        stale_after_seconds: float = 1800.0,
        base_backoff_seconds: float = 30.0,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._max_attempts = max_attempts
        self._stale_after = timedelta(seconds=stale_after_seconds)
        self._base_backoff = base_backoff_seconds

    async def enqueue(
        self,
        uow: UnitOfWork,
        *,
        type: str,
        payload: dict[str, Any],
        tenant_id: UUID | None,
        dedupe_key: str | None = None,
        max_attempts: int | None = None,
        run_after: datetime | None = None,
    ) -> Job:
        """Inside the caller's transaction (outbox pattern). A queued/running job with the same key is reused."""
        if dedupe_key is not None:
            existing = await uow.jobs.find_active_by_dedupe(dedupe_key)
            if existing is not None:
                return existing
        now = self._clock.now()
        job = Job(
            id=self._ids.new_id(),
            type=type,
            payload=dict(payload),
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            tenant_id=tenant_id,
            max_attempts=max_attempts or self._max_attempts,
            run_after=run_after or now,
            dedupe_key=dedupe_key,
        )
        await uow.jobs.add(job)
        return job

    async def claim(self, worker_id: str, *, types: Sequence[str] | None = None) -> Job | None:
        """Own transaction: the queued job with the earliest `run_after`, or a running job whose heartbeat went stale."""
        now = self._clock.now()
        async with self._uow(None) as uow:
            job = await uow.jobs.claim(
                now=now, worker_id=worker_id, stale_before=now - self._stale_after, types=types
            )
            await uow.commit()
        return job

    async def heartbeat(self, job_id: UUID) -> None:
        async with self._uow(None) as uow:
            job = await uow.jobs.get(job_id)
            if job is not None and job.status is JobStatus.RUNNING:
                job.heartbeat_at = self._clock.now()
                await uow.jobs.update(job)
                await uow.commit()

    async def complete(self, job_id: UUID, *, extra: dict[str, Any] | None = None) -> Job:
        now = self._clock.now()
        async with self._uow(None) as uow:
            job = await self._load(uow, job_id)
            job.status = JobStatus.SUCCEEDED
            job.finished_at = now
            job.updated_at = now
            job.locked_by = None
            job.last_error = None
            if extra:
                job.extra = {**job.extra, **extra}
            await uow.jobs.update(job)
            await uow.commit()
        return job

    async def fail(self, job_id: UUID, error: str) -> Job:
        """Retry with exponential backoff (30 s · 2^(attempts−1)); after `max_attempts` the job is a dead letter."""
        now = self._clock.now()
        async with self._uow(None) as uow:
            job = await self._load(uow, job_id)
            job.last_error = error[:2000]
            job.updated_at = now
            job.locked_by = None
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.finished_at = now
            else:
                job.status = JobStatus.QUEUED
                job.run_after = now + timedelta(seconds=self._base_backoff * 2 ** max(job.attempts - 1, 0))
            await uow.jobs.update(job)
            await uow.commit()
        return job

    async def get(self, job_id: UUID) -> Job | None:
        async with self._uow(None) as uow:
            return await uow.jobs.get(job_id)

    async def active_count(self) -> int:
        async with self._uow(None) as uow:
            counts = await uow.jobs.counts()
        return counts.get(JobStatus.QUEUED.value, 0) + counts.get(JobStatus.RUNNING.value, 0)

    async def counts(self) -> dict[str, int]:
        async with self._uow(None) as uow:
            return await uow.jobs.counts()

    # --- administration (doc 03 §4.5: dead letters visible in the admin UI) ----------------------------------
    async def list_for_tenant(
        self, principal: Principal, *, status: JobStatus | None, type: str | None, limit: int
    ) -> builtins.list[Job]:
        require(principal, Action.READ_AUDIT_TENANT)
        async with self._uow(None) as uow:
            return await uow.jobs.list(
                filters=JobFilter(status=status, type=type, tenant_id=principal.tenant_id), limit=limit
            )

    async def retry(self, principal: Principal, job_id: UUID) -> Job:
        require(principal, Action.MANAGE_ORGANISATION)
        now = self._clock.now()
        async with self._uow(None) as uow:
            job = await uow.jobs.get(job_id)
            if job is None or job.tenant_id != principal.tenant_id:
                raise NotFoundError("job not found")
            if job.status is not JobStatus.FAILED:
                raise ConflictError("only failed jobs can be retried", problem_type="job/not-failed")
            job.status = JobStatus.QUEUED
            job.attempts = 0
            job.run_after = now
            job.updated_at = now
            job.finished_at = None
            await uow.jobs.update(job)
            await uow.commit()
        return job

    @staticmethod
    async def _load(uow: UnitOfWork, job_id: UUID) -> Job:
        job = await uow.jobs.get(job_id)
        if job is None:
            raise NotFoundError("job not found")
        return job
