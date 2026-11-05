"""Security policy schemas (doc 09 §2.8 company, §2.9 platform) — M5."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.ports import SecurityDefaults, SecurityPolicy


class SecurityPolicyOut(ApiModel):
    tenant_id: UUID
    risk_low_max: float
    risk_medium_max: float
    injection_llm_judge: bool
    user_input_block_threshold: float
    max_upload_mb: int
    allowed_mime: list[str]
    updated_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, p: SecurityPolicy) -> SecurityPolicyOut:
        return cls(
            tenant_id=p.tenant_id,
            risk_low_max=p.risk_low_max,
            risk_medium_max=p.risk_medium_max,
            injection_llm_judge=p.injection_llm_judge,
            user_input_block_threshold=p.user_input_block_threshold,
            max_upload_mb=p.max_upload_mb,
            allowed_mime=list(p.allowed_mime),
            updated_by=p.updated_by,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class SecurityPolicyUpdate(ApiModel):
    risk_low_max: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_medium_max: float | None = Field(default=None, ge=0.0, le=1.0)
    injection_llm_judge: bool | None = None
    user_input_block_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_upload_mb: int | None = Field(default=None, ge=1, le=500)
    allowed_mime: list[str] | None = Field(default=None, max_length=20)


class SecurityDefaultsOut(ApiModel):
    risk_low_max: float
    risk_medium_max: float
    injection_llm_judge: bool
    user_input_block_threshold: float
    max_upload_mb: int
    allowed_mime: list[str]
    threshold_min: float
    threshold_max: float
    max_upload_mb_limit: int
    updated_by: str

    @classmethod
    def from_domain(cls, d: SecurityDefaults) -> SecurityDefaultsOut:
        return cls(
            risk_low_max=d.risk_low_max,
            risk_medium_max=d.risk_medium_max,
            injection_llm_judge=d.injection_llm_judge,
            user_input_block_threshold=d.user_input_block_threshold,
            max_upload_mb=d.max_upload_mb,
            allowed_mime=list(d.allowed_mime),
            threshold_min=d.threshold_min,
            threshold_max=d.threshold_max,
            max_upload_mb_limit=d.max_upload_mb_limit,
            updated_by=d.updated_by,
        )


class SecurityDefaultsUpdate(ApiModel):
    risk_low_max: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_medium_max: float | None = Field(default=None, ge=0.0, le=1.0)
    injection_llm_judge: bool | None = None
    user_input_block_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_upload_mb: int | None = Field(default=None, ge=1, le=500)
    threshold_min: float | None = Field(default=None, ge=0.0, le=1.0)
    threshold_max: float | None = Field(default=None, ge=0.0, le=1.0)
    max_upload_mb_limit: int | None = Field(default=None, ge=1, le=500)
