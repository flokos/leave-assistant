"""Company security policy (doc 09 §2.8): thresholds within the platform's ranges. UI arrives with M10."""

from __future__ import annotations

from fastapi import APIRouter

from leave_assistant.api.deps import CompanyAdmin, CurrentServices
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.security import SecurityPolicyOut, SecurityPolicyUpdate
from leave_assistant.application.security.policy import UpdateSecurityPolicy
from leave_assistant.application.tenancy.commands import UNSET

router = APIRouter(prefix="/admin/security-policy", tags=["admin"], route_class=IdempotentRoute)


@router.get("", response_model=SecurityPolicyOut)
async def get_security_policy(principal: CompanyAdmin, services: CurrentServices) -> SecurityPolicyOut:
    return SecurityPolicyOut.from_domain(await services.security_policies.get_policy(principal))


@router.put("", response_model=SecurityPolicyOut, openapi_extra=NO_IDEMPOTENCY)
async def update_security_policy(
    body: SecurityPolicyUpdate, principal: CompanyAdmin, services: CurrentServices
) -> SecurityPolicyOut:
    given = body.model_dump(exclude_none=True)
    cmd = UpdateSecurityPolicy(
        risk_low_max=given.get("risk_low_max", UNSET),
        risk_medium_max=given.get("risk_medium_max", UNSET),
        injection_llm_judge=given.get("injection_llm_judge", UNSET),
        user_input_block_threshold=given.get("user_input_block_threshold", UNSET),
        max_upload_mb=given.get("max_upload_mb", UNSET),
        allowed_mime=tuple(given["allowed_mime"]) if "allowed_mime" in given else UNSET,
    )
    return SecurityPolicyOut.from_domain(await services.security_policies.update_policy(principal, cmd))
