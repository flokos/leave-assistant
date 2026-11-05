"""Catalogue administration, simulation, ledger, team views, notifications and rollover."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import AuthorizationError, ConflictError, InvalidInputError
from leave_assistant.application.leave.commands import (
    Adjustment,
    CreateHoliday,
    CreateLeaveType,
    CreatePolicy,
    CreateRule,
    Decide,
    SimulateRequest,
    SubmitRequest,
    UpdateLeaveType,
    UpdatePolicy,
    UpdateRule,
)
from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.leave.ledger import EntryType
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.types import StepRole
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World


async def test_catalogue_crud_and_validation(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin = principal_for(leave_world.a.employees["company_admin"])
    employee = principal_for(leave_world.a.employees["employee"])
    assert [t.code for t in await services.catalog.list_types(employee)] == ["annual", "sick", "unpaid"]
    created = await services.catalog.create_type(
        admin, CreateLeaveType("study", "Εκπαιδευτική", "Study leave", False)
    )
    assert created.code == "study" and store.audit[-1].action == "admin.config.changed"
    with pytest.raises(ConflictError):
        await services.catalog.create_type(admin, CreateLeaveType("study", "x", "x", False))
    with pytest.raises(InvalidValue):
        await services.catalog.create_type(admin, CreateLeaveType("bad code!", "x", "x", False))
    with pytest.raises(AuthorizationError):
        await services.catalog.create_type(employee, CreateLeaveType("x", "x", "x", False))
    updated = await services.catalog.update_type(
        admin, created.id, UpdateLeaveType(active=False, requires_document_above_days=2)
    )
    assert updated.active is False and updated.requires_document_above_days == 2
    assert "study" not in [t.code for t in await services.catalog.list_types(employee)]

    policy = await services.catalog.create_policy(admin, CreatePolicy("unpaid", 2026, Decimal("0")))
    with pytest.raises(ConflictError):
        await services.catalog.create_policy(admin, CreatePolicy("unpaid", 2026, Decimal("0")))
    with pytest.raises(InvalidValue):
        await services.catalog.update_policy(admin, policy.id, UpdatePolicy(max_consecutive_days=0))
    assert (
        await services.catalog.update_policy(admin, policy.id, UpdatePolicy(min_notice_days=2))
    ).min_notice_days == 2
    await services.catalog.delete_policy(admin, policy.id)
    assert len(await services.catalog.list_policies(admin, year=2026)) == 2

    holiday = await services.catalog.create_holiday(
        admin, CreateHoliday(date(2026, 11, 20), "Τοπική αργία", "Local holiday")
    )
    with pytest.raises(ConflictError):
        await services.catalog.create_holiday(admin, CreateHoliday(date(2026, 11, 20), "x", "x"))
    imported = await services.catalog.import_holidays_csv(
        admin, "date,name_el,name_en,recurring\n2026-11-20,dup,dup,false\n2026-11-27,Αργία,Bridge day,true\n"
    )
    assert [h.holiday_date for h in imported] == [date(2026, 11, 27)] and imported[0].recurring
    with pytest.raises(InvalidInputError):
        await services.catalog.import_holidays_csv(admin, "day,name\n2026-01-01,x\n")
    await services.catalog.delete_holiday(admin, holiday.id)
    assert date(2026, 11, 20) not in [
        h.holiday_date for h in await services.catalog.list_holidays(admin, year=2026)
    ]

    rule = await services.catalog.create_rule(
        admin, CreateRule((StepRole.HR,), leave_type_code="unpaid", priority=5)
    )
    assert rule.leave_type_id == leave_world.leave_types["a"]["unpaid"].id  # type: ignore[attr-defined]
    with pytest.raises(InvalidValue):
        await services.catalog.create_rule(admin, CreateRule((), priority=1))
    rule = await services.catalog.update_rule(
        admin, rule.id, UpdateRule(active=False, max_working_days=Decimal("2"))
    )
    assert rule.active is False and rule.max_working_days == Decimal("2")
    await services.catalog.delete_rule(admin, rule.id)
    assert len(await services.catalog.list_rules(admin, include_inactive=True)) == 4


async def test_simulation_matches_submission(services: Services, leave_world: World) -> None:
    admin = principal_for(leave_world.a.employees["company_admin"])
    requester = leave_world.a.employees["employee"]
    sim = await services.catalog.simulate(
        admin, SimulateRequest("annual", date(2026, 11, 16), date(2026, 11, 24), requester.id)
    )
    assert sim.approval_chain == (StepRole.MANAGER, StepRole.HR) and sim.working_days == 7
    detail = await services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", date(2026, 11, 16), date(2026, 11, 24))
    )
    assert tuple(s.approver_role for s in detail.steps) == sim.approval_chain
    sick = await services.catalog.simulate(
        admin, SimulateRequest("sick", date(2026, 11, 2), date(2026, 11, 3), requester.id)
    )
    assert sick.auto_acknowledged and sick.approval_chain == ()
    with pytest.raises(AuthorizationError):
        await services.catalog.simulate(
            principal_for(requester),
            SimulateRequest("annual", date(2026, 11, 16), date(2026, 11, 17), requester.id),
        )


async def test_ledger_reads_and_adjustments(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, hr, admin = (leave_world.a.employees[k] for k in ("employee", "hr_admin", "company_admin"))
    views = await services.ledger.balances(principal_for(requester), year=2026)
    assert [(v.leave_type.code, v.balance_days, v.available_days) for v in views] == [
        ("annual", Decimal("20"), Decimal("20")),
        ("sick", Decimal("10"), Decimal("10")),
    ]
    entry = await services.ledger.adjust(
        principal_for(hr), Adjustment(requester.id, "annual", Decimal("2.5"), "bonus days")
    )
    assert entry.entry_type is EntryType.ADJUSTMENT and entry.created_by_employee_id == hr.id
    assert (
        store.audit[-1].action == "ledger.entry.posted" and store.audit[-1].details["quantity_days"] == "2.5"
    )
    with pytest.raises(AuthorizationError):
        await services.ledger.adjust(
            principal_for(admin), Adjustment(requester.id, "annual", Decimal("1"), "nope")
        )
    with pytest.raises(InvalidInputError):
        await services.ledger.adjust(
            principal_for(hr), Adjustment(requester.id, "unpaid", Decimal("1"), "no balance")
        )
    with pytest.raises(InvalidInputError):
        await services.ledger.adjust(
            principal_for(hr), Adjustment(requester.id, "annual", Decimal("0"), "zero")
        )
    ledger = await services.ledger.ledger(principal_for(requester), leave_type_code="annual", year=2026)
    assert [e.entry_type for e in ledger] == [EntryType.GRANT, EntryType.ADJUSTMENT]
    rows = await services.ledger.tenant_balances(principal_for(admin), year=2026)
    assert len(rows) == 12 and all(
        r.employee_id in {e.id for e in leave_world.a.employees.values()} for r in rows
    )
    with pytest.raises(AuthorizationError):
        await services.ledger.tenant_balances(principal_for(requester), year=2026)


async def test_team_views_follow_need_to_know(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, manager, hr, admin, fin = (
        leave_world.a.employees[k] for k in ("employee", "manager", "hr_admin", "company_admin", "employee2")
    )
    detail = await services.leave_requests.submit(
        principal_for(requester), SubmitRequest("sick", date(2026, 11, 2), date(2026, 11, 3), reason="flu")
    )
    assert detail.request.status is RequestStatus.APPROVED
    annual = await services.leave_requests.submit(
        principal_for(fin), SubmitRequest("annual", date(2026, 12, 14), date(2026, 12, 15), reason="trip")
    )
    assert [e.employee_number for e in await services.team.employees(principal_for(manager))] == [
        "AEG-003",
        "AEG-004",
    ]
    assert (
        len(await services.team.employees(principal_for(hr))) == 5
        and len(await services.team.employees(principal_for(admin))) == 5
    )
    with pytest.raises(AuthorizationError):
        await services.team.employees(principal_for(requester))
    rows = await services.team.availability(
        principal_for(manager), start=date(2026, 11, 1), end=date(2026, 12, 31)
    )
    assert [(r.display_name, r.leave_type_code, r.status) for r in rows] == [
        ("Worker Aegean", "sick", "APPROVED")
    ]
    admin_rows = await services.team.availability(
        principal_for(admin), start=date(2026, 11, 1), end=date(2026, 12, 31)
    )
    assert [(r.employee_id, r.leave_type_code) for r in admin_rows] == [(requester.id, None)]  # absence only
    await services.approvals.approve(
        principal_for(leave_world.a.employees["manager"]), annual.steps[0].id, Decide(version=1)
    ) if annual.steps[0].designated_employee_id == manager.id else None
    page = await services.team.requests(principal_for(hr), statuses=None, limit=None, cursor=None)
    assert {(r.request.id, r.reason_visible) for r in page.items} >= {(detail.request.id, True)}
    manager_page = await services.team.requests(
        principal_for(manager), statuses=None, limit=None, cursor=None
    )
    assert [(r.request.id, r.reason_visible) for r in manager_page.items] == [(detail.request.id, False)]
    with pytest.raises(AuthorizationError):
        await services.team.requests(principal_for(admin), statuses=None, limit=None, cursor=None)


async def test_notifications_listing_and_read_state(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, manager = leave_world.a.employees["employee"], leave_world.a.employees["manager"]
    await services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", date(2026, 12, 14), date(2026, 12, 15))
    )
    page = await services.notifications.list(
        principal_for(manager), unread_only=True, limit=None, cursor=None
    )
    assert [n.type for n in page.items] == ["approval.pending"] and page.items[0].channels == (
        "in_app",
        "email",
    )
    marked = await services.notifications.mark_read(principal_for(manager), page.items[0].id)
    assert marked.read_at is not None
    assert (
        await services.notifications.list(principal_for(manager), unread_only=True, limit=None, cursor=None)
    ).items == []
    assert (
        len(
            (
                await services.notifications.list(
                    principal_for(manager), unread_only=False, limit=None, cursor=None
                )
            ).items
        )
        == 1
    )
    with pytest.raises(Exception):  # noqa: B017 - someone else's notification is not found
        await services.notifications.mark_read(principal_for(requester), page.items[0].id)
    assert await services.notifications.mark_all_read(principal_for(requester)) == 1
    assert len(store.integration_events) == 2


async def test_rollover_statuses_and_ledger_calendar(
    services: Services, leave_world: World, store: MemoryStore, clock
) -> None:  # type: ignore[no-untyped-def]
    requester, manager = leave_world.a.employees["employee"], leave_world.a.employees["manager"]
    tenant = store.tenants[leave_world.a.tenant.id]
    annual = leave_world.leave_types["a"]["annual"].id  # type: ignore[attr-defined]
    detail = await services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", date(2026, 11, 9), date(2026, 11, 13))
    )
    await services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))

    first = await services.rollover.run_for_tenant(tenant, date(2026, 11, 5))
    assert (first.started, first.completed, first.grants, first.carryovers, first.expiries) == (
        0,
        0,
        0,
        0,
        0,
    )  # grants exist already
    active = await services.rollover.run_for_tenant(tenant, date(2026, 11, 10))
    assert active.started == 1 and store.leave_requests[detail.request.id].status is RequestStatus.ACTIVE
    done = await services.rollover.run_for_tenant(tenant, date(2026, 11, 16))
    assert done.completed == 1 and store.leave_requests[detail.request.id].status is RequestStatus.COMPLETED
    assert [e.event_type for e in store.request_events if e.request_id == detail.request.id][-2:] == [
        "started",
        "completed",
    ]

    clock.current = datetime(2027, 1, 5, 9, 0, tzinfo=UTC)
    new_year = await services.rollover.run_for_tenant(tenant, date(2027, 1, 5))
    assert (
        new_year.grants == 10 and new_year.carryovers == 10
    )  # 5 active employees × (annual, sick); inactive skipped
    carried = [
        e
        for e in store.ledger
        if e.entry_type is EntryType.CARRYOVER and e.employee_id == requester.id and e.leave_type_id == annual
    ]
    assert [e.quantity_days for e in carried] == [Decimal("5")]  # 15 unused, capped at 5
    again = await services.rollover.run_for_tenant(tenant, date(2027, 1, 6))
    assert again.changes == 0  # idempotent

    clock.current = datetime(2027, 4, 1, 9, 0, tzinfo=UTC)
    expired = await services.rollover.run_for_tenant(tenant, date(2027, 4, 1))
    assert expired.expiries == 10
    expiry = [
        e
        for e in store.ledger
        if e.entry_type is EntryType.EXPIRY and e.employee_id == requester.id and e.leave_type_id == annual
    ]
    assert [e.quantity_days for e in expiry] == [Decimal("-5")]
    assert store.audit[-1].action == "system.rollover" and store.audit[-1].actor_kind.value == "system"

    reports = await services.rollover.run_due(datetime(2027, 4, 2, 9, 0, tzinfo=UTC))
    assert {r.tenant_slug for r in reports} == {"aegean", "brightwater"} and all(
        not r.errors for r in reports
    )
