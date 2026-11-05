"""Company administration: employees (doc 09 §2.8)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from leave_assistant.api.deps import CompanyAdmin, CurrentServices
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.api.schemas.employees import (
    EmployeeCreate,
    EmployeeCreated,
    EmployeeOut,
    EmployeeRolesUpdate,
    EmployeeUpdate,
)
from leave_assistant.application.ports import EmployeeFilter
from leave_assistant.application.tenancy.commands import UNSET, CreateEmployee, UpdateEmployee
from leave_assistant.domain.tenancy import Locale, Role

router = APIRouter(prefix="/admin/employees", tags=["admin"], route_class=IdempotentRoute)


@router.get("", response_model=PageOut[EmployeeOut])
async def list_employees(
    principal: CompanyAdmin,
    services: CurrentServices,
    department_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status", pattern="^(active|inactive)$")] = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[EmployeeOut]:
    page = await services.employees.list(
        principal,
        filters=EmployeeFilter(department_id=department_id, status=status_filter, query=q),
        limit=limit,
        cursor=cursor,
    )
    return PageOut(items=[EmployeeOut.from_domain(e) for e in page.items], next_cursor=page.next_cursor)


@router.post("", response_model=EmployeeCreated, status_code=status.HTTP_201_CREATED)
async def create_employee(
    body: EmployeeCreate, principal: CompanyAdmin, services: CurrentServices
) -> EmployeeCreated:
    created = await services.employees.create(
        principal,
        CreateEmployee(
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            employee_number=body.employee_number,
            department_id=body.department_id,
            locale=Locale(body.locale),
            hire_date=body.hire_date,
            roles=frozenset(Role(r) for r in body.roles),
            manager_override_employee_id=body.manager_override_employee_id,
        ),
    )
    return EmployeeCreated(
        employee=EmployeeOut.from_domain(created.employee), temporary_password=created.temporary_password
    )


@router.get("/{employee_id}", response_model=EmployeeOut)
async def get_employee(employee_id: UUID, principal: CompanyAdmin, services: CurrentServices) -> EmployeeOut:
    return EmployeeOut.from_domain(await services.employees.get(principal, employee_id))


@router.patch("/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: UUID, body: EmployeeUpdate, principal: CompanyAdmin, services: CurrentServices
) -> EmployeeOut:
    employee = await services.employees.update(
        principal,
        employee_id,
        UpdateEmployee(
            first_name=body.first_name,
            last_name=body.last_name,
            employee_number=body.employee_number,
            department_id=body.department_id,
            locale=Locale(body.locale) if body.locale else None,
            hire_date=body.hire_date,
            manager_override_employee_id=(
                body.manager_override_employee_id
                if "manager_override_employee_id" in body.model_fields_set
                else UNSET
            ),
        ),
    )
    return EmployeeOut.from_domain(employee)


@router.post("/{employee_id}/deactivate", response_model=EmployeeOut)
async def deactivate_employee(
    employee_id: UUID, principal: CompanyAdmin, services: CurrentServices
) -> EmployeeOut:
    return EmployeeOut.from_domain(await services.employees.deactivate(principal, employee_id))


@router.put("/{employee_id}/roles", response_model=EmployeeOut)
async def set_employee_roles(
    employee_id: UUID, body: EmployeeRolesUpdate, principal: CompanyAdmin, services: CurrentServices
) -> EmployeeOut:
    employee = await services.employees.set_roles(
        principal, employee_id, frozenset(Role(r) for r in body.roles)
    )
    return EmployeeOut.from_domain(employee)
