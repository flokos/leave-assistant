"""Idempotent seed: runs through the provisioning and administration services (doc 07 §8).

Tenants are created as a synthetic platform principal (`seed`, audited as `system`); employees and departments
as a synthetic Company Administrator of each tenant, so the same authorization and audit paths apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from uuid import UUID

from leave_assistant.application.container import Services
from leave_assistant.application.leave.commands import (
    CancelRequest,
    CreateHoliday,
    CreateLeaveType,
    CreatePolicy,
    CreateRule,
    Decide,
    SubmitRequest,
)
from leave_assistant.application.tenancy.commands import (
    CreateDepartment,
    CreateEmployee,
    CreateTenant,
    UpdateDepartment,
    UpdateEmployee,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.request import StepStatus
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.tenancy import Employee, Principal, Role, Tenant
from leave_assistant.seed.data import (
    APPROVAL_RULES,
    CARRYOVER_MAX_DAYS,
    COMPANIES,
    LEAVE_TYPES,
    SEED_YEAR,
    SeedCompany,
    SeedRequest,
)

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

    await seed_leave_catalogue(services, company, tenant, admin, report)
    await seed_grants(services, tenant, report)
    await seed_requests(services, company, tenant, employees, report)
    return tenant


class _FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _services_as_of(services: Services, day: date) -> Services:
    """The same adapters with a clock set to `day` (sample requests are submitted 'in the past')."""
    return Services.build(
        uow_factory=services.uow_factory,
        identity=services.identity,
        verifier=services.verifier,
        clock=_FixedClock(datetime.combine(day, time(9, 0), tzinfo=UTC)),
        ids=services.ids,
    )


def employee_principal(tenant: Tenant, employee: Employee) -> Principal:
    return Principal(
        user_id=employee.keycloak_sub,
        username=employee.email,
        roles=employee.roles,
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=employee.department_id,
        locale=employee.locale,
        display_name=employee.display_name,
    )


async def seed_leave_catalogue(
    services: Services, company: SeedCompany, tenant: Tenant, admin: Principal, report: SeedReport
) -> None:
    existing_types = {t.code: t for t in await services.catalog.list_types(admin, include_inactive=True)}
    for spec in LEAVE_TYPES:
        label = f"leave-type:{company.slug}/{spec.code}"
        if spec.code in existing_types:
            report.unchanged.append(label)
        else:
            existing_types[spec.code] = await services.catalog.create_type(
                admin,
                CreateLeaveType(
                    spec.code,
                    spec.name_el,
                    spec.name_en,
                    spec.balance_bearing,
                    spec.allow_retroactive_days,
                    spec.requires_document_above_days,
                ),
            )
            report.created.append(label)
    policies = {(p.leave_type_id, p.year) for p in await services.catalog.list_policies(admin)}
    for spec in LEAVE_TYPES:
        if spec.entitlement_days is None:
            continue
        for year in (SEED_YEAR, SEED_YEAR + 1):
            label = f"policy:{company.slug}/{spec.code}/{year}"
            if (existing_types[spec.code].id, year) in policies:
                report.unchanged.append(label)
                continue
            await services.catalog.create_policy(
                admin,
                CreatePolicy(
                    spec.code,
                    year,
                    spec.entitlement_days,
                    CARRYOVER_MAX_DAYS,
                    date(year, 3, 31),
                    spec.allow_negative,
                    0,
                    None,
                    True,
                ),
            )
            report.created.append(label)
    holidays = {h.holiday_date for h in await services.catalog.list_holidays(admin)}
    for holiday in company.holidays:
        label = f"holiday:{company.slug}/{holiday.day.isoformat()}"
        if holiday.day in holidays:
            report.unchanged.append(label)
            continue
        await services.catalog.create_holiday(
            admin, CreateHoliday(holiday.day, holiday.name_el, holiday.name_en, holiday.recurring)
        )
        report.created.append(label)
    rules = {r.priority for r in await services.catalog.list_rules(admin, include_inactive=True)}
    for rule in APPROVAL_RULES:
        label = f"rule:{company.slug}/{rule.priority}"
        if rule.priority in rules:
            report.unchanged.append(label)
            continue
        await services.catalog.create_rule(
            admin,
            CreateRule(
                rule.steps,
                rule.leave_type_code,
                rule.min_working_days,
                rule.max_working_days,
                rule.requires_document,
                rule.auto_acknowledge,
                rule.priority,
            ),
        )
        report.created.append(label)


async def seed_grants(services: Services, tenant: Tenant, report: SeedReport) -> None:
    """Year grants through the rollover service (idempotent ledger keys)."""
    rollover = await services.rollover.run_for_tenant(tenant, date(SEED_YEAR, 1, 1))
    label = f"grants:{tenant.slug}/{SEED_YEAR}"
    (report.created if rollover.grants else report.unchanged).append(label)


async def seed_requests(
    services: Services,
    company: SeedCompany,
    tenant: Tenant,
    employees: dict[str, Employee],
    report: SeedReport,
) -> None:
    async with services.uow_factory(
        TenantContext(tenant_id=tenant.id, user_id=SEED_ACTOR, roles=("company_admin",))
    ) as uow:
        existing = {
            (r.employee_id, r.start_date)
            for r in await uow.leave_requests.list_by_status(frozenset(RequestStatus))
        }
        hr_admins = await uow.employees.list_active(role=Role.HR_ADMIN)
    for spec in company.requests:
        employee = employees[spec.employee_number]
        label = f"request:{company.slug}/{spec.employee_number}/{spec.start.isoformat()}"
        if (employee.id, spec.start) in existing:
            report.unchanged.append(label)
            continue
        await _submit_sample(services, tenant, employees, hr_admins, employee, spec)
        report.created.append(label)
    # past approved requests become ACTIVE/COMPLETED as of today
    await services.rollover.run_for_tenant(tenant, services.clock.now().date())


async def _submit_sample(
    services: Services,
    tenant: Tenant,
    employees: dict[str, Employee],
    hr_admins: list[Employee],
    employee: Employee,
    spec: SeedRequest,
) -> None:
    as_of = _services_as_of(services, spec.as_of)
    requester = employee_principal(tenant, employee)
    detail = await as_of.leave_requests.submit(
        requester,
        SubmitRequest(
            spec.leave_type_code,
            spec.start,
            spec.end,
            spec.reason,
            idempotency_key=f"seed:{employee.id}:{spec.start}",
        ),
    )
    if spec.outcome == "pending" or not detail.steps:
        return
    by_id = {e.id: e for e in employees.values()}
    for index, step in enumerate(detail.steps):
        if step.status is not StepStatus.PENDING:
            continue
        if step.designated_employee_id is not None:
            approver = by_id[step.designated_employee_id]
        else:
            approver = next(h for h in hr_admins if h.id != employee.id)
        decision = Decide(version=step.version, comment="seed")
        if spec.outcome == "rejected":
            await as_of.approvals.reject(
                employee_principal(tenant, approver),
                step.id,
                Decide(version=step.version, comment="Rejected in the seed data"),
            )
            return
        if spec.outcome == "pending_hr" and index == len(detail.steps) - 1:
            return  # leave the last (HR) step pending
        detail = await as_of.approvals.approve(employee_principal(tenant, approver), step.id, decision)
    if spec.outcome == "cancelled":
        await as_of.leave_requests.cancel(
            requester, detail.request.id, CancelRequest(version=detail.request.version, comment="seed")
        )


async def run_seed(services: Services, password: str) -> SeedReport:
    report = SeedReport()
    for company in COMPANIES:
        await seed_company(services, company, password, report)
    return report
