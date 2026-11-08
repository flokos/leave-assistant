"""Company settings (doc 09 §2.8, M10): locale, timezone, work week, retention of conversations, card lifetime,
HR fallback. Upload limits belong to the security policy (`/admin/security-policy`)."""

from __future__ import annotations

from fastapi import APIRouter

from leave_assistant.api.deps import CompanyAdmin, CurrentServices
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.tenants import CompanySettingsOut, SettingsUpdate
from leave_assistant.application.tenancy.settings import UpdateSettings
from leave_assistant.domain.tenancy import Locale

router = APIRouter(prefix="/admin/settings", tags=["admin"], route_class=IdempotentRoute)


@router.get("", response_model=CompanySettingsOut)
async def get_company_settings(principal: CompanyAdmin, services: CurrentServices) -> CompanySettingsOut:
    return CompanySettingsOut.from_domain(await services.settings.get_company(principal))


@router.put("", response_model=CompanySettingsOut, openapi_extra=NO_IDEMPOTENCY)
async def update_company_settings(
    body: SettingsUpdate, principal: CompanyAdmin, services: CurrentServices
) -> CompanySettingsOut:
    cmd = UpdateSettings(
        default_locale=Locale(body.default_locale) if body.default_locale else None,
        timezone=body.timezone,
        settings=body.settings,
    )
    return CompanySettingsOut.from_domain(await services.settings.update_company(principal, cmd))
