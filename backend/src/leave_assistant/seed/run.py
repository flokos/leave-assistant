"""Idempotent seed: runs through the provisioning and administration services (doc 07 §8).

Tenants are created as a synthetic platform principal (`seed`, audited as `system`); employees and departments
as a synthetic Company Administrator of each tenant, so the same authorization and audit paths apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import UUID

from leave_assistant.application.container import Services
from leave_assistant.application.documents.commands import CreateDocument, Upload
from leave_assistant.application.documents.service import sha256_of
from leave_assistant.application.leave.commands import (
    CancelRequest,
    CreateHoliday,
    CreateLeaveType,
    CreatePolicy,
    CreateRule,
    Decide,
    SubmitRequest,
)
from leave_assistant.application.retention.service import CreateRetentionPolicy
from leave_assistant.application.tenancy.commands import (
    CreateDepartment,
    CreateEmployee,
    CreateTenant,
    UpdateDepartment,
    UpdateEmployee,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.formats import DOCX, MARKDOWN, PDF, TEXT
from leave_assistant.domain.leave.request import StepStatus
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.retention.eligibility import DocumentClass, RetentionAction
from leave_assistant.domain.tenancy import Department, Employee, Principal, Role, Tenant
from leave_assistant.seed.data import (
    APPROVAL_RULES,
    CARRYOVER_MAX_DAYS,
    COMPANIES,
    LEAVE_TYPES,
    SEED_YEAR,
    SeedCompany,
    SeedRequest,
)

CORPUS_FILES = Path(__file__).resolve().parent / "corpus" / "files"
CORPUS_SUPPORTING = Path(__file__).resolve().parent / "corpus" / "supporting"
CORPUS_MIME = {"pdf": PDF, "docx": DOCX, "md": MARKDOWN, "txt": TEXT}

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
    await seed_documents(services, company, tenant, employees, departments, report)
    await seed_security(services, company, tenant, admin, report)
    await seed_supporting_documents(services, company, tenant, employees, report)
    return tenant


async def seed_security(
    services: Services, company: SeedCompany, tenant: Tenant, admin: Principal, report: SeedReport
) -> None:
    """M5: the tenant's security-policy row (platform defaults) and placeholder retention policies."""
    context = TenantContext.for_principal(admin)
    async with services.uow_factory(context) as uow:
        existed = (
            await uow.security_policies.get() is not None
        )  # provisioning creates it; seed back-fills older tenants
        await services.security_policies.ensure_policy(uow, tenant.id, updated_by=SEED_ACTOR)
        await uow.commit()
    if not existed:
        report.created.append(f"security-policy:{company.slug}")
    existing = {p.document_class.value for p in await services.retention.list_policies(admin)}
    for spec in company.retention_policies:
        label = f"retention-policy:{company.slug}/{spec.document_class}"
        if spec.document_class in existing:
            report.unchanged.append(label)
            continue
        await services.retention.create_policy(
            admin,
            CreateRetentionPolicy(
                document_class=DocumentClass(spec.document_class),
                retention_days=spec.retention_days,
                action=RetentionAction(spec.action),
            ),
        )
        report.created.append(label)


async def seed_supporting_documents(
    services: Services,
    company: SeedCompany,
    tenant: Tenant,
    employees: dict[str, Employee],
    report: SeedReport,
) -> None:
    """M5: fictional supporting documents uploaded by the employee to their own seeded request (idempotent by
    request + file name). Skipped with a report entry when the encryption keys are not configured."""
    for spec in company.supporting_documents:
        employee = employees[spec.employee_number]
        principal = employee_principal(tenant, employee)
        label = f"supporting:{company.slug}/{spec.employee_number}/{spec.request_start.isoformat()}"
        async with services.uow_factory(TenantContext.for_principal(principal)) as uow:
            request = next(
                (
                    r
                    for r in await uow.leave_requests.list_for_employee(employee.id)
                    if r.start_date == spec.request_start
                ),
                None,
            )
            attached = await uow.supporting_documents.list_for_request(request.id) if request else []
        if request is None:
            report.unchanged.append(label + " (request missing)")
            continue
        if any(d.original_filename == spec.file and d.status != "deleted" for d in attached):
            report.unchanged.append(label)
            continue
        if not services.supporting.configured:
            report.unchanged.append(label + " (APP_KEK not configured)")
            continue
        path = CORPUS_SUPPORTING / spec.file
        await services.supporting.upload(
            principal,
            request.id,
            Upload(path=path, filename=spec.file, declared_mime=None, size_bytes=path.stat().st_size),
        )
        report.created.append(label)


async def seed_documents(
    services: Services,
    company: SeedCompany,
    tenant: Tenant,
    employees: dict[str, Employee],
    departments: dict[str, Department],
    report: SeedReport,
) -> None:
    """Upload the corpus through DocumentService as the real uploader; processing jobs run in the worker
    (`make ingest`). Idempotent by title + content hash: re-runs add nothing."""
    for spec in company.documents:
        principal = employee_principal(tenant, employees[spec.uploader_number])
        department_id = departments[spec.department_code].id if spec.department_code else None
        async with services.uow_factory(TenantContext.for_principal(principal)) as uow:
            existing = await uow.documents.get_by_title(spec.title)
        document_id = existing.id if existing else None
        known: set[str] = set()
        if existing is not None:
            known = {v.sha256 for v in await services.documents.versions(principal, existing.id)}
        for stem in (*spec.earlier_versions, spec.slug):
            path = CORPUS_FILES / company.slug / f"{stem}.{spec.extension}"
            label = f"document:{company.slug}/{stem}"
            if sha256_of(path) in known:
                report.unchanged.append(label)
                continue
            upload = Upload(
                path=path,
                filename=path.name,
                declared_mime=CORPUS_MIME[spec.extension],
                size_bytes=path.stat().st_size,
            )
            if document_id is None:
                created = await services.documents.create(
                    principal,
                    CreateDocument(
                        title=spec.title,
                        category=spec.category,
                        upload=upload,
                        department_id=department_id,
                        language_hint=spec.language_hint,
                    ),
                )
                document_id = created.document.id
            else:
                await services.documents.add_version(principal, document_id, upload)
            report.created.append(label)


class _FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _services_as_of(services: Services, day: date) -> Services:
    """The same adapters with a clock set to `day` (sample requests are submitted 'in the past')."""
    return services.with_clock(_FixedClock(datetime.combine(day, time(9, 0), tzinfo=UTC)))


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
