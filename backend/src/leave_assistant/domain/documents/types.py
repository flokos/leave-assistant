"""Document entities and value objects (doc 05 §1, doc 07 §4.4).

`supporting_document` is deliberately not a `Category` member: sick-leave notes never enter this pipeline
(doc 05 §9), so the type system has no path from them to parsing, chunking or embedding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class Category(StrEnum):
    POLICY_GENERAL = "policy_general"
    POLICY_HR_RESTRICTED = "policy_hr_restricted"
    DEPARTMENT_NOTICE = "department_notice"
    ADMIN_INTERNAL = "admin_internal"


class VersionStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"
    ACTIVE = "ACTIVE"
    PENDING_REVIEW = "PENDING_REVIEW"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"
    DELETED = "DELETED"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChunkType(StrEnum):
    TEXT = "text"
    TABLE = "table"


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"


class AclPrincipalType(StrEnum):
    ROLE = "role"
    DEPARTMENT = "department"
    EMPLOYEE = "employee"


class ReviewAction(StrEnum):
    RELEASE = "release"
    REJECT = "reject"
    DELETE = "delete"
    INSPECT = "inspect"


@dataclass(frozen=True, slots=True)
class Block:
    """One normalised structural element of a parsed document (doc 05 §3 "structure")."""

    type: BlockType
    text: str
    level: int = 0  # heading level (1 = top) for headings, 0 otherwise
    page: int | None = None
    rows: tuple[tuple[str, ...], ...] = ()  # table cells, header row first (tables only)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    blocks: tuple[Block, ...]
    pages: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text)


@dataclass(slots=True)
class Document:
    id: UUID
    tenant_id: UUID
    title: str
    category: Category
    owner_employee_id: UUID
    created_at: datetime
    updated_at: datetime
    department_id: UUID | None = None
    active_version_id: UUID | None = None
    language_hint: str | None = None
    deleted_at: datetime | None = None
    version: int = 1


@dataclass(slots=True)
class DocumentVersion:
    id: UUID
    tenant_id: UUID
    document_id: UUID
    version_no: int
    object_key: str
    original_filename: str
    mime: str
    size_bytes: int
    sha256: str
    status: VersionStatus
    uploaded_by_employee_id: UUID
    created_at: datetime
    updated_at: datetime
    pages: int | None = None
    language: str | None = None
    extracted_object_key: str | None = None
    risk_score: float = 0.0
    risk_tier: RiskTier = RiskTier.LOW
    risk_findings: list[dict[str, Any]] = field(default_factory=list)
    pii_summary: dict[str, Any] = field(default_factory=dict)
    processing_error: str | None = None
    processed_at: datetime | None = None
    index_generation: str | None = None
    retrievable: bool = False
    attempts: int = 0


@dataclass(slots=True)
class Chunk:
    id: UUID
    tenant_id: UUID
    version_id: UUID
    ordinal: int
    chunk_type: ChunkType
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    language: str | None
    content: str
    content_llm_safe: str
    token_count: int
    embedding: list[float] | None
    embedding_model_id: str
    risk_flags: tuple[str, ...] = ()
    has_pii: bool = False
    retrievable: bool = True


@dataclass(frozen=True, slots=True)
class AclEntry:
    id: UUID
    tenant_id: UUID
    document_id: UUID
    principal_type: AclPrincipalType
    principal_id: str  # role name, department id or employee id as text
    created_at: datetime
    permission: str = "read"


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    id: UUID
    tenant_id: UUID
    version_id: UUID
    reviewer_employee_id: UUID
    action: ReviewAction
    comment: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class IndexGeneration:
    tenant_id: UUID
    generation: str
    embedding_model_id: str
    chunking_rules_version: str
    status: str  # building | active | retired
    started_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    version_no: int
    title: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
