"""Object-storage keys (ADR-0016): `tenants/{tenant}/documents/{document}/{version}/original.{ext}` + extracted.json."""

from __future__ import annotations

from uuid import UUID


def original_key(tenant_id: UUID, document_id: UUID, version_no: int, extension: str) -> str:
    return f"tenants/{tenant_id}/documents/{document_id}/{version_no}/original.{extension}"


def extracted_key(tenant_id: UUID, document_id: UUID, version_no: int) -> str:
    return f"tenants/{tenant_id}/documents/{document_id}/{version_no}/extracted.json"
