"""SecurityPolicyService: per-tenant thresholds within platform-set ranges (doc 08 §8.2, doc 09 §2.8/§2.9)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.errors import InvalidInputError
from leave_assistant.application.ports import (
    Clock,
    SecurityDefaults,
    SecurityPolicy,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.security.scoring import RiskThresholds
from leave_assistant.domain.tenancy import Principal


@dataclass(frozen=True, slots=True)
class UpdateSecurityPolicy:
    risk_low_max: float = UNSET
    risk_medium_max: float = UNSET
    injection_llm_judge: bool = UNSET
    user_input_block_threshold: float = UNSET
    max_upload_mb: int = UNSET
    allowed_mime: tuple[str, ...] = UNSET


@dataclass(frozen=True, slots=True)
class UpdateSecurityDefaults:
    risk_low_max: float = UNSET
    risk_medium_max: float = UNSET
    injection_llm_judge: bool = UNSET
    user_input_block_threshold: float = UNSET
    max_upload_mb: int = UNSET
    threshold_min: float = UNSET
    threshold_max: float = UNSET
    max_upload_mb_limit: int = UNSET


def _changes(cmd: UpdateSecurityPolicy | UpdateSecurityDefaults) -> dict[str, Any]:
    """Fields explicitly given (slots dataclasses have no `__dict__`; `asdict` would deep-copy the sentinel)."""
    return {
        f.name: getattr(cmd, f.name) for f in dataclasses.fields(cmd) if getattr(cmd, f.name) is not UNSET
    }


def policy_from_defaults(
    tenant_id: UUID, defaults: SecurityDefaults, *, updated_by: str, now: datetime
) -> SecurityPolicy:
    return SecurityPolicy(
        tenant_id=tenant_id,
        risk_low_max=defaults.risk_low_max,
        risk_medium_max=defaults.risk_medium_max,
        injection_llm_judge=defaults.injection_llm_judge,
        user_input_block_threshold=defaults.user_input_block_threshold,
        max_upload_mb=defaults.max_upload_mb,
        allowed_mime=tuple(defaults.allowed_mime),
        updated_by=updated_by,
        created_at=now,
        updated_at=now,
    )


def validate_policy(policy: SecurityPolicy, defaults: SecurityDefaults) -> None:
    errors: list[dict[str, object]] = []
    for name in ("risk_low_max", "risk_medium_max", "user_input_block_threshold"):
        value = getattr(policy, name)
        if not defaults.threshold_min <= value <= defaults.threshold_max:
            errors.append(
                {
                    "loc": ["body", name],
                    "msg": f"must be within {defaults.threshold_min}–{defaults.threshold_max}",
                }
            )
    if policy.risk_low_max >= policy.risk_medium_max:
        errors.append({"loc": ["body", "risk_medium_max"], "msg": "must be greater than risk_low_max"})
    if not 1 <= policy.max_upload_mb <= defaults.max_upload_mb_limit:
        errors.append(
            {"loc": ["body", "max_upload_mb"], "msg": f"must be within 1–{defaults.max_upload_mb_limit}"}
        )
    if not policy.allowed_mime or not set(policy.allowed_mime) <= set(defaults.allowed_mime):
        errors.append(
            {
                "loc": ["body", "allowed_mime"],
                "msg": "must be a non-empty subset of the platform allowed types",
            }
        )
    if errors:
        raise InvalidInputError("invalid security policy", errors=errors)


class SecurityPolicyService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, audit: AuditService) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._audit = audit

    # --- resolution used by the pipeline and the chat ingress ------------------------------------------------
    async def policy_for(self, uow: UnitOfWork, tenant_id: UUID) -> SecurityPolicy:
        policy = await uow.security_policies.get()
        if policy is not None:
            return policy
        defaults = await uow.security_defaults.get()
        return policy_from_defaults(tenant_id, defaults, updated_by="defaults", now=self._clock.now())

    @staticmethod
    def thresholds(policy: SecurityPolicy) -> RiskThresholds:
        return RiskThresholds(low_max=policy.risk_low_max, medium_max=policy.risk_medium_max)

    async def ensure_policy(self, uow: UnitOfWork, tenant_id: UUID, *, updated_by: str) -> SecurityPolicy:
        """Create the tenant row from the platform defaults when missing (provisioning, seed back-fill)."""
        existing = await uow.security_policies.get()
        if existing is not None:
            return existing
        policy = policy_from_defaults(
            tenant_id, await uow.security_defaults.get(), updated_by=updated_by, now=self._clock.now()
        )
        await uow.security_policies.upsert(policy)
        return policy

    # --- company administration ---------------------------------------------------------------------------
    async def get_policy(self, principal: Principal) -> SecurityPolicy:
        require(principal, Action.MANAGE_SECURITY_POLICY)
        assert principal.tenant_id is not None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await self.policy_for(uow, principal.tenant_id)

    async def update_policy(self, principal: Principal, cmd: UpdateSecurityPolicy) -> SecurityPolicy:
        require(principal, Action.MANAGE_SECURITY_POLICY)
        assert principal.tenant_id is not None
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            defaults = await uow.security_defaults.get()
            current = await self.policy_for(uow, principal.tenant_id)
            changes = _changes(cmd)
            policy = replace(current, **changes, updated_by=principal.user_id, updated_at=now)
            if current.created_at is None:  # pragma: no cover - defaults carry a timestamp
                policy = replace(policy, created_at=now)
            validate_policy(policy, defaults)
            await uow.security_policies.upsert(policy)
            await self._audit.record(
                uow,
                principal,
                "admin.security_policy.changed",
                resource_type="security_policy",
                resource_id=principal.tenant_id,
                details={k: (list(v) if isinstance(v, tuple) else v) for k, v in changes.items()},
            )
            await uow.commit()
        return policy

    # --- platform administration ---------------------------------------------------------------------------
    async def get_defaults(self, principal: Principal) -> SecurityDefaults:
        require(principal, Action.MANAGE_SECURITY_DEFAULTS)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.security_defaults.get()

    async def update_defaults(self, principal: Principal, cmd: UpdateSecurityDefaults) -> SecurityDefaults:
        require(principal, Action.MANAGE_SECURITY_DEFAULTS)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            current = await uow.security_defaults.get()
            changes = _changes(cmd)
            defaults = replace(current, **changes, updated_by=principal.user_id)
            if not (0.0 <= defaults.threshold_min < defaults.threshold_max <= 1.0):
                raise InvalidInputError(
                    "invalid threshold range",
                    errors=[{"loc": ["body", "threshold_min"], "msg": "0 ≤ min < max ≤ 1"}],
                )
            if (
                not defaults.threshold_min
                <= defaults.risk_low_max
                < defaults.risk_medium_max
                <= defaults.threshold_max
            ):
                raise InvalidInputError(
                    "defaults must respect the range and ordering",
                    errors=[{"loc": ["body", "risk_low_max"], "msg": "min ≤ low < medium ≤ max"}],
                )
            if not 1 <= defaults.max_upload_mb <= defaults.max_upload_mb_limit <= 500:
                raise InvalidInputError(
                    "invalid upload limits",
                    errors=[{"loc": ["body", "max_upload_mb"], "msg": "1 ≤ default ≤ limit ≤ 500"}],
                )
            await uow.security_defaults.update(defaults)
            await self._audit.record(
                uow,
                principal,
                "platform.security_defaults.changed",
                resource_type="security_policy_defaults",
                details={k: v for k, v in changes.items()},
            )
            await uow.commit()
        return defaults
