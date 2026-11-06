"""Calendar HTTP API (doc 09 §2.7): mock consent round trip, connection list, sync history, retry, reconcile."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient

from leave_assistant.application.container import Services
from leave_assistant.infrastructure.calendar.mock import MockCalendarProvider
from tests.conftest import TokenFactory, bearer
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World
from tests.unit.calendar.helpers import approve_annual, run_sync_jobs


async def _connect(api: AsyncClient, token: str, kind: str) -> str:
    start = await api.post(
        "/api/v1/calendar/connections/google/start",
        json={"kind": kind},
        headers=bearer(token, **{"Idempotency-Key": f"start-{kind}"}),
    )
    assert start.status_code == 200, start.text
    url = start.json()["authorization_url"]
    assert url.startswith("http://localhost:8080/api/v1/calendar/connections/google/callback?")
    path = urlparse(url)
    callback = await api.get(f"{path.path}?{path.query}")  # the browser follows the redirect (no bearer)
    assert callback.status_code == 303
    return callback.headers["location"]


async def test_connect_list_sync_retry_and_reconcile(
    api: AsyncClient,
    services: Services,
    leave_world: World,
    tokens: TokenFactory,
    store: MemoryStore,
    calendar_mock: MockCalendarProvider,
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    admin_token = tokens.for_employee(admin)
    location = await _connect(api, admin_token, "company_shared")
    assert location == "http://localhost:8080/calendar?status=connected&kind=company_shared"
    listed = (await api.get("/api/v1/calendar/connections", headers=bearer(admin_token))).json()
    assert listed["provider"] == "mock" and [c["kind"] for c in listed["items"]] == ["company_shared"]
    assert "refresh_token" not in str(listed) and listed["items"][0]["status"] == "active"

    calendar_mock.fail_mode = "permanent"
    request_id = await approve_annual(services, requester, manager)
    await run_sync_jobs(services)
    jobs = (await api.get("/api/v1/calendar/sync-jobs?status=dead", headers=bearer(admin_token))).json()[
        "items"
    ]
    assert [(j["request_id"], j["request_number"], j["connection_kind"]) for j in jobs] == [
        (str(request_id), "LR-2026-000001", "company_shared")
    ]
    own = (await api.get("/api/v1/calendar/sync-jobs", headers=bearer(tokens.for_employee(requester)))).json()
    assert len(own["items"]) == 1
    calendar_mock.fail_mode = "none"
    retried = await api.post(
        f"/api/v1/calendar/sync-jobs/{jobs[0]['id']}/retry",
        headers=bearer(admin_token, **{"Idempotency-Key": "retry-1"}),
    )
    assert retried.status_code == 200 and retried.json()["status"] == "pending"
    denied = await api.post(
        f"/api/v1/calendar/sync-jobs/{jobs[0]['id']}/retry",
        headers=bearer(tokens.for_employee(requester), **{"Idempotency-Key": "retry-2"}),
    )
    assert denied.status_code == 403
    await run_sync_jobs(services)

    report = await api.post(
        "/api/v1/calendar/reconcile", headers=bearer(admin_token, **{"Idempotency-Key": "r1"})
    )
    assert report.status_code == 200 and report.json()["items"] == []
    latest = (await api.get("/api/v1/calendar/reconciliation", headers=bearer(admin_token))).json()
    assert latest["run_id"] is None  # a clean run stores no items


async def test_callback_errors_redirect_with_a_reason(api: AsyncClient) -> None:
    response = await api.get("/api/v1/calendar/connections/google/callback?state=forged&code=x")
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query == {"status": ["error"], "reason": ["state_invalid"]}


async def test_personal_connection_and_revoke(
    api: AsyncClient, leave_world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    employee = leave_world.a.employees["employee"]
    token = tokens.for_employee(employee)
    hr_token = tokens.for_employee(leave_world.a.employees["hr_admin"])
    forbidden = await api.post(
        "/api/v1/calendar/connections/google/start",
        json={"kind": "company_shared"},
        headers=bearer(hr_token, **{"Idempotency-Key": "s"}),
    )
    assert forbidden.status_code == 403
    await _connect(api, token, "personal")
    [connection] = (await api.get("/api/v1/calendar/connections", headers=bearer(token))).json()["items"]
    gone = await api.delete(
        f"/api/v1/calendar/connections/{connection['id']}",
        headers=bearer(hr_token, **{"Idempotency-Key": "d0"}),
    )
    assert gone.status_code == 404  # someone else's connection is indistinguishable from none
    deleted = await api.delete(
        f"/api/v1/calendar/connections/{connection['id']}", headers=bearer(token, **{"Idempotency-Key": "d1"})
    )
    assert deleted.status_code == 204
    assert (await api.get("/api/v1/calendar/connections", headers=bearer(token))).json()["items"] == []
