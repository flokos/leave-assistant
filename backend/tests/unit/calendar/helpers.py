"""Shared steps for the calendar scenarios (S-cal-*): connect through the mock consent, run the worker's jobs."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from leave_assistant.application.calendar.models import JOB_CALENDAR_SYNC, CalendarConnection
from leave_assistant.application.container import Services
from leave_assistant.application.leave.commands import Decide, SubmitRequest
from leave_assistant.domain.calendar.model import ConnectionKind
from leave_assistant.domain.tenancy import Employee
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock

DEC_14, DEC_18 = date(2026, 12, 14), date(2026, 12, 18)


async def connect(services: Services, employee: Employee, kind: ConnectionKind) -> CalendarConnection:
    url = await services.calendar_connections.start(principal_for(employee), kind)
    query = parse_qs(urlparse(url).query)
    result = await services.calendar_connections.callback(
        state=query["state"][0], code=query["code"][0], error=None
    )
    assert result.status == "connected", result
    connections = await services.calendar_connections.list(principal_for(employee))
    return next(c for c in connections if c.kind is kind)


async def run_sync_jobs(services: Services, *, limit: int = 20) -> list[object]:
    """What the worker does: claim due `calendar.sync` jobs, run the handler, complete the queue job."""
    results: list[object] = []
    for _ in range(limit):
        job = await services.jobs.claim("test-worker", types=[JOB_CALENDAR_SYNC])
        if job is None:
            break
        results.append(await services.calendar_sync.handle_job(job))
        await services.jobs.complete(job.id)
    return results


async def approve_annual(
    services: Services, requester: Employee, manager: Employee, start: date = DEC_14, end: date = DEC_18
) -> UUID:
    detail = await services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", start, end)
    )
    final = await services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))
    return final.request.id


def mock_events(store: MemoryStore) -> dict[str, dict[str, object]]:
    return {event_id: body for (_, _, event_id), body in store.mock_calendar.items()}


def advance(clock: FixedClock, seconds: float) -> None:
    clock.tick(seconds)
