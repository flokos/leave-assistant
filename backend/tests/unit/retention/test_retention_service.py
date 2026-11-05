"""RetentionService: policies (C), scan → review items + HR notification, HR decisions, deletion job + tombstones."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import AuthorizationError, ConflictError, InvalidInputError
from leave_assistant.application.jobs.models import JobStatus
from leave_assistant.application.leave.commands import SubmitRequest
from leave_assistant.application.retention.service import (
    JOB_RETENTION_DELETE,
    CreateRetentionPolicy,
    UpdateRetentionPolicy,
)
from leave_assistant.domain.documents.types import VersionStatus
from leave_assistant.domain.retention.eligibility import DocumentClass, RetentionAction, ReviewStatus
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock
from tests.fakes.world import World
from tests.unit.documents.conftest import ENGLISH_HANDBOOK_MD, create_markdown, make_upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
DAY = 86400


async def test_policy_crud_rules_and_audit(services: Services, world: World, store: MemoryStore) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    hr = principal_for(world.a.employees["hr_admin"])
    policy = await services.retention.create_policy(
        admin,
        CreateRetentionPolicy(DocumentClass.SUPPORTING_DOCUMENT, 365, RetentionAction.DELETE_AFTER_REVIEW),
    )
    assert policy.retention_days == 365 and store.audit[-1].action == "admin.config.changed"
    with pytest.raises(ConflictError):
        await services.retention.create_policy(
            admin, CreateRetentionPolicy(DocumentClass.SUPPORTING_DOCUMENT, 10)
        )
    with pytest.raises(InvalidInputError):  # leave records are never deleted (brief §5.7)
        await services.retention.create_policy(admin, CreateRetentionPolicy(DocumentClass.LEAVE_RECORD, 10))
    with pytest.raises(InvalidInputError):
        await services.retention.create_policy(admin, CreateRetentionPolicy(DocumentClass.CONVERSATION, 4000))
    with pytest.raises(AuthorizationError):
        await services.retention.create_policy(hr, CreateRetentionPolicy(DocumentClass.CONVERSATION, 90))
    updated = await services.retention.update_policy(
        admin, policy.id, UpdateRetentionPolicy(retention_days=400, enabled=False)
    )
    assert updated.retention_days == 400 and not updated.enabled
    assert [p.id for p in await services.retention.list_policies(hr)] == [policy.id]  # HR may read
    await services.retention.delete_policy(admin, policy.id)
    assert await services.retention.list_policies(admin) == []


async def test_scan_creates_items_once_notifies_hr_and_decisions_drive_deletion(
    services: Services, leave_world: World, store: MemoryStore, clock: FixedClock, tmp_path: Path
) -> None:
    world = leave_world
    admin = principal_for(world.a.employees["company_admin"])
    hr = principal_for(world.a.employees["hr_admin"])
    manager = principal_for(world.a.employees["manager"])
    employee = principal_for(world.a.employees["employee"])
    await services.retention.create_policy(
        admin,
        CreateRetentionPolicy(DocumentClass.SUPPORTING_DOCUMENT, 365, RetentionAction.DELETE_AFTER_REVIEW),
    )
    await services.retention.create_policy(
        admin, CreateRetentionPolicy(DocumentClass.COMPANY_DOCUMENT, 730, RetentionAction.DELETE_AFTER_REVIEW)
    )
    request = (
        await services.leave_requests.submit(
            employee, SubmitRequest("sick", date(2026, 11, 9), date(2026, 11, 10), reason="flu")
        )
    ).request
    supporting = await services.supporting.upload(
        employee, request.id, make_upload(tmp_path, "note.png", PNG)
    )
    created = await create_markdown(services, hr, tmp_path, title="Handbook", content=ENGLISH_HANDBOOK_MD)
    v1 = created.latest_version
    assert v1 is not None
    await services.ingestion.process(world.a.tenant.id, v1.id)
    v2 = await services.documents.add_version(
        hr,
        created.document.id,
        make_upload(
            tmp_path, "v2.md", ENGLISH_HANDBOOK_MD.replace("twenty", "21").encode(), declared="text/markdown"
        ),
    )
    await services.ingestion.process(world.a.tenant.id, v2.id)
    assert store.document_versions[v1.id].status is VersionStatus.SUPERSEDED

    # nothing is eligible yet
    report = await services.retention.scan_tenant(world.a.tenant.id)
    assert (report.eligible, report.created) == (0, 0) and store.audit[-1].action == "retention.scan"
    clock.tick(372 * DAY)  # 2026-11-10 (leave end) + 365 d < 2027-11-12
    report = await services.retention.scan_tenant(world.a.tenant.id)
    assert (report.eligible, report.created) == (1, 1)  # the supporting document (365 d after the leave end)
    clock.tick(400 * DAY)
    report = await services.retention.scan_tenant(world.a.tenant.id)
    assert (report.eligible, report.created) == (
        2,
        1,
    )  # + the superseded version; the first item is not duplicated
    items = await services.retention.list_items(hr, status=ReviewStatus.PENDING)
    assert [(i.subject_type, i.subject_id) for i in items] == [
        ("supporting_document", supporting.id),
        ("document_version", v1.id),
    ]
    assert items[0].label.endswith("note.png") and items[1].label == "Handbook v1"
    found = [e for e in store.integration_events if e.type == "retention.items_found"]
    assert len(found) == 2 and [
        n.type for n in store.notifications.values() if n.type == "retention.items_found"
    ]
    with pytest.raises(AuthorizationError):
        await services.retention.list_items(manager, status=None)

    # decisions
    with pytest.raises(InvalidInputError) as invalid:
        await services.retention.decide(hr, items[1].id, ReviewStatus.DELETED, None)
    assert invalid.value.problem_type == "retention/invalid-decision"
    retained = await services.retention.decide(hr, items[1].id, ReviewStatus.RETAINED, "still referenced")
    assert retained.status is ReviewStatus.RETAINED and retained.reviewer_employee_id == hr.employee_id
    with pytest.raises(ConflictError):
        await services.retention.decide(hr, items[1].id, ReviewStatus.APPROVED_DELETE, None)
    approved = await services.retention.decide(hr, items[0].id, ReviewStatus.APPROVED_DELETE, "expired")
    assert approved.status is ReviewStatus.APPROVED_DELETE
    # approved but not yet executed: a scan must not open a second item for the same subject
    assert (await services.retention.scan_tenant(world.a.tenant.id)).created == 0
    job = next(j for j in store.jobs.values() if j.type == JOB_RETENTION_DELETE)
    assert job.status is JobStatus.QUEUED and job.payload["item_id"] == str(approved.id)
    assert [a.action for a in store.audit if a.action == "retention.decision"] and next(
        a for a in reversed(store.audit) if a.action == "retention.decision"
    ).details["decision"] == "approved_delete"

    # deletion job
    done = await services.retention.handle_delete_job(job)
    assert done.status is ReviewStatus.DELETED  # type: ignore[union-attr]
    tombstone = store.supporting_documents[supporting.id]
    assert (
        tombstone.status == "deleted"
        and tombstone.deleted_at is not None
        and tombstone.sha256 == supporting.sha256
    )
    assert supporting.object_key not in services.adapters.storage.objects  # type: ignore[attr-defined]
    deleted_audit = next(a for a in reversed(store.audit) if a.action == "retention.deleted")
    assert deleted_audit.details["objects"] == [supporting.object_key]
    # idempotent re-run
    assert (
        await services.retention.execute_deletion(world.a.tenant.id, approved.id)
    ).status is ReviewStatus.DELETED
    # a retained version is not raised again by the next daily scan, only after another retention period
    clock.tick(DAY)
    assert (await services.retention.scan_tenant(world.a.tenant.id)).created == 0
    assert await services.retention.list_items(hr, status=ReviewStatus.PENDING) == []
    clock.tick(731 * DAY)
    await services.retention.scan_tenant(world.a.tenant.id)
    again = [i for i in await services.retention.list_items(hr, status=ReviewStatus.PENDING)]
    assert [i.subject_id for i in again] == [v1.id]
    await services.retention.decide(hr, again[0].id, ReviewStatus.APPROVED_DELETE, None)
    await services.retention.execute_deletion(world.a.tenant.id, again[0].id)
    assert store.document_versions[v1.id].status is VersionStatus.DELETED
    assert not any(c.version_id == v1.id for c in store.chunks.values())
    assert store.document_versions[v1.id].object_key not in services.adapters.storage.objects  # type: ignore[attr-defined]
    # scan_all covers both tenants
    reports = await services.retention.scan_all()
    assert {r.tenant_id for r in reports} == {world.a.tenant.id, world.b.tenant.id}
