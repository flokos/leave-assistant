"""`GET /me` view: the principal plus tenant, employee and department summaries (doc 09 §2.1)."""

from __future__ import annotations

from dataclasses import dataclass

from leave_assistant.application.ports import UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Department, Employee, Principal, Tenant


@dataclass(frozen=True, slots=True)
class MeView:
    principal: Principal
    tenant: Tenant | None
    employee: Employee | None
    department: Department | None
    pending_interrupt_count: int = 0


async def load_me(principal: Principal, uow_factory: UnitOfWorkFactory) -> MeView:
    if not principal.is_tenant_user or principal.tenant_id is None or principal.employee_id is None:
        return MeView(principal=principal, tenant=None, employee=None, department=None)
    async with uow_factory(TenantContext.for_principal(principal)) as uow:
        tenant = await uow.tenants.get(principal.tenant_id)
        employee = await uow.employees.get(principal.employee_id)
        department = await uow.departments.get(employee.department_id) if employee else None
    return MeView(principal=principal, tenant=tenant, employee=employee, department=department)
