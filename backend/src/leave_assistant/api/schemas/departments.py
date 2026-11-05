from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.domain.tenancy import Department


class DepartmentOut(ApiModel):
    id: UUID
    code: str
    name: str
    manager_employee_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, department: Department) -> DepartmentOut:
        return cls(
            id=department.id,
            code=department.code,
            name=department.name,
            manager_employee_id=department.manager_employee_id,
            created_at=department.created_at,
            updated_at=department.updated_at,
        )


class DepartmentCreate(ApiModel):
    code: str = Field(min_length=1, max_length=32, examples=["OPS"])
    name: str = Field(min_length=1, max_length=120)


class DepartmentUpdate(ApiModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class DepartmentManagerUpdate(ApiModel):
    employee_id: UUID | None = Field(
        description="Active member of the department holding the manager role; null clears it."
    )
