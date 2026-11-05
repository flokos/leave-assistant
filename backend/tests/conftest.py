from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from leave_assistant.application.container import DocumentAdapters, DocumentOptions, Services
from leave_assistant.application.health import CheckResult
from leave_assistant.config import Settings
from leave_assistant.domain.tenancy import Employee, Principal, Role
from leave_assistant.infrastructure.identity.tokens import TokenVerifier
from leave_assistant.infrastructure.ml.language import ScriptLanguageDetector
from leave_assistant.infrastructure.scanning.noop import NoPiiScanner, NoRiskScanner
from leave_assistant.main import create_app
from tests.fakes.documents import FakeEmbedder, FakeParser, MemoryDocumentStorage, WordTokenizer
from tests.fakes.identity import FakeIdentityAdmin
from tests.fakes.leave_world import seed_leave_catalogue
from tests.fakes.memory import MemoryStore, MemoryUnitOfWorkFactory
from tests.fakes.system import FixedClock, SequentialIds
from tests.fakes.tokens import AUDIENCE, ISSUER, TestKeys, claims_for
from tests.fakes.world import World, build_world

PLATFORM_ADMIN_SUB = "sub-platform-admin"
AUTOMATION_SUB = "sub-service-account-n8n"


@pytest.fixture(scope="session")
def test_keys() -> TestKeys:
    return TestKeys()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        worker_poll_interval_seconds=0.01,
        worker_heartbeat_path="/tmp/la-test.heartbeat",  # noqa: S108
        keycloak_public_url="http://localhost:8081",
        keycloak_realm="leave-assistant",
        keycloak_audience=AUDIENCE,
    )


def ok_check(name: str):  # type: ignore[no-untyped-def]
    async def check() -> CheckResult:
        return CheckResult(name, True)

    return check


def failing_check(name: str, detail: str = "boom"):  # type: ignore[no-untyped-def]
    async def check() -> CheckResult:
        return CheckResult(name, False, detail)

    return check


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def identity() -> FakeIdentityAdmin:
    return FakeIdentityAdmin()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> SequentialIds:
    return SequentialIds(1000)


@pytest.fixture
def uow_factory(store: MemoryStore) -> MemoryUnitOfWorkFactory:
    return MemoryUnitOfWorkFactory(store)


@pytest.fixture
def services(
    uow_factory: MemoryUnitOfWorkFactory,
    identity: FakeIdentityAdmin,
    clock: FixedClock,
    ids: SequentialIds,
    test_keys: TestKeys,
    document_adapters: DocumentAdapters,
) -> Services:
    verifier = TokenVerifier(test_keys.resolver(), issuer=ISSUER, audience=AUDIENCE, leeway_seconds=30)
    return Services.build(
        uow_factory=uow_factory,
        identity=identity,
        verifier=verifier,
        clock=clock,
        ids=ids,
        adapters=document_adapters,
        options=DocumentOptions(max_upload_mb=1, job_stale_after_seconds=600),
    )


@pytest.fixture
def document_adapters() -> DocumentAdapters:
    return fake_document_adapters()


def fake_document_adapters(dimension: int = 32) -> DocumentAdapters:
    return DocumentAdapters(
        storage=MemoryDocumentStorage(),
        parser=FakeParser(),
        tokenizer=WordTokenizer(),
        embedder=FakeEmbedder(dimension),
        language=ScriptLanguageDetector(),
        risk_scanner=NoRiskScanner(),
        pii_scanner=NoPiiScanner(),
    )


@pytest.fixture
def world(store: MemoryStore, clock: FixedClock, ids: SequentialIds) -> World:
    return build_world(store, clock, ids)


@pytest.fixture
def leave_world(store: MemoryStore, world: World, clock: FixedClock, ids: SequentialIds) -> World:
    """The two-tenant world plus leave types, 2026/2027 policies, holidays, rules and 2026 grants."""
    world.leave_types = seed_leave_catalogue(store, world, clock, ids)  # type: ignore[attr-defined]
    return world


@pytest.fixture
def platform_admin() -> Principal:
    return Principal(
        user_id=PLATFORM_ADMIN_SUB,
        username="platform.admin",
        roles=frozenset({Role.PLATFORM_ADMIN}),
        display_name="Platform",
    )


def principal_for(employee: Employee) -> Principal:
    return Principal(
        user_id=employee.keycloak_sub,
        username=employee.email,
        roles=employee.roles,
        tenant_id=employee.tenant_id,
        employee_id=employee.id,
        department_id=employee.department_id,
        locale=employee.locale,
        display_name=employee.display_name,
    )


class TokenFactory:
    def __init__(self, keys: TestKeys) -> None:
        self._keys = keys

    def for_employee(self, employee: Employee, **overrides: Any) -> str:
        kwargs: dict[str, Any] = {
            "sub": employee.keycloak_sub,
            "roles": sorted(r.value for r in employee.roles),
            "tenant_id": employee.tenant_id,
            "username": employee.email,
        }
        kwargs.update(overrides)
        return self._keys.sign(claims_for(**kwargs))

    def platform_admin(self, **overrides: Any) -> str:
        return self._keys.sign(
            claims_for(
                sub=PLATFORM_ADMIN_SUB, roles=["platform_admin"], username="platform.admin", **overrides
            )
        )

    def automation(self, **overrides: Any) -> str:
        return self._keys.sign(
            claims_for(
                sub=AUTOMATION_SUB,
                roles=["automation"],
                username="service-account-n8n-automation",
                **overrides,
            )
        )

    def raw(self, claims: dict[str, Any], **kwargs: Any) -> str:
        return self._keys.sign(claims, **kwargs)


@pytest.fixture
def tokens(test_keys: TestKeys) -> TokenFactory:
    return TokenFactory(test_keys)


def bearer(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


@pytest.fixture
async def api(settings: Settings, services: Services) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings, services=services, readiness_checks=[ok_check("postgres")], bootstrap_infrastructure=False
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings, readiness_checks=[ok_check("postgres"), ok_check("s3")], bootstrap_infrastructure=False
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http


@pytest.fixture
async def degraded_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings,
        readiness_checks=[ok_check("postgres"), failing_check("keycloak", "HTTP 503")],
        bootstrap_infrastructure=False,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
