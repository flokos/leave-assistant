"""DocumentService: permissions per role, validation, versions, visibility, lifecycle actions, review, download."""

from __future__ import annotations

from pathlib import Path

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.documents.commands import CreateDocument, ReviewDecision, UpdateDocument
from leave_assistant.application.errors import AuthorizationError, ConflictError, NotFoundError
from leave_assistant.application.leave.errors import VersionConflict
from leave_assistant.application.ports import DocumentFilter
from leave_assistant.domain.documents.formats import TooLarge, UnsupportedType
from leave_assistant.domain.documents.lifecycle import InvalidTransition
from leave_assistant.domain.documents.types import AclPrincipalType, Category, ReviewAction, VersionStatus
from leave_assistant.domain.documents.visibility import AclNotAllowed
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World
from tests.unit.documents.conftest import (
    ENGLISH_HANDBOOK_MD,
    GREEK_POLICY_MD,
    HR_GUIDE_MD,
    create_markdown,
    make_upload,
)


async def test_hr_upload_creates_document_version_job_audit_and_outbox(
    services: Services, world: World, store: MemoryStore, uploads: Path
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    summary = await create_markdown(
        services, hr, uploads, title="Κανονισμός Αδειών", content=GREEK_POLICY_MD, name="kanonismos.md"
    )
    document, version = summary.document, summary.latest_version
    assert version is not None and version.status is VersionStatus.UPLOADED and version.version_no == 1
    assert version.mime == "text/markdown" and version.original_filename == "kanonismos.md"
    assert version.object_key == f"tenants/{document.tenant_id}/documents/{document.id}/1/original.md"
    assert document.owner_employee_id == hr.employee_id and document.category is Category.POLICY_GENERAL
    assert len(version.sha256) == 64
    assert services.adapters.storage.objects[version.object_key][0] == GREEK_POLICY_MD.encode()  # type: ignore[attr-defined]
    jobs = list(store.jobs.values())
    assert (
        len(jobs) == 1
        and jobs[0].type == "document.process"
        and jobs[0].payload["version_id"] == str(version.id)
    )
    assert jobs[0].dedupe_key == f"document.process:{version.id}" and jobs[0].tenant_id == document.tenant_id
    assert [a.action for a in store.audit][-1] == "document.uploaded"
    assert [e.type for e in store.integration_events] == ["document.uploaded"]


async def test_upload_permissions_per_role(services: Services, world: World, uploads: Path) -> None:
    a = world.a
    employee = principal_for(a.employees["employee"])
    manager = principal_for(a.employees["manager"])
    admin = principal_for(a.employees["company_admin"])
    hr = principal_for(a.employees["hr_admin"])
    with pytest.raises(AuthorizationError):
        await create_markdown(services, employee, uploads, title="x", content=GREEK_POLICY_MD)
    with pytest.raises(AuthorizationError):
        await create_markdown(
            services, admin, uploads, title="x", content=HR_GUIDE_MD, category=Category.POLICY_HR_RESTRICTED
        )
    with pytest.raises(AuthorizationError):
        await create_markdown(
            services,
            manager,
            uploads,
            title="x",
            content="notice",
            category=Category.DEPARTMENT_NOTICE,
            department_id=a.departments["FIN"].id,
        )
    notice = await create_markdown(
        services,
        manager,
        uploads,
        title="Freeze",
        content="# Freeze\n\nNo leave in week 52.",
        category=Category.DEPARTMENT_NOTICE,
        department_id=a.departments["OPS"].id,
    )
    assert notice.document.department_id == a.departments["OPS"].id
    internal = await create_markdown(
        services,
        admin,
        uploads,
        title="Config",
        content="# Config\n\nNotes.",
        category=Category.ADMIN_INTERNAL,
    )
    assert internal.document.department_id is None
    restricted = await create_markdown(
        services, hr, uploads, title="Guide", content=HR_GUIDE_MD, category=Category.POLICY_HR_RESTRICTED
    )
    assert restricted.document.category is Category.POLICY_HR_RESTRICTED


async def test_upload_validation(services: Services, world: World, uploads: Path) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    with pytest.raises(UnsupportedType):
        await services.documents.create(
            hr,
            CreateDocument(
                "Image", Category.POLICY_GENERAL, make_upload(uploads, "x.png", b"\x89PNG\r\n\x1a\n....")
            ),
        )
    with pytest.raises(UnsupportedType):
        await services.documents.create(
            hr,
            CreateDocument("Spoof", Category.POLICY_GENERAL, make_upload(uploads, "x.txt", b"%PDF-1.4 fake")),
        )
    with pytest.raises(TooLarge):  # test options: 1 MB limit
        await services.documents.create(
            hr,
            CreateDocument(
                "Big", Category.POLICY_GENERAL, make_upload(uploads, "big.txt", b"a" * (1024 * 1024 + 1))
            ),
        )
    with pytest.raises(Exception, match="title"):
        await services.documents.create(
            hr, CreateDocument("   ", Category.POLICY_GENERAL, make_upload(uploads, "x.txt", b"text"))
        )


async def test_visibility_in_list_get_and_versions(services: Services, world: World, uploads: Path) -> None:
    a = world.a
    hr = principal_for(a.employees["hr_admin"])
    employee = principal_for(a.employees["employee"])
    admin = principal_for(a.employees["company_admin"])
    general = await create_markdown(services, hr, uploads, title="Κανονισμός", content=GREEK_POLICY_MD)
    restricted = await create_markdown(
        services, hr, uploads, title="Οδηγός", content=HR_GUIDE_MD, category=Category.POLICY_HR_RESTRICTED
    )
    page = await services.documents.list(employee, filters=DocumentFilter(), limit=None, cursor=None)
    assert [s.document.id for s in page.items] == [general.document.id]
    assert [s.latest_version.version_no for s in page.items if s.latest_version] == [1]
    page = await services.documents.list(hr, filters=DocumentFilter(), limit=None, cursor=None)
    assert {s.document.id for s in page.items} == {general.document.id, restricted.document.id}
    page = await services.documents.list(
        admin, filters=DocumentFilter(category=Category.POLICY_HR_RESTRICTED), limit=None, cursor=None
    )
    assert [s.document.id for s in page.items] == [restricted.document.id]
    page = await services.documents.list(
        hr, filters=DocumentFilter(status=VersionStatus.UPLOADED, query="οδηγ"), limit=None, cursor=None
    )
    assert [s.document.id for s in page.items] == [restricted.document.id]
    detail = await services.documents.get(employee, general.document.id)
    assert detail.can_manage is False and detail.can_review is False and len(detail.versions) == 1
    with pytest.raises(NotFoundError):
        await services.documents.get(employee, restricted.document.id)
    with pytest.raises(NotFoundError):
        await services.documents.versions(employee, restricted.document.id)
    # the other tenant sees nothing at all
    stranger = principal_for(world.b.employees["hr_admin"])
    with pytest.raises(NotFoundError):
        await services.documents.get(stranger, general.document.id)
    platform = principal_for(a.employees["employee"]).__class__(
        user_id="p",
        username="p",
        roles=frozenset(
            {__import__("leave_assistant.domain.tenancy", fromlist=["Role"]).Role.PLATFORM_ADMIN}
        ),
    )
    with pytest.raises(AuthorizationError):
        await services.documents.list(platform, filters=DocumentFilter(), limit=None, cursor=None)


async def test_add_version_duplicates_numbering_and_permissions(
    services: Services, world: World, uploads: Path, store: MemoryStore
) -> None:
    a = world.a
    hr = principal_for(a.employees["hr_admin"])
    employee = principal_for(a.employees["employee"])
    created = await create_markdown(services, hr, uploads, title="Handbook", content=ENGLISH_HANDBOOK_MD)
    with pytest.raises(ConflictError) as info:
        await services.documents.add_version(
            hr, created.document.id, make_upload(uploads, "same.md", ENGLISH_HANDBOOK_MD.encode())
        )
    assert info.value.problem_type == "document/duplicate-content"
    v2 = await services.documents.add_version(
        hr, created.document.id, make_upload(uploads, "v2.md", (ENGLISH_HANDBOOK_MD + "\nChanged.").encode())
    )
    assert v2.version_no == 2 and v2.status is VersionStatus.UPLOADED
    assert len([j for j in store.jobs.values() if j.type == "document.process"]) == 2
    with pytest.raises(AuthorizationError):
        await services.documents.add_version(
            employee, created.document.id, make_upload(uploads, "v3.md", b"# x\n\nnope")
        )


async def test_update_delete_and_optimistic_locking(
    services: Services, world: World, uploads: Path, store: MemoryStore
) -> None:
    a = world.a
    hr = principal_for(a.employees["hr_admin"])
    employee = principal_for(a.employees["employee"])
    created = await create_markdown(
        services, hr, uploads, title="Guide", content=HR_GUIDE_MD, category=Category.POLICY_HR_RESTRICTED
    )
    doc_id = created.document.id
    with pytest.raises(AclNotAllowed):
        await services.documents.update(
            hr, doc_id, UpdateDocument(version=1, acl=[(AclPrincipalType.ROLE, "employee")])
        )
    updated = await services.documents.update(
        hr, doc_id, UpdateDocument(version=1, title="Guide v2", category=Category.POLICY_GENERAL)
    )
    assert (
        updated.version == 2 and updated.title == "Guide v2" and updated.category is Category.POLICY_GENERAL
    )
    with pytest.raises(VersionConflict):
        await services.documents.update(hr, doc_id, UpdateDocument(version=1, title="stale"))
    updated = await services.documents.update(
        hr,
        doc_id,
        UpdateDocument(version=2, acl=[(AclPrincipalType.DEPARTMENT, str(a.departments["OPS"].id))]),
    )
    assert len([e for e in store.document_acl.values() if e.document_id == doc_id]) == 1
    assert (
        await services.documents.get(employee, doc_id)
    ).document.title == "Guide v2"  # general now: visible
    with pytest.raises(AuthorizationError):  # HR cannot move a document into admin_internal
        await services.documents.update(
            hr, doc_id, UpdateDocument(version=3, category=Category.ADMIN_INTERNAL)
        )
    with pytest.raises(AuthorizationError):
        await services.documents.delete(employee, doc_id, version=3)
    await services.documents.delete(hr, doc_id, version=3)
    with pytest.raises(NotFoundError):
        await services.documents.get(hr, doc_id)
    assert not (await services.documents.list(hr, filters=DocumentFilter(), limit=None, cursor=None)).items
    assert [a.action for a in store.audit][-1] == "document.deleted"


async def test_retire_review_reprocess_and_download(
    services: Services, world: World, uploads: Path, store: MemoryStore
) -> None:
    a = world.a
    hr = principal_for(a.employees["hr_admin"])
    admin = principal_for(a.employees["company_admin"])
    employee = principal_for(a.employees["employee"])
    created = await create_markdown(services, hr, uploads, title="Κανονισμός", content=GREEK_POLICY_MD)
    doc_id, version_id = created.document.id, created.latest_version.id  # type: ignore[union-attr]
    with pytest.raises(InvalidTransition):  # UPLOADED cannot be retired
        await services.documents.retire(hr, doc_id, 1)
    # simulate the pipeline's routing decision: medium risk → pending review
    store.document_versions[version_id].status = VersionStatus.PENDING_REVIEW
    assert [i.version.id for i in await services.documents.review_queue(hr)] == [version_id]
    assert [i.version.id for i in await services.documents.review_queue(admin)] == [version_id]
    with pytest.raises(AuthorizationError):
        await services.documents.review(employee, doc_id, 1, ReviewDecision(ReviewAction.RELEASE))
    released = await services.documents.review(
        admin, doc_id, 1, ReviewDecision(ReviewAction.RELEASE, "looks fine")
    )
    assert released.status is VersionStatus.ACTIVE and released.retrievable
    assert store.documents[doc_id].active_version_id == version_id
    assert [e.action for e in store.review_events] == [ReviewAction.RELEASE]
    assert "document.released" in [x.action for x in store.audit]
    with pytest.raises(ConflictError):
        await services.documents.review(admin, doc_id, 1, ReviewDecision(ReviewAction.INSPECT))
    with pytest.raises(InvalidTransition):
        await services.documents.reprocess(hr, doc_id, 1)
    detail = await services.documents.version_detail(employee, doc_id, 1)
    assert detail.findings_visible is False and detail.chunk_count == 0
    document, version, stream = await services.documents.download(employee, doc_id, 1)
    assert document.id == doc_id and b"".join([chunk async for chunk in stream]) == GREEK_POLICY_MD.encode()
    assert [x.action for x in store.audit][-1] == "document.downloaded"
    retired = await services.documents.retire(hr, doc_id, 1)
    assert retired.status is VersionStatus.RETIRED and not retired.retrievable
    assert store.documents[doc_id].active_version_id is None
    store.document_versions[version_id].status = VersionStatus.FAILED
    job = await services.documents.reprocess(hr, doc_id, 1)
    assert (
        job.type == "document.process"
        and store.document_versions[version_id].status is VersionStatus.UPLOADED
    )
