"""Platform-level audit entries (doc 09 §2.9) — tenant rows are never visible here."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response

from leave_assistant.api.deps import CurrentServices, PlatformAdmin
from leave_assistant.api.schemas.audit import AuditEntryOut
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.application.audit.export import export_audit
from leave_assistant.application.audit.queries import list_platform_audit
from leave_assistant.application.ports import AuditFilter

router = APIRouter(prefix="/platform/audit", tags=["platform"])


@router.get("", response_model=PageOut[AuditEntryOut])
async def platform_audit(
    principal: PlatformAdmin,
    services: CurrentServices,
    occurred_from: Annotated[datetime | None, Query(alias="from")] = None,
    occurred_to: Annotated[datetime | None, Query(alias="to")] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[AuditEntryOut]:
    page = await list_platform_audit(
        principal,
        services.uow_factory,
        filters=AuditFilter(occurred_from=occurred_from, occurred_to=occurred_to, action=action),
        limit=limit,
        cursor=cursor,
    )
    return PageOut(items=[AuditEntryOut.from_domain(e) for e in page.items], next_cursor=page.next_cursor)


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV, UTF-8 with BOM; at most 10,000 rows"}},
)
async def platform_audit_export(
    principal: PlatformAdmin,
    services: CurrentServices,
    occurred_from: Annotated[datetime | None, Query(alias="from")] = None,
    occurred_to: Annotated[datetime | None, Query(alias="to")] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
) -> Response:
    """M10: the filtered audit log as CSV; the export itself is audited (`audit.exported`)."""
    export = await export_audit(
        principal,
        services.uow_factory,
        services.audit,
        filters=AuditFilter(occurred_from=occurred_from, occurred_to=occurred_to, action=action),
        platform=True,
    )
    return Response(
        content=export.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit-platform.csv"',
            "X-Audit-Rows": str(export.rows),
            "X-Audit-Truncated": "true" if export.truncated else "false",
        },
    )
