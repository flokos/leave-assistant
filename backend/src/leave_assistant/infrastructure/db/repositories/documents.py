"""Documents, versions, ACL, review events and index generations — plus the SQL visibility predicate."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, delete, exists, false, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.leave.errors import VersionConflict
from leave_assistant.application.ports import DocumentFilter
from leave_assistant.domain.documents.types import (
    AclEntry,
    AclPrincipalType,
    Category,
    Document,
    DocumentVersion,
    IndexGeneration,
    ReviewAction,
    ReviewEvent,
    RiskTier,
    VersionStatus,
)
from leave_assistant.domain.documents.visibility import TENANT_ROLES, Reader
from leave_assistant.domain.tenancy import Role
from leave_assistant.infrastructure.db.models import (
    DepartmentRow,
    DocumentAclRow,
    DocumentReviewEventRow,
    DocumentRow,
    DocumentVersionRow,
    IndexGenerationRow,
)
from leave_assistant.infrastructure.db.session import flush_or_conflict


def visibility_predicate(reader: Reader) -> ColumnElement[bool]:
    """The SQL rendering of `domain.documents.visibility.can_read` (defence in depth; parity-tested)."""
    roles = {r.value for r in reader.roles}
    if reader.employee_id is None or not (reader.roles & TENANT_ROLES):
        return false()
    conditions: list[ColumnElement[bool]] = [
        DocumentRow.owner_employee_id == reader.employee_id,
        DocumentRow.category == Category.POLICY_GENERAL.value,
    ]
    if roles & {Role.HR_ADMIN.value, Role.COMPANY_ADMIN.value}:
        conditions.append(
            DocumentRow.category.in_([Category.POLICY_HR_RESTRICTED.value, Category.DEPARTMENT_NOTICE.value])
        )
    if Role.COMPANY_ADMIN.value in roles:
        conditions.append(DocumentRow.category == Category.ADMIN_INTERNAL.value)
    department_conditions: list[ColumnElement[bool]] = [
        DocumentRow.department_id.in_(
            select(DepartmentRow.id).where(DepartmentRow.manager_employee_id == reader.employee_id)
        )
    ]
    if reader.department_id is not None:
        department_conditions.append(DocumentRow.department_id == reader.department_id)
    conditions.append(
        and_(DocumentRow.category == Category.DEPARTMENT_NOTICE.value, or_(*department_conditions))
    )
    acl_match: list[ColumnElement[bool]] = [
        and_(DocumentAclRow.principal_type == "role", DocumentAclRow.principal_id.in_(sorted(roles))),
        and_(
            DocumentAclRow.principal_type == "employee",
            DocumentAclRow.principal_id == str(reader.employee_id),
        ),
    ]
    if reader.department_id is not None:
        acl_match.append(
            and_(
                DocumentAclRow.principal_type == "department",
                DocumentAclRow.principal_id == str(reader.department_id),
            )
        )
    acl_exists = exists().where(
        DocumentAclRow.document_id == DocumentRow.id, DocumentAclRow.permission == "read", or_(*acl_match)
    )
    conditions.append(and_(DocumentRow.category != Category.POLICY_HR_RESTRICTED.value, acl_exists))
    return or_(*conditions)


def _document_to_domain(r: DocumentRow) -> Document:
    return Document(
        id=r.id,
        tenant_id=r.tenant_id,
        title=r.title,
        category=Category(r.category),
        owner_employee_id=r.owner_employee_id,
        created_at=r.created_at,
        updated_at=r.updated_at,
        department_id=r.department_id,
        active_version_id=r.active_version_id,
        language_hint=r.language_hint,
        deleted_at=r.deleted_at,
        version=r.version,
    )


def _version_to_domain(r: DocumentVersionRow) -> DocumentVersion:
    return DocumentVersion(
        id=r.id,
        tenant_id=r.tenant_id,
        document_id=r.document_id,
        version_no=r.version_no,
        object_key=r.object_key,
        original_filename=r.original_filename,
        mime=r.mime,
        size_bytes=int(r.size_bytes),
        sha256=r.sha256,
        status=VersionStatus(r.status),
        uploaded_by_employee_id=r.uploaded_by_employee_id,
        created_at=r.created_at,
        updated_at=r.updated_at,
        pages=r.pages,
        language=r.language,
        extracted_object_key=r.extracted_object_key,
        risk_score=float(r.risk_score),
        risk_tier=RiskTier(r.risk_tier),
        risk_findings=list(r.risk_findings or []),
        pii_summary=dict(r.pii_summary or {}),
        processing_error=r.processing_error,
        processed_at=r.processed_at,
        index_generation=r.index_generation,
        retrievable=bool(r.retrievable),
        attempts=r.attempts,
    )


VERSION_FIELDS = (
    "pages",
    "language",
    "extracted_object_key",
    "risk_score",
    "risk_findings",
    "pii_summary",
    "processing_error",
    "processed_at",
    "index_generation",
    "retrievable",
    "attempts",
    "updated_at",
    "object_key",
    "original_filename",
)


class SqlDocumentRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[DocumentRow]:
        return select(DocumentRow).where(DocumentRow.tenant_id == self._tenant)

    async def get(self, document_id: UUID) -> Document | None:
        if self._tenant is None:
            return None
        row = (await self._s.execute(self._base().where(DocumentRow.id == document_id))).scalar_one_or_none()
        return _document_to_domain(row) if row else None

    async def get_by_title(self, title: str) -> Document | None:
        if self._tenant is None:
            return None
        stmt = self._base().where(DocumentRow.title == title, DocumentRow.deleted_at.is_(None)).limit(1)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _document_to_domain(row) if row else None

    async def list(
        self, *, reader: Reader, filters: DocumentFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[Document]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(DocumentRow.deleted_at.is_(None), visibility_predicate(reader))
            .order_by(DocumentRow.created_at, DocumentRow.id)
            .limit(limit)
        )
        if filters.category is not None:
            stmt = stmt.where(DocumentRow.category == filters.category.value)
        if filters.query:
            stmt = stmt.where(func.lower(DocumentRow.title).contains(filters.query.lower()))
        if filters.status is not None:
            latest = (
                select(
                    DocumentVersionRow.document_id, func.max(DocumentVersionRow.version_no).label("latest")
                )
                .where(DocumentVersionRow.tenant_id == self._tenant)
                .group_by(DocumentVersionRow.document_id)
                .subquery()
            )
            current = (
                select(DocumentVersionRow.document_id)
                .join(latest, latest.c.document_id == DocumentVersionRow.document_id)
                .join(DocumentRow, DocumentRow.id == DocumentVersionRow.document_id)
                .where(
                    DocumentVersionRow.status == filters.status.value,
                    or_(
                        DocumentVersionRow.id == DocumentRow.active_version_id,
                        and_(
                            DocumentRow.active_version_id.is_(None),
                            DocumentVersionRow.version_no == latest.c.latest,
                        ),
                    ),
                )
            )
            stmt = stmt.where(DocumentRow.id.in_(current))
        if after is not None:
            stmt = stmt.where(tuple_(DocumentRow.created_at, DocumentRow.id) > after)
        return [_document_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, document: Document) -> None:
        self._s.add(
            DocumentRow(
                id=document.id,
                tenant_id=document.tenant_id,
                title=document.title,
                category=document.category.value,
                department_id=document.department_id,
                owner_employee_id=document.owner_employee_id,
                active_version_id=document.active_version_id,
                language_hint=document.language_hint,
                deleted_at=document.deleted_at,
                version=document.version,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, document: Document, *, expected_version: int) -> None:
        row = (
            await self._s.execute(self._base().where(DocumentRow.id == document.id).with_for_update())
        ).scalar_one_or_none()
        if row is None or row.version != expected_version:
            raise VersionConflict("the document was changed by someone else; reload and retry")
        row.title = document.title
        row.category = document.category.value
        row.department_id = document.department_id
        row.active_version_id = document.active_version_id
        row.language_hint = document.language_hint
        row.deleted_at = document.deleted_at
        row.updated_at = document.updated_at
        row.version = expected_version + 1
        document.version = row.version
        await flush_or_conflict(self._s)


class SqlDocumentVersionRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[DocumentVersionRow]:
        return select(DocumentVersionRow).where(DocumentVersionRow.tenant_id == self._tenant)

    async def get(self, version_id: UUID) -> DocumentVersion | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(DocumentVersionRow.id == version_id))
        ).scalar_one_or_none()
        return _version_to_domain(row) if row else None

    async def get_by_number(self, document_id: UUID, version_no: int) -> DocumentVersion | None:
        if self._tenant is None:
            return None
        stmt = self._base().where(
            DocumentVersionRow.document_id == document_id, DocumentVersionRow.version_no == version_no
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _version_to_domain(row) if row else None

    async def list_for_document(self, document_id: UUID) -> builtins.list[DocumentVersion]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(DocumentVersionRow.document_id == document_id)
            .order_by(DocumentVersionRow.version_no)
        )
        return [_version_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def list_for_documents(
        self, document_ids: Sequence[UUID]
    ) -> dict[UUID, builtins.list[DocumentVersion]]:
        result: dict[UUID, builtins.list[DocumentVersion]] = {}
        if self._tenant is None or not document_ids:
            return result
        stmt = (
            self._base()
            .where(DocumentVersionRow.document_id.in_(list(document_ids)))
            .order_by(DocumentVersionRow.document_id, DocumentVersionRow.version_no)
        )
        for row in (await self._s.execute(stmt)).scalars():
            result.setdefault(row.document_id, []).append(_version_to_domain(row))
        return result

    async def find_by_sha256(self, document_id: UUID, sha256: str) -> DocumentVersion | None:
        if self._tenant is None:
            return None
        stmt = (
            self._base()
            .where(DocumentVersionRow.document_id == document_id, DocumentVersionRow.sha256 == sha256)
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _version_to_domain(row) if row else None

    async def list_by_status(self, statuses: frozenset[VersionStatus]) -> builtins.list[DocumentVersion]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(DocumentVersionRow.status.in_([s.value for s in statuses]))
            .order_by(DocumentVersionRow.created_at, DocumentVersionRow.id)
        )
        return [_version_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, version: DocumentVersion) -> None:
        self._s.add(
            DocumentVersionRow(
                id=version.id,
                tenant_id=version.tenant_id,
                document_id=version.document_id,
                version_no=version.version_no,
                object_key=version.object_key,
                original_filename=version.original_filename,
                mime=version.mime,
                size_bytes=version.size_bytes,
                sha256=version.sha256,
                status=version.status.value,
                pages=version.pages,
                language=version.language,
                extracted_object_key=version.extracted_object_key,
                risk_score=version.risk_score,
                risk_tier=version.risk_tier.value,
                risk_findings=version.risk_findings,
                pii_summary=version.pii_summary,
                processing_error=version.processing_error,
                processed_at=version.processed_at,
                index_generation=version.index_generation,
                retrievable=version.retrievable,
                uploaded_by_employee_id=version.uploaded_by_employee_id,
                attempts=version.attempts,
                created_at=version.created_at,
                updated_at=version.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, version: DocumentVersion) -> None:
        row = (await self._s.execute(self._base().where(DocumentVersionRow.id == version.id))).scalar_one()
        for field in VERSION_FIELDS:
            setattr(row, field, getattr(version, field))
        row.status = version.status.value
        row.risk_tier = version.risk_tier.value
        await flush_or_conflict(self._s)


class SqlAclRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def list_for_document(self, document_id: UUID) -> builtins.list[AclEntry]:
        if self._tenant is None:
            return []
        stmt = (
            select(DocumentAclRow)
            .where(DocumentAclRow.tenant_id == self._tenant, DocumentAclRow.document_id == document_id)
            .order_by(DocumentAclRow.created_at, DocumentAclRow.id)
        )
        return [
            AclEntry(
                id=r.id,
                tenant_id=r.tenant_id,
                document_id=r.document_id,
                principal_type=AclPrincipalType(r.principal_type),
                principal_id=r.principal_id,
                created_at=r.created_at,
                permission=r.permission,
            )
            for r in (await self._s.execute(stmt)).scalars()
        ]

    async def replace_for_document(self, document_id: UUID, entries: Sequence[AclEntry]) -> None:
        await self._s.execute(
            delete(DocumentAclRow).where(
                DocumentAclRow.tenant_id == self._tenant, DocumentAclRow.document_id == document_id
            )
        )
        for entry in entries:
            self._s.add(
                DocumentAclRow(
                    id=entry.id,
                    tenant_id=entry.tenant_id,
                    document_id=entry.document_id,
                    principal_type=entry.principal_type.value,
                    principal_id=entry.principal_id,
                    permission=entry.permission,
                    created_at=entry.created_at,
                )
            )
        await flush_or_conflict(self._s)


class SqlReviewEventRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, event: ReviewEvent) -> None:
        self._s.add(
            DocumentReviewEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                version_id=event.version_id,
                reviewer_employee_id=event.reviewer_employee_id,
                action=event.action.value,
                comment=event.comment,
                occurred_at=event.occurred_at,
            )
        )
        await flush_or_conflict(self._s)

    async def list_for_version(self, version_id: UUID) -> builtins.list[ReviewEvent]:
        if self._tenant is None:
            return []
        stmt = (
            select(DocumentReviewEventRow)
            .where(
                DocumentReviewEventRow.tenant_id == self._tenant,
                DocumentReviewEventRow.version_id == version_id,
            )
            .order_by(DocumentReviewEventRow.occurred_at, DocumentReviewEventRow.id)
        )
        return [
            ReviewEvent(
                id=r.id,
                tenant_id=r.tenant_id,
                version_id=r.version_id,
                reviewer_employee_id=r.reviewer_employee_id,
                action=ReviewAction(r.action),
                comment=r.comment,
                occurred_at=r.occurred_at,
            )
            for r in (await self._s.execute(stmt)).scalars()
        ]


class SqlIndexGenerationRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(r: IndexGenerationRow) -> IndexGeneration:
        return IndexGeneration(
            tenant_id=r.tenant_id,
            generation=r.generation,
            embedding_model_id=r.embedding_model_id,
            chunking_rules_version=r.chunking_rules_version,
            status=r.status,
            started_at=r.started_at,
            completed_at=r.completed_at,
        )

    async def get(self, generation: str) -> IndexGeneration | None:
        if self._tenant is None:
            return None
        row = await self._s.get(IndexGenerationRow, (self._tenant, generation))
        return self._to_domain(row) if row else None

    async def upsert(self, generation: IndexGeneration) -> None:
        row = await self._s.get(IndexGenerationRow, (generation.tenant_id, generation.generation))
        if row is None:
            self._s.add(
                IndexGenerationRow(
                    tenant_id=generation.tenant_id,
                    generation=generation.generation,
                    embedding_model_id=generation.embedding_model_id,
                    chunking_rules_version=generation.chunking_rules_version,
                    status=generation.status,
                    started_at=generation.started_at,
                    completed_at=generation.completed_at,
                )
            )
        else:
            row.status = generation.status
            row.completed_at = generation.completed_at
        await flush_or_conflict(self._s)

    async def list(self) -> builtins.list[IndexGeneration]:
        if self._tenant is None:
            return []
        stmt = select(IndexGenerationRow).where(IndexGenerationRow.tenant_id == self._tenant)
        return [self._to_domain(r) for r in (await self._s.execute(stmt)).scalars()]
