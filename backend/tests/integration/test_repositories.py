"""Repositories and the unit of work against PostgreSQL; schema matches the models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine as create_sync_engine

from leave_assistant.application.container import Services
from leave_assistant.application.errors import ConflictError
from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.application.ports import AuditFilter, EmployeeFilter
from leave_assistant.application.tenancy.commands import CreateEmployee
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Locale, Role
from leave_assistant.infrastructure.db.models import Base
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.seed.run import run_seed, tenant_admin_principal
from tests.integration.conftest import OWNER_URL

pytestmark = pytest.mark.integration


def test_models_match_migrated_schema(migrated_database: str) -> None:
    engine = create_sync_engine(OWNER_URL)
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "include_schemas": True,
                "include_name": lambda name, type_, parent: (
                    name in ("platform", "app", "audit")
                    if type_ == "schema"
                    else name not in {"alembic_version", "leave_balances"}
                ),
                "include_object": lambda obj, name, type_, reflected, compare_to: (
                    not (
                        type_ == "index"
                        and name
                        in {
                            "ex_leave_requests_no_overlap",
                            "ix_document_chunks_embedding",
                            "ix_document_chunks_tsv_el",
                            "ix_document_chunks_tsv_en",
                            "ix_document_chunks_tsv_simple",
                            "uq_jobs_active_dedupe",
                            "uq_retention_items_open",
                            "ix_action_proposals_pending_expiry",
                            "uq_leave_requests_idempotency",
                            "ix_leave_request_events_proposal",
                        }
                    )  # raw-SQL indexes (exclusion, HNSW, GIN, partial unique)
                ),
            },
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == [], diff


async def test_seed_and_repositories_round_trip(db_services: Services) -> None:
    report = await run_seed(db_services, "pw")
    assert report.changed >= 34 and not report.unchanged
    second = await run_seed(db_services, "pw")
    assert second.changed == 0 and len(second.unchanged) == 109

    async with db_services.uow_factory(
        TenantContext(tenant_id=None, user_id="seed", platform_admin=True)
    ) as uow:
        tenants = await uow.tenants.list(limit=10, after=None)
        assert [t.slug for t in tenants] == ["aegean", "brightwater"]
        aegean = tenants[0]
        audit_rows = await uow.audit.list(filters=AuditFilter(platform_only=True), limit=50, after=None)
        assert {e.action for e in audit_rows} == {"admin.tenant.created"}

    admin = tenant_admin_principal(aegean)
    page = await db_services.employees.list(
        admin, filters=EmployeeFilter(query="παπαδ"), limit=None, cursor=None
    )
    assert [e.employee_number for e in page.items] == ["AEG-003"]
    manager = page.items[0]
    assert manager.roles == {Role.MANAGER, Role.EMPLOYEE}
    departments = {d.code: d for d in await db_services.departments.list(admin)}
    assert departments["OPS"].manager_employee_id == manager.id

    async with db_services.uow_factory(TenantContext.for_principal(admin)) as uow:
        by_sub = await uow.employees.get_by_sub(manager.keycloak_sub)
        assert by_sub is not None and by_sub.email == "nikos.papadakis@aegean-logistics.example"
        assert await uow.employees.count_in_department(departments["OPS"].id) == 4
        audit = await uow.audit.list(
            filters=AuditFilter(action="admin.employee.created"), limit=100, after=None
        )
        assert len(audit) == 10 and all(e.tenant_id == aegean.id for e in audit)

    with pytest.raises(ConflictError):  # unique (tenant, employee_number) through the unit of work
        await db_services.employees.create(
            admin,
            CreateEmployee(
                email="dup@aegean-logistics.example",
                first_name="D",
                last_name="U",
                employee_number="AEG-003",
                department_id=departments["OPS"].id,
                locale=Locale.EL,
                hire_date=datetime.now(UTC).date(),
                roles=frozenset({Role.EMPLOYEE}),
                password="pw",
                temporary_password=False,
            ),
        )


async def test_idempotency_repository(sql_uow_factory: SqlAlchemyUnitOfWorkFactory) -> None:
    now = datetime.now(UTC)
    record = IdempotencyRecord(
        id=uuid4(),
        tenant_id=None,
        user_id="u1",
        route="POST /x",
        key="k",
        request_hash="h",
        response_status=201,
        response_body={"ok": True},
        created_at=now,
        expires_at=now - timedelta(seconds=1),
    )
    ctx = TenantContext(tenant_id=None, user_id="u1")
    async with sql_uow_factory(ctx) as uow:
        await uow.idempotency.add(record)
        await uow.commit()
    async with sql_uow_factory(ctx) as uow:
        assert (await uow.idempotency.get("u1", "POST /x", "k")) is not None
        assert (await uow.idempotency.get("u2", "POST /x", "k")) is None
        assert await uow.idempotency.purge_expired(now) == 1
        await uow.commit()
    async with sql_uow_factory(ctx) as uow:
        assert (await uow.idempotency.get("u1", "POST /x", "k")) is None


async def test_uow_rolls_back_without_commit(
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory, db_services: Services
) -> None:
    await run_seed(db_services, "pw")
    async with sql_uow_factory(TenantContext(tenant_id=None, user_id="x", platform_admin=True)) as uow:
        tenant = await uow.tenants.get_by_slug("aegean")
        assert tenant is not None
        tenant.name = "Renamed without commit"
        await uow.tenants.update(tenant)
    async with sql_uow_factory(TenantContext(tenant_id=None, user_id="x")) as uow:
        fresh = await uow.tenants.get_by_slug("aegean")
    assert fresh is not None and fresh.name == "Aegean Logistics S.A."


async def test_uow_entered_twice_is_independent(sql_uow_factory: SqlAlchemyUnitOfWorkFactory) -> None:
    ctx = TenantContext(tenant_id=UUID(int=1), user_id="x")
    async with sql_uow_factory(ctx) as first, sql_uow_factory(ctx) as second:
        assert first.session is not second.session
