"""`GET /platform/health` (doc 09 §2.9): queues, dead events and jobs, calendar parking, provider health — counts
only, never tenant business data."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from leave_assistant.api.deps import CurrentServices, PlatformAdmin

router = APIRouter(prefix="/platform/health", tags=["platform"])


@router.get("")
async def platform_health(principal: PlatformAdmin, services: CurrentServices) -> dict[str, Any]:
    return await services.ops.platform_health()
