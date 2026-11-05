from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel, LocaleField
from leave_assistant.api.schemas.departments import DepartmentOut
from leave_assistant.api.schemas.employees import EmployeeOut
from leave_assistant.domain.tenancy import Tenant


class TenantSettingsOut(ApiModel):
    work_week: list[int]
    max_upload_mb: int
    conversation_retention_days: int
    proposal_ttl_minutes: int
    allow_hr_fallback: bool


class TenantOut(ApiModel):
    id: UUID
    slug: str
    name: str
    status: str
    default_locale: LocaleField
    timezone: str
    settings: TenantSettingsOut
    keycloak_group_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, tenant: Tenant) -> TenantOut:
        return cls(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status.value,
            default_locale=tenant.default_locale.value,
            timezone=tenant.timezone,
            settings=TenantSettingsOut(**tenant.settings.to_dict()),
            keycloak_group_id=tenant.keycloak_group_id,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )


class TenantCreate(ApiModel):
    slug: str = Field(min_length=3, max_length=32, examples=["aegean"])
    name: str = Field(min_length=1, max_length=120)
    default_locale: LocaleField = "en"
    timezone: str = Field(default="Europe/Athens", max_length=64)
    settings: dict[str, Any] | None = None


class TenantUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_locale: LocaleField | None = None
    timezone: str | None = Field(default=None, max_length=64)
    settings: dict[str, Any] | None = None


class BootstrapAdminRequest(ApiModel):
    email: str = Field(max_length=254)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    locale: LocaleField = "en"
    employee_number: str = Field(default="ADMIN-001", min_length=1, max_length=32)
    hire_date: date | None = None


class BootstrapAdminResponse(ApiModel):
    employee: EmployeeOut
    department: DepartmentOut
    username: str
    temporary_password: str = Field(description="Shown once; the user must change it at first login.")
