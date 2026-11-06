"""Calendar sync scenarios on the mock provider (doc 10, doc 11 S-cal-1…9; M8 exit: retry/parking/dead/reconcile)."""

from __future__ import annotations

from datetime import date

import pytest

from leave_assistant.application.calendar.errors import SyncNotRetryable
from leave_assistant.application.container import Services
from leave_assistant.application.errors import AuthorizationError
from leave_assistant.application.leave.commands import CancelRequest, Decide, ModifyRequest, SubmitRequest
from leave_assistant.domain.calendar.model import (
    BACKOFF_SECONDS,
    ConnectionKind,
    ConnectionStatus,
    ErrorClass,
    EventStatus,
    SyncOperation,
    SyncStatus,
    event_id,
)
from leave_assistant.infrastructure.calendar.mock import MockCalendarProvider
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock
from tests.fakes.world import World
from tests.unit.calendar.helpers import DEC_14, DEC_18, approve_annual, connect, mock_events, run_sync_jobs


def _jobs(store: MemoryStore) -> list:  # type: ignore[type-arg]
    return sorted(store.calendar_jobs.values(), key=lambda j: j.created_at)


async def test_s_cal_1_approval_creates_the_event(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    company = await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    assert (
        company.status is ConnectionStatus.ACTIVE and company.external_calendar_id == f"mock-cal-{company.id}"
    )
    assert company.refresh_token_encrypted and b"mock-rt" not in company.refresh_token_encrypted  # encrypted

    request_id = await approve_annual(services, requester, manager)
    [job] = _jobs(store)
    assert job.operation is SyncOperation.CREATE and job.status is SyncStatus.PENDING
    await run_sync_jobs(services)

    [job] = _jobs(store)
    assert job.status is SyncStatus.SUCCEEDED and job.attempts == 1
    eid = event_id(request_id, ConnectionKind.COMPANY_SHARED)
    body = mock_events(store)[eid]
    assert body["start"] == {"date": "2026-12-14"} and body["end"] == {"date": "2026-12-19"}  # exclusive end
    assert body["transparency"] == "transparent"
    [record] = store.calendar_events.values()
    assert record.status is EventStatus.PRESENT and record.external_event_id == eid
    assert store.calendar_connections[company.id].last_success_at is not None


async def test_s_cal_2_transient_error_retries_with_backoff(
    services: Services,
    leave_world: World,
    store: MemoryStore,
    clock: FixedClock,
    calendar_mock: MockCalendarProvider,
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    calendar_mock.fail_mode = "transient_once"
    await approve_annual(services, requester, manager)
    await run_sync_jobs(services)
    [job] = _jobs(store)
    assert job.status is SyncStatus.PENDING and job.error_class is ErrorClass.TRANSIENT and job.attempts == 1
    assert job.next_attempt_at is not None
    delay = (job.next_attempt_at - clock.now()).total_seconds()
    assert BACKOFF_SECONDS[0] * 0.9 <= delay <= BACKOFF_SECONDS[0] * 1.1

    assert await run_sync_jobs(services) == []  # not due yet
    clock.tick(BACKOFF_SECONDS[0] * 1.2)
    await run_sync_jobs(services)
    [job] = _jobs(store)
    assert job.status is SyncStatus.SUCCEEDED and job.attempts == 2 and len(mock_events(store)) == 1


async def test_s_cal_3_auth_expired_parks_until_reauthorised(
    services: Services, leave_world: World, store: MemoryStore, calendar_mock: MockCalendarProvider
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    company = await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    calendar_mock.fail_mode = "auth_expired"
    calendar_mock.valid_after_ms = 10**15  # every token issued so far is "revoked"
    await approve_annual(services, requester, manager)
    await run_sync_jobs(services)

    [job] = _jobs(store)
    assert job.status is SyncStatus.PARKED and job.error_class is ErrorClass.AUTH_EXPIRED
    assert store.calendar_connections[company.id].status is ConnectionStatus.EXPIRED
    notes = [n for n in store.notifications.values() if n.type == "calendar.connection_expired"]
    assert [n.recipient_employee_id for n in notes] == [admin.id]
    assert any(e.type == "calendar.connection_expired" for e in store.integration_events)

    # a second approval while expired is parked straight away (no provider call)
    other, hr = leave_world.a.employees["employee2"], leave_world.a.employees["hr_admin"]
    await approve_annual(services, other, hr)  # Finance has no manager: HR decides
    assert [j.status for j in _jobs(store)] == [SyncStatus.PARKED, SyncStatus.PARKED]

    calendar_mock.valid_after_ms = 0  # re-authorisation issues a fresh token
    reconnected = await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    assert reconnected.id == company.id and reconnected.status is ConnectionStatus.ACTIVE
    assert [j.status for j in _jobs(store)] == [SyncStatus.PENDING, SyncStatus.PENDING]
    await run_sync_jobs(services)
    assert [j.status for j in _jobs(store)] == [SyncStatus.SUCCEEDED, SyncStatus.SUCCEEDED]
    assert len(mock_events(store)) == 2


async def test_s_cal_4_permanent_error_dead_letters_and_manual_retry(
    services: Services, leave_world: World, store: MemoryStore, calendar_mock: MockCalendarProvider
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    calendar_mock.fail_mode = "permanent"
    await approve_annual(services, requester, manager)
    await run_sync_jobs(services)
    [job] = _jobs(store)
    assert job.status is SyncStatus.DEAD and job.error_class is ErrorClass.PERMANENT and job.finished_at
    failed = [n for n in store.notifications.values() if n.type == "calendar.sync_failed"]
    assert [n.recipient_employee_id for n in failed] == [admin.id]
    assert failed[0].params["request_number"].startswith("LR-2026-")
    assert any(e.type == "calendar.sync_failed" for e in store.integration_events)

    with pytest.raises(AuthorizationError):
        await services.calendar_sync.retry(principal_for(manager), job.id)
    calendar_mock.fail_mode = "none"
    retried = await services.calendar_sync.retry(principal_for(admin), job.id)
    assert retried.status is SyncStatus.PENDING and retried.attempts == 0
    with pytest.raises(SyncNotRetryable):
        await services.calendar_sync.retry(principal_for(admin), job.id)
    await run_sync_jobs(services)
    assert _jobs(store)[0].status is SyncStatus.SUCCEEDED
    assert any(a.action == "calendar.sync.retried" for a in store.audit)


async def test_attempts_are_exhausted_after_five_transient_failures(
    services: Services,
    leave_world: World,
    store: MemoryStore,
    clock: FixedClock,
    calendar_mock: MockCalendarProvider,
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    await approve_annual(services, requester, manager)

    from leave_assistant.application.calendar.models import CalendarProviderError

    async def always_503(*_: object, **__: object) -> None:
        raise CalendarProviderError(ErrorClass.TRANSIENT, "503 backendError", retry_after=7200)

    calendar_mock.insert = always_503  # type: ignore[method-assign,assignment]
    for _ in range(5):
        await run_sync_jobs(services)
        clock.tick(5 * 3600)
    [job] = _jobs(store)
    assert job.status is SyncStatus.DEAD and job.attempts == 5 and job.last_error == "503 backendError"


async def test_retry_after_is_honoured(
    services: Services,
    leave_world: World,
    store: MemoryStore,
    clock: FixedClock,
    calendar_mock: MockCalendarProvider,
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    await approve_annual(services, requester, manager)
    from leave_assistant.application.calendar.models import CalendarProviderError

    async def limited(*_: object, **__: object) -> None:
        raise CalendarProviderError(ErrorClass.TRANSIENT, "429 rateLimitExceeded", retry_after=900)

    calendar_mock.insert = limited  # type: ignore[method-assign,assignment]
    await run_sync_jobs(services)
    [job] = _jobs(store)
    assert job.next_attempt_at is not None and (job.next_attempt_at - clock.now()).total_seconds() == 900


async def test_s_cal_5_cancellation_deletes_the_event(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    request_id = await approve_annual(services, requester, manager)
    await run_sync_jobs(services)
    assert len(mock_events(store)) == 1
    version = store.leave_requests[request_id].version
    await services.leave_requests.cancel(principal_for(requester), request_id, CancelRequest(version=version))
    delete_job = _jobs(store)[-1]
    assert delete_job.operation is SyncOperation.DELETE
    await run_sync_jobs(services)
    assert mock_events(store) == {}
    assert next(iter(store.calendar_events.values())).status is EventStatus.DELETED

    # deleting again (event already gone → 404) is still a success
    store.calendar_jobs[delete_job.id].status = SyncStatus.PENDING
    from leave_assistant.application.calendar.sync import system_context  # noqa: F401 - documents the context

    outcome = await services.calendar_sync.run(requester.tenant_id, delete_job.id)
    assert outcome.status is SyncStatus.SUCCEEDED


async def test_cancelling_a_pending_request_writes_nothing(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, requester = leave_world.a.employees["company_admin"], leave_world.a.employees["employee"]
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    detail = await services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", DEC_14, DEC_18)
    )
    await services.leave_requests.cancel(
        principal_for(requester), detail.request.id, CancelRequest(version=1)
    )
    assert store.calendar_jobs == {}


async def test_s_cal_6_approved_modification_moves_the_event(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    original_id = await approve_annual(services, requester, manager)
    await run_sync_jobs(services)
    modification = await services.leave_requests.modify(
        principal_for(requester), original_id, ModifyRequest(DEC_14, date(2026, 12, 16), version=2)
    )
    assert len(_jobs(store)) == 1  # nothing changes until the modification is approved
    await services.approvals.approve(principal_for(manager), modification.steps[0].id, Decide(version=1))
    operations = sorted((j.request_id == original_id, j.operation.value) for j in _jobs(store)[1:])
    assert operations == [(False, "create"), (True, "delete")]
    await run_sync_jobs(services)
    events = mock_events(store)
    assert list(events) == [event_id(modification.request.id, ConnectionKind.COMPANY_SHARED)]
    assert next(iter(events.values()))["end"] == {"date": "2026-12-17"}


async def test_s_cal_7_duplicate_approval_hook_never_duplicates_a_job(
    services: Services, leave_world: World, store: MemoryStore, uow_factory
) -> None:  # type: ignore[no-untyped-def]
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    request_id = await approve_annual(services, requester, manager)
    from leave_assistant.application.calendar.sync import system_context

    async with uow_factory(system_context(requester.tenant_id)) as uow:
        request = await uow.leave_requests.get(request_id)
        assert await services.calendar_sync.request_approved(uow, request) == []  # same key → skipped
        await uow.commit()
    assert len(store.calendar_jobs) == 1
    assert len([j for j in store.jobs.values() if j.type == "calendar.sync"]) == 1


async def test_s_cal_9_privacy_of_company_and_personal_events(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    company = await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    personal = await connect(services, requester, ConnectionKind.PERSONAL)
    assert personal.external_calendar_id == "primary" and personal.employee_id == requester.id
    detail = await services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", DEC_14, DEC_18, reason="family wedding in Crete")
    )
    final = await services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))
    await run_sync_jobs(services)
    by_calendar = {cal: body for (_, cal, _), body in store.mock_calendar.items()}
    company_body = by_calendar[company.external_calendar_id]
    personal_body = by_calendar["primary"]
    name = f"{requester.first_name} {requester.last_name}"
    assert company_body["summary"] in (f"{name} — Out of office", f"{name} — Εκτός γραφείου")
    assert personal_body["summary"] in ("Out of office", "Εκτός γραφείου")
    assert personal_body["transparency"] == "opaque"
    for body in (company_body, personal_body):
        text = str(body)
        assert "annual" not in text.lower() and "wedding" not in text and "LR-" not in text
        assert manager.first_name not in text  # no approver names
        private = body["extendedProperties"]["private"]  # type: ignore[index]
        assert private["leave_request_id"] == str(final.request.id) and private["app"] == "leave-assistant"


async def test_other_employees_requests_do_not_reach_a_personal_calendar(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, other, hr = (leave_world.a.employees[k] for k in ("employee", "employee2", "hr_admin"))
    await connect(services, requester, ConnectionKind.PERSONAL)
    await approve_annual(services, other, hr)
    assert store.calendar_jobs == {}


async def test_sync_history_visibility(services: Services, leave_world: World, store: MemoryStore) -> None:
    admin, requester, other, manager, hr = (
        leave_world.a.employees[k] for k in ("company_admin", "employee", "employee2", "manager", "hr_admin")
    )
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    mine = await approve_annual(services, requester, manager)
    await approve_annual(services, other, hr)
    assert (
        len(await services.calendar_sync.list_jobs(principal_for(admin), status=None, request_id=None)) == 2
    )
    assert len(await services.calendar_sync.list_jobs(principal_for(hr), status=None, request_id=None)) == 2
    own = await services.calendar_sync.list_jobs(principal_for(requester), status=None, request_id=None)
    assert [j.request_id for j in own] == [mine]
    foreign = leave_world.b.employees["company_admin"]
    assert await services.calendar_sync.list_jobs(principal_for(foreign), status=None, request_id=None) == []
