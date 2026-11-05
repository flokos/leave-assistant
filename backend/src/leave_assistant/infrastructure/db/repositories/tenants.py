from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.domain.tenancy import Locale, Tenant, TenantSettings, TenantStatus
from leave_assistant.infrastructure.db.models import TenantRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _to_domain(row: TenantRow) -> Tenant:
    return Tenant(
        id=row.id,
        slug=row.slug,
        name=row.name,
        status=TenantStatus(row.status),
        default_locale=Locale(row.default_locale),
        timezone=row.timezone,
        settings=TenantSettings.from_dict(row.settings),
        keycloak_group_id=row.keycloak_group_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply(row: TenantRow, tenant: Tenant) -> None:
    row.slug = tenant.slug
    row.name = tenant.name
    row.status = tenant.status.value
    row.default_locale = tenant.default_locale.value
    row.timezone = tenant.timezone
    row.settings = tenant.settings.to_dict()
    row.keycloak_group_id = tenant.keycloak_group_id
    row.created_at = tenant.created_at
    row.updated_at = tenant.updated_at


class SqlTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, tenant_id: UUID) -> Tenant | None:
        row = await self._s.get(TenantRow, tenant_id)
        return _to_domain(row) if row else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        row = (await self._s.execute(select(TenantRow).where(TenantRow.slug == slug))).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list(self, *, limit: int, after: tuple[datetime, UUID] | None) -> list[Tenant]:
        stmt = select(TenantRow).order_by(TenantRow.created_at, TenantRow.id).limit(limit)
        if after is not None:
            stmt = stmt.where(tuple_(TenantRow.created_at, TenantRow.id) > after)
        return [_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, tenant: Tenant) -> None:
        row = TenantRow(id=tenant.id)
        _apply(row, tenant)
        self._s.add(row)
        await flush_or_conflict(self._s)

    async def update(self, tenant: Tenant) -> None:
        row = await self._s.get(TenantRow, tenant.id)
        if row is None:
            raise LookupError(f"tenant {tenant.id} does not exist")
        _apply(row, tenant)
        await flush_or_conflict(self._s)
