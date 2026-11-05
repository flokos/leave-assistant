"""In-app notification records (doc 09 §2.7); rendering/e-mail/SSE arrive in M8."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.api.schemas.notifications import NotificationOut, ReadAllOut

router = APIRouter(prefix="/notifications", tags=["notifications"], route_class=IdempotentRoute)


@router.get("", response_model=PageOut[NotificationOut])
async def list_notifications(
    principal: CurrentPrincipal,
    services: CurrentServices,
    unread: bool = False,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[NotificationOut]:
    page = await services.notifications.list(principal, unread_only=unread, limit=limit, cursor=cursor)
    return PageOut(items=[NotificationOut.from_domain(n) for n in page.items], next_cursor=page.next_cursor)


@router.post("/read-all", response_model=ReadAllOut)
async def read_all(principal: CurrentPrincipal, services: CurrentServices) -> ReadAllOut:
    return ReadAllOut(marked=await services.notifications.mark_all_read(principal))


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> NotificationOut:
    return NotificationOut.from_domain(await services.notifications.mark_read(principal, notification_id))
