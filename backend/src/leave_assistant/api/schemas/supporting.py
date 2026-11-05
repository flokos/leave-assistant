"""Supporting-document schemas (doc 09 §2.3/§2.6) — M5. Metadata only; bytes travel through download tokens."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.ports import SupportingAccessEvent, SupportingDocument
from leave_assistant.application.supporting.service import IssuedToken, SupportingDocumentDetail


class SupportingDocumentOut(ApiModel):
    id: UUID
    request_id: UUID
    employee_id: UUID
    original_filename: str
    mime: str
    size_bytes: int
    sha256: str
    status: str
    uploaded_by_employee_id: UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, d: SupportingDocument) -> SupportingDocumentOut:
        return cls(
            id=d.id,
            request_id=d.request_id,
            employee_id=d.employee_id,
            original_filename=d.original_filename,
            mime=d.mime,
            size_bytes=d.size_bytes,
            sha256=d.sha256,
            status=d.status,
            uploaded_by_employee_id=d.uploaded_by_employee_id,
            created_at=d.created_at,
        )


class AccessEventOut(ApiModel):
    id: UUID
    actor_employee_id: UUID
    action: str
    occurred_at: datetime
    reason: str | None

    @classmethod
    def from_domain(cls, e: SupportingAccessEvent) -> AccessEventOut:
        return cls(
            id=e.id,
            actor_employee_id=e.actor_employee_id,
            action=e.action,
            occurred_at=e.occurred_at,
            reason=e.reason,
        )


class SupportingDocumentDetailOut(SupportingDocumentOut):
    access_events: list[AccessEventOut]

    @classmethod
    def from_detail(cls, detail: SupportingDocumentDetail) -> SupportingDocumentDetailOut:
        base = SupportingDocumentOut.from_domain(detail.document)
        return cls(
            **base.model_dump(), access_events=[AccessEventOut.from_domain(e) for e in detail.access_events]
        )


class DownloadTokenIn(ApiModel):
    reason: str | None = Field(default=None, max_length=500, description="Required for HR administrators")


class DownloadTokenOut(ApiModel):
    token: str
    expires_at: datetime
    url: str

    @classmethod
    def from_issued(cls, issued: IssuedToken, *, url: str) -> DownloadTokenOut:
        return cls(token=issued.token, expires_at=issued.expires_at, url=url)  # type: ignore[arg-type]


class SupportingDeleteIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
