"""FastAPI dependencies: settings, services, bearer authentication, Principal and role guards (doc 03 §4.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, Request

from leave_assistant.application.container import Services
from leave_assistant.application.errors import AuthenticationError, AuthorizationError
from leave_assistant.application.identity.principal_builder import build_principal
from leave_assistant.config import Settings
from leave_assistant.domain.tenancy import Principal, Role


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_services(request: Request) -> Services:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:
        raise RuntimeError("application services are not configured")
    return services


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("bearer token required")
    return token.strip()


async def resolve_principal(request: Request) -> Principal:
    """Validate the token and build the Principal once per request (cached on `request.state`)."""
    cached: Principal | None = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    services = get_services(request)
    claims = await services.verifier.verify(_bearer_token(request))
    principal = await build_principal(claims, services.uow_factory)
    request.state.principal = principal
    structlog.contextvars.bind_contextvars(
        user_id=principal.user_id, tenant_id=str(principal.tenant_id) if principal.tenant_id else None
    )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(resolve_principal)]
CurrentServices = Annotated[Services, Depends(get_services)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]


def require_tenant_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Tenant users holding any of `roles`. Fine-grained scope checks still happen in the services."""

    async def dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.is_tenant_user:
            raise AuthorizationError("tenant context required", action="route")
        if roles and not principal.has_any(*roles):
            raise AuthorizationError("role not permitted for this route", action="route")
        return principal

    return dependency


async def require_platform_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.is_platform_admin:
        raise AuthorizationError("platform administrators only", action="route")
    return principal


PlatformAdmin = Annotated[Principal, Depends(require_platform_admin)]
CompanyAdmin = Annotated[Principal, Depends(require_tenant_roles(Role.COMPANY_ADMIN))]
AuditReader = Annotated[Principal, Depends(require_tenant_roles(Role.HR_ADMIN, Role.COMPANY_ADMIN))]
