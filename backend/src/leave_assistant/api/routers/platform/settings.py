"""Platform configuration (doc 09 §2.9, M10): the defaults a new company gets."""

from __future__ import annotations

from fastapi import APIRouter

from leave_assistant.api.deps import CurrentServices, PlatformAdmin
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.tenants import PlatformSettingsOut, SettingsUpdate
from leave_assistant.application.tenancy.settings import UpdateSettings
from leave_assistant.domain.tenancy import Locale

router = APIRouter(prefix="/platform/settings", tags=["platform"], route_class=IdempotentRoute)


@router.get("", response_model=PlatformSettingsOut)
async def get_platform_settings(principal: PlatformAdmin, services: CurrentServices) -> PlatformSettingsOut:
    return PlatformSettingsOut.from_domain(await services.settings.get_platform(principal))


@router.put("", response_model=PlatformSettingsOut, openapi_extra=NO_IDEMPOTENCY)
async def update_platform_settings(
    body: SettingsUpdate, principal: PlatformAdmin, services: CurrentServices
) -> PlatformSettingsOut:
    cmd = UpdateSettings(
        default_locale=Locale(body.default_locale) if body.default_locale else None,
        timezone=body.timezone,
        settings=body.settings,
    )
    return PlatformSettingsOut.from_domain(await services.settings.update_platform(principal, cmd))
