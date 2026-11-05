"""DocumentService (doc 09 §2.5): uploads and versions, library listing, lifecycle actions, review, downloads.

Every mutation is audited in the same transaction; uploads enqueue `document.process` (outbox pattern) and emit
`document.uploaded` for n8n. Invisible documents answer 404 (no existence leak); the storage key never reaches
the browser (ADR-0016).
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.documents.activation import activate_version
from leave_assistant.application.documents.commands import (
    CreateDocument,
    ReviewDecision,
    UpdateDocument,
    Upload,
)
from leave_assistant.application.documents.keys import original_key
from leave_assistant.application.documents.policy import (
    load_visible,
    reader_for,
    require_manage,
    require_review,
    require_tenant_user,
    require_upload,
)
from leave_assistant.application.documents.views import (
    DocumentDetail,
    DocumentSummary,
    ReviewItem,
    VersionDetail,
)
from leave_assistant.application.errors import ConflictError, InvalidInputError, NotFoundError
from leave_assistant.application.jobs.models import JOB_DOCUMENT_PROCESS, Job
from leave_assistant.application.jobs.service import JobService
from leave_assistant.application.leave.errors import VersionConflict
from leave_assistant.application.notifications.models import IntegrationEvent
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import (
    Clock,
    DocumentFilter,
    DocumentStorage,
    IdGenerator,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.application.tenancy.validation import validate_name
from leave_assistant.domain.documents.formats import (
    DOCX,
    HEAD_BYTES,
    FileFormat,
    UnsupportedType,
    check_size,
    sanitize_filename,
    sniff,
    verify_docx,
)
from leave_assistant.domain.documents.lifecycle import REVIEW_STATUSES, VersionEvent, transition
from leave_assistant.domain.documents.types import (
    AclEntry,
    Category,
    Document,
    DocumentVersion,
    ReviewAction,
    ReviewEvent,
    VersionStatus,
)
from leave_assistant.domain.documents.visibility import can_manage, can_review, validate_acl
from leave_assistant.domain.tenancy import Principal


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    max_upload_mb: int = 25
    max_pages: int = 500


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class DocumentService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: DocumentStorage,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        jobs: JobService,
        *,
        limits: DocumentLimits | None = None,
    ) -> None:
        self._uow = uow_factory
        self._storage = storage
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._jobs = jobs
        self._limits = limits or DocumentLimits()

    # --- uploads ---------------------------------------------------------------------------------------------
    async def create(self, principal: Principal, cmd: CreateDocument) -> DocumentSummary:
        tenant_id, employee_id = require_tenant_user(principal, action="document.upload")
        title = validate_name(cmd.title, "title", max_length=200)
        department_id = cmd.department_id if cmd.category is Category.DEPARTMENT_NOTICE else None
        require_upload(principal, cmd.category, department_id)
        fmt, digest = await self._validate_upload(cmd.upload)
        now = self._clock.now()
        document_id, version_id = self._ids.new_id(), self._ids.new_id()
        key = original_key(tenant_id, document_id, 1, fmt.extension)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await self._enforce_policy(uow, cmd.upload, fmt)
            if department_id is not None and await uow.departments.get(department_id) is None:
                raise InvalidInputError(
                    "unknown department",
                    errors=[{"loc": ["body", "department_id"], "msg": "unknown department"}],
                )
            await self._storage.put_file(key, cmd.upload.path, content_type=fmt.mime)
            document = Document(
                id=document_id,
                tenant_id=tenant_id,
                title=title,
                category=cmd.category,
                owner_employee_id=employee_id,
                created_at=now,
                updated_at=now,
                department_id=department_id,
                language_hint=cmd.language_hint,
            )
            version = self._new_version(
                version_id, document, 1, cmd.upload, fmt, digest, key, uploader=employee_id, now=now
            )
            await uow.documents.add(document)
            await uow.document_versions.add(version)
            await self._after_upload(uow, principal, document, version)
            await uow.commit()
        return DocumentSummary(document=document, active_version=None, latest_version=version)

    async def add_version(self, principal: Principal, document_id: UUID, upload: Upload) -> DocumentVersion:
        tenant_id, employee_id = require_tenant_user(principal, action="document.upload")
        fmt, digest = await self._validate_upload(upload)
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await self._enforce_policy(uow, upload, fmt)
            document, scope = await load_visible(uow, principal, document_id)
            require_manage(principal, document, scope)
            versions = await uow.document_versions.list_for_document(document.id)
            duplicate = next(
                (v for v in versions if v.sha256 == digest and v.status is not VersionStatus.DELETED), None
            )
            if duplicate is not None:
                raise ConflictError(
                    f"identical content was already uploaded as version {duplicate.version_no}",
                    problem_type="document/duplicate-content",
                )
            version_no = max((v.version_no for v in versions), default=0) + 1
            key = original_key(tenant_id, document.id, version_no, fmt.extension)
            await self._storage.put_file(key, upload.path, content_type=fmt.mime)
            version = self._new_version(
                self._ids.new_id(),
                document,
                version_no,
                upload,
                fmt,
                digest,
                key,
                uploader=employee_id,
                now=now,
            )
            await uow.document_versions.add(version)
            await self._after_upload(uow, principal, document, version)
            await uow.commit()
        return version

    @staticmethod
    async def _enforce_policy(uow: UnitOfWork, upload: Upload, fmt: FileFormat) -> None:
        """The tenant's security policy may narrow the instance limits (M5); it can never widen `MAX_UPLOAD_MB`."""
        policy = await uow.security_policies.get()
        if policy is None:
            return
        check_size(upload.size_bytes, max_mb=policy.max_upload_mb)
        if fmt.mime not in policy.allowed_mime:
            raise UnsupportedType(f"{fmt.mime} is not allowed by the company's security policy")

    async def _validate_upload(self, upload: Upload) -> tuple[FileFormat, str]:
        check_size(upload.size_bytes, max_mb=self._limits.max_upload_mb)
        with upload.path.open("rb") as handle:
            head = handle.read(HEAD_BYTES)
        fmt = sniff(head, filename=upload.filename, declared_mime=upload.declared_mime)
        if fmt.mime == DOCX:
            verify_docx(upload.path)
        digest = await asyncio.to_thread(sha256_of, upload.path)
        return fmt, digest

    @staticmethod
    def _new_version(
        version_id: UUID,
        document: Document,
        version_no: int,
        upload: Upload,
        fmt: FileFormat,
        digest: str,
        key: str,
        *,
        uploader: UUID,
        now: object,
    ) -> DocumentVersion:
        return DocumentVersion(
            id=version_id,
            tenant_id=document.tenant_id,
            document_id=document.id,
            version_no=version_no,
            object_key=key,
            original_filename=sanitize_filename(upload.filename, extension=fmt.extension),
            mime=fmt.mime,
            size_bytes=upload.size_bytes,
            sha256=digest,
            status=VersionStatus.UPLOADED,
            uploaded_by_employee_id=uploader,
            created_at=now,  # type: ignore[arg-type]
            updated_at=now,  # type: ignore[arg-type]
        )

    async def _after_upload(
        self, uow: UnitOfWork, principal: Principal, document: Document, version: DocumentVersion
    ) -> Job:
        await self._audit.record(
            uow,
            principal,
            "document.uploaded",
            resource_type="document_version",
            resource_id=version.id,
            details={
                "document_id": str(document.id),
                "version_no": version.version_no,
                "title": document.title,
                "category": document.category.value,
                "mime": version.mime,
                "size_bytes": version.size_bytes,
                "sha256": version.sha256,
            },
        )
        await uow.integration_events.add(
            IntegrationEvent(
                id=self._ids.new_id(),
                tenant_id=document.tenant_id,
                type="document.uploaded",
                payload={
                    "tenant_id": str(document.tenant_id),
                    "document_id": str(document.id),
                    "version_id": str(version.id),
                    "version_no": version.version_no,
                },
                created_at=self._clock.now(),
            )
        )
        return await self._jobs.enqueue(
            uow,
            type=JOB_DOCUMENT_PROCESS,
            payload={
                "tenant_id": str(document.tenant_id),
                "document_id": str(document.id),
                "version_id": str(version.id),
            },
            tenant_id=document.tenant_id,
            dedupe_key=f"{JOB_DOCUMENT_PROCESS}:{version.id}",
        )

    # --- reads -----------------------------------------------------------------------------------------------
    async def list(
        self, principal: Principal, *, filters: DocumentFilter, limit: int | None, cursor: str | None
    ) -> Page[DocumentSummary]:
        require_tenant_user(principal, action="document.read")
        size = clamp_limit(limit)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            documents = await uow.documents.list(
                reader=reader_for(principal), filters=filters, limit=size + 1, after=decode_cursor(cursor)
            )
            versions = await uow.document_versions.list_for_documents([d.id for d in documents])
        page = build_page(documents, size, lambda d: (d.created_at, d.id))
        return Page(
            items=[self._summary(d, versions.get(d.id, [])) for d in page.items], next_cursor=page.next_cursor
        )

    @staticmethod
    def _summary(document: Document, versions: builtins.list[DocumentVersion]) -> DocumentSummary:
        active = next((v for v in versions if v.id == document.active_version_id), None)
        latest = max(versions, key=lambda v: v.version_no, default=None)
        return DocumentSummary(document=document, active_version=active, latest_version=latest)

    async def get(self, principal: Principal, document_id: UUID) -> DocumentDetail:
        require_tenant_user(principal, action="document.read")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, scope = await load_visible(uow, principal, document_id)
            versions = await uow.document_versions.list_for_document(document.id)
        reader = reader_for(principal)
        return DocumentDetail(
            document=document,
            active_version=next((v for v in versions if v.id == document.active_version_id), None),
            versions=sorted(versions, key=lambda v: v.version_no),
            acl=list(scope.acl),
            can_manage=can_manage(reader, scope),
            can_review=can_review(reader, document.category),
        )

    async def versions(self, principal: Principal, document_id: UUID) -> builtins.list[DocumentVersion]:
        return (await self.get(principal, document_id)).versions

    async def version_detail(self, principal: Principal, document_id: UUID, version_no: int) -> VersionDetail:
        require_tenant_user(principal, action="document.read")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, scope = await load_visible(uow, principal, document_id)
            version = await self._version(uow, document, version_no)
            chunk_count = await uow.chunks.count_for_version(version.id)
            events = await uow.review_events.list_for_version(version.id)
        reader = reader_for(principal)
        visible = (
            principal.employee_id == version.uploaded_by_employee_id
            or can_manage(reader, scope)
            or can_review(reader, document.category)
        )
        return VersionDetail(
            document=document,
            version=version,
            chunk_count=chunk_count,
            review_events=events,
            findings_visible=visible,
        )

    async def download(
        self, principal: Principal, document_id: UUID, version_no: int
    ) -> tuple[Document, DocumentVersion, AsyncIterator[bytes]]:
        require_tenant_user(principal, action="document.read")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, _ = await load_visible(uow, principal, document_id)
            version = await self._version(uow, document, version_no)
            if version.status is VersionStatus.DELETED:
                raise NotFoundError("version not found")
            await self._audit.record(
                uow,
                principal,
                "document.downloaded",
                resource_type="document_version",
                resource_id=version.id,
                details={"document_id": str(document.id), "version_no": version.version_no},
            )
            await uow.commit()
        return document, version, self._storage.stream(version.object_key)

    async def review_queue(self, principal: Principal) -> builtins.list[ReviewItem]:
        require_tenant_user(principal, action="document.review")
        reader = reader_for(principal)
        items: builtins.list[ReviewItem] = []
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            for version in await uow.document_versions.list_by_status(REVIEW_STATUSES):
                document = await uow.documents.get(version.document_id)
                if document is None or document.deleted_at is not None:
                    continue
                if can_review(reader, document.category):
                    items.append(ReviewItem(document=document, version=version))
        return sorted(items, key=lambda i: (i.version.created_at, i.version.id))

    # --- mutations -------------------------------------------------------------------------------------------
    async def update(self, principal: Principal, document_id: UUID, cmd: UpdateDocument) -> Document:
        require_tenant_user(principal, action="document.manage")
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, scope = await load_visible(uow, principal, document_id)
            require_manage(principal, document, scope)
            if document.version != cmd.version:
                raise VersionConflict("the document was changed by someone else; reload and retry")
            changes: dict[str, object] = {}
            if cmd.title is not UNSET:
                document.title = validate_name(cmd.title, "title", max_length=200)
                changes["title"] = document.title
            category = cmd.category if cmd.category is not UNSET else document.category
            department_id = cmd.department_id if cmd.department_id is not UNSET else document.department_id
            if category is not Category.DEPARTMENT_NOTICE:
                department_id = None
            if category != document.category or department_id != document.department_id:
                require_upload(principal, category, department_id)
                if department_id is not None and await uow.departments.get(department_id) is None:
                    raise InvalidInputError(
                        "unknown department",
                        errors=[{"loc": ["body", "department_id"], "msg": "unknown department"}],
                    )
                document.category, document.department_id = category, department_id
                changes["category"] = category.value
            current_acl = [(e.principal_type, e.principal_id) for e in scope.acl]
            acl = list(cmd.acl) if cmd.acl is not UNSET else current_acl
            validate_acl(document.category, acl)
            if cmd.acl is not UNSET:
                entries = [
                    AclEntry(
                        id=self._ids.new_id(),
                        tenant_id=document.tenant_id,
                        document_id=document.id,
                        principal_type=principal_type,
                        principal_id=principal_id,
                        created_at=now,
                    )
                    for principal_type, principal_id in acl
                ]
                await uow.document_acl.replace_for_document(document.id, entries)
                changes["acl"] = [f"{t.value}:{i}" for t, i in acl]
            document.updated_at = now
            await uow.documents.update(document, expected_version=cmd.version)
            await self._audit.record(
                uow,
                principal,
                "document.updated",
                resource_type="document",
                resource_id=document.id,
                details=changes,
            )
            await uow.commit()
        return document

    async def delete(self, principal: Principal, document_id: UUID, *, version: int) -> None:
        require_tenant_user(principal, action="document.manage")
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, scope = await load_visible(uow, principal, document_id)
            require_manage(principal, document, scope)
            if document.version != version:
                raise VersionConflict("the document was changed by someone else; reload and retry")
            for item in await uow.document_versions.list_for_document(document.id):
                if item.retrievable:
                    item.retrievable = False
                    item.updated_at = now
                    await uow.document_versions.update(item)
                await uow.chunks.set_retrievable(item.id, False)
            document.deleted_at = now
            document.updated_at = now
            await uow.documents.update(document, expected_version=version)
            await self._audit.record(
                uow, principal, "document.deleted", resource_type="document", resource_id=document.id
            )
            await uow.integration_events.add(
                IntegrationEvent(
                    id=self._ids.new_id(),
                    tenant_id=document.tenant_id,
                    type="document.deleted",
                    payload={"tenant_id": str(document.tenant_id), "document_id": str(document.id)},
                    created_at=now,
                )
            )
            await uow.commit()

    async def retire(self, principal: Principal, document_id: UUID, version_no: int) -> DocumentVersion:
        require_tenant_user(principal, action="document.manage")
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, scope = await load_visible(uow, principal, document_id)
            require_manage(principal, document, scope)
            version = await self._version(uow, document, version_no)
            version.status = transition(version.status, VersionEvent.RETIRE)
            version.retrievable = False
            version.updated_at = now
            await uow.document_versions.update(version)
            await uow.chunks.set_retrievable(version.id, False)
            if document.active_version_id == version.id:
                document.active_version_id = None
                document.updated_at = now
                await uow.documents.update(document, expected_version=document.version)
            await self._audit.record(
                uow,
                principal,
                "document.retired",
                resource_type="document_version",
                resource_id=version.id,
                details={"document_id": str(document.id), "version_no": version.version_no},
            )
            await uow.commit()
        return version

    async def review(
        self, principal: Principal, document_id: UUID, version_no: int, decision: ReviewDecision
    ) -> DocumentVersion:
        require_tenant_user(principal, action="document.review")
        now = self._clock.now()
        comment = " ".join(decision.comment.split()) if decision.comment else None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, _ = await load_visible(uow, principal, document_id)
            require_review(principal, document)
            version = await self._version(uow, document, version_no)
            action = decision.action
            if action is ReviewAction.RELEASE:
                await activate_version(
                    uow,
                    document,
                    version,
                    now=now,
                    audit=self._audit,
                    principal=principal,
                    event=VersionEvent.RELEASE,
                )
                audit_action = "document.released"
            elif action is ReviewAction.REJECT:
                version.status = transition(version.status, VersionEvent.REJECT)
                version.retrievable = False
                version.updated_at = now
                await uow.document_versions.update(version)
                audit_action = "document.quarantined"
            elif action is ReviewAction.DELETE:
                version.status = transition(version.status, VersionEvent.DELETE)
                version.retrievable = False
                version.updated_at = now
                await uow.document_versions.update(version)
                await uow.chunks.set_retrievable(version.id, False)
                audit_action = "document.deleted"
            else:
                if version.status not in REVIEW_STATUSES:
                    raise ConflictError(
                        "only pending or quarantined versions can be inspected",
                        problem_type="document/invalid-state",
                    )
                audit_action = "document.inspected"
            assert principal.employee_id is not None
            await uow.review_events.add(
                ReviewEvent(
                    id=self._ids.new_id(),
                    tenant_id=document.tenant_id,
                    version_id=version.id,
                    reviewer_employee_id=principal.employee_id,
                    action=action,
                    comment=comment,
                    occurred_at=now,
                )
            )
            await self._audit.record(
                uow,
                principal,
                audit_action,
                resource_type="document_version",
                resource_id=version.id,
                details={
                    "document_id": str(document.id),
                    "version_no": version.version_no,
                    "review_action": action.value,
                    "comment": comment,
                },
            )
            await uow.integration_events.add(
                IntegrationEvent(
                    id=self._ids.new_id(),
                    tenant_id=document.tenant_id,
                    type="document.reviewed",
                    payload={
                        "tenant_id": str(document.tenant_id),
                        "document_id": str(document.id),
                        "version_id": str(version.id),
                        "action": action.value,
                        "status": version.status.value,
                    },
                    created_at=now,
                )
            )
            await uow.commit()
        if action is ReviewAction.DELETE:
            try:
                await self._storage.delete(version.object_key)
            except Exception:  # noqa: BLE001 - the row is the source of truth; retention sweeps orphans
                pass
        return version

    async def reprocess(self, principal: Principal, document_id: UUID, version_no: int) -> Job:
        require_tenant_user(principal, action="document.manage")
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document, scope = await load_visible(uow, principal, document_id)
            require_manage(principal, document, scope)
            version = await self._version(uow, document, version_no)
            version.status = transition(version.status, VersionEvent.REPROCESS)
            version.processing_error = None
            version.updated_at = now
            await uow.document_versions.update(version)
            job = await self._jobs.enqueue(
                uow,
                type=JOB_DOCUMENT_PROCESS,
                payload={
                    "tenant_id": str(document.tenant_id),
                    "document_id": str(document.id),
                    "version_id": str(version.id),
                },
                tenant_id=document.tenant_id,
                dedupe_key=f"{JOB_DOCUMENT_PROCESS}:{version.id}",
            )
            await self._audit.record(
                uow,
                principal,
                "document.reprocess",
                resource_type="document_version",
                resource_id=version.id,
                details={
                    "document_id": str(document.id),
                    "version_no": version.version_no,
                    "job_id": str(job.id),
                },
            )
            await uow.commit()
        return job

    @staticmethod
    async def _version(uow: UnitOfWork, document: Document, version_no: int) -> DocumentVersion:
        version = await uow.document_versions.get_by_number(document.id, version_no)
        if version is None:
            raise NotFoundError("version not found")
        return version
