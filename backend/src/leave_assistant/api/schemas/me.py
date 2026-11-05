from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from leave_assistant.api.schemas.common import ApiModel, LocaleField
from leave_assistant.application.identity.me import MeView


class TenantSummary(ApiModel):
    id: UUID
    slug: str
    name: str
    default_locale: LocaleField
    timezone: str


class DepartmentSummary(ApiModel):
    id: UUID
    code: str
    name: str


class EmployeeSummary(ApiModel):
    id: UUID
    employee_number: str
    first_name: str
    last_name: str
    email: str
    hire_date: date
    status: str
    department: DepartmentSummary | None


class MeResponse(ApiModel):
    user_id: str
    username: str
    display_name: str
    roles: list[str]
    locale: LocaleField
    is_platform_admin: bool
    is_automation: bool
    tenant: TenantSummary | None
    employee: EmployeeSummary | None
    feature_flags: dict[str, Any]
    pending_interrupt_count: int

    @classmethod
    def from_view(cls, view: MeView, feature_flags: dict[str, Any]) -> MeResponse:
        p = view.principal
        tenant = (
            TenantSummary(
                id=view.tenant.id,
                slug=view.tenant.slug,
                name=view.tenant.name,
                default_locale=view.tenant.default_locale.value,
                timezone=view.tenant.timezone,
            )
            if view.tenant
            else None
        )
        department = (
            DepartmentSummary(id=view.department.id, code=view.department.code, name=view.department.name)
            if view.department
            else None
        )
        employee = (
            EmployeeSummary(
                id=view.employee.id,
                employee_number=view.employee.employee_number,
                first_name=view.employee.first_name,
                last_name=view.employee.last_name,
                email=view.employee.email,
                hire_date=view.employee.hire_date,
                status=view.employee.status.value,
                department=department,
            )
            if view.employee
            else None
        )
        return cls(
            user_id=p.user_id,
            username=p.username,
            display_name=p.display_name,
            roles=sorted(r.value for r in p.roles),
            locale=p.locale.value,
            is_platform_admin=p.is_platform_admin,
            is_automation=p.is_automation,
            tenant=tenant,
            employee=employee,
            feature_flags=feature_flags,
            pending_interrupt_count=view.pending_interrupt_count,
        )
