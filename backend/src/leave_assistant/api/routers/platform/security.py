"""Platform security defaults (doc 09 §2.9): the defaults new tenants inherit and the ranges they may configure."""

from __future__ import annotations

from fastapi import APIRouter

from leave_assistant.api.deps import CurrentServices, PlatformAdmin
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.security import SecurityDefaultsOut, SecurityDefaultsUpdate
from leave_assistant.application.security.policy import UpdateSecurityDefaults
from leave_assistant.application.tenancy.commands import UNSET

router = APIRouter(prefix="/platform/security-defaults", tags=["platform"], route_class=IdempotentRoute)


@router.get("", response_model=SecurityDefaultsOut)
async def get_security_defaults(principal: PlatformAdmin, services: CurrentServices) -> SecurityDefaultsOut:
    return SecurityDefaultsOut.from_domain(await services.security_policies.get_defaults(principal))


@router.put("", response_model=SecurityDefaultsOut, openapi_extra=NO_IDEMPOTENCY)
async def update_security_defaults(
    body: SecurityDefaultsUpdate, principal: PlatformAdmin, services: CurrentServices
) -> SecurityDefaultsOut:
    given = body.model_dump(exclude_none=True)
    cmd = UpdateSecurityDefaults(
        **{k: given.get(k, UNSET) for k in UpdateSecurityDefaults.__dataclass_fields__}
    )
    return SecurityDefaultsOut.from_domain(await services.security_policies.update_defaults(principal, cmd))
