"""`/me` for every role and the authentication failure modes through the HTTP layer (doc 09 §2.1)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

from leave_assistant.domain.tenancy import TenantStatus
from tests.conftest import TokenFactory, bearer
from tests.fakes.memory import MemoryStore
from tests.fakes.tokens import claims_for
from tests.fakes.world import World


@pytest.mark.parametrize("role", ["employee", "manager", "hr_admin", "company_admin"])
async def test_me_for_tenant_roles(api: AsyncClient, world: World, tokens: TokenFactory, role: str) -> None:
    employee = world.a.employees[role]
    response = await api.get("/api/v1/me", headers=bearer(tokens.for_employee(employee)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == employee.keycloak_sub
    assert body["roles"] == sorted(r.value for r in employee.roles)
    assert body["tenant"]["slug"] == "aegean" and body["locale"] == "el"
    assert body["employee"]["employee_number"] == employee.employee_number
    assert body["employee"]["department"]["code"] in {"OPS", "HR"}
    assert body["is_platform_admin"] is False and body["pending_interrupt_count"] == 0
    assert body["feature_flags"]["retrieval_backend"] == "pgvector"


async def test_me_for_platform_admin_and_automation(api: AsyncClient, tokens: TokenFactory) -> None:
    body = (await api.get("/api/v1/me", headers=bearer(tokens.platform_admin()))).json()
    assert body["is_platform_admin"] and body["tenant"] is None and body["employee"] is None
    body = (await api.get("/api/v1/me", headers=bearer(tokens.automation()))).json()
    assert body["is_automation"] and body["tenant"] is None


async def test_missing_or_bad_scheme_is_401_problem(api: AsyncClient) -> None:
    response = await api.get("/api/v1/me", headers={"X-Request-Id": "req-1"})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["type"] == "urn:leave-assistant:problem:authentication-required"
    assert response.json()["request_id"] == "req-1"
    assert (await api.get("/api/v1/me", headers={"Authorization": "Basic abc"})).status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [{"lifetime": -120}, {"audience": "other"}, {"issuer": "http://evil/realms/x"}],
)
async def test_invalid_tokens_are_401(
    api: AsyncClient, world: World, tokens: TokenFactory, overrides: dict
) -> None:
    employee = world.a.employees["employee"]
    response = await api.get("/api/v1/me", headers=bearer(tokens.for_employee(employee, **overrides)))
    assert response.status_code == 401


async def test_multi_tenant_or_mixed_tokens_are_401(
    api: AsyncClient, world: World, tokens: TokenFactory
) -> None:
    employee = world.a.employees["employee"]
    two = tokens.for_employee(employee, tenant_id=[str(world.a.tenant.id), str(world.b.tenant.id)])
    assert (await api.get("/api/v1/me", headers=bearer(two))).status_code == 401
    mixed = tokens.platform_admin(tenant_id=world.a.tenant.id)
    assert (await api.get("/api/v1/me", headers=bearer(mixed))).status_code == 401


async def test_unknown_inactive_or_suspended_are_403(
    api: AsyncClient, world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    unknown = tokens.raw(claims_for(sub="stranger", roles=["employee"], tenant_id=world.a.tenant.id))
    response = await api.get("/api/v1/me", headers=bearer(unknown))
    assert response.status_code == 403 and response.json()["type"].endswith("authorization-denied")
    inactive = tokens.for_employee(world.a.employees["inactive"])
    assert (await api.get("/api/v1/me", headers=bearer(inactive))).status_code == 403
    store.tenants[world.b.tenant.id].status = TenantStatus.SUSPENDED
    response = await api.get("/api/v1/me", headers=bearer(tokens.for_employee(world.b.employees["employee"])))
    assert response.status_code == 403 and response.json()["type"].endswith("tenant/suspended")


async def test_unknown_tenant_claim_is_401(api: AsyncClient, world: World, tokens: TokenFactory) -> None:
    token = tokens.for_employee(world.a.employees["employee"], tenant_id=uuid4())
    assert (await api.get("/api/v1/me", headers=bearer(token))).status_code == 401


async def test_openapi_is_served(api: AsyncClient) -> None:
    response = await api.get("/api/v1/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert (
        "/api/v1/me" in paths and "/api/v1/platform/tenants" in paths and "/api/v1/admin/employees" in paths
    )
