"""`platform.settings` (M10): a single row readable by every context, written by platform administrators."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.ports import PlatformSettings
from leave_assistant.domain.tenancy import Locale, TenantSettings
from leave_assistant.infrastructure.db.models import PlatformSettingsRow


class SqlPlatformSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self) -> PlatformSettings:
        row = await self._s.get(PlatformSettingsRow, 1)
        if row is None:
            return PlatformSettings()
        return PlatformSettings(
            default_locale=Locale(row.default_locale),
            timezone=row.timezone,
            tenant_settings=TenantSettings.from_dict(row.tenant_settings),
            updated_by=row.updated_by,
        )

    async def update(self, settings: PlatformSettings) -> None:
        row = await self._s.get(PlatformSettingsRow, 1)
        if row is None:
            row = PlatformSettingsRow(id=1)
            self._s.add(row)
        row.default_locale = settings.default_locale.value
        row.timezone = settings.timezone
        row.tenant_settings = settings.tenant_settings.to_dict()
        row.updated_by = settings.updated_by
        row.updated_at = func.now()
        await self._s.flush()
