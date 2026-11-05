"""Platform-level audit entries (doc 09 §2.9) — tenant rows are never visible here."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from leave_assistant.api.deps import CurrentServices, PlatformAdmin
from leave_assistant.api.schemas.audit import AuditEntryOut
from leave_assistant.api.schemas.common import PageOut
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
