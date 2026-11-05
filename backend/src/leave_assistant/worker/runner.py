"""JobRunner: claim → run (inline or in a child process with timeout + heartbeat) → complete/fail (M4 decision 3)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field

from leave_assistant.application.jobs.models import Job, JobStatus
from leave_assistant.application.jobs.registry import JobHandler, JobRegistry
from leave_assistant.application.jobs.service import JobService
from leave_assistant.infrastructure.observability.logging import get_logger

log = get_logger("worker.jobs")
CHILD_MODULE = "leave_assistant.worker.run_job"


@dataclass(slots=True)
class RunnerStats:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    durations: list[float] = field(default_factory=list)


class JobRunner:
    def __init__(
        self,
        jobs: JobService,
        registry: JobRegistry,
        *,
        worker_id: str,
        timeout_seconds: float = 900.0,
        heartbeat_seconds: float = 30.0,
        inline: bool = False,
    ) -> None:
        self._jobs = jobs
        self._registry = registry
        self._worker_id = worker_id
        self._timeout = timeout_seconds
        self._heartbeat = heartbeat_seconds
        self._inline = inline  # tests and the child process run handlers in-process
        self.stats = RunnerStats()

    async def run_next(self) -> bool:
        """Claim one job and run it. Returns False when nothing is claimable."""
        job = await self._jobs.claim(self._worker_id, types=self._registry.types())
        if job is None:
            return False
        handler = self._registry.get(job.type)
        started = time.monotonic()
        log.info(
            "job.start", job_id=str(job.id), type=job.type, attempt=job.attempts, tenant_id=str(job.tenant_id)
        )
        if handler.isolated and not self._inline:
            await self._run_isolated(job, handler)
        else:
            await self.run_inline(job, handler)
        elapsed = time.monotonic() - started
        self.stats.processed += 1
        self.stats.durations.append(elapsed)
        log.info("job.done", job_id=str(job.id), type=job.type, seconds=round(elapsed, 2))
        return True

    async def run_inline(self, job: Job, handler: JobHandler) -> None:
        try:
            result = await handler.run(job)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job, never raised to the loop
            await self._fail(job, handler, f"{type(exc).__name__}: {str(exc)[:500]}")
            return
        await self._jobs.complete(job.id, extra={"result": str(result)[:500]})
        self.stats.succeeded += 1

    async def _run_isolated(self, job: Job, handler: JobHandler) -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", CHILD_MODULE, str(job.id), env=os.environ.copy()
        )
        deadline = time.monotonic() + self._timeout
        timed_out = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                await process.wait()
                break
            try:
                await asyncio.wait_for(process.wait(), timeout=min(self._heartbeat, remaining))
                break
            except TimeoutError:
                await self._jobs.heartbeat(job.id)
        current = await self._jobs.get(job.id)
        if current is None:
            return
        if timed_out:
            await self._fail(current, handler, f"timeout after {self._timeout:.0f} s")
        elif current.status is JobStatus.RUNNING:
            await self._fail(current, handler, f"child process exited with code {process.returncode}")
        elif current.status is JobStatus.SUCCEEDED:
            self.stats.succeeded += 1
        else:
            self.stats.failed += 1

    async def _fail(self, job: Job, handler: JobHandler, error: str) -> None:
        self.stats.failed += 1
        updated = await self._jobs.fail(job.id, error)
        final = updated.status is JobStatus.FAILED
        log.warning(
            "job.failed",
            job_id=str(job.id),
            type=job.type,
            error=error,
            final=final,
            attempts=updated.attempts,
        )
        if handler.on_failure is not None:
            try:
                await handler.on_failure(updated, error, final)
            except Exception as exc:  # noqa: BLE001
                log.error("job.failure_hook.error", job_id=str(job.id), error=f"{type(exc).__name__}: {exc}")
