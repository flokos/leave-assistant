"""M5 endpoints through HTTP: supporting documents, retention, security policy, platform defaults, review findings."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from httpx import AsyncClient

from leave_assistant.application.container import Services
from leave_assistant.application.leave.commands import SubmitRequest
from tests.conftest import TokenFactory, bearer, principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World
from tests.unit.documents.conftest import create_markdown
from tests.unit.documents.test_ingestion_security import ADVERSARIAL_EN

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


async def test_supporting_document_flow_over_http(
    api: AsyncClient, leave_world: World, tokens: TokenFactory, services: Services, store: MemoryStore
) -> None:
    world = leave_world
    employee_principal = principal_for(world.a.employees["employee"])
    request = (
        await services.leave_requests.submit(
            employee_principal, SubmitRequest("sick", date(2026, 11, 9), date(2026, 11, 10), reason="flu")
        )
    ).request
    employee = bearer(tokens.for_employee(world.a.employees["employee"]))
    hr = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    manager = bearer(tokens.for_employee(world.a.employees["manager"]))
    base = f"/api/v1/leave/requests/{request.id}/supporting-documents"
    missing = await api.post(base, files={"file": ("note.png", PNG, "image/png")}, headers=employee)
    assert missing.status_code == 422
    created = await api.post(
        base, files={"file": ("note.png", PNG, "image/png")}, headers={**employee, **key()}
    )
    assert created.status_code == 201, created.text
    doc = created.json()
    assert doc["mime"] == "image/png" and doc["status"] == "stored" and "object_key" not in doc
    bad = await api.post(
        base,
        files={"file": ("x.docx", b"PK\x03\x04" + b"\x00" * 10, "application/octet-stream")},
        headers={**employee, **key()},
    )
    assert bad.status_code == 415 and bad.json()["type"].endswith("supporting/unsupported-type")
    assert (await api.get(base, headers=manager)).status_code == 404
    assert [d["id"] for d in (await api.get(base, headers=hr)).json()] == [doc["id"]]
    detail = await api.get(f"/api/v1/supporting-documents/{doc['id']}", headers=hr)
    assert detail.status_code == 200 and [e["action"] for e in detail.json()["access_events"]] == ["view"]
    assert (await api.get(f"/api/v1/supporting-documents/{doc['id']}", headers=manager)).status_code == 404
    no_reason = await api.post(
        f"/api/v1/supporting-documents/{doc['id']}/download-token", json={}, headers={**hr, **key()}
    )
    assert no_reason.status_code == 422 and no_reason.json()["errors"][0]["loc"] == ["body", "reason"]
    issued = await api.post(
        f"/api/v1/supporting-documents/{doc['id']}/download-token",
        json={"reason": "verifying"},
        headers={**hr, **key()},
    )
    assert issued.status_code == 200 and issued.json()["url"].endswith(issued.json()["token"])
    download = await api.get(issued.json()["url"])  # no bearer: the token is the credential
    assert (
        download.status_code == 200
        and download.content == PNG
        and download.headers["content-type"] == "image/png"
    )
    assert download.headers["cache-control"] == "no-store"
    assert (await api.get(issued.json()["url"])).status_code == 404  # single use
    request_view = await api.get(f"/api/v1/leave/requests/{request.id}", headers=hr)
    assert request_view.json()["supporting_documents"] == 1
    assert (await api.get(f"/api/v1/leave/requests/{request.id}", headers=manager)).json()[
        "supporting_documents"
    ] == 0
    inbox = await api.get("/api/v1/approvals/pending", headers=hr)
    assert inbox.status_code == 200
    forbidden = await api.request(
        "DELETE",
        f"/api/v1/supporting-documents/{doc['id']}",
        json={"reason": "x"},
        headers={**employee, **key()},
    )
    assert forbidden.status_code == 403
    deleted = await api.request(
        "DELETE",
        f"/api/v1/supporting-documents/{doc['id']}",
        json={"reason": "duplicate"},
        headers={**hr, **key()},
    )
    assert deleted.status_code == 200 and deleted.json()["status"] == "deleted"
    actions = [a.action for a in store.audit if a.action.startswith("supporting.")]
    assert actions[0] == "supporting.uploaded" and actions[-1] == "supporting.delete"


async def test_retention_endpoints(api: AsyncClient, leave_world: World, tokens: TokenFactory) -> None:
    world = leave_world
    admin = bearer(tokens.for_employee(world.a.employees["company_admin"]))
    hr = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    manager = bearer(tokens.for_employee(world.a.employees["manager"]))
    body = {"document_class": "supporting_document", "retention_days": 365, "action": "delete_after_review"}
    created = await api.post("/api/v1/admin/retention-policies", json=body, headers={**admin, **key()})
    assert created.status_code == 201 and created.json()["document_class"] == "supporting_document"
    never = await api.post(
        "/api/v1/admin/retention-policies",
        json={"document_class": "leave_record", "retention_days": 10},
        headers={**admin, **key()},
    )
    assert never.status_code == 422
    assert (
        await api.post("/api/v1/admin/retention-policies", json=body, headers={**hr, **key()})
    ).status_code == 403
    listed = await api.get("/api/v1/admin/retention-policies", headers=hr)
    assert listed.status_code == 200 and len(listed.json()) == 1
    patched = await api.patch(
        f"/api/v1/admin/retention-policies/{created.json()['id']}",
        json={"retention_days": 400},
        headers={**admin, **key()},
    )
    assert patched.status_code == 200 and patched.json()["retention_days"] == 400
    items = await api.get("/api/v1/admin/retention/review-items", headers=hr)
    assert items.status_code == 200 and items.json() == []
    assert (await api.get("/api/v1/admin/retention/review-items", headers=manager)).status_code == 403
    unknown = await api.post(
        f"/api/v1/admin/retention/review-items/{uuid4()}/decide",
        json={"decision": "retained"},
        headers={**hr, **key()},
    )
    assert unknown.status_code == 404
    removed = await api.delete(
        f"/api/v1/admin/retention-policies/{created.json()['id']}", headers={**admin, **key()}
    )
    assert (
        removed.status_code == 204
        and (await api.get("/api/v1/admin/retention-policies", headers=admin)).json() == []
    )


async def test_security_policy_and_platform_defaults(
    api: AsyncClient, world: World, tokens: TokenFactory
) -> None:
    admin = bearer(tokens.for_employee(world.a.employees["company_admin"]))
    hr = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    platform = bearer(tokens.platform_admin())
    current = await api.get("/api/v1/admin/security-policy", headers=admin)
    assert current.status_code == 200 and current.json()["risk_low_max"] == 0.3
    assert (await api.get("/api/v1/admin/security-policy", headers=hr)).status_code == 403
    updated = await api.put(
        "/api/v1/admin/security-policy", json={"risk_low_max": 0.2, "risk_medium_max": 0.5}, headers=admin
    )
    assert updated.status_code == 200 and (
        updated.json()["risk_low_max"],
        updated.json()["risk_medium_max"],
    ) == (0.2, 0.5)
    invalid = await api.put("/api/v1/admin/security-policy", json={"risk_low_max": 0.6}, headers=admin)
    assert invalid.status_code == 422 and invalid.json()["errors"][0]["loc"] == ["body", "risk_medium_max"]
    defaults = await api.get("/api/v1/platform/security-defaults", headers=platform)
    assert defaults.status_code == 200 and defaults.json()["threshold_max"] == 0.95
    assert (await api.get("/api/v1/platform/security-defaults", headers=admin)).status_code == 403
    changed = await api.put(
        "/api/v1/platform/security-defaults", json={"max_upload_mb_limit": 80}, headers=platform
    )
    assert changed.status_code == 200 and changed.json()["max_upload_mb_limit"] == 80
    bad = await api.put("/api/v1/platform/security-defaults", json={"threshold_min": 0.9}, headers=platform)
    assert bad.status_code == 422


async def test_review_queue_exposes_findings_with_pseudonymised_excerpts(
    api: AsyncClient,
    world: World,
    tokens: TokenFactory,
    services: Services,
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    created = await create_markdown(services, hr, tmp_path, title="Appendix", content=ADVERSARIAL_EN)
    assert created.latest_version is not None
    await services.ingestion.process(world.a.tenant.id, created.latest_version.id)
    admin = bearer(tokens.for_employee(world.a.employees["company_admin"]))
    queue = await api.get("/api/v1/documents/review-queue", headers=admin)
    assert queue.status_code == 200 and len(queue.json()) == 1
    item = queue.json()[0]
    assert item["version"]["status"] == "QUARANTINED" and item["version"]["risk_tier"] == "high"
    assert {f["family"] for f in item["findings"]} >= {"override", "similarity"}
    assert all(set(f) == {"detector", "family", "score", "chunk", "excerpt"} for f in item["findings"])
    assert item["pii_summary"]["engine"] == "presidio+known_entities"
    assert item["version"]["findings_count"] == len(item["findings"])


async def test_null_fields_are_ignored_and_tokens_are_not_kept_by_idempotency(
    api: AsyncClient,
    leave_world: World,
    tokens: TokenFactory,
    services: Services,
    store: MemoryStore,
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    admin = bearer(tokens.for_employee(leave_world.a.employees["company_admin"]))
    updated = await api.put(
        "/api/v1/admin/security-policy", json={"risk_low_max": None, "risk_medium_max": 0.6}, headers=admin
    )
    assert updated.status_code == 200 and updated.json()["risk_low_max"] == 0.3
    employee = principal_for(leave_world.a.employees["employee"])
    request = (
        await services.leave_requests.submit(
            employee, SubmitRequest("sick", date(2026, 11, 9), date(2026, 11, 10))
        )
    ).request
    auth = bearer(tokens.for_employee(leave_world.a.employees["employee"]))
    doc = (
        await api.post(
            f"/api/v1/leave/requests/{request.id}/supporting-documents",
            files={"file": ("n.png", PNG, "image/png")},
            headers={**auth, **key()},
        )
    ).json()
    issued = await api.post(f"/api/v1/supporting-documents/{doc['id']}/download-token", json={}, headers=auth)
    assert issued.status_code == 200
    assert issued.json()["token"] not in str(store.idempotency)
