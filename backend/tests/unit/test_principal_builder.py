"""FR-AUTH-03/05: the Principal is built from the token plus the employee record; mixed tokens are rejected."""

from __future__ import annotations

from uuid import uuid4

import pytest

from leave_assistant.application.errors import AuthenticationError, AuthorizationError, TenantSuspendedError
from leave_assistant.application.identity.claims import TokenClaims
from leave_assistant.application.identity.principal_builder import build_principal
from leave_assistant.domain.tenancy import Role, TenantStatus
from tests.fakes.memory import MemoryStore, MemoryUnitOfWorkFactory
from tests.fakes.world import World


def claims(sub: str, roles: set[str], tenant_ids: tuple[str, ...] = ()) -> TokenClaims:
    return TokenClaims(sub=sub, username=sub, roles=frozenset(roles), tenant_ids=tenant_ids, expires_at=0)


@pytest.mark.parametrize("role", ["employee", "manager", "hr_admin", "company_admin"])
async def test_tenant_roles_build_tenant_principals(
    uow_factory: MemoryUnitOfWorkFactory, world: World, role: str
) -> None:
    employee = world.a.employees[role]
    principal = await build_principal(
        claims(
            employee.keycloak_sub,
            {r.value for r in employee.roles} | {"offline_access"},
            (str(employee.tenant_id),),
        ),
        uow_factory,
    )
    assert principal.tenant_id == world.a.tenant.id
    assert principal.employee_id == employee.id
    assert principal.department_id == employee.department_id
    assert principal.roles == employee.roles
    assert principal.locale == employee.locale
    assert principal.is_tenant_user and not principal.is_platform_admin


async def test_platform_admin_has_no_tenant(uow_factory: MemoryUnitOfWorkFactory) -> None:
    principal = await build_principal(claims("p", {"platform_admin"}), uow_factory)
    assert principal.is_platform_admin and principal.tenant_id is None and principal.employee_id is None
    assert principal.roles == {Role.PLATFORM_ADMIN}


async def test_automation_principal(uow_factory: MemoryUnitOfWorkFactory) -> None:
    principal = await build_principal(claims("svc", {"automation"}), uow_factory)
    assert principal.is_automation and not principal.is_tenant_user


@pytest.mark.parametrize(
    ("roles", "tenants"),
    [
        ({"platform_admin"}, (str(uuid4()),)),  # platform admin inside a tenant group
        ({"platform_admin", "employee"}, ()),  # platform admin with tenant roles
        ({"automation", "employee"}, ()),
    ],
)
async def test_mixed_tokens_are_rejected(
    uow_factory: MemoryUnitOfWorkFactory, roles: set[str], tenants: tuple[str, ...]
) -> None:
    with pytest.raises(AuthenticationError):
        await build_principal(claims("x", roles, tenants), uow_factory)


async def test_zero_or_multiple_tenants_rejected(uow_factory: MemoryUnitOfWorkFactory, world: World) -> None:
    employee = world.a.employees["employee"]
    with pytest.raises(AuthenticationError, match="exactly one tenant"):
        await build_principal(claims(employee.keycloak_sub, {"employee"}), uow_factory)
    with pytest.raises(AuthenticationError, match="exactly one tenant"):
        await build_principal(
            claims(employee.keycloak_sub, {"employee"}, (str(world.a.tenant.id), str(world.b.tenant.id))),
            uow_factory,
        )


async def test_invalid_or_unknown_tenant_rejected(uow_factory: MemoryUnitOfWorkFactory, world: World) -> None:
    with pytest.raises(AuthenticationError, match="invalid tenant claim"):
        await build_principal(claims("x", {"employee"}, ("not-a-uuid",)), uow_factory)
    with pytest.raises(AuthenticationError, match="unknown tenant"):
        await build_principal(claims("x", {"employee"}, (str(uuid4()),)), uow_factory)


async def test_no_application_role_rejected(uow_factory: MemoryUnitOfWorkFactory, world: World) -> None:
    employee = world.a.employees["employee"]
    with pytest.raises(AuthorizationError, match="no application role"):
        await build_principal(
            claims(employee.keycloak_sub, {"offline_access"}, (str(employee.tenant_id),)), uow_factory
        )


async def test_suspended_tenant_rejected(
    store: MemoryStore, uow_factory: MemoryUnitOfWorkFactory, world: World
) -> None:
    store.tenants[world.a.tenant.id].status = TenantStatus.SUSPENDED
    employee = world.a.employees["employee"]
    with pytest.raises(TenantSuspendedError):
        await build_principal(
            claims(employee.keycloak_sub, {"employee"}, (str(employee.tenant_id),)), uow_factory
        )


async def test_unknown_or_inactive_employee_rejected(
    uow_factory: MemoryUnitOfWorkFactory, world: World
) -> None:
    with pytest.raises(AuthorizationError, match="no employee record"):
        await build_principal(claims("stranger", {"employee"}, (str(world.a.tenant.id),)), uow_factory)
    gone = world.a.employees["inactive"]
    with pytest.raises(AuthorizationError, match="inactive"):
        await build_principal(claims(gone.keycloak_sub, {"employee"}, (str(gone.tenant_id),)), uow_factory)


async def test_employee_of_other_tenant_is_invisible(
    uow_factory: MemoryUnitOfWorkFactory, world: World
) -> None:
    """A token claiming tenant B with a sub that only exists in tenant A finds no employee (RLS + explicit filter)."""
    employee = world.a.employees["employee"]
    with pytest.raises(AuthorizationError, match="no employee record"):
        await build_principal(
            claims(employee.keycloak_sub, {"employee"}, (str(world.b.tenant.id),)), uow_factory
        )
