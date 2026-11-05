"""ADR-0003 / FR-TEN-02/03: forced RLS, transaction-scoped context, no leakage across the connection pool."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.container import Services
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.seed.run import run_seed

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded(db_services: Services) -> dict[str, UUID]:
    """Both companies through the real SQL unit of work (Keycloak faked)."""
    report = await run_seed(db_services, "pw")
    assert len(report.created) == 86 and not report.unchanged
    async with db_services.uow_factory(
        TenantContext(tenant_id=None, user_id="seed", platform_admin=True)
    ) as uow:
        a = await uow.tenants.get_by_slug("aegean")
        b = await uow.tenants.get_by_slug("brightwater")
    assert a and b
    return {"a": a.id, "b": b.id}


async def test_every_tenant_table_has_forced_policy(owner_engine: AsyncEngine) -> None:
    """Meta-test (doc 07 §2): every app/audit table with a tenant_id column is RLS-enabled, forced and has the policy."""
    sql = text(
        """
        SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity,
               EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = n.nspname AND p.tablename = c.relname
                       AND p.policyname = 'tenant_isolation') AS has_policy
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
        WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('app', 'audit')
        """
    )
    async with owner_engine.connect() as conn:
        rows = (await conn.execute(sql)).all()
    assert {(r[0], r[1]) for r in rows} == {
        ("app", "departments"),
        ("app", "employees"),
        ("app", "employee_roles"),
        ("app", "leave_types"),
        ("app", "leave_policies"),
        ("app", "public_holidays"),
        ("app", "approval_rules"),
        ("app", "request_counters"),
        ("app", "leave_requests"),
        ("app", "leave_request_events"),
        ("app", "approval_steps"),
        ("app", "leave_ledger_entries"),
        ("app", "notifications"),
        ("app", "integration_events"),
        ("audit", "audit_log"),
    }
    for schema, table, enabled, forced, has_policy in rows:
        assert enabled and forced and has_policy, f"{schema}.{table} is not fully protected"


async def test_runtime_role_cannot_bypass_rls(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert row == (False, False)


async def test_rls_blocks_cross_tenant_select_without_context(
    engine: AsyncEngine, seeded: dict[str, UUID]
) -> None:
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM app.employees"))).scalar_one()
        setting = (await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar_one()
    assert count == 0 and setting in ("", None)


async def test_context_shows_only_own_tenant(
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory, seeded: dict[str, UUID]
) -> None:
    for key, expected in (("a", 10), ("b", 8)):
        async with sql_uow_factory(TenantContext(tenant_id=seeded[key], user_id="u")) as uow:
            tenants = await uow.session.execute(text("SELECT DISTINCT tenant_id FROM app.employees"))
            count = await uow.session.execute(text("SELECT count(*) FROM app.employees"))
        assert [r[0] for r in tenants.all()] == [seeded[key]]
        assert count.scalar_one() == expected


async def test_set_local_does_not_leak_across_pooled_connections(
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory, engine: AsyncEngine, seeded: dict[str, UUID]
) -> None:
    """100 interleaved units of work from two tenants on a pool of 2 connections (doc 11 §3)."""

    async def one(i: int) -> tuple[str, str, set[UUID]]:
        key = "a" if i % 2 == 0 else "b"
        async with sql_uow_factory(TenantContext(tenant_id=seeded[key], user_id=f"user-{i}")) as uow:
            await asyncio.sleep(0.001 * (i % 3))
            setting = (
                await uow.session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            ).scalar_one()
            seen = {
                r[0] for r in (await uow.session.execute(text("SELECT tenant_id FROM app.employees"))).all()
            }
            if i % 5 == 0:
                await uow.commit()
        return key, setting, seen

    results = await asyncio.gather(*(one(i) for i in range(100)))
    for key, setting, seen in results:
        assert setting == str(seeded[key])
        assert seen == {seeded[key]}

    async with engine.connect() as conn:  # the pool's connections carry nothing after the burst
        assert (await conn.execute(text("SELECT count(*) FROM app.employees"))).scalar_one() == 0


async def test_with_check_rejects_rows_of_another_tenant(
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory, seeded: dict[str, UUID]
) -> None:
    async with sql_uow_factory(TenantContext(tenant_id=seeded["a"], user_id="u")) as uow:
        with pytest.raises(ProgrammingError, match="row-level security"):
            await uow.session.execute(
                text(
                    "INSERT INTO app.departments (id, tenant_id, code, name, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :tenant, 'X', 'x', now(), now())"
                ),
                {"tenant": seeded["b"]},
            )


PROBE = text(
    "SELECT action FROM audit.audit_log WHERE action IN ('tenant.row', 'platform.row', 'automation.row', 'sneaky') "
    "ORDER BY action"
)


async def test_audit_policies(sql_uow_factory: SqlAlchemyUnitOfWorkFactory, seeded: dict[str, UUID]) -> None:
    insert = text(
        "INSERT INTO audit.audit_log (id, tenant_id, occurred_at, actor_kind, actor_id, actor_roles, action, "
        "resource_type, outcome, details) VALUES (gen_random_uuid(), :tenant, now(), 'user', :actor, '{}', :action, 'x', 'success', '{}')"
    )
    tenant_ctx = TenantContext(tenant_id=seeded["a"], user_id="ua", roles=("hr_admin",))
    platform_ctx = TenantContext(tenant_id=None, user_id="padmin", platform_admin=True)
    automation_ctx = TenantContext(tenant_id=None, user_id="svc", roles=("automation",))

    async with sql_uow_factory(tenant_ctx) as uow:
        await uow.session.execute(insert, {"tenant": seeded["a"], "actor": "ua", "action": "tenant.row"})
        with pytest.raises(
            ProgrammingError, match="row-level security"
        ):  # a tenant context cannot write platform rows
            await uow.session.execute(insert, {"tenant": None, "actor": "ua", "action": "sneaky"})
    async with sql_uow_factory(tenant_ctx) as uow:
        await uow.session.execute(insert, {"tenant": seeded["a"], "actor": "ua", "action": "tenant.row"})
        await uow.commit()
    async with sql_uow_factory(platform_ctx) as uow:
        await uow.session.execute(insert, {"tenant": None, "actor": "padmin", "action": "platform.row"})
        await uow.commit()
    async with sql_uow_factory(automation_ctx) as uow:
        await uow.session.execute(insert, {"tenant": None, "actor": "svc", "action": "automation.row"})
        await uow.commit()

    async with sql_uow_factory(tenant_ctx) as uow:
        rows = (await uow.session.execute(PROBE)).scalars().all()
    assert rows == ["tenant.row"]
    async with sql_uow_factory(platform_ctx) as uow:
        rows = (await uow.session.execute(PROBE)).scalars().all()
    assert rows == ["automation.row", "platform.row"]
    async with sql_uow_factory(automation_ctx) as uow:  # not a platform admin: sees nothing
        rows = (await uow.session.execute(PROBE)).scalars().all()
    assert rows == []
    async with sql_uow_factory(tenant_ctx) as uow:
        with pytest.raises(ProgrammingError, match="permission denied"):  # append-only for the runtime role
            await uow.session.execute(text("DELETE FROM audit.audit_log"))
    async with sql_uow_factory(tenant_ctx) as uow:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await uow.session.execute(text("UPDATE audit.audit_log SET action = 'x'"))
