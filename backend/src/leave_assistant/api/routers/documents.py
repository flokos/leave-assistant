"""Documents and knowledge (doc 09 §2.5): library, multipart uploads, versions, downloads, review, search, citations."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices, CurrentSettings
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.api.schemas.documents import (
    CategoryField,
    CitationOut,
    DocumentDetailOut,
    DocumentOut,
    DocumentUpdate,
    ReviewIn,
    ReviewItemOut,
    SearchHitOut,
    SearchOut,
    VersionDetailOut,
    VersionOut,
)
from leave_assistant.api.schemas.jobs import JobOut
from leave_assistant.application.documents.commands import (
    CreateDocument,
    ReviewDecision,
    UpdateDocument,
    Upload,
)
from leave_assistant.application.ports import DocumentFilter
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.domain.documents.formats import TooLarge
from leave_assistant.domain.documents.types import AclPrincipalType, Category, ReviewAction, VersionStatus

router = APIRouter(prefix="/documents", tags=["documents"], route_class=IdempotentRoute)
READ_CHUNK = 1 << 20


@asynccontextmanager
async def spooled(upload: UploadFile, *, max_mb: int) -> AsyncIterator[Upload]:
    """Copy the multipart part to a temporary file, enforcing the size limit while streaming (never in memory)."""
    limit = max_mb * 1024 * 1024
    with tempfile.NamedTemporaryFile(prefix="upload-", delete=False) as handle:
        path = Path(handle.name)
        size = 0
        try:
            while block := await upload.read(READ_CHUNK):
                size += len(block)
                if size > limit:
                    raise TooLarge(f"the file exceeds the {max_mb} MB limit", params={"max_mb": max_mb})
                handle.write(block)
        except BaseException:
            path.unlink(missing_ok=True)  # noqa: ASYNC240 - tiny metadata call on a local temp file
            raise
    try:
        yield Upload(path=path, filename=upload.filename, declared_mime=upload.content_type, size_bytes=size)
    finally:
        path.unlink(missing_ok=True)  # noqa: ASYNC240


@router.get("", response_model=PageOut[DocumentOut])
async def list_documents(
    principal: CurrentPrincipal,
    services: CurrentServices,
    category: CategoryField | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[DocumentOut]:
    page = await services.documents.list(
        principal,
        filters=DocumentFilter(
            category=Category(category) if category else None,
            status=VersionStatus(status_filter) if status_filter else None,
            query=q,
        ),
        limit=limit,
        cursor=cursor,
    )
    return PageOut(items=[DocumentOut.from_summary(s) for s in page.items], next_cursor=page.next_cursor)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    principal: CurrentPrincipal,
    services: CurrentServices,
    settings: CurrentSettings,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    category: Annotated[CategoryField, Form()],
    department_id: Annotated[UUID | None, Form()] = None,
    language_hint: Annotated[str | None, Form(max_length=8)] = None,
) -> DocumentOut:
    async with spooled(file, max_mb=settings.max_upload_mb) as upload:
        summary = await services.documents.create(
            principal,
            CreateDocument(
                title=title,
                category=Category(category),
                upload=upload,
                department_id=department_id,
                language_hint=language_hint,
            ),
        )
    return DocumentOut.from_summary(summary)


@router.get("/search", response_model=SearchOut)
async def search(
    principal: CurrentPrincipal,
    services: CurrentServices,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    k: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> SearchOut:
    bundle = await services.retrieval.search(principal, q, k=k)
    return SearchOut(
        query=bundle.query, language=bundle.language, hits=[SearchHitOut.from_domain(h) for h in bundle.hits]
    )


@router.get("/review-queue", response_model=list[ReviewItemOut])
async def review_queue(principal: CurrentPrincipal, services: CurrentServices) -> list[ReviewItemOut]:
    return [ReviewItemOut.from_domain(i) for i in await services.documents.review_queue(principal)]


@router.get("/citations/{chunk_id}", response_model=CitationOut)
async def citation(chunk_id: UUID, principal: CurrentPrincipal, services: CurrentServices) -> CitationOut:
    return CitationOut.from_domain(await services.retrieval.citation(principal, chunk_id))


@router.get("/{document_id}", response_model=DocumentDetailOut)
async def get_document(
    document_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> DocumentDetailOut:
    return DocumentDetailOut.from_detail(await services.documents.get(principal, document_id))


@router.patch("/{document_id}", response_model=DocumentDetailOut)
async def update_document(
    document_id: UUID, body: DocumentUpdate, principal: CurrentPrincipal, services: CurrentServices
) -> DocumentDetailOut:
    fields = body.model_fields_set
    await services.documents.update(
        principal,
        document_id,
        UpdateDocument(
            version=body.version,
            title=body.title if "title" in fields and body.title is not None else UNSET,
            category=Category(body.category) if "category" in fields and body.category else UNSET,
            department_id=body.department_id if "department_id" in fields else UNSET,
            acl=[(AclPrincipalType(a.principal_type), a.principal_id) for a in body.acl]
            if "acl" in fields and body.acl is not None
            else UNSET,
        ),
    )
    return DocumentDetailOut.from_detail(await services.documents.get(principal, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    principal: CurrentPrincipal,
    services: CurrentServices,
    version: Annotated[int, Query(ge=1)],
) -> None:
    await services.documents.delete(principal, document_id, version=version)


@router.get("/{document_id}/versions", response_model=list[VersionOut])
async def list_versions(
    document_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> list[VersionOut]:
    return [VersionOut.from_domain(v) for v in await services.documents.versions(principal, document_id)]


@router.post("/{document_id}/versions", response_model=VersionOut, status_code=status.HTTP_201_CREATED)
async def add_version(
    document_id: UUID,
    principal: CurrentPrincipal,
    services: CurrentServices,
    settings: CurrentSettings,
    file: Annotated[UploadFile, File()],
) -> VersionOut:
    async with spooled(file, max_mb=settings.max_upload_mb) as upload:
        version = await services.documents.add_version(principal, document_id, upload)
    return VersionOut.from_domain(version)


@router.get("/{document_id}/versions/{version_no}", response_model=VersionDetailOut)
async def get_version(
    document_id: UUID, version_no: int, principal: CurrentPrincipal, services: CurrentServices
) -> VersionDetailOut:
    return VersionDetailOut.from_detail(
        await services.documents.version_detail(principal, document_id, version_no)
    )


@router.get("/{document_id}/versions/{version_no}/download", response_class=StreamingResponse)
async def download_version(
    document_id: UUID, version_no: int, principal: CurrentPrincipal, services: CurrentServices
) -> StreamingResponse:
    _, version, stream = await services.documents.download(principal, document_id, version_no)
    filename = quote(version.original_filename)
    return StreamingResponse(
        stream,
        media_type=version.mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Length": str(version.size_bytes),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{document_id}/versions/{version_no}/retire", response_model=VersionOut)
async def retire_version(
    document_id: UUID, version_no: int, principal: CurrentPrincipal, services: CurrentServices
) -> VersionOut:
    return VersionOut.from_domain(await services.documents.retire(principal, document_id, version_no))


@router.post("/{document_id}/versions/{version_no}/review", response_model=VersionOut)
async def review_version(
    document_id: UUID, version_no: int, body: ReviewIn, principal: CurrentPrincipal, services: CurrentServices
) -> VersionOut:
    version = await services.documents.review(
        principal,
        document_id,
        version_no,
        ReviewDecision(action=ReviewAction(body.action), comment=body.comment),
    )
    return VersionOut.from_domain(version)


@router.post(
    "/{document_id}/versions/{version_no}/reprocess",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_version(
    document_id: UUID, version_no: int, principal: CurrentPrincipal, services: CurrentServices
) -> JobOut:
    return JobOut.from_domain(await services.documents.reprocess(principal, document_id, version_no))
