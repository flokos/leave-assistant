"""Database-backed fixtures: a dedicated `leave_test` database on the running Postgres (127.0.0.1), migrated to head.

Credentials come from deploy/.env (`make test-integration` exports it). The test database is (re)created from
scratch once per session as the superuser, migrated as `app_owner`, and used through `app_rls` like the API.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.container import Services
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import Settings
from leave_assistant.domain.tenancy import Employee
from leave_assistant.infrastructure.db.migrations import upgrade_to_head
from leave_assistant.infrastructure.db.session import create_engine, make_session_factory
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.infrastructure.identity.tokens import TokenVerifier
from leave_assistant.seed.run import run_seed
from tests.conftest import fake_document_adapters
from tests.fakes.identity import FakeIdentityAdmin
from tests.fakes.system import FixedClock, SequentialIds
from tests.fakes.tokens import AUDIENCE, ISSUER, TestKeys

pytestmark = pytest.mark.integration

HOST = os.environ.get("TEST_DATABASE_HOST", "127.0.0.1")
PORT = os.environ.get("POSTGRES_PORT", "5432")
TEST_DB = os.environ.get("TEST_DATABASE_NAME", "leave_test")
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL") or (
    f"postgresql://postgres:{os.environ.get('POSTGRES_PASSWORD', 'postgres-dev')}@{HOST}:{PORT}/postgres"
)
OWNER_URL = f"postgresql+psycopg://app_owner:{os.environ.get('LEAVE_DB_OWNER_PASSWORD', 'app-owner-dev')}@{HOST}:{PORT}/{TEST_DB}"
RLS_URL = f"postgresql+psycopg://app_rls:{os.environ.get('LEAVE_DB_PASSWORD', 'app-rls-dev')}@{HOST}:{PORT}/{TEST_DB}"
TENANT_TABLES = (
    "platform.jobs",
    "app.provider_events",
    "app.agent_runs",
    "app.conversation_messages",
    "app.conversations",
    "platform.supporting_download_tokens",
    "app.retention_review_items",
    "app.retention_policies",
    "app.supporting_document_access_events",
    "app.supporting_documents",
    "app.injection_findings",
    "app.security_policies",
    "app.pii_vault_counters",
    "app.pii_vault",
    "app.document_chunks",
    "app.document_review_events",
    "app.document_acl",
    "app.index_generations",
    "app.document_versions",
    "app.documents",
    "app.integration_events",
    "app.notifications",
    "app.leave_ledger_entries",
    "app.approval_steps",
    "app.leave_request_events",
    "app.leave_requests",
    "app.request_counters",
    "app.approval_rules",
    "app.public_holidays",
    "app.leave_policies",
    "app.leave_types",
    "app.employee_roles",
    "app.employees",
    "app.departments",
    "platform.idempotency_keys",
    "platform.tenants",
    "audit.audit_log",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError as exc:  # pragma: no cover - environment guard
        pytest.fail(
            f"PostgreSQL not reachable at {HOST}:{PORT} — start the stack (`make up-staged`) first: {exc}"
        )
    with admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone()
        if not exists:
            admin.execute(f'CREATE DATABASE "{TEST_DB}" OWNER app_owner')
    with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}", autocommit=True) as conn:
        for extension in ("vector", "unaccent", "pgcrypto", "btree_gist"):
            conn.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
        conn.execute("GRANT ALL ON SCHEMA public TO app_owner")
        conn.execute("GRANT USAGE ON SCHEMA public TO app_rls")
        conn.execute(f'GRANT CONNECT ON DATABASE "{TEST_DB}" TO app_rls, langgraph_rw')
        conn.execute("DROP SCHEMA IF EXISTS audit, app, platform, langgraph CASCADE")
        conn.execute("DROP TABLE IF EXISTS public.alembic_version")
    upgrade_to_head(OWNER_URL)
    yield RLS_URL


@pytest.fixture
async def engine(migrated_database: str, settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings, url=migrated_database, pool_size=2, max_overflow=0)
    yield engine
    await engine.dispose()


@pytest.fixture
async def owner_engine(migrated_database: str, settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings, url=OWNER_URL, pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TENANT_TABLES)} CASCADE"))
    yield


@pytest.fixture
def sql_uow_factory(engine: AsyncEngine) -> SqlAlchemyUnitOfWorkFactory:
    return SqlAlchemyUnitOfWorkFactory(make_session_factory(engine))


@pytest.fixture
def db_services(
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory,
    identity: FakeIdentityAdmin,
    clock: FixedClock,
    ids: SequentialIds,
    test_keys: TestKeys,
) -> Services:
    verifier = TokenVerifier(test_keys.resolver(), issuer=ISSUER, audience=AUDIENCE)
    return Services.build(
        uow_factory=sql_uow_factory,
        identity=identity,
        verifier=verifier,
        clock=clock,
        ids=ids,
        adapters=fake_document_adapters(dimension=384),
    )


@pytest.fixture
async def seeded(db_services: Services) -> dict[str, object]:
    report = await run_seed(db_services, "pw")
    assert not report.unchanged
    async with db_services.uow_factory(
        TenantContext(tenant_id=None, user_id="seed", platform_admin=True)
    ) as uow:
        aegean = await uow.tenants.get_by_slug("aegean")
        brightwater = await uow.tenants.get_by_slug("brightwater")
    assert aegean and brightwater
    employees: dict[str, dict[str, Employee]] = {}
    for tenant in (aegean, brightwater):
        async with db_services.uow_factory(
            TenantContext(tenant_id=tenant.id, user_id="seed", roles=("company_admin",))
        ) as uow:
            employees[tenant.slug] = {e.employee_number: e for e in await uow.employees.list_active()}
    return {"aegean": aegean, "brightwater": brightwater, "employees": employees}
