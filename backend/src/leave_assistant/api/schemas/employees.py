from __future__ import annotations

from datetime import date, datetime
from typing import cast
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel, LocaleField, RoleField
from leave_assistant.domain.tenancy import Employee


class EmployeeOut(ApiModel):
    id: UUID
    employee_number: str
    first_name: str
    last_name: str
    email: str
    department_id: UUID
    manager_override_employee_id: UUID | None
    hire_date: date
    locale: LocaleField
    status: str
    roles: list[RoleField]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, employee: Employee) -> EmployeeOut:
        return cls(
            id=employee.id,
            employee_number=employee.employee_number,
            first_name=employee.first_name,
            last_name=employee.last_name,
            email=employee.email,
            department_id=employee.department_id,
            manager_override_employee_id=employee.manager_override_employee_id,
            hire_date=employee.hire_date,
            locale=employee.locale.value,
            status=employee.status.value,
            roles=[cast(RoleField, r.value) for r in sorted(employee.roles, key=lambda r: r.value)],
            created_at=employee.created_at,
            updated_at=employee.updated_at,
        )


class EmployeeCreate(ApiModel):
    email: str = Field(max_length=254)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    employee_number: str = Field(min_length=1, max_length=32)
    department_id: UUID
    locale: LocaleField = "en"
    hire_date: date
    roles: list[RoleField] = Field(min_length=1)
    manager_override_employee_id: UUID | None = None


class EmployeeCreated(ApiModel):
    employee: EmployeeOut
    temporary_password: str | None = Field(description="Shown once; the user must change it at first login.")


class EmployeeUpdate(ApiModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    employee_number: str | None = Field(default=None, min_length=1, max_length=32)
    department_id: UUID | None = None
    locale: LocaleField | None = None
    hire_date: date | None = None
    manager_override_employee_id: UUID | None = None


class EmployeeRolesUpdate(ApiModel):
    roles: list[RoleField] = Field(min_length=1)
