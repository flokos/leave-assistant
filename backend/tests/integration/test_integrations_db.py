"""M8 on PostgreSQL: the calendar outbox commits and rolls back with the approval, RLS on the new tables, the OAuth
state is consumed exactly once under concurrency, the outbox lease spans tenants, and the mock calendar store."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.calendar.models import OAuthState
from leave_assistant.application.container import Services
from leave_assistant.application.errors import ConflictError
from leave_assistant.application.leave.commands import Decide, SubmitRequest
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.calendar.model import ConnectionKind, EventStatus, SyncStatus, event_id
from leave_assistant.domain.tenancy import Employee
from leave_assistant.infrastructure.calendar.mock import MockCalendarProvider
from tests.conftest import principal_for
from tests.unit.calendar.helpers import run_sync_jobs

pytestmark = pytest.mark.integration


def people(seeded: dict[str, object], tenant: str) -> dict[str, Employee]:
    return seeded["employees"][tenant]  # type: ignore[index, no-any-return]


async def connect(services: Services, employee: Employee, kind: ConnectionKind) -> None:
    url = await services.calendar_connections.start(principal_for(employee), kind)
    query = parse_qs(urlparse(url).query)
    result = await services.calendar_connections.callback(
        state=query["state"][0], code=query["code"][0], error=None
    )
    assert result.status == "connected", result


async def test_approval_writes_the_outbox_and_the_worker_syncs(
    db_services: Services, seeded: dict[str, object], engine: AsyncEngine
) -> None:
    aegean = people(seeded, "aegean")
    admin, requester, manager = aegean["AEG-001"], aegean["AEG-004"], aegean["AEG-003"]
    await connect(db_services, admin, ConnectionKind.COMPANY_SHARED)
    await connect(db_services, requester, ConnectionKind.PERSONAL)
    detail = await db_services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", date(2026, 12, 14), date(2026, 12, 18))
    )
    final = await db_services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))
    tenant = TenantContext(tenant_id=requester.tenant_id, user_id="t", roles=("system",))
    async with db_services.uow_factory(tenant) as uow:
        jobs = await uow.calendar_jobs.list(
            filters=__import__(
                "leave_assistant.application.ports", fromlist=["SyncJobFilter"]
            ).SyncJobFilter(),
            limit=10,
        )
    assert sorted(j.operation.value for j in jobs) == ["create", "create"] and all(
        j.status is SyncStatus.PENDING for j in jobs
    )
    await run_sync_jobs(db_services)
    async with db_services.uow_factory(tenant) as uow:
        company = await uow.calendar_connections.find_live(ConnectionKind.COMPANY_SHARED, None)
        assert company is not None
        body = await uow.mock_calendar.get(
            company.external_calendar_id or "", event_id(final.request.id, company.kind)
        )
        personal_body = await uow.mock_calendar.get(
            "primary", event_id(final.request.id, ConnectionKind.PERSONAL)
        )
        records = await uow.calendar_events.list_for_connection(company.id)
    assert (
        body is not None and body["end"] == {"date": "2026-12-19"} and requester.last_name in body["summary"]
    )
    assert personal_body is not None and requester.last_name not in personal_body["summary"]
    assert [r.status for r in records] == [EventStatus.PRESENT]
    # RLS: the other tenant sees none of it
    brightwater = people(seeded, "brightwater")
    other = TenantContext(
        tenant_id=next(iter(brightwater.values())).tenant_id, user_id="t", roles=("system",)
    )
    async with db_services.uow_factory(other) as uow:
        assert await uow.calendar_connections.list_live() == []
        assert await uow.mock_calendar.list(company.external_calendar_id or "") == []
    async with engine.connect() as conn:  # no tenant context at all → nothing
        assert (await conn.execute(text("SELECT count(*) FROM app.calendar_sync_jobs"))).scalar_one() == 0


async def test_outbox_rows_roll_back_with_a_failed_approval(
    db_services: Services, seeded: dict[str, object], calendar_db_mock: MockCalendarProvider
) -> None:
    aegean = people(seeded, "aegean")
    admin, requester, manager = aegean["AEG-001"], aegean["AEG-004"], aegean["AEG-003"]
    await connect(db_services, admin, ConnectionKind.COMPANY_SHARED)
    detail = await db_services.leave_requests.submit(
        principal_for(requester), SubmitRequest("annual", date(2026, 12, 14), date(2026, 12, 15))
    )
    original = db_services.calendar_sync.request_approved

    async def explode(uow, request):  # type: ignore[no-untyped-def]
        await original(uow, request)  # the outbox row is written …
        raise RuntimeError("boom")  # … and the transaction fails afterwards

    db_services.calendar_sync.request_approved = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await db_services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))
    tenant = TenantContext(tenant_id=requester.tenant_id, user_id="t", roles=("system",))
    async with db_services.uow_factory(tenant) as uow:
        request = await uow.leave_requests.get(detail.request.id)
        due = await uow.calendar_jobs.list_due(db_services.clock.now() + timedelta(days=1), 10)
    assert request is not None and request.status.value == "PENDING_MANAGER" and due == []


async def test_oauth_state_is_consumed_once_under_concurrency(
    db_services: Services, seeded: dict[str, object]
) -> None:
    employee = people(seeded, "aegean")["AEG-004"]
    now = db_services.clock.now()
    async with db_services.uow_factory(None) as uow:
        await uow.oauth_states.add(
            OAuthState(
                nonce_hash="h" * 64,
                tenant_id=employee.tenant_id,
                user_id=employee.keycloak_sub,
                employee_id=employee.id,
                kind=ConnectionKind.PERSONAL,
                provider="mock",
                roles=("employee",),
                expires_at=now + timedelta(minutes=10),
                created_at=now,
            )
        )
        await uow.commit()

    async def consume() -> bool:
        async with db_services.uow_factory(None) as uow:
            state = await uow.oauth_states.consume("h" * 64, now)
            await uow.commit()
            return state is not None

    results = await asyncio.gather(*(consume() for _ in range(4)))
    assert sorted(results) == [False, False, False, True]


async def test_one_live_connection_per_kind(db_services: Services, seeded: dict[str, object]) -> None:
    aegean = people(seeded, "aegean")
    await connect(db_services, aegean["AEG-001"], ConnectionKind.COMPANY_SHARED)
    await connect(db_services, aegean["AEG-001"], ConnectionKind.COMPANY_SHARED)  # re-authorisation updates
    tenant = TenantContext(tenant_id=aegean["AEG-001"].tenant_id, user_id="t", roles=("system",))
    async with db_services.uow_factory(tenant) as uow:
        live = await uow.calendar_connections.list_live()
        assert len(live) == 1
        duplicate = live[0]
        duplicate.id = uuid4()
        with pytest.raises(ConflictError):
            await uow.calendar_connections.add(duplicate)  # the partial unique index


async def test_outbox_lease_spans_tenants_and_respects_rls(
    db_services: Services, seeded: dict[str, object], clock
) -> None:  # type: ignore[no-untyped-def]
    from leave_assistant.domain.tenancy import Principal, Role

    automation = Principal(user_id="svc", username="svc", roles=frozenset({Role.AUTOMATION}))
    leased = await db_services.ops.lease_events(automation, limit=200)
    tenants = {e.tenant_id for e in leased}
    assert len(tenants) == 2  # the seed's notifications in both companies
    assert await db_services.ops.lease_events(automation, limit=200) == []  # leased
    acked = await db_services.ops.ack_events(automation, [e.id for e in leased])
    assert acked == len(leased)
    clock.tick(301)
    assert await db_services.ops.lease_events(automation, limit=200) == []  # delivered stays delivered
