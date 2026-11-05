"""`GET /me` — the authenticated principal (doc 09 §2.1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices, CurrentSettings
from leave_assistant.api.schemas.me import MeResponse
from leave_assistant.application.identity.me import load_me

router = APIRouter(tags=["identity"])


@router.get("/me", response_model=MeResponse, summary="Current principal, tenant, employee and feature flags")
async def get_me(
    principal: CurrentPrincipal, services: CurrentServices, settings: CurrentSettings
) -> MeResponse:
    view = await load_me(principal, services.uow_factory)
    flags: dict[str, Any] = {
        "retrieval_backend": settings.retrieval_backend,
        "calendar_provider": settings.calendar_provider,
        "langfuse_enabled": settings.langfuse_enabled,
        "injection_llm_judge": settings.injection_llm_judge,
        "worker_mode": settings.worker_mode,
    }
    return MeResponse.from_view(view, flags)
