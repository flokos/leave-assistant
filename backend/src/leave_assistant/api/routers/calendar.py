"""Calendar connections, sync history and reconciliation (doc 09 §2.7, doc 10)."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices, CurrentSettings
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.calendar import (
    ConnectionOut,
    ConnectionsOut,
    ReconciliationItemOut,
    ReconciliationOut,
    StartIn,
    StartOut,
    SyncJobOut,
    SyncJobsOut,
)
from leave_assistant.domain.calendar.model import ConnectionKind

router = APIRouter(prefix="/calendar", tags=["calendar"], route_class=IdempotentRoute)
callback_router = APIRouter(prefix="/calendar", tags=["calendar"])  # browser redirect: no bearer, no key


@router.get("/connections", response_model=ConnectionsOut)
async def list_connections(principal: CurrentPrincipal, services: CurrentServices) -> ConnectionsOut:
    items = await services.calendar_connections.list(principal)
    return ConnectionsOut(
        provider=services.calendar_connections.provider_name,
        items=[ConnectionOut.from_domain(c) for c in items],
    )


@router.post("/connections/google/start", response_model=StartOut)
async def start(body: StartIn, principal: CurrentPrincipal, services: CurrentServices) -> StartOut:
    url = await services.calendar_connections.start(principal, ConnectionKind(body.kind))
    return StartOut(authorization_url=url)


@callback_router.get("/connections/google/callback", include_in_schema=True, response_class=RedirectResponse)
async def callback(
    services: CurrentServices,
    settings: CurrentSettings,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Completes the consent (identity from the stored state) and sends the browser back to the SPA."""
    result = await services.calendar_connections.callback(state=state, code=code, error=error)
    query = {"status": result.status}
    if result.reason:
        query["reason"] = result.reason
    if result.kind:
        query["kind"] = result.kind.value
    return RedirectResponse(
        f"{settings.public_base_url.rstrip('/')}/calendar?{urlencode(query)}", status_code=303
    )


@router.delete("/connections/{connection_id}", status_code=204)
async def revoke(connection_id: UUID, principal: CurrentPrincipal, services: CurrentServices) -> Response:
    await services.calendar_connections.revoke(principal, connection_id)
    return Response(status_code=204)


@router.get("/sync-jobs", response_model=SyncJobsOut)
async def sync_jobs(
    principal: CurrentPrincipal,
    services: CurrentServices,
    status: Literal["pending", "running", "succeeded", "parked", "dead"] | None = None,
    request_id: UUID | None = None,
) -> SyncJobsOut:
    views = await services.calendar_sync.list_views(principal, status=status, request_id=request_id)
    return SyncJobsOut(items=[SyncJobOut.from_view(v) for v in views])


@router.post("/sync-jobs/{job_id}/retry", response_model=SyncJobOut)
async def retry(job_id: UUID, principal: CurrentPrincipal, services: CurrentServices) -> SyncJobOut:
    job = await services.calendar_sync.retry(principal, job_id)
    views = await services.calendar_sync.list_views(principal, status=None, request_id=job.request_id)
    view = next(v for v in views if v.job.id == job_id)
    return SyncJobOut.from_view(view)


@router.post("/reconcile", response_model=ReconciliationOut)
async def reconcile(principal: CurrentPrincipal, services: CurrentServices) -> ReconciliationOut:
    return ReconciliationOut.from_report(await services.calendar_reconcile.run_for(principal))


@router.get("/reconciliation", response_model=ReconciliationOut)
async def reconciliation(principal: CurrentPrincipal, services: CurrentServices) -> ReconciliationOut:
    return ReconciliationOut.from_report(await services.calendar_reconcile.latest(principal))


@router.post("/reconciliation/items/{item_id}/{action}", response_model=ReconciliationItemOut)
async def act(
    item_id: UUID,
    action: Literal["relink", "ignore", "delete-orphan"],
    principal: CurrentPrincipal,
    services: CurrentServices,
) -> ReconciliationItemOut:
    return ReconciliationItemOut.from_domain(
        await services.calendar_reconcile.act(principal, item_id, action)
    )
