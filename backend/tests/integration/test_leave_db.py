"""Leave domain against PostgreSQL: exclusion constraint, append-only ledger, balance view under RLS, concurrency, rollover."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.container import Services
from leave_assistant.application.errors import ConflictError
from leave_assistant.application.leave.commands import Decide, SubmitRequest
from leave_assistant.application.leave.errors import LeaveValidationError
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.ledger import EntryType
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.tenancy import Principal, Role, Tenant
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.seed.run import run_seed
from tests.conftest import principal_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded_leave(db_services: Services) -> dict[str, object]:
    """Both companies incl. the leave catalogue, grants and sample requests (through the real SQL path)."""
    report = await run_seed(db_services, "pw")
    assert not report.unchanged
    async with db_services.uow_factory(
        TenantContext(tenant_id=None, user_id="seed", platform_admin=True)
    ) as uow:
        aegean = await uow.tenants.get_by_slug("aegean")
        brightwater = await uow.tenants.get_by_slug("brightwater")
    assert aegean and brightwater
    async with db_services.uow_factory(
        TenantContext(tenant_id=aegean.id, user_id="seed", roles=("company_admin",))
    ) as uow:
        employees = {e.employee_number: e for e in await uow.employees.list_active()}
    return {"aegean": aegean, "brightwater": brightwater, "employees": employees}


def _tenant(seeded: dict[str, object], key: str) -> Tenant:
    tenant = seeded[key]
    assert isinstance(tenant, Tenant)
    return tenant


async def test_seed_creates_catalogue_grants_and_sample_requests(
    db_services: Services, seeded_leave: dict[str, object]
) -> None:
    aegean = _tenant(seeded_leave, "aegean")
    employees = seeded_leave["employees"]
    assert isinstance(employees, dict)
    async with db_services.uow_factory(
        TenantContext(tenant_id=aegean.id, user_id="seed", roles=("company_admin",))
    ) as uow:
        types = await uow.leave_types.list()
        assert [t.code for t in types] == ["annual", "sick", "unpaid"]
        assert len(await uow.holidays.list(year=2026)) == 12
        assert [r.priority for r in await uow.approval_rules.list()] == [10, 20, 30, 40]
        balances = await uow.ledger.balances(year=2026)
        assert len(balances) == 20  # 10 employees × (annual, sick)
        statuses = {r.status for r in await uow.leave_requests.list_by_status(frozenset(RequestStatus))}
        assert {
            RequestStatus.COMPLETED,
            RequestStatus.APPROVED,
            RequestStatus.PENDING_MANAGER,
            RequestStatus.PENDING_HR,
            RequestStatus.REJECTED,
            RequestStatus.CANCELLED,
        } <= statuses


async def test_exclusion_constraint_rejects_overlapping_open_requests(
    db_services: Services, seeded_leave: dict[str, object], sql_uow_factory: SqlAlchemyUnitOfWorkFactory
) -> None:
    aegean = _tenant(seeded_leave, "aegean")
    employees = seeded_leave["employees"]
    assert isinstance(employees, dict)
    employee = employees["AEG-004"]
    ctx = TenantContext(tenant_id=aegean.id, user_id="x", roles=("employee",))
    insert = text(
        "INSERT INTO app.leave_requests (id, tenant_id, request_number, employee_id, leave_type_id, start_date, end_date, working_days, status, version, created_at, updated_at) "
        "SELECT gen_random_uuid(), :tenant, :number, :employee, id, :start, :end, 1, :status, 1, now(), now() FROM app.leave_types WHERE code = 'annual' AND tenant_id = :tenant"
    )
    async with sql_uow_factory(ctx) as uow:
        await uow.session.execute(
            insert,
            {
                "tenant": aegean.id,
                "number": "LR-2099-000001",
                "employee": employee.id,
                "start": date(2099, 3, 2),
                "end": date(2099, 3, 6),
                "status": "APPROVED",
            },
        )
        with pytest.raises(IntegrityError, match="ex_leave_requests_no_overlap"):
            await uow.session.execute(
                insert,
                {
                    "tenant": aegean.id,
                    "number": "LR-2099-000002",
                    "employee": employee.id,
                    "start": date(2099, 3, 4),
                    "end": date(2099, 3, 10),
                    "status": "PENDING_MANAGER",
                },
            )
    async with sql_uow_factory(ctx) as uow:  # cancelled rows do not block
        await uow.session.execute(
            insert,
            {
                "tenant": aegean.id,
                "number": "LR-2099-000003",
                "employee": employee.id,
                "start": date(2099, 4, 1),
                "end": date(2099, 4, 3),
                "status": "CANCELLED",
            },
        )
        await uow.session.execute(
            insert,
            {
                "tenant": aegean.id,
                "number": "LR-2099-000004",
                "employee": employee.id,
                "start": date(2099, 4, 2),
                "end": date(2099, 4, 7),
                "status": "APPROVED",
            },
        )


async def test_ledger_and_events_are_append_only_for_the_runtime_role(  # outbox: content only (M8)
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory, seeded_leave: dict[str, object]
) -> None:
    aegean = _tenant(seeded_leave, "aegean")
    ctx = TenantContext(tenant_id=aegean.id, user_id="x", roles=("hr_admin",))
    for table in ("app.leave_ledger_entries", "app.leave_request_events"):
        async with sql_uow_factory(ctx) as uow:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await uow.session.execute(text(f"DELETE FROM {table}"))
        async with sql_uow_factory(ctx) as uow:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await uow.session.execute(text(f"UPDATE {table} SET tenant_id = tenant_id"))
    # M8: the outbox's delivery bookkeeping is updatable (lease, ack, dead), its content is not
    for column in ("tenant_id", "type", "payload", "created_at"):
        async with sql_uow_factory(ctx) as uow:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await uow.session.execute(text(f"UPDATE app.integration_events SET {column} = {column}"))
    async with sql_uow_factory(ctx) as uow:
        await uow.session.execute(text("UPDATE app.integration_events SET attempts = attempts"))


async def test_balance_view_respects_rls(
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory, engine: AsyncEngine, seeded_leave: dict[str, object]
) -> None:
    aegean, brightwater = _tenant(seeded_leave, "aegean"), _tenant(seeded_leave, "brightwater")
    async with engine.connect() as conn:  # no context → nothing
        assert (await conn.execute(text("SELECT count(*) FROM app.leave_balances"))).scalar_one() == 0
    for tenant, expected in ((aegean, 20), (brightwater, 16)):
        async with sql_uow_factory(TenantContext(tenant_id=tenant.id, user_id="x")) as uow:
            rows = (
                await uow.session.execute(
                    text("SELECT DISTINCT tenant_id, count(*) OVER () FROM app.leave_balances")
                )
            ).all()
        assert rows and rows[0][0] == tenant.id and rows[0][1] == expected


async def test_concurrent_submissions_for_the_same_dates_yield_one_request(
    db_services: Services, seeded_leave: dict[str, object]
) -> None:
    employees = seeded_leave["employees"]
    assert isinstance(employees, dict)
    requester = principal_for(employees["AEG-005"])
    cmd = SubmitRequest("annual", date(2026, 12, 7), date(2026, 12, 9))
    results = await asyncio.gather(
        *(db_services.leave_requests.submit(requester, cmd) for _ in range(5)), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1 and all(
        isinstance(f, LeaveValidationError | ConflictError) for f in failures
    ), results


async def test_full_flow_with_ledger_and_rollover(
    db_services: Services, seeded_leave: dict[str, object]
) -> None:
    aegean = _tenant(seeded_leave, "aegean")
    employees = seeded_leave["employees"]
    assert isinstance(employees, dict)
    requester, manager, hr = (
        principal_for(employees["AEG-005"]),
        principal_for(employees["AEG-003"]),
        principal_for(employees["AEG-002"]),
    )
    before = {
        v.leave_type.code: v.available_days for v in await db_services.ledger.balances(requester, year=2026)
    }
    detail = await db_services.leave_requests.submit(
        requester, SubmitRequest("annual", date(2026, 11, 16), date(2026, 11, 24), reason="trip")
    )
    assert (
        detail.request.status is RequestStatus.PENDING_MANAGER
        and detail.request.request_number.startswith("LR-2026-")
    )
    step1 = await db_services.approvals.approve(manager, detail.steps[0].id, Decide(version=1))
    assert step1.request.status is RequestStatus.PENDING_HR
    final = await db_services.approvals.approve(hr, step1.steps[1].id, Decide(version=1))
    assert final.request.status is RequestStatus.APPROVED
    after = {
        v.leave_type.code: v.available_days for v in await db_services.ledger.balances(requester, year=2026)
    }
    assert before["annual"] - after["annual"] == Decimal("7")
    ledger = await db_services.ledger.ledger(requester, leave_type_code="annual", year=2026)
    assert [e.entry_type for e in ledger][-1] is EntryType.DEDUCTION
    report = await db_services.rollover.run_for_tenant(aegean, date(2026, 11, 30))
    assert report.started >= 1 and report.completed >= 1
    async with db_services.uow_factory(TenantContext(tenant_id=aegean.id, user_id="x")) as uow:
        refreshed = await uow.leave_requests.get(final.request.id)
    assert refreshed is not None and refreshed.status is RequestStatus.COMPLETED
    notifications = await db_services.notifications.list(requester, unread_only=True, limit=None, cursor=None)
    assert {n.type for n in notifications.items} >= {"request.submitted", "request.approved"}


async def test_cross_tenant_requests_are_invisible(
    db_services: Services, seeded_leave: dict[str, object]
) -> None:
    brightwater = _tenant(seeded_leave, "brightwater")
    employees = seeded_leave["employees"]
    assert isinstance(employees, dict)
    async with db_services.uow_factory(
        TenantContext(tenant_id=brightwater.id, user_id="x", roles=("hr_admin",))
    ) as uow:
        bw_hr = next(e for e in await uow.employees.list_active(role=Role.HR_ADMIN))
        aegean_request_ids = {r.id for r in await uow.leave_requests.list_by_status(frozenset(RequestStatus))}
    aegean_employee = employees["AEG-004"]
    page = await db_services.leave_requests.list(
        principal_for(aegean_employee), statuses=None, date_from=None, date_to=None, limit=None, cursor=None
    )
    assert page.items and not ({r.id for r in page.items} & aegean_request_ids)
    hr_principal: Principal = principal_for(bw_hr)
    from leave_assistant.application.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await db_services.leave_requests.get(hr_principal, page.items[0].id)


async def test_models_match_migration_including_leave_tables(migrated_database: str) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine as create_sync_engine

    from leave_assistant.infrastructure.db.models import Base
    from tests.integration.conftest import OWNER_URL

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
                            "uq_calendar_connections_company",
                            "uq_calendar_connections_personal",
                        }
                    )  # raw-SQL indexes (exclusion, HNSW, GIN, partial unique)
                ),
            },
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == [], diff
