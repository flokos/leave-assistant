"""M10 against PostgreSQL: the `platform.settings` row (migration 0008) and its grants, defaults applied to a new
company, company settings, and the audit CSV export with RLS (a company sees only its own rows)."""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.audit.export import export_audit
from leave_assistant.application.container import Services
from leave_assistant.application.ports import AuditFilter, PlatformSettings
from leave_assistant.application.tenancy.commands import CreateTenant
from leave_assistant.application.tenancy.settings import UpdateSettings
from leave_assistant.domain.tenancy import Employee, Locale, Principal, Tenant
from tests.conftest import principal_for

pytestmark = pytest.mark.integration


async def test_platform_settings_row_grants_and_defaults_for_new_companies(
    db_services: Services, owner_engine: AsyncEngine, engine: AsyncEngine, platform_admin: Principal
) -> None:
    async with owner_engine.connect() as conn:
        row = (await conn.execute(text("SELECT id, default_locale, timezone FROM platform.settings"))).one()
        grants = {
            r[0]
            for r in await conn.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_schema = 'platform' AND table_name = 'settings' AND grantee = 'app_rls'"
                )
            )
        }
    assert row == (1, "en", "Europe/Athens") and grants == {"SELECT", "UPDATE"}
    try:
        updated = await db_services.settings.update_platform(
            platform_admin,
            UpdateSettings(
                default_locale=Locale.EL, settings={"proposal_ttl_minutes": 45, "work_week": [0, 1, 2, 3]}
            ),
        )
        assert updated.tenant_settings.proposal_ttl_minutes == 45
        assert (await db_services.settings.get_platform(platform_admin)).default_locale is Locale.EL
        tenant = await db_services.provisioning.create_tenant(
            platform_admin,
            CreateTenant(slug="kyklades", name="Kyklades Ferries", settings={"max_upload_mb": 20}),
        )
        assert tenant.default_locale is Locale.EL and tenant.timezone == "Europe/Athens"
        assert tenant.settings.proposal_ttl_minutes == 45 and tenant.settings.work_week == (0, 1, 2, 3)
        assert tenant.settings.max_upload_mb == 20  # the creation's own value wins
    finally:  # the platform row is not truncated between tests: put the migration's values back
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE platform.settings SET default_locale = 'en', timezone = 'Europe/Athens', "
                    "tenant_settings = CAST(:s AS jsonb), updated_by = 'migration' WHERE id = 1"
                ),
                {
                    "s": '{"work_week": [0, 1, 2, 3, 4], "max_upload_mb": 10, "conversation_retention_days": 90, '
                    '"proposal_ttl_minutes": 15, "allow_hr_fallback": true}'
                },
            )
    assert PlatformSettings().default_locale is Locale.EN


async def test_company_settings_and_audit_export_stay_within_the_tenant(
    db_services: Services, seeded: dict[str, object], platform_admin: Principal
) -> None:
    aegean: Tenant = seeded["aegean"]  # type: ignore[assignment]
    admins = {
        slug: principal_for(seeded["employees"][slug][number])  # type: ignore[index]
        for slug, number in (("aegean", "AEG-001"), ("brightwater", "BWS-001"))
    }
    changed = await db_services.settings.update_company(
        admins["aegean"], UpdateSettings(timezone="Europe/Athens", settings={"allow_hr_fallback": False})
    )
    assert changed.id == aegean.id and changed.settings.allow_hr_fallback is False
    reread = await db_services.settings.get_company(admins["aegean"])
    assert reread.settings.allow_hr_fallback is False
    await db_services.settings.update_company(admins["brightwater"], UpdateSettings(timezone="Europe/Dublin"))

    export = await export_audit(
        admins["aegean"],
        db_services.uow_factory,
        db_services.audit,
        filters=AuditFilter(action="admin.tenant.settings_changed"),
        platform=False,
    )
    rows = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))
    assert export.rows == 1 and len(rows) == 2 and '"allow_hr_fallback": false' in rows[1][-1]
    assert "Europe/Dublin" not in export.content.decode("utf-8-sig")  # Brightwater's row is invisible (RLS)

    seeded_admin: Employee = seeded["employees"]["aegean"]["AEG-001"]  # type: ignore[index]
    exported = await export_audit(
        principal_for(seeded_admin),
        db_services.uow_factory,
        db_services.audit,
        filters=AuditFilter(action="audit.exported"),
        platform=False,
    )
    assert exported.rows == 1  # the first export was itself audited
    platform = await export_audit(
        platform_admin, db_services.uow_factory, db_services.audit, filters=AuditFilter(), platform=True
    )
    assert "admin.tenant.settings_changed" not in platform.content.decode("utf-8-sig")
