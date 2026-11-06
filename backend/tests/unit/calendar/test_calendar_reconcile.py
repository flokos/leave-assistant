"""S-cal-8: reconciliation finds missing, mismatched, orphaned and unmapped events; manual actions are audited."""

from __future__ import annotations

from datetime import date

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import AuthorizationError, ConflictError
from leave_assistant.application.leave.commands import CancelRequest
from leave_assistant.domain.calendar.model import ConnectionKind, Finding, ItemStatus, event_id
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World
from tests.unit.calendar.helpers import approve_annual, connect, mock_events, run_sync_jobs


async def test_s_cal_8_reconcile_repairs_and_flags(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, requester, manager, hr, other = (
        leave_world.a.employees[k] for k in ("company_admin", "employee", "manager", "hr_admin", "employee2")
    )
    company = await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    cal = company.external_calendar_id
    deleted_by_hand = await approve_annual(services, requester, manager)
    edited_by_hand = await approve_annual(services, other, hr, date(2026, 12, 21), date(2026, 12, 23))
    unmapped = await approve_annual(services, requester, manager, date(2026, 11, 23), date(2026, 11, 24))
    cancelled_while_down = await approve_annual(services, other, hr, date(2026, 11, 25), date(2026, 11, 26))
    await run_sync_jobs(services)
    assert len(mock_events(store)) == 4
    tenant = requester.tenant_id
    ev = lambda r: event_id(r, ConnectionKind.COMPANY_SHARED)  # noqa: E731

    # damage done outside the app
    del store.mock_calendar[(tenant, cal, ev(deleted_by_hand))]
    store.mock_calendar[(tenant, cal, ev(edited_by_hand))]["summary"] = "renamed by someone"
    store.mock_calendar[(tenant, cal, ev(edited_by_hand))]["extendedProperties"]["private"][
        "payload_hash"
    ] = "x"  # type: ignore[index]
    store.calendar_events = {k: v for k, v in store.calendar_events.items() if v.request_id != unmapped}
    # cancelled while the sync could not reach the calendar: the delete job never ran
    version = store.leave_requests[cancelled_while_down].version
    await services.leave_requests.cancel(
        principal_for(other), cancelled_while_down, CancelRequest(version=version)
    )
    store.calendar_jobs = {k: v for k, v in store.calendar_jobs.items() if v.operation.value != "delete"}
    store.jobs = {k: v for k, v in store.jobs.items() if v.status.value != "queued"}
    # an event the app wrote for a request it does not know (e.g. another environment)
    store.mock_calendar[(tenant, cal, "lastrayevent")] = {
        "id": "lastrayevent",
        "start": {"date": "2026-12-01"},
        "end": {"date": "2026-12-02"},
        "extendedProperties": {"private": {"app": "leave-assistant", "leave_request_id": "not-a-uuid"}},
    }

    with pytest.raises(AuthorizationError):
        await services.calendar_reconcile.run_for(principal_for(hr))
    report = await services.calendar_reconcile.run_for(principal_for(admin))
    found = {(i.finding, i.request_id, i.status, i.action) for i in report.items}
    assert found == {
        (Finding.MISSING, deleted_by_hand, ItemStatus.RESOLVED, "enqueued_create"),
        (Finding.MISMATCHED, edited_by_hand, ItemStatus.RESOLVED, "enqueued_update"),
        (Finding.UNKNOWN_EXTERNAL_ID, unmapped, ItemStatus.OPEN, None),
        (Finding.ORPHANED, cancelled_while_down, ItemStatus.RESOLVED, "enqueued_delete"),
        (Finding.ORPHANED, None, ItemStatus.OPEN, None),
    }
    await run_sync_jobs(services)
    events = mock_events(store)
    assert ev(deleted_by_hand) in events and ev(cancelled_while_down) not in events
    assert events[ev(edited_by_hand)]["summary"] != "renamed by someone"

    latest = await services.calendar_reconcile.latest(principal_for(admin))
    assert latest.run_id == report.run_id and len(latest.items) == 5
    by_finding = {i.finding: i for i in latest.items if i.status is ItemStatus.OPEN}
    relinked = await services.calendar_reconcile.act(
        principal_for(admin), by_finding[Finding.UNKNOWN_EXTERNAL_ID].id, "relink"
    )
    assert relinked.status is ItemStatus.RESOLVED and relinked.action == "relinked"
    assert any(r.request_id == unmapped for r in store.calendar_events.values())
    orphan = by_finding[Finding.ORPHANED]
    with pytest.raises(ConflictError):
        await services.calendar_reconcile.act(principal_for(admin), orphan.id, "relink")
    deleted = await services.calendar_reconcile.act(principal_for(admin), orphan.id, "delete-orphan")
    assert deleted.action == "deleted" and "lastrayevent" not in mock_events(store)
    with pytest.raises(ConflictError):
        await services.calendar_reconcile.act(principal_for(admin), orphan.id, "ignore")
    actions = [a.action for a in store.audit if a.action.startswith("calendar.reconciliation")]
    assert actions == [
        "calendar.reconciliation.run",
        "calendar.reconciliation.relinked",
        "calendar.reconciliation.deleted",
    ]

    # a clean second run finds nothing
    second = await services.calendar_reconcile.run_for(principal_for(admin))
    assert second.items == []


async def test_reconcile_all_tenants_counts_findings(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    company = await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    request_id = await approve_annual(services, requester, manager)
    await run_sync_jobs(services)
    del store.mock_calendar[
        (requester.tenant_id, company.external_calendar_id, event_id(request_id, company.kind))
    ]
    summary = await services.calendar_reconcile.run_all()
    assert summary == {"tenants": 2, "findings": {"missing": 1}}
