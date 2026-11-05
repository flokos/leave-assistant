"""JobService: dedupe, claim order, attempts, backoff, dead letters, stale reclaim, administration."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import ConflictError, NotFoundError
from leave_assistant.application.jobs.models import JobStatus
from leave_assistant.application.tenancy.context import TenantContext
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock
from tests.fakes.world import World


async def enqueue(
    services: Services, tenant_id: UUID, *, type: str = "document.process", dedupe: str | None = None
):  # type: ignore[no-untyped-def]
    async with services.uow_factory(TenantContext(tenant_id=tenant_id, user_id="t")) as uow:
        job = await services.jobs.enqueue(
            uow,
            type=type,
            payload={"tenant_id": str(tenant_id), "version_id": str(UUID(int=1))},
            tenant_id=tenant_id,
            dedupe_key=dedupe,
        )
        await uow.commit()
    return job


async def test_enqueue_dedupes_active_jobs_only(services: Services, world: World) -> None:
    tenant = world.a.tenant.id
    first = await enqueue(services, tenant, dedupe="document.process:x")
    again = await enqueue(services, tenant, dedupe="document.process:x")
    assert again.id == first.id
    claimed = await services.jobs.claim("w1")
    assert claimed is not None and claimed.id == first.id and claimed.status is JobStatus.RUNNING
    assert claimed.attempts == 1 and claimed.locked_by == "w1"
    await services.jobs.complete(first.id)
    fresh = await enqueue(services, tenant, dedupe="document.process:x")
    assert fresh.id != first.id


async def test_claim_order_types_and_empty_queue(services: Services, world: World, clock: FixedClock) -> None:
    tenant = world.a.tenant.id
    a = await enqueue(services, tenant, type="document.process")
    clock.tick(1)
    b = await enqueue(services, tenant, type="document.reindex")
    assert await services.jobs.claim("w", types=["document.reindex"]) is not None  # b first by type filter
    assert (await services.jobs.claim("w")).id == a.id  # type: ignore[union-attr]
    assert await services.jobs.claim("w") is None
    assert (await services.jobs.get(b.id)).status is JobStatus.RUNNING  # type: ignore[union-attr]
    assert await services.jobs.active_count() == 2
    assert (await services.jobs.counts()) == {"running": 2}


async def test_failure_backoff_and_dead_letter(services: Services, world: World, clock: FixedClock) -> None:
    tenant = world.a.tenant.id
    job = await enqueue(services, tenant)
    for attempt, backoff in ((1, 30), (2, 60)):
        claimed = await services.jobs.claim("w")
        assert claimed is not None and claimed.attempts == attempt
        failed = await services.jobs.fail(job.id, "boom")
        assert failed.status is JobStatus.QUEUED and failed.last_error == "boom"
        assert failed.run_after == clock.now() + timedelta(seconds=backoff)
        assert await services.jobs.claim("w") is None  # not before run_after
        clock.tick(backoff)
    claimed = await services.jobs.claim("w")
    assert claimed is not None and claimed.attempts == 3
    dead = await services.jobs.fail(job.id, "boom again")
    assert dead.status is JobStatus.FAILED and dead.finished_at == clock.now()
    assert await services.jobs.claim("w") is None


async def test_stale_running_jobs_are_reclaimed(services: Services, world: World, clock: FixedClock) -> None:
    job = await enqueue(services, world.a.tenant.id)
    claimed = await services.jobs.claim("w1")
    assert claimed is not None
    clock.tick(300)
    await services.jobs.heartbeat(job.id)
    clock.tick(500)
    assert await services.jobs.claim("w2") is None  # heartbeat 500 s ago < 600 s stale window
    clock.tick(200)
    reclaimed = await services.jobs.claim("w2")
    assert reclaimed is not None and reclaimed.locked_by == "w2" and reclaimed.attempts == 2


async def test_administration(services: Services, world: World, store: MemoryStore) -> None:
    tenant = world.a.tenant.id
    job = await enqueue(services, tenant)
    other = await enqueue(services, world.b.tenant.id)
    hr = principal_for(world.a.employees["hr_admin"])
    admin = principal_for(world.a.employees["company_admin"])
    listed = await services.jobs.list_for_tenant(hr, status=None, type=None, limit=50)
    assert [j.id for j in listed] == [job.id]
    with pytest.raises(ConflictError) as info:
        await services.jobs.retry(admin, job.id)
    assert info.value.problem_type == "job/not-failed"
    store.jobs[job.id].status = JobStatus.FAILED
    store.jobs[job.id].attempts = 3
    retried = await services.jobs.retry(admin, job.id)
    assert retried.status is JobStatus.QUEUED and retried.attempts == 0
    with pytest.raises(NotFoundError):
        await services.jobs.retry(admin, other.id)
