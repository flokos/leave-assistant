"""Build the trusted Principal from token claims plus the employee record (FR-AUTH-03, doc 08 §5.1)."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.application.errors import AuthenticationError, AuthorizationError, TenantSuspendedError
from leave_assistant.application.identity.claims import TokenClaims
from leave_assistant.application.ports import UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import TENANT_ROLES, Principal, Role, parse_roles


async def build_principal(claims: TokenClaims, uow_factory: UnitOfWorkFactory) -> Principal:
    roles = parse_roles(claims.roles)
    tenant_roles = roles & TENANT_ROLES
    base_context = TenantContext(
        tenant_id=None,
        user_id=claims.sub,
        roles=tuple(sorted(r.value for r in roles)),
        platform_admin=Role.PLATFORM_ADMIN in roles,
    )

    if Role.PLATFORM_ADMIN in roles:
        # FR-AUTH-05: platform administrators belong to no tenant — a token mixing both is rejected outright.
        if claims.tenant_ids or tenant_roles:
            raise AuthenticationError("platform administrators cannot hold tenant membership or tenant roles")
        return Principal(
            user_id=claims.sub,
            username=claims.username,
            roles=frozenset({Role.PLATFORM_ADMIN}),
            display_name=claims.username,
        )

    if Role.AUTOMATION in roles:
        if claims.tenant_ids or tenant_roles:
            raise AuthenticationError("service accounts cannot hold tenant membership or tenant roles")
        return Principal(
            user_id=claims.sub,
            username=claims.username,
            roles=frozenset({Role.AUTOMATION}),
            display_name=claims.username,
        )

    if len(claims.tenant_ids) != 1:
        raise AuthenticationError("token must carry exactly one tenant")
    try:
        tenant_id = UUID(claims.tenant_ids[0])
    except ValueError as exc:
        raise AuthenticationError("invalid tenant claim") from exc
    if not tenant_roles:
        raise AuthorizationError("no application role assigned", action="authenticate")

    async with uow_factory(base_context) as uow:
        tenant = await uow.tenants.get(tenant_id)
    if tenant is None:
        raise AuthenticationError("unknown tenant")
    if not tenant.is_active:
        raise TenantSuspendedError("tenant is suspended")

    async with uow_factory(base_context.with_tenant(tenant.id)) as uow:
        employee = await uow.employees.get_by_sub(claims.sub)
    if employee is None:
        raise AuthorizationError("no employee record for this user", action="authenticate")
    if not employee.is_active:
        raise AuthorizationError("employee is inactive", action="authenticate")

    return Principal(
        user_id=claims.sub,
        username=claims.username,
        roles=tenant_roles,
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=employee.department_id,
        locale=employee.locale,
        display_name=employee.display_name,
    )
