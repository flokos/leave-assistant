"""Document and job endpoints through HTTP: multipart uploads, key-only idempotency, problem types, visibility."""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient

from leave_assistant.application.container import Services
from leave_assistant.application.jobs.models import JobStatus
from leave_assistant.domain.documents.types import VersionStatus
from tests.conftest import TokenFactory, bearer
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World
from tests.unit.documents.conftest import GREEK_POLICY_MD, HR_GUIDE_MD


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


def upload(name: str, content: bytes, mime: str) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, content, mime)}


async def test_upload_replay_and_problems(
    api: AsyncClient, world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    hr = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    employee = bearer(tokens.for_employee(world.a.employees["employee"]))
    data = {"title": "Κανονισμός Αδειών", "category": "policy_general"}
    missing = await api.post(
        "/api/v1/documents",
        data=data,
        files=upload("k.md", GREEK_POLICY_MD.encode(), "text/markdown"),
        headers=hr,
    )
    assert missing.status_code == 422 and missing.json()["errors"][0]["loc"] == ["header", "Idempotency-Key"]
    k = key()
    created = await api.post(
        "/api/v1/documents",
        data=data,
        files=upload("k.md", GREEK_POLICY_MD.encode(), "text/markdown"),
        headers={**hr, **k},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["category"] == "policy_general" and body["latest_version"]["status"] == "UPLOADED"
    assert body["latest_version"]["original_filename"] == "k.md" and body["active_version"] is None
    replay = await api.post(
        "/api/v1/documents",
        data=data,
        files=upload("k.md", GREEK_POLICY_MD.encode(), "text/markdown"),
        headers={**hr, **k},
    )
    assert (
        replay.status_code == 201
        and replay.headers.get("Idempotent-Replayed") == "true"
        and replay.json()["id"] == body["id"]
    )
    assert len(store.documents) == 1
    forbidden = await api.post(
        "/api/v1/documents",
        data=data,
        files=upload("k.md", b"# x\n\ntext", "text/markdown"),
        headers={**employee, **key()},
    )
    assert forbidden.status_code == 403 and forbidden.json()["type"].endswith("authorization-denied")
    png = await api.post(
        "/api/v1/documents",
        data=data,
        files=upload("x.png", b"\x89PNG\r\n\x1a\n....", "image/png"),
        headers={**hr, **key()},
    )
    assert png.status_code == 415 and png.json()["type"].endswith("document/unsupported-type")
    big = await api.post(
        "/api/v1/documents",
        data=data,
        files=upload("big.txt", b"a" * (1024 * 1024 + 10), "text/plain"),
        headers={**hr, **key()},
    )
    assert big.status_code == 413 and big.json()["type"].endswith("document/too-large")
    bad_category = await api.post(
        "/api/v1/documents",
        data={**data, "category": "supporting_document"},
        files=upload("k.md", b"# x\n\ntext", "text/markdown"),
        headers={**hr, **key()},
    )
    assert bad_category.status_code == 422


async def test_library_detail_versions_download_update_delete(
    api: AsyncClient, world: World, tokens: TokenFactory, services: Services
) -> None:
    hr = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    employee = bearer(tokens.for_employee(world.a.employees["employee"]))
    platform = bearer(tokens.platform_admin())
    general = (
        await api.post(
            "/api/v1/documents",
            data={"title": "Κανονισμός", "category": "policy_general"},
            files=upload("k.md", GREEK_POLICY_MD.encode(), "text/markdown"),
            headers={**hr, **key()},
        )
    ).json()
    restricted = (
        await api.post(
            "/api/v1/documents",
            data={"title": "Guide", "category": "policy_hr_restricted"},
            files=upload("g.md", HR_GUIDE_MD.encode(), "text/markdown"),
            headers={**hr, **key()},
        )
    ).json()
    listing = await api.get("/api/v1/documents", headers=employee)
    assert listing.status_code == 200 and [d["id"] for d in listing.json()["items"]] == [general["id"]]
    listing = await api.get("/api/v1/documents", params={"category": "policy_hr_restricted"}, headers=hr)
    assert [d["id"] for d in listing.json()["items"]] == [restricted["id"]]
    assert (await api.get("/api/v1/documents", headers=platform)).status_code == 403
    assert (await api.get(f"/api/v1/documents/{restricted['id']}", headers=employee)).status_code == 404
    detail = await api.get(f"/api/v1/documents/{general['id']}", headers=hr)
    assert (
        detail.status_code == 200
        and detail.json()["can_manage"] is True
        and len(detail.json()["versions"]) == 1
    )
    versions = await api.get(f"/api/v1/documents/{general['id']}/versions", headers=employee)
    assert versions.status_code == 200 and versions.json()[0]["version_no"] == 1
    version_detail = await api.get(f"/api/v1/documents/{general['id']}/versions/1", headers=employee)
    assert (
        version_detail.status_code == 200
        and version_detail.json()["risk_findings"] is None
        and version_detail.json()["chunk_count"] == 0
    )
    download = await api.get(f"/api/v1/documents/{general['id']}/versions/1/download", headers=employee)
    assert download.status_code == 200 and download.content == GREEK_POLICY_MD.encode()
    assert (
        download.headers["content-type"].startswith("text/markdown")
        and "k.md" in download.headers["content-disposition"]
    )
    assert (
        await api.get(f"/api/v1/documents/{general['id']}/versions/9", headers=employee)
    ).status_code == 404
    v2 = await api.post(
        f"/api/v1/documents/{general['id']}/versions",
        files=upload("k2.md", (GREEK_POLICY_MD + "\nΤροποποίηση.").encode(), "text/markdown"),
        headers={**hr, **key()},
    )
    assert v2.status_code == 201 and v2.json()["version_no"] == 2
    duplicate = await api.post(
        f"/api/v1/documents/{general['id']}/versions",
        files=upload("k2.md", (GREEK_POLICY_MD + "\nΤροποποίηση.").encode(), "text/markdown"),
        headers={**hr, **key()},
    )
    assert duplicate.status_code == 409 and duplicate.json()["type"].endswith("document/duplicate-content")
    patched = await api.patch(
        f"/api/v1/documents/{general['id']}",
        json={
            "version": 1,
            "title": "Κανονισμός v2",
            "acl": [{"principal_type": "role", "principal_id": "manager"}],
        },
        headers={**hr, **key()},
    )
    assert (
        patched.status_code == 200
        and patched.json()["title"] == "Κανονισμός v2"
        and patched.json()["version"] == 2
    )
    assert [a["principal_id"] for a in patched.json()["acl"]] == ["manager"]
    stale = await api.patch(
        f"/api/v1/documents/{general['id']}", json={"version": 1, "title": "x"}, headers={**hr, **key()}
    )
    assert stale.status_code == 409 and stale.json()["type"].endswith("version-conflict")
    acl = await api.patch(
        f"/api/v1/documents/{restricted['id']}",
        json={"version": 1, "acl": [{"principal_type": "role", "principal_id": "employee"}]},
        headers={**hr, **key()},
    )
    assert acl.status_code == 422 and acl.json()["type"].endswith("document/acl-not-allowed")
    deleted = await api.delete(
        f"/api/v1/documents/{general['id']}", params={"version": 2}, headers={**hr, **key()}
    )
    assert deleted.status_code == 204
    assert (await api.get(f"/api/v1/documents/{general['id']}", headers=hr)).status_code == 404


async def test_search_citations_retire_review_reprocess_and_jobs(
    api: AsyncClient, world: World, tokens: TokenFactory, services: Services, store: MemoryStore
) -> None:
    hr = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    admin = bearer(tokens.for_employee(world.a.employees["company_admin"]))
    employee = bearer(tokens.for_employee(world.a.employees["employee"]))
    created = (
        await api.post(
            "/api/v1/documents",
            data={"title": "Κανονισμός Αδειών", "category": "policy_general"},
            files=upload("k.md", GREEK_POLICY_MD.encode(), "text/markdown"),
            headers={**hr, **key()},
        )
    ).json()
    document_id, version_id = UUID(created["id"]), UUID(created["latest_version"]["id"])
    assert (
        await api.get("/api/v1/documents/search", params={"q": "μεταφορά υπολοίπου"}, headers=employee)
    ).json()["hits"] == []
    await services.ingestion.process(world.a.tenant.id, version_id)
    search = await api.get(
        "/api/v1/documents/search", params={"q": "μεταφορά υπολοίπου", "k": 3}, headers=employee
    )
    assert search.status_code == 200 and search.json()["language"] == "el" and search.json()["hits"]
    hit = search.json()["hits"][0]
    assert (
        hit["title"] == "Κανονισμός Αδειών"
        and "3.2 Μεταφορά υπολοίπου" in hit["heading_path"]
        and hit["snippet"]
    )
    assert (await api.get("/api/v1/documents/search", params={"q": ""}, headers=employee)).status_code == 422
    citation = await api.get(f"/api/v1/documents/citations/{hit['chunk_id']}", headers=employee)
    assert citation.status_code == 200 and "μεταφέρονται" in citation.json()["content"]
    assert (await api.get(f"/api/v1/documents/citations/{uuid4()}", headers=employee)).status_code == 404
    status_filter = await api.get("/api/v1/documents", params={"status": "ACTIVE"}, headers=employee)
    assert [d["id"] for d in status_filter.json()["items"]] == [created["id"]]
    assert (await api.get("/api/v1/documents/review-queue", headers=hr)).json() == []
    inspect = await api.post(
        f"/api/v1/documents/{document_id}/versions/1/review",
        json={"action": "inspect"},
        headers={**admin, **key()},
    )
    assert inspect.status_code == 409 and inspect.json()["type"].endswith("document/invalid-state")
    assert (
        await api.post(
            f"/api/v1/documents/{document_id}/versions/1/review",
            json={"action": "release"},
            headers={**employee, **key()},
        )
    ).status_code == 403
    retired = await api.post(f"/api/v1/documents/{document_id}/versions/1/retire", headers={**hr, **key()})
    assert retired.status_code == 200 and retired.json()["status"] == "RETIRED"
    gone = await api.get(f"/api/v1/documents/citations/{hit['chunk_id']}", headers=employee)
    assert gone.status_code == 409 and gone.json()["type"].endswith("document/not-retrievable")
    assert (
        await api.get("/api/v1/documents/search", params={"q": "μεταφορά υπολοίπου"}, headers=employee)
    ).json()["hits"] == []
    again = await api.post(f"/api/v1/documents/{document_id}/versions/1/retire", headers={**hr, **key()})
    assert again.status_code == 409 and again.json()["type"].endswith("document/invalid-state")
    store.document_versions[version_id].status = VersionStatus.FAILED
    reprocess = await api.post(
        f"/api/v1/documents/{document_id}/versions/1/reprocess", headers={**hr, **key()}
    )
    assert (
        reprocess.status_code == 202
        and reprocess.json()["type"] == "document.process"
        and reprocess.json()["status"] == "queued"
    )
    # jobs administration
    jobs = await api.get("/api/v1/admin/jobs", headers=hr)
    assert jobs.status_code == 200 and {j["type"] for j in jobs.json()} == {"document.process"}
    assert (await api.get("/api/v1/admin/jobs", headers=employee)).status_code == 403
    job_id = jobs.json()[0]["id"]
    not_failed = await api.post(f"/api/v1/admin/jobs/{job_id}/retry", headers={**admin, **key()})
    assert not_failed.status_code == 409 and not_failed.json()["type"].endswith("job/not-failed")
    store.jobs[UUID(job_id)].status = JobStatus.FAILED
    retried = await api.post(f"/api/v1/admin/jobs/{job_id}/retry", headers={**admin, **key()})
    assert (
        retried.status_code == 200
        and retried.json()["status"] == "queued"
        and retried.json()["attempts"] == 0
    )
    assert (await api.post(f"/api/v1/admin/jobs/{job_id}/retry", headers={**hr, **key()})).status_code == 403
