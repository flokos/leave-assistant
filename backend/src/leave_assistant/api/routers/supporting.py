"""Supporting documents (doc 09 §2.3/§2.6, doc 08 §9): attach to a leave request, HR metadata, token downloads.

Managers and company administrators receive 404 from the service (no existence leak). Files are never parsed or
embedded — this router talks to `SupportingDocumentService` only, which has no path to the ingestion pipeline.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Response, UploadFile, status

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices, CurrentSettings
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.routers.documents import spooled
from leave_assistant.api.schemas.supporting import (
    DownloadTokenIn,
    DownloadTokenOut,
    SupportingDeleteIn,
    SupportingDocumentDetailOut,
    SupportingDocumentOut,
)

router = APIRouter(tags=["supporting-documents"], route_class=IdempotentRoute)


@router.post(
    "/leave/requests/{request_id}/supporting-documents",
    response_model=SupportingDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_supporting_document(
    request_id: UUID,
    principal: CurrentPrincipal,
    services: CurrentServices,
    settings: CurrentSettings,
    file: Annotated[UploadFile, File()],
) -> SupportingDocumentOut:
    async with spooled(file, max_mb=settings.supporting_max_upload_mb) as upload:
        document = await services.supporting.upload(principal, request_id, upload)
    return SupportingDocumentOut.from_domain(document)


@router.get("/leave/requests/{request_id}/supporting-documents", response_model=list[SupportingDocumentOut])
async def list_supporting_documents(
    request_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> list[SupportingDocumentOut]:
    documents = await services.supporting.list_for_request(principal, request_id)
    return [SupportingDocumentOut.from_domain(d) for d in documents]


@router.get("/supporting-documents/download/{token}")
async def download_supporting_document(token: str, services: CurrentServices) -> Response:
    """Token bearer: the 60-second single-use token issued below is the only credential (doc 08 §9)."""
    document, plaintext = await services.supporting.download(token)
    filename = quote(document.original_filename)
    return Response(
        content=plaintext,
        media_type=document.mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/supporting-documents/{document_id}", response_model=SupportingDocumentDetailOut)
async def get_supporting_document(
    document_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> SupportingDocumentDetailOut:
    return SupportingDocumentDetailOut.from_detail(await services.supporting.get(principal, document_id))


# No idempotency record: it would keep the raw token in `platform.idempotency_keys` for a day, and every call simply
# issues a fresh, separately audited 60-second token.
@router.post(
    "/supporting-documents/{document_id}/download-token",
    response_model=DownloadTokenOut,
    openapi_extra=NO_IDEMPOTENCY,
)
async def issue_download_token(
    document_id: UUID,
    body: DownloadTokenIn,
    principal: CurrentPrincipal,
    services: CurrentServices,
    settings: CurrentSettings,
) -> DownloadTokenOut:
    issued = await services.supporting.issue_download_token(principal, document_id, body.reason)
    url = f"{settings.api_prefix}/supporting-documents/download/{issued.token}"
    return DownloadTokenOut.from_issued(issued, url=url)


@router.delete("/supporting-documents/{document_id}", response_model=SupportingDocumentOut)
async def delete_supporting_document(
    document_id: UUID, body: SupportingDeleteIn, principal: CurrentPrincipal, services: CurrentServices
) -> SupportingDocumentOut:
    return SupportingDocumentOut.from_domain(
        await services.supporting.delete(principal, document_id, body.reason)
    )
