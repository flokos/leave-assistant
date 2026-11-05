"""Approval endpoints (doc 09 §2.4)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.approvals import DecisionIn, PendingItemOut
from leave_assistant.api.schemas.leave import LeaveRequestOut
from leave_assistant.application.leave.commands import Decide

router = APIRouter(prefix="/approvals", tags=["approvals"], route_class=IdempotentRoute)


@router.get("/pending", response_model=list[PendingItemOut])
async def pending(principal: CurrentPrincipal, services: CurrentServices) -> list[PendingItemOut]:
    return [PendingItemOut.from_view(i) for i in await services.approvals.pending(principal)]


@router.get("/{step_id}", response_model=PendingItemOut)
async def get_step(step_id: UUID, principal: CurrentPrincipal, services: CurrentServices) -> PendingItemOut:
    return PendingItemOut.from_view(await services.approvals.get_step(principal, step_id))


@router.post("/{step_id}/approve", response_model=LeaveRequestOut)
async def approve(
    step_id: UUID, body: DecisionIn, principal: CurrentPrincipal, services: CurrentServices
) -> LeaveRequestOut:
    return LeaveRequestOut.from_detail(
        await services.approvals.approve(principal, step_id, Decide(body.version, body.comment))
    )


@router.post("/{step_id}/reject", response_model=LeaveRequestOut)
async def reject(
    step_id: UUID, body: DecisionIn, principal: CurrentPrincipal, services: CurrentServices
) -> LeaveRequestOut:
    return LeaveRequestOut.from_detail(
        await services.approvals.reject(principal, step_id, Decide(body.version, body.comment))
    )
