"""Idempotent seed: runs through the provisioning and administration services (doc 07 §8).

Tenants are created as a synthetic platform principal (`seed`, audited as `system`); employees and departments
as a synthetic Company Administrator of each tenant, so the same authorization and audit paths apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from leave_assistant.application.container import Services
from leave_assistant.application.tenancy.commands import (
    CreateDepartment,
    CreateEmployee,
    CreateTenant,
    UpdateDepartment,
    UpdateEmployee,
)
from leave_assistant.domain.tenancy import Employee, Principal, Role, Tenant
from leave_assistant.seed.data import COMPANIES, SeedCompany

SEED_ACTOR = "seed"
SEED_ACTOR_EMPLOYEE_ID = UUID("00000000-0000-0000-0000-0000000005ee")
PLATFORM_PRINCIPAL = Principal(
    user_id=SEED_ACTOR, username=SEED_ACTOR, roles=frozenset({Role.PLATFORM_ADMIN}), display_name="seed"
)


def tenant_admin_principal(tenant: Tenant) -> Principal:
    return Principal(
        user_id=SEED_ACTOR,
        username=SEED_ACTOR,
        roles=frozenset({Role.COMPANY_ADMIN}),
        tenant_id=tenant.id,
        employee_id=SEED_ACTOR_EMPLOYEE_ID,
        display_name="seed",
    )


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.created) + len(self.updated)


async def seed_company(services: Services, company: SeedCompany, password: str, report: SeedReport) -> Tenant:
    tenant, created = await services.provisioning.ensure_tenant(
        PLATFORM_PRINCIPAL,
        CreateTenant(
            slug=company.slug,
            name=company.name,
            default_locale=company.default_locale,
            timezone=company.timezone,
            settings=dict(company.settings) or None,
        ),
        allow_existing=True,
    )
    (report.created if created else report.unchanged).append(f"tenant:{company.slug}")
    admin = tenant_admin_principal(tenant)

    departments = {d.code: d for d in await services.departments.list(admin)}
    for dept_spec in company.departments:
        current_dept = departments.get(dept_spec.code)
        if current_dept is None:
            departments[dept_spec.code] = await services.departments.create(
                admin, CreateDepartment(dept_spec.code, dept_spec.name)
            )
            report.created.append(f"department:{company.slug}/{dept_spec.code}")
        elif current_dept.name != dept_spec.name:
            departments[dept_spec.code] = await services.departments.update(
                admin, current_dept.id, UpdateDepartment(name=dept_spec.name)
            )
            report.updated.append(f"department:{company.slug}/{dept_spec.code}")
        else:
            report.unchanged.append(f"department:{company.slug}/{dept_spec.code}")

    employees: dict[str, Employee] = {}
    for emp_spec in company.employees:
        locale = emp_spec.locale or company.default_locale
        department = departments[emp_spec.department_code]
        current = await services.employees.find_by_email(admin, emp_spec.email)
        label = f"employee:{company.slug}/{emp_spec.number}"
        if current is None:
            created_employee = await services.employees.create(
                admin,
                CreateEmployee(
                    email=emp_spec.email,
                    first_name=emp_spec.first_name,
                    last_name=emp_spec.last_name,
                    employee_number=emp_spec.number,
                    department_id=department.id,
                    locale=locale,
                    hire_date=emp_spec.hire_date,
                    roles=emp_spec.roles,
                    password=password,
                    temporary_password=False,
                ),
            )
            employees[emp_spec.number] = created_employee.employee
            report.created.append(label)
            continue
        changed = False
        if (
            current.first_name != emp_spec.first_name
            or current.last_name != emp_spec.last_name
            or current.employee_number != emp_spec.number
            or current.department_id != department.id
            or current.locale != locale
            or current.hire_date != emp_spec.hire_date
        ):
            current = await services.employees.update(
                admin,
                current.id,
                UpdateEmployee(
                    first_name=emp_spec.first_name,
                    last_name=emp_spec.last_name,
                    employee_number=emp_spec.number,
                    department_id=department.id,
                    locale=locale,
                    hire_date=emp_spec.hire_date,
                ),
            )
            changed = True
        if current.roles != emp_spec.roles:
            current = await services.employees.set_roles(admin, current.id, emp_spec.roles)
            changed = True
        employees[emp_spec.number] = current
        (report.updated if changed else report.unchanged).append(label)

    for dept_spec in company.departments:
        if dept_spec.manager_number is None:
            continue
        department = departments[dept_spec.code]
        manager = employees[dept_spec.manager_number]
        if department.manager_employee_id != manager.id:
            await services.departments.set_manager(admin, department.id, manager.id)
            report.updated.append(f"department-manager:{company.slug}/{dept_spec.code}")
    return tenant


async def run_seed(services: Services, password: str) -> SeedReport:
    report = SeedReport()
    for company in COMPANIES:
        await seed_company(services, company, password, report)
    return report
