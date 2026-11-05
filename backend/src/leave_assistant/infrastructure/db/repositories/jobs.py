"""`platform.jobs` repository: SKIP LOCKED claiming across tenants, stale reclaim, dedupe of active jobs."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.jobs.models import Job, JobStatus
from leave_assistant.application.ports import JobFilter
from leave_assistant.infrastructure.db.models import JobRow
from leave_assistant.infrastructure.db.session import flush_or_conflict

CLAIM_SQL = text(
    """
    UPDATE platform.jobs SET status = 'running', locked_by = :worker_id, locked_at = :now, heartbeat_at = :now,
                             attempts = attempts + 1, updated_at = :now
    WHERE id = (
        SELECT id FROM platform.jobs
        WHERE ((status = 'queued' AND (run_after IS NULL OR run_after <= :now))
               OR (status = 'running' AND COALESCE(heartbeat_at, locked_at, created_at) < :stale_before))
          AND (:any_type OR type = ANY(:types))
        ORDER BY run_after NULLS FIRST, created_at
        LIMIT 1 FOR UPDATE SKIP LOCKED
    )
    RETURNING id
    """
)

JOB_FIELDS = (
    "tenant_id",
    "type",
    "payload",
    "attempts",
    "max_attempts",
    "run_after",
    "locked_by",
    "locked_at",
    "heartbeat_at",
    "last_error",
    "dedupe_key",
    "finished_at",
    "extra",
    "updated_at",
)


def _to_domain(r: JobRow) -> Job:
    return Job(
        id=r.id,
        type=r.type,
        payload=dict(r.payload or {}),
        status=JobStatus(r.status),
        created_at=r.created_at,
        updated_at=r.updated_at,
        tenant_id=r.tenant_id,
        attempts=r.attempts,
        max_attempts=r.max_attempts,
        run_after=r.run_after,
        locked_by=r.locked_by,
        locked_at=r.locked_at,
        heartbeat_at=r.heartbeat_at,
        last_error=r.last_error,
        dedupe_key=r.dedupe_key,
        finished_at=r.finished_at,
        extra=dict(r.extra or {}),
    )


class SqlJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, job: Job) -> None:
        self._s.add(
            JobRow(
                id=job.id,
                tenant_id=job.tenant_id,
                type=job.type,
                payload=job.payload,
                status=job.status.value,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                run_after=job.run_after,
                locked_by=job.locked_by,
                locked_at=job.locked_at,
                heartbeat_at=job.heartbeat_at,
                last_error=job.last_error,
                dedupe_key=job.dedupe_key,
                finished_at=job.finished_at,
                extra=job.extra,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def get(self, job_id: UUID) -> Job | None:
        row = await self._s.get(JobRow, job_id)
        return _to_domain(row) if row else None

    async def find_active_by_dedupe(self, dedupe_key: str) -> Job | None:
        stmt = (
            select(JobRow)
            .where(JobRow.dedupe_key == dedupe_key, JobRow.status.in_(["queued", "running"]))
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def claim(
        self, *, now: datetime, worker_id: str, stale_before: datetime, types: Sequence[str] | None
    ) -> Job | None:
        result = await self._s.execute(
            CLAIM_SQL,
            {
                "worker_id": worker_id,
                "now": now,
                "stale_before": stale_before,
                "any_type": types is None,
                "types": list(types or ["-"]),
            },
        )
        job_id = result.scalar_one_or_none()
        if job_id is None:
            return None
        row = await self._s.get(JobRow, job_id, populate_existing=True)
        return _to_domain(row) if row else None

    async def update(self, job: Job) -> None:
        row = await self._s.get(JobRow, job.id)
        if row is None:
            return
        for field in JOB_FIELDS:
            setattr(row, field, getattr(job, field))
        row.status = job.status.value
        await flush_or_conflict(self._s)

    async def list(self, *, filters: JobFilter, limit: int) -> builtins.list[Job]:
        stmt = select(JobRow).order_by(JobRow.created_at.desc(), JobRow.id.desc()).limit(limit)
        if filters.status is not None:
            stmt = stmt.where(JobRow.status == filters.status.value)
        if filters.type is not None:
            stmt = stmt.where(JobRow.type == filters.type)
        if filters.tenant_id is not None:
            stmt = stmt.where(JobRow.tenant_id == filters.tenant_id)
        return [_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def counts(self) -> dict[str, int]:
        stmt = select(JobRow.status, func.count()).group_by(JobRow.status)
        return {str(status): int(count) for status, count in (await self._s.execute(stmt)).all()}
