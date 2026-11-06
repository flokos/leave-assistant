"""Internal API for the automation principal (n8n; doc 06 §5, M8 decisions 8–10).

Role `automation` only (any other principal → 403, audited). nginx answers 404 for this prefix, so it is reachable
only on the compose network (`http://api:8000`). The operations are idempotent by design (leases, dedupe keys,
per-id delivery state), so no `Idempotency-Key` is required here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices
from leave_assistant.application.errors import AuthorizationError
from leave_assistant.domain.tenancy import Principal


async def require_automation(principal: CurrentPrincipal) -> Principal:
    if not principal.is_automation:
        raise AuthorizationError("service accounts only", action="internal.call")
    return principal


Automation = Annotated[Principal, Depends(require_automation)]
router = APIRouter(prefix="/internal", tags=["internal"])


class EventOut(BaseModel):
    id: UUID
    tenant_id: UUID
    type: str
    payload: dict[str, Any]
    created_at: datetime
    attempts: int


class EventsOut(BaseModel):
    items: list[EventOut]


class IdsIn(BaseModel):
    ids: list[UUID] = Field(max_length=500)


class CountOut(BaseModel):
    count: int


class ReminderIn(BaseModel):
    tenant_id: UUID
    type: Literal["document.processing_stalled", "document.review_reminder", "retention.review_reminder"]
    subject_id: UUID | None = None
    older_than_hours: float | None = Field(default=None, ge=0)


class RenderOut(BaseModel):
    notification_id: UUID
    tenant_id: UUID
    type: str
    send: bool
    status: str
    to: str | None
    from_address: str | None
    subject: str | None
    text: str | None
    html: str | None


class DeliveryIn(BaseModel):
    tenant_id: UUID
    status: Literal["sent", "failed", "skipped"]


class ReindexIn(BaseModel):
    tenant_id: UUID | None = None


class MaintenanceIn(BaseModel):
    tasks: list[str] | None = None


class AutomationErrorIn(BaseModel):
    workflow: str = Field(max_length=200)
    node: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=2000)
    execution_id: str | None = Field(default=None, max_length=100)
    mode: str | None = Field(default=None, max_length=50)


@router.get("/events", response_model=EventsOut)
async def events(
    principal: Automation, services: CurrentServices, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> EventsOut:
    """Leases undelivered events (5 min); unacknowledged ones come back after the lease, up to 10 times."""
    leased = await services.ops.lease_events(principal, limit=limit)
    return EventsOut(
        items=[
            EventOut(
                id=e.id,
                tenant_id=e.tenant_id,
                type=e.type,
                payload=e.payload,
                created_at=e.created_at,
                attempts=e.attempts,
            )
            for e in leased
        ]
    )


@router.post("/events/ack", response_model=CountOut)
async def ack(body: IdsIn, principal: Automation, services: CurrentServices) -> CountOut:
    return CountOut(count=await services.ops.ack_events(principal, body.ids))


@router.post("/events/dead", response_model=CountOut)
async def dead(body: IdsIn, principal: Automation, services: CurrentServices) -> CountOut:
    return CountOut(count=await services.ops.ack_events(principal, body.ids, dead=True))


@router.get("/documents/pending-review")
async def pending_review(
    principal: Automation, services: CurrentServices, older_than_hours: Annotated[float, Query(ge=0)] = 48
) -> dict[str, Any]:
    return {"items": await services.ops.pending_review(principal, older_than_hours=older_than_hours)}


@router.get("/documents/{version_id}/processing")
async def processing(
    version_id: UUID, tenant_id: UUID, principal: Automation, services: CurrentServices
) -> dict[str, Any]:
    return await services.ops.processing_status(principal, tenant_id, version_id)


@router.get("/index/status")
async def index_status(principal: Automation, services: CurrentServices) -> dict[str, Any]:
    items = await services.ops.index_status(principal)
    return {"items": items, "stale": any(i["stale_generation"] for i in items)}


@router.post("/index/reindex")
async def reindex(body: ReindexIn, principal: Automation, services: CurrentServices) -> dict[str, int]:
    return await services.ops.reindex(principal, body.tenant_id)


@router.post("/retention/scan")
async def retention_scan(principal: Automation, services: CurrentServices) -> dict[str, int]:
    return await services.ops.retention_scan(principal)


@router.get("/retention/pending")
async def retention_pending(
    principal: Automation, services: CurrentServices, older_than_days: Annotated[float, Query(ge=0)] = 7
) -> dict[str, Any]:
    return {"items": await services.ops.retention_pending(principal, older_than_days=older_than_days)}


@router.post("/notifications")
async def create_notification(
    body: ReminderIn, principal: Automation, services: CurrentServices
) -> dict[str, Any]:
    return await services.ops.create_reminder(
        principal,
        tenant_id=body.tenant_id,
        type=body.type,
        subject_id=body.subject_id,
        older_than_hours=body.older_than_hours,
    )


@router.get("/notifications/{notification_id}/render", response_model=RenderOut)
async def render(
    notification_id: UUID, tenant_id: UUID, principal: Automation, services: CurrentServices
) -> RenderOut:
    r = await services.ops.render_email(principal, tenant_id, notification_id)
    return RenderOut(
        notification_id=r.notification_id,
        tenant_id=r.tenant_id,
        type=r.type,
        send=r.send,
        status=r.status,
        to=r.to,
        from_address=r.from_address,
        subject=r.subject,
        text=r.text,
        html=r.html,
    )


@router.post("/notifications/{notification_id}/delivery")
async def delivery(
    notification_id: UUID, body: DeliveryIn, principal: Automation, services: CurrentServices
) -> dict[str, Any]:
    return await services.ops.record_delivery(principal, body.tenant_id, notification_id, body.status)


@router.post("/calendar/retry-failed")
async def calendar_retry(principal: Automation, services: CurrentServices) -> dict[str, int]:
    return await services.ops.calendar_retry(principal)


@router.post("/calendar/reconcile")
async def calendar_reconcile(principal: Automation, services: CurrentServices) -> dict[str, Any]:
    return await services.ops.calendar_reconcile(principal)


@router.post("/maintenance/run")
async def maintenance(
    body: MaintenanceIn, principal: Automation, services: CurrentServices
) -> dict[str, Any]:
    return await services.ops.maintenance(principal, body.tasks)


@router.get("/health/providers")
async def provider_health(
    principal: Automation,
    services: CurrentServices,
    window_minutes: Annotated[int, Query(ge=5, le=1440)] = 60,
) -> dict[str, Any]:
    report = await services.ops.provider_health(principal, window_minutes=window_minutes)
    return {
        "healthy": report.healthy,
        "window_minutes": report.window_minutes,
        "providers": report.providers,
        "alert": report.alert,
    }


@router.post("/automation/errors", status_code=204)
async def automation_error(body: AutomationErrorIn, principal: Automation, services: CurrentServices) -> None:
    await services.ops.record_automation_error(principal, body.model_dump(exclude_none=True))
