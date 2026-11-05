"""Company administration: departments (doc 09 §2.8)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from leave_assistant.api.deps import CompanyAdmin, CurrentServices
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.departments import (
    DepartmentCreate,
    DepartmentManagerUpdate,
    DepartmentOut,
    DepartmentUpdate,
)
from leave_assistant.application.tenancy.commands import CreateDepartment, UpdateDepartment

router = APIRouter(prefix="/admin/departments", tags=["admin"], route_class=IdempotentRoute)


@router.get("", response_model=list[DepartmentOut])
async def list_departments(principal: CompanyAdmin, services: CurrentServices) -> list[DepartmentOut]:
    return [DepartmentOut.from_domain(d) for d in await services.departments.list(principal)]


@router.post("", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
async def create_department(
    body: DepartmentCreate, principal: CompanyAdmin, services: CurrentServices
) -> DepartmentOut:
    department = await services.departments.create(
        principal, CreateDepartment(code=body.code, name=body.name)
    )
    return DepartmentOut.from_domain(department)


@router.get("/{department_id}", response_model=DepartmentOut)
async def get_department(
    department_id: UUID, principal: CompanyAdmin, services: CurrentServices
) -> DepartmentOut:
    return DepartmentOut.from_domain(await services.departments.get(principal, department_id))


@router.patch("/{department_id}", response_model=DepartmentOut)
async def update_department(
    department_id: UUID, body: DepartmentUpdate, principal: CompanyAdmin, services: CurrentServices
) -> DepartmentOut:
    department = await services.departments.update(
        principal, department_id, UpdateDepartment(code=body.code, name=body.name)
    )
    return DepartmentOut.from_domain(department)


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_department(
    department_id: UUID, principal: CompanyAdmin, services: CurrentServices
) -> Response:
    await services.departments.delete(principal, department_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{department_id}/manager", response_model=DepartmentOut)
async def set_department_manager(
    department_id: UUID, body: DepartmentManagerUpdate, principal: CompanyAdmin, services: CurrentServices
) -> DepartmentOut:
    department = await services.departments.set_manager(principal, department_id, body.employee_id)
    return DepartmentOut.from_domain(department)
