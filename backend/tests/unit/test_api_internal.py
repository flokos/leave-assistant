"""Internal API (doc 06 §5): automation role only, outbox lease/ack, render and delivery, reminders, maintenance,
calendar triggers, provider health, error sink; `GET /platform/health` for platform administrators."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient

from leave_assistant.application.chat.models import ProviderEventRecord
from leave_assistant.application.container import Services
from leave_assistant.domain.calendar.model import ConnectionKind
from tests.conftest import TokenFactory, bearer, principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock
from tests.fakes.world import World
from tests.unit.calendar.helpers import approve_annual, connect

INTERNAL_GETS = (
    "/api/v1/internal/events",
    "/api/v1/internal/index/status",
    "/api/v1/internal/retention/pending",
    "/api/v1/internal/documents/pending-review",
    "/api/v1/internal/health/providers",
)


@pytest.mark.parametrize("who", ["employee", "company_admin", "platform"])
async def test_only_the_automation_role_reaches_internal_routes(
    api: AsyncClient, leave_world: World, tokens: TokenFactory, store: MemoryStore, who: str
) -> None:
    token = (
        tokens.platform_admin() if who == "platform" else tokens.for_employee(leave_world.a.employees[who])
    )
    for path in INTERNAL_GETS:
        response = await api.get(path, headers=bearer(token))
        assert response.status_code == 403, path
    response = await api.post("/api/v1/internal/maintenance/run", json={}, headers=bearer(token))
    assert response.status_code == 403
    assert any(a.action == "authz.denied" for a in store.audit)
    assert (await api.get("/api/v1/internal/events")).status_code == 401


async def test_automation_cannot_use_user_routes(api: AsyncClient, tokens: TokenFactory) -> None:
    for path in ("/api/v1/calendar/connections", "/api/v1/leave/requests", "/api/v1/platform/health"):
        assert (await api.get(path, headers=bearer(tokens.automation()))).status_code == 403, path


async def test_outbox_lease_ack_and_redelivery(
    api: AsyncClient,
    services: Services,
    leave_world: World,
    tokens: TokenFactory,
    store: MemoryStore,
    clock: FixedClock,
) -> None:
    requester, manager = leave_world.a.employees["employee"], leave_world.a.employees["manager"]
    await approve_annual(services, requester, manager)
    other = leave_world.b.employees["employee"]
    await approve_annual(services, other, leave_world.b.employees["manager"])
    auth = bearer(tokens.automation())
    first = (await api.get("/api/v1/internal/events?limit=200", headers=auth)).json()["items"]
    tenants = {e["tenant_id"] for e in first}
    assert len(tenants) == 2 and all(e["type"] == "notification.created" for e in first)
    assert [e["id"] for e in first] == sorted(e["id"] for e in first)  # UUIDv7 / id order
    assert all(e["attempts"] == 1 for e in first)
    # leased: a second poll returns nothing until the lease runs out
    assert (await api.get("/api/v1/internal/events", headers=auth)).json()["items"] == []
    acked = [e["id"] for e in first[:2]]
    assert (await api.post("/api/v1/internal/events/ack", json={"ids": acked}, headers=auth)).json() == {
        "count": 2
    }
    clock.tick(301)
    again = (await api.get("/api/v1/internal/events?limit=200", headers=auth)).json()["items"]
    assert {e["id"] for e in again} == {e["id"] for e in first[2:]} and all(e["attempts"] == 2 for e in again)
    # past ten attempts an event is dead and no longer offered
    for _ in range(9):
        clock.tick(301)
        await api.get("/api/v1/internal/events?limit=200", headers=auth)
    clock.tick(301)
    assert (await api.get("/api/v1/internal/events?limit=200", headers=auth)).json()["items"] == []
    assert sum(e.dead for e in store.integration_events) == len(first) - 2
    health = (await api.get("/api/v1/platform/health", headers=bearer(tokens.platform_admin()))).json()
    assert sum(t["events_dead"] for t in health["tenants"]) == len(first) - 2
    assert "payload" not in str(health)


async def test_render_and_delivery_are_idempotent(
    api: AsyncClient, services: Services, leave_world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    requester, manager = leave_world.a.employees["employee"], leave_world.a.employees["manager"]
    await approve_annual(services, requester, manager)
    note = next(n for n in store.notifications.values() if n.type == "approval.pending")
    auth = bearer(tokens.automation())
    tenant = str(requester.tenant_id)
    url = f"/api/v1/internal/notifications/{note.id}/render?tenant_id={tenant}"
    body = (await api.get(url, headers=auth)).json()
    tenant_row = store.tenants[requester.tenant_id]
    assert body["send"] is True and body["to"] == manager.email
    assert body["from_address"] == f"no-reply@{tenant_row.slug}.leave.local"
    assert "LR-2026-000001" in body["subject"] and "LR-2026-000001" in body["text"]
    assert "http://localhost:8080/approvals" in body["text"] and "<p>" in body["html"]
    delivery = f"/api/v1/internal/notifications/{note.id}/delivery"
    sent = await api.post(delivery, json={"tenant_id": tenant, "status": "sent"}, headers=auth)
    assert sent.json() == {"status": "sent", "attempts": 1}
    again = await api.post(delivery, json={"tenant_id": tenant, "status": "failed"}, headers=auth)
    assert again.json() == {"status": "sent", "attempts": 1}  # once sent, nothing changes
    assert (await api.get(url, headers=auth)).json()["send"] is False  # W4 skips duplicates
    wrong_tenant = f"/api/v1/internal/notifications/{note.id}/render?tenant_id={leave_world.b.tenant.id}"
    assert (
        await api.get(wrong_tenant, headers=auth)
    ).status_code == 404  # RLS: another tenant's id is unknown


async def test_reminders_pick_their_own_recipients(
    api: AsyncClient, services: Services, leave_world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    auth = bearer(tokens.automation())
    tenant = str(leave_world.a.tenant.id)
    nothing = await api.post(
        "/api/v1/internal/notifications",
        json={"tenant_id": tenant, "type": "retention.review_reminder"},
        headers=auth,
    )
    assert nothing.json() == {"created": 0, "reason": "nothing to remind"}
    bad = await api.post(
        "/api/v1/internal/notifications", json={"tenant_id": tenant, "type": "request.approved"}, headers=auth
    )
    assert bad.status_code == 422  # only reminder types; business notifications come from the backend


async def test_calendar_and_maintenance_triggers(
    api: AsyncClient, services: Services, leave_world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    admin, requester, manager = (leave_world.a.employees[k] for k in ("company_admin", "employee", "manager"))
    await connect(services, admin, ConnectionKind.COMPANY_SHARED)
    await approve_annual(services, requester, manager)
    auth = bearer(tokens.automation())
    assert (await api.post("/api/v1/internal/calendar/retry-failed", headers=auth)).json() == {"requeued": 1}
    assert len([j for j in store.jobs.values() if j.type == "calendar.sync"]) == 1  # deduplicated
    summary = (await api.post("/api/v1/internal/calendar/reconcile", headers=auth)).json()
    assert summary == {"tenants": 2, "findings": {"missing": 1}}
    result = (await api.post("/api/v1/internal/maintenance/run", json={}, headers=auth)).json()
    assert set(result) == {
        "expired_proposals",
        "conversation_cleanup",
        "events_archive",
        "jobs_archive",
        "oauth_states",
    }
    unknown = await api.post(
        "/api/v1/internal/maintenance/run", json={"tasks": ["drop_tables"]}, headers=auth
    )
    assert unknown.status_code == 422
    scan = (await api.post("/api/v1/internal/retention/scan", headers=auth)).json()
    assert scan == {"enqueued": 2}
    status = (await api.get("/api/v1/internal/index/status", headers=auth)).json()
    assert {i["tenant_id"] for i in status["items"]} == {
        str(leave_world.a.tenant.id),
        str(leave_world.b.tenant.id),
    }


async def test_provider_health_alerts_on_failovers(
    api: AsyncClient, leave_world: World, tokens: TokenFactory, store: MemoryStore, clock: FixedClock
) -> None:
    auth = bearer(tokens.automation())
    assert (await api.get("/api/v1/internal/health/providers", headers=auth)).json()["healthy"] is True
    for i in range(3):
        store.provider_events.append(
            ProviderEventRecord(
                id=UUID(int=900 + i),
                tenant_id=leave_world.a.tenant.id,
                provider="gemini",
                model="gemini-3.6-flash",
                event="failover",
                reason="rate_limited",
                occurred_at=clock.now(),
            )
        )
    report = (await api.get("/api/v1/internal/health/providers", headers=auth)).json()
    assert report["healthy"] is False and report["alert"]["to"] == "platform-alerts@leave.local"
    assert "gemini: 3 failover(s)" in report["alert"]["text"]
    clock.tick(3601)
    assert (await api.get("/api/v1/internal/health/providers", headers=auth)).json()["healthy"] is True


async def test_error_sink_is_audited_without_tenant(
    api: AsyncClient, tokens: TokenFactory, store: MemoryStore
) -> None:
    response = await api.post(
        "/api/v1/internal/automation/errors",
        json={"workflow": "W4", "node": "SMTP", "message": "connection refused", "execution_id": "42"},
        headers=bearer(tokens.automation()),
    )
    assert response.status_code == 204
    entry = next(a for a in store.audit if a.action == "automation.workflow.failed")
    assert entry.tenant_id is None and entry.details["node"] == "SMTP"


async def test_processing_status_and_watchdog(
    api: AsyncClient,
    services: Services,
    leave_world: World,
    tokens: TokenFactory,
    store: MemoryStore,
    clock: FixedClock,
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    from leave_assistant.application.documents.commands import CreateDocument
    from leave_assistant.domain.documents.types import Category
    from tests.unit.documents.conftest import make_upload

    hr, admin = leave_world.a.employees["hr_admin"], leave_world.a.employees["company_admin"]
    created = await services.documents.create(
        principal_for(hr),
        CreateDocument("Leave policy", Category.POLICY_GENERAL, make_upload(tmp_path, "p.md", b"# Leave\n")),
    )
    version = created.latest_version
    auth = bearer(tokens.automation())
    tenant = leave_world.a.tenant.id
    url = f"/api/v1/internal/documents/{version.id}/processing?tenant_id={tenant}"
    status = (await api.get(url, headers=auth)).json()
    assert status["processing"] is True and status["stalled"] is False and "title" not in status
    assert (await api.get("/api/v1/internal/documents/stalled", headers=auth)).json()["items"] == []
    clock.tick(31 * 60)
    assert (await api.get(url, headers=auth)).json()["stalled"] is True
    swept = (await api.get("/api/v1/internal/documents/stalled", headers=auth)).json()["items"]
    assert swept == [{"tenant_id": str(tenant), "version_id": str(version.id)}]
    body = {"tenant_id": str(tenant), "type": "document.processing_stalled", "subject_id": str(version.id)}
    assert (await api.post("/api/v1/internal/notifications", json=body, headers=auth)).json() == {
        "created": 2
    }
    stalled = [n for n in store.notifications.values() if n.type == "document.processing_stalled"]
    assert {n.recipient_employee_id for n in stalled} == {hr.id, admin.id}
    assert stalled[0].params["title"] == "Leave policy"
    # a second trigger on the same version is deduplicated
    await api.post("/api/v1/internal/notifications", json=body, headers=auth)
    assert len([n for n in store.notifications.values() if n.type == "document.processing_stalled"]) == 2
    missing = f"/api/v1/internal/documents/{UUID(int=1)}/processing?tenant_id={tenant}"
    assert (await api.get(missing, headers=auth)).status_code == 404
