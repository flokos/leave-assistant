"""Read models of the document platform."""

from __future__ import annotations

from dataclasses import dataclass

from leave_assistant.domain.documents.types import (
    AclEntry,
    Chunk,
    Citation,
    Document,
    DocumentVersion,
    ReviewEvent,
)


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    document: Document
    active_version: DocumentVersion | None
    latest_version: DocumentVersion | None


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    document: Document
    active_version: DocumentVersion | None
    versions: list[DocumentVersion]
    acl: list[AclEntry]
    can_manage: bool
    can_review: bool


@dataclass(frozen=True, slots=True)
class VersionDetail:
    document: Document
    version: DocumentVersion
    chunk_count: int
    review_events: list[ReviewEvent]
    findings_visible: bool  # uploader, managers of the category and reviewers see risk findings


@dataclass(frozen=True, slots=True)
class ReviewItem:
    document: Document
    version: DocumentVersion


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: Chunk
    document: Document
    version: DocumentVersion
    score: float
    vector_rank: int | None
    keyword_rank: int | None
    citation: Citation


@dataclass(frozen=True, slots=True)
class RetrievalBundle:
    query: str
    language: str | None
    hits: list[SearchHit]
