"""Document platform schemas (doc 09 §2.5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.documents.views import (
    DocumentDetail,
    DocumentSummary,
    ReviewItem,
    SearchHit,
    VersionDetail,
)
from leave_assistant.domain.documents.types import AclEntry, Document, DocumentVersion, ReviewEvent

CategoryField = Literal["policy_general", "policy_hr_restricted", "department_notice", "admin_internal"]
AclPrincipalField = Literal["role", "department", "employee"]
ReviewActionField = Literal["release", "reject", "delete", "inspect"]
SNIPPET_CHARS = 320


class VersionOut(ApiModel):
    id: UUID
    document_id: UUID
    version_no: int
    status: str
    original_filename: str
    mime: str
    size_bytes: int
    sha256: str
    pages: int | None
    language: str | None
    risk_tier: str
    risk_score: float
    findings_count: int = 0
    processing_error: str | None
    processed_at: datetime | None
    retrievable: bool
    index_generation: str | None
    uploaded_by_employee_id: UUID
    attempts: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, v: DocumentVersion) -> VersionOut:
        return cls(
            id=v.id,
            document_id=v.document_id,
            version_no=v.version_no,
            status=v.status.value,
            original_filename=v.original_filename,
            mime=v.mime,
            size_bytes=v.size_bytes,
            sha256=v.sha256,
            pages=v.pages,
            language=v.language,
            risk_tier=v.risk_tier.value,
            risk_score=v.risk_score,
            findings_count=len(v.risk_findings),
            processing_error=v.processing_error,
            processed_at=v.processed_at,
            retrievable=v.retrievable,
            index_generation=v.index_generation,
            uploaded_by_employee_id=v.uploaded_by_employee_id,
            attempts=v.attempts,
            created_at=v.created_at,
            updated_at=v.updated_at,
        )


class ReviewEventOut(ApiModel):
    id: UUID
    action: str
    comment: str | None
    reviewer_employee_id: UUID
    occurred_at: datetime

    @classmethod
    def from_domain(cls, e: ReviewEvent) -> ReviewEventOut:
        return cls(
            id=e.id,
            action=e.action.value,
            comment=e.comment,
            reviewer_employee_id=e.reviewer_employee_id,
            occurred_at=e.occurred_at,
        )


class VersionDetailOut(VersionOut):
    chunk_count: int
    risk_findings: list[dict[str, Any]] | None
    pii_summary: dict[str, Any] | None
    review_events: list[ReviewEventOut]

    @classmethod
    def from_detail(cls, d: VersionDetail) -> VersionDetailOut:
        base = VersionOut.from_domain(d.version).model_dump()
        return cls(
            **base,
            chunk_count=d.chunk_count,
            risk_findings=d.version.risk_findings if d.findings_visible else None,
            pii_summary=d.version.pii_summary if d.findings_visible else None,
            review_events=[ReviewEventOut.from_domain(e) for e in d.review_events],
        )


class AclEntryIn(ApiModel):
    principal_type: AclPrincipalField
    principal_id: str = Field(min_length=1, max_length=64)


class AclEntryOut(AclEntryIn):
    id: UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, e: AclEntry) -> AclEntryOut:
        return cls(
            id=e.id,
            principal_type=e.principal_type.value,
            principal_id=e.principal_id,
            created_at=e.created_at,
        )


class DocumentOut(ApiModel):
    id: UUID
    title: str
    category: str
    department_id: UUID | None
    owner_employee_id: UUID
    active_version_id: UUID | None
    language_hint: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    active_version: VersionOut | None
    latest_version: VersionOut | None

    @classmethod
    def from_summary(cls, s: DocumentSummary) -> DocumentOut:
        return cls._build(s.document, s.active_version, s.latest_version)

    @classmethod
    def _build(
        cls, d: Document, active: DocumentVersion | None, latest: DocumentVersion | None
    ) -> DocumentOut:
        return cls(
            id=d.id,
            title=d.title,
            category=d.category.value,
            department_id=d.department_id,
            owner_employee_id=d.owner_employee_id,
            active_version_id=d.active_version_id,
            language_hint=d.language_hint,
            version=d.version,
            created_at=d.created_at,
            updated_at=d.updated_at,
            active_version=VersionOut.from_domain(active) if active else None,
            latest_version=VersionOut.from_domain(latest) if latest else None,
        )


class DocumentDetailOut(DocumentOut):
    versions: list[VersionOut]
    acl: list[AclEntryOut]
    can_manage: bool
    can_review: bool

    @classmethod
    def from_detail(cls, d: DocumentDetail) -> DocumentDetailOut:
        latest = max(d.versions, key=lambda v: v.version_no, default=None)
        base = DocumentOut._build(d.document, d.active_version, latest).model_dump()
        return cls(
            **base,
            versions=[VersionOut.from_domain(v) for v in d.versions],
            acl=[AclEntryOut.from_domain(a) for a in d.acl],
            can_manage=d.can_manage,
            can_review=d.can_review,
        )


class DocumentUpdate(ApiModel):
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: CategoryField | None = None
    department_id: UUID | None = None
    acl: list[AclEntryIn] | None = None


class ReviewIn(ApiModel):
    action: ReviewActionField
    comment: str | None = Field(default=None, max_length=1000)


class RiskFindingOut(ApiModel):
    detector: str
    family: str
    score: float
    chunk: int | None = None
    excerpt: str = ""


class ReviewItemOut(ApiModel):
    document_id: UUID
    title: str
    category: str
    version: VersionOut
    findings: list[RiskFindingOut]
    pii_summary: dict[str, Any]

    @classmethod
    def from_domain(cls, item: ReviewItem) -> ReviewItemOut:
        return cls(
            document_id=item.document.id,
            title=item.document.title,
            category=item.document.category.value,
            version=VersionOut.from_domain(item.version),
            findings=[
                RiskFindingOut(**{k: v for k, v in f.items() if k in RiskFindingOut.model_fields})
                for f in item.version.risk_findings
            ],
            pii_summary=dict(item.version.pii_summary),
        )


class SearchHitOut(ApiModel):
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    version_no: int
    title: str
    category: str
    chunk_type: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    language: str | None
    snippet: str
    score: float
    vector_rank: int | None
    keyword_rank: int | None

    @classmethod
    def from_domain(cls, hit: SearchHit) -> SearchHitOut:
        content = hit.chunk.content
        snippet = (
            content if len(content) <= SNIPPET_CHARS else content[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
        )
        return cls(
            chunk_id=hit.chunk.id,
            document_id=hit.document.id,
            version_id=hit.version.id,
            version_no=hit.version.version_no,
            title=hit.document.title,
            category=hit.document.category.value,
            chunk_type=hit.chunk.chunk_type.value,
            heading_path=list(hit.chunk.heading_path),
            page_start=hit.chunk.page_start,
            page_end=hit.chunk.page_end,
            language=hit.chunk.language,
            snippet=snippet,
            score=hit.score,
            vector_rank=hit.vector_rank,
            keyword_rank=hit.keyword_rank,
        )


class SearchOut(ApiModel):
    query: str
    language: str | None
    hits: list[SearchHitOut]


class CitationOut(ApiModel):
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    version_no: int
    title: str
    category: str
    chunk_type: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    language: str | None
    content: str

    @classmethod
    def from_domain(cls, hit: SearchHit) -> CitationOut:
        return cls(
            chunk_id=hit.chunk.id,
            document_id=hit.document.id,
            version_id=hit.version.id,
            version_no=hit.version.version_no,
            title=hit.document.title,
            category=hit.document.category.value,
            chunk_type=hit.chunk.chunk_type.value,
            heading_path=list(hit.chunk.heading_path),
            page_start=hit.chunk.page_start,
            page_end=hit.chunk.page_end,
            language=hit.chunk.language,
            content=hit.chunk.content,
        )
