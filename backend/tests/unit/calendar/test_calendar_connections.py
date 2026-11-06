"""OAuth state handling and connection lifecycle (doc 10 §2, M8 decision 4)."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from leave_assistant.application.calendar.errors import CalendarNotConnected
from leave_assistant.application.calendar.models import OAuthGrant
from leave_assistant.application.container import Services
from leave_assistant.application.errors import AuthorizationError
from leave_assistant.domain.calendar.model import ConnectionKind, ConnectionStatus, EventStatus
from leave_assistant.domain.tenancy import EmployeeStatus, Role
from leave_assistant.infrastructure.calendar.mock import MockCalendarProvider
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock
from tests.fakes.world import World
from tests.unit.calendar.helpers import approve_annual, connect, run_sync_jobs


async def _start(services: Services, employee, kind=ConnectionKind.PERSONAL) -> dict[str, str]:  # type: ignore[no-untyped-def]
    url = await services.calendar_connections.start(principal_for(employee), kind)
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


async def test_state_is_single_use_and_stored_hashed(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    employee = leave_world.a.employees["employee"]
    query = await _start(services, employee)
    assert query["state"] not in store.oauth_states and len(store.oauth_states) == 1
    first = await services.calendar_connections.callback(state=query["state"], code=query["code"], error=None)
    assert first.status == "connected" and first.kind is ConnectionKind.PERSONAL
    replay = await services.calendar_connections.callback(
        state=query["state"], code=query["code"], error=None
    )
    assert (replay.status, replay.reason) == ("error", "state_invalid")
    assert (
        await services.calendar_connections.callback(state=None, code="x", error=None)
    ).reason == "state_missing"
    assert (
        await services.calendar_connections.callback(state="forged", code="x", error=None)
    ).reason == "state_invalid"


async def test_state_expires_after_ten_minutes(
    services: Services, leave_world: World, clock: FixedClock
) -> None:
    query = await _start(services, leave_world.a.employees["employee"])
    clock.tick(timedelta(minutes=10, seconds=1).total_seconds())
    result = await services.calendar_connections.callback(
        state=query["state"], code=query["code"], error=None
    )
    assert result.reason == "state_invalid"


async def test_denied_consent_and_bad_code(services: Services, leave_world: World) -> None:
    employee = leave_world.a.employees["employee"]
    query = await _start(services, employee)
    denied = await services.calendar_connections.callback(
        state=query["state"], code=None, error="access_denied"
    )
    assert denied.reason == "consent_denied"
    query = await _start(services, employee)
    bad = await services.calendar_connections.callback(
        state=query["state"], code="not-a-mock-code", error=None
    )
    assert bad.reason == "exchange_failed"


async def test_principal_must_still_qualify_at_the_callback(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin = leave_world.a.employees["company_admin"]
    query = await _start(services, admin, ConnectionKind.COMPANY_SHARED)
    store.employees[admin.id].roles = frozenset({Role.EMPLOYEE})  # demoted between start and callback
    result = await services.calendar_connections.callback(
        state=query["state"], code=query["code"], error=None
    )
    assert result.reason == "principal_changed" and store.calendar_connections == {}
    employee = leave_world.a.employees["employee"]
    query = await _start(services, employee)
    store.employees[employee.id].status = EmployeeStatus.INACTIVE
    result = await services.calendar_connections.callback(
        state=query["state"], code=query["code"], error=None
    )
    assert result.reason == "principal_changed"


async def test_insufficient_scope_is_refused(
    services: Services, leave_world: World, store: MemoryStore, calendar_mock: MockCalendarProvider
) -> None:
    revoked: list[str] = []

    async def narrow(**_: object) -> OAuthGrant:
        return OAuthGrant("mock-rt:1:abc", ("https://www.googleapis.com/auth/calendar.readonly",))

    async def revoke(token: str) -> None:
        revoked.append(token)

    calendar_mock.exchange = narrow  # type: ignore[method-assign,assignment]
    calendar_mock.revoke = revoke  # type: ignore[method-assign]
    query = await _start(services, leave_world.a.employees["employee"])
    result = await services.calendar_connections.callback(
        state=query["state"], code=query["code"], error=None
    )
    assert (
        result.reason == "insufficient_scope"
        and revoked == ["mock-rt:1:abc"]
        and store.calendar_connections == {}
    )


async def test_fallback_scope_is_accepted(
    services: Services, leave_world: World, calendar_mock: MockCalendarProvider
) -> None:
    async def broad(**_: object) -> OAuthGrant:
        return OAuthGrant("mock-rt:1:abc", ("https://www.googleapis.com/auth/calendar.events",))

    calendar_mock.exchange = broad  # type: ignore[method-assign,assignment]
    query = await _start(services, leave_world.a.employees["employee"])
    result = await services.calendar_connections.callback(
        state=query["state"], code=query["code"], error=None
    )
    assert result.status == "connected"


async def test_who_may_connect_and_see_what(services: Services, leave_world: World) -> None:
    admin, employee, hr = (leave_world.a.employees[k] for k in ("company_admin", "employee", "hr_admin"))
    with pytest.raises(AuthorizationError):
        await services.calendar_connections.start(principal_for(hr), ConnectionKind.COMPANY_SHARED)
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    await connect(services, employee, ConnectionKind.PERSONAL)
    assert [c.kind for c in await services.calendar_connections.list(principal_for(admin))] == [
        ConnectionKind.COMPANY_SHARED
    ]
    assert [c.kind for c in await services.calendar_connections.list(principal_for(employee))] == [
        ConnectionKind.PERSONAL
    ]
    other_tenant = leave_world.b.employees["company_admin"]
    assert await services.calendar_connections.list(principal_for(other_tenant)) == []


async def test_revoke_deletes_tokens_and_orphans_events(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    admin, employee, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    personal = await connect(services, employee, ConnectionKind.PERSONAL)
    await approve_annual(services, employee, manager)
    await run_sync_jobs(services)
    with pytest.raises(CalendarNotConnected):  # someone else's personal connection is invisible
        await services.calendar_connections.revoke(principal_for(admin), personal.id)
    await services.calendar_connections.revoke(principal_for(employee), personal.id)
    row = store.calendar_connections[personal.id]
    assert (
        row.status is ConnectionStatus.REVOKED
        and row.refresh_token_encrypted is None
        and row.refresh_token_dek is None
    )
    assert [e.status for e in store.calendar_events.values()] == [EventStatus.ORPHANED]
    assert len(store.mock_calendar) == 1  # the event stays in the owner's calendar
    assert any(a.action == "calendar.connection.revoked" for a in store.audit)
    with pytest.raises(CalendarNotConnected):
        await services.calendar_connections.revoke(principal_for(employee), personal.id)
    # a new connection after revocation is a new row; new approvals sync again
    again = await connect(services, employee, ConnectionKind.PERSONAL)
    assert again.id != personal.id
