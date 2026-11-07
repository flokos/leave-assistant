"""Platform and company settings (M10, doc 09 §2.8/§2.9, doc 07 §4.1).

- Platform administrators edit `platform.settings`: the default locale, timezone and per-tenant settings a new
  company gets when its creation leaves them out (`ProvisioningService.ensure_tenant`).
- Company administrators edit their own company's locale, timezone and `tenants.settings`, except `max_upload_mb`:
  uploads have been limited by the company's security policy since M5 (doc 07 amendment).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.errors import InvalidInputError
from leave_assistant.application.leave.common import load_tenant
from leave_assistant.application.ports import Clock, PlatformSettings, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.application.tenancy.validation import validate_timezone
from leave_assistant.domain.tenancy import Locale, Principal, Tenant, TenantSettings

COMPANY_EDITABLE = frozenset(
    {"work_week", "conversation_retention_days", "proposal_ttl_minutes", "allow_hr_fallback"}
)


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    default_locale: Locale | None = None
    timezone: str | None = None
    settings: dict[str, Any] | None = None


class SettingsService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, audit: AuditService) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._audit = audit

    # --- platform --------------------------------------------------------------------------------------------
    async def get_platform(self, principal: Principal) -> PlatformSettings:
        require(principal, Action.MANAGE_PLATFORM_SETTINGS)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.platform_settings.get()

    async def update_platform(self, principal: Principal, cmd: UpdateSettings) -> PlatformSettings:
        require(principal, Action.MANAGE_PLATFORM_SETTINGS)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            current = await uow.platform_settings.get()
            updated = replace(
                current,
                default_locale=cmd.default_locale or current.default_locale,
                timezone=validate_timezone(cmd.timezone) if cmd.timezone else current.timezone,
                tenant_settings=_merged(current.tenant_settings, cmd.settings),
                updated_by=principal.user_id,
            )
            await uow.platform_settings.update(updated)
            await self._audit.record(
                uow,
                principal,
                "platform.settings.changed",
                resource_type="platform_settings",
                details=_changes(cmd),
            )
            await uow.commit()
        return updated

    # --- company ---------------------------------------------------------------------------------------------
    async def get_company(self, principal: Principal) -> Tenant:
        require(principal, Action.MANAGE_TENANT_SETTINGS)
        assert principal.tenant_id is not None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await load_tenant(uow, principal.tenant_id)

    async def update_company(self, principal: Principal, cmd: UpdateSettings) -> Tenant:
        require(principal, Action.MANAGE_TENANT_SETTINGS)
        assert principal.tenant_id is not None
        refused = sorted(set(cmd.settings or {}) - COMPANY_EDITABLE)
        if refused:
            raise InvalidInputError(
                "these settings are not editable by the company",
                errors=[{"loc": ["body", "settings", k], "msg": "not editable here"} for k in refused],
            )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            tenant = await load_tenant(uow, principal.tenant_id)
            if cmd.default_locale is not None:
                tenant.default_locale = cmd.default_locale
            if cmd.timezone is not None:
                tenant.timezone = validate_timezone(cmd.timezone)
            tenant.settings = _merged(tenant.settings, cmd.settings)
            tenant.updated_at = self._clock.now()
            await uow.tenants.update(tenant)
            await self._audit.record(
                uow,
                principal,
                "admin.tenant.settings_changed",
                resource_type="tenant",
                resource_id=tenant.id,
                details=_changes(cmd),
            )
            await uow.commit()
        return tenant


def _merged(current: TenantSettings, changes: dict[str, Any] | None) -> TenantSettings:
    return TenantSettings.from_dict({**current.to_dict(), **(changes or {})}) if changes else current


def _changes(cmd: UpdateSettings) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if cmd.default_locale is not None:
        out["default_locale"] = cmd.default_locale.value
    if cmd.timezone is not None:
        out["timezone"] = cmd.timezone
    if cmd.settings:
        out["settings"] = dict(cmd.settings)
    return out
