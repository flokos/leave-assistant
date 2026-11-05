"""JobRunner (inline) and the worker loop with job claiming and draining."""

from __future__ import annotations

import asyncio
from uuid import UUID

from leave_assistant.application.container import Services
from leave_assistant.application.jobs.models import Job, JobStatus
from leave_assistant.application.jobs.registry import JobHandler, JobRegistry
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import Settings
from leave_assistant.worker.loop import run_worker_loop
from leave_assistant.worker.runner import JobRunner
from tests.fakes.world import World


async def enqueue(services: Services, tenant_id: UUID, n: int) -> list[Job]:
    jobs = []
    async with services.uow_factory(TenantContext(tenant_id=tenant_id, user_id="t")) as uow:
        for i in range(n):
            jobs.append(
                await services.jobs.enqueue(uow, type="test.echo", payload={"i": i}, tenant_id=tenant_id)
            )
        await uow.commit()
    return jobs


async def test_runner_runs_handlers_and_records_failures(services: Services, world: World) -> None:
    seen: list[int] = []
    failures: list[tuple[str, bool]] = []

    async def run(job: Job) -> object:
        if job.payload["i"] == 1 and job.attempts == 1:
            raise RuntimeError("first attempt fails")
        seen.append(int(job.payload["i"]))
        return "ok"

    async def on_failure(job: Job, error: str, final: bool) -> None:
        failures.append((error, final))

    registry = JobRegistry([JobHandler("test.echo", run, isolated=True, on_failure=on_failure)])
    runner = JobRunner(services.jobs, registry, worker_id="t", inline=True)
    jobs = await enqueue(services, world.a.tenant.id, 3)
    assert await runner.run_next() and await runner.run_next() and await runner.run_next()
    assert not await runner.run_next()  # the failed job waits for its backoff
    assert seen == [0, 2]
    assert failures == [("RuntimeError: first attempt fails", False)]
    assert (await services.jobs.get(jobs[1].id)).status is JobStatus.QUEUED  # type: ignore[union-attr]
    assert runner.stats.processed == 3 and runner.stats.succeeded == 2 and runner.stats.failed == 1
    done = await services.jobs.get(jobs[0].id)
    assert done is not None and done.status is JobStatus.SUCCEEDED and done.extra["result"] == "ok"


async def test_worker_loop_drains_jobs(services: Services, world: World, settings: Settings) -> None:
    processed: list[int] = []

    async def run(job: Job) -> object:
        processed.append(int(job.payload["i"]))
        return None

    registry = JobRegistry([JobHandler("test.echo", run)])
    runner = JobRunner(services.jobs, registry, worker_id="t")
    await enqueue(services, world.a.tenant.id, 4)
    settings.worker_max_jobs_per_iteration = 3
    settings.worker_poll_interval_seconds = 0.01
    iterations = await run_worker_loop(settings, asyncio.Event(), runner=runner, drain=True)
    assert sorted(processed) == [0, 1, 2, 3]
    assert iterations == 3  # 3 jobs, then 1 job, then an empty iteration ends the drain
    assert await services.jobs.active_count() == 0
