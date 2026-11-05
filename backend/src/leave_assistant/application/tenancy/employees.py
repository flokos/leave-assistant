"""Employee administration (doc 09 §2.8): Keycloak user + employee row stay consistent (FR-AUTH-07)."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.errors import ConflictError, InvalidInputError, NotFoundError
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import (
    Clock,
    EmployeeFilter,
    IdentityAdmin,
    IdGenerator,
    NewIdentityUser,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.tenancy.commands import (
    UNSET,
    CreatedEmployee,
    CreateEmployee,
    UpdateEmployee,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.application.tenancy.passwords import generate_temporary_password
from leave_assistant.application.tenancy.validation import validate_code, validate_name
from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.tenancy import TENANT_ROLES, Employee, EmployeeStatus, Principal, Role
from leave_assistant.domain.tenancy.entities import validate_email


def _validate_roles(roles: frozenset[Role]) -> frozenset[Role]:
    if not roles:
        raise InvalidValue("at least one role is required", params={"field": "roles"})
    if not roles <= TENANT_ROLES:
        raise InvalidValue(
            "roles must be tenant roles (employee, manager, hr_admin, company_admin)",
            params={"field": "roles"},
        )
    return roles


class EmployeeService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        identity: IdentityAdmin,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
    ) -> None:
        self._uow = uow_factory
        self._identity = identity
        self._ids = ids
        self._clock = clock
        self._audit = audit

    async def list(
        self, principal: Principal, *, filters: EmployeeFilter, limit: int | None, cursor: str | None
    ) -> Page[Employee]:
        require(principal, Action.MANAGE_ORGANISATION)
        size = clamp_limit(limit)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            rows = await uow.employees.list(filters=filters, limit=size + 1, after=decode_cursor(cursor))
        return build_page(rows, size, lambda e: (e.created_at, e.id))

    async def get(self, principal: Principal, employee_id: UUID) -> Employee:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            employee = await uow.employees.get(employee_id)
        return self._found(principal, employee)

    async def find_by_email(self, principal: Principal, email: str) -> Employee | None:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.employees.get_by_email(validate_email(email))

    async def create(self, principal: Principal, cmd: CreateEmployee) -> CreatedEmployee:
        require(principal, Action.MANAGE_ORGANISATION)
        email = validate_email(cmd.email)
        first_name = validate_name(cmd.first_name, "first_name", max_length=80)
        last_name = validate_name(cmd.last_name, "last_name", max_length=80)
        employee_number = validate_code(cmd.employee_number, "employee_number")
        roles = _validate_roles(cmd.roles)
        context = TenantContext.for_principal(principal)

        async with self._uow(context) as uow:
            tenant = await uow.tenants.get(context.tenant_id) if context.tenant_id else None
            if tenant is None:
                raise NotFoundError("tenant not found")
            await self._require_department(uow, cmd.department_id)
            if cmd.manager_override_employee_id is not None:
                await self._require_employee(
                    uow, cmd.manager_override_employee_id, "manager_override_employee_id"
                )
            if await uow.employees.get_by_email(email) is not None:
                raise ConflictError("an employee with this e-mail already exists")
        if await self._identity.find_user_by_username(email) is not None:
            raise ConflictError("a user with this e-mail already exists in the identity provider")

        password = cmd.password or generate_temporary_password()
        temporary = cmd.temporary_password
        sub = await self._identity.create_user(
            NewIdentityUser(
                username=email,
                email=email,
                first_name=first_name,
                last_name=last_name,
                locale=cmd.locale.value,
                password=password,
                temporary_password=temporary,
                realm_roles=frozenset(r.value for r in roles),
                group_path=f"/tenants/{tenant.slug}",
            )
        )
        now = self._clock.now()
        employee = Employee(
            id=self._ids.new_id(),
            tenant_id=tenant.id,
            keycloak_sub=sub,
            employee_number=employee_number,
            first_name=first_name,
            last_name=last_name,
            email=email,
            department_id=cmd.department_id,
            hire_date=cmd.hire_date,
            locale=cmd.locale,
            status=EmployeeStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            roles=roles,
            manager_override_employee_id=cmd.manager_override_employee_id,
        )
        try:
            async with self._uow(context) as uow:
                await uow.employees.add(employee)
                await uow.employees.set_roles(employee.id, roles)
                await self._audit.record(
                    uow,
                    principal,
                    "admin.employee.created",
                    resource_type="employee",
                    resource_id=employee.id,
                    details={"roles": sorted(r.value for r in roles), "department_id": cmd.department_id},
                )
                await uow.commit()
        except Exception:
            await self._identity.delete_user(sub)
            raise
        return CreatedEmployee(employee=employee, temporary_password=password if temporary else None)

    async def update(self, principal: Principal, employee_id: UUID, cmd: UpdateEmployee) -> Employee:
        require(principal, Action.MANAGE_ORGANISATION)
        context = TenantContext.for_principal(principal)
        changes: dict[str, object] = {}
        async with self._uow(context) as uow:
            employee = self._found(principal, await uow.employees.get(employee_id))
            if cmd.first_name is not None:
                employee.first_name = validate_name(cmd.first_name, "first_name", max_length=80)
                changes["first_name"] = True
            if cmd.last_name is not None:
                employee.last_name = validate_name(cmd.last_name, "last_name", max_length=80)
                changes["last_name"] = True
            if cmd.employee_number is not None:
                employee.employee_number = validate_code(cmd.employee_number, "employee_number")
                changes["employee_number"] = employee.employee_number
            if cmd.department_id is not None and cmd.department_id != employee.department_id:
                await self._require_department(uow, cmd.department_id)
                employee.department_id = cmd.department_id
                changes["department_id"] = cmd.department_id
            if cmd.locale is not None:
                employee.locale = cmd.locale
                changes["locale"] = cmd.locale
            if cmd.hire_date is not None:
                employee.hire_date = cmd.hire_date
                changes["hire_date"] = cmd.hire_date
            if cmd.manager_override_employee_id is not UNSET:
                if cmd.manager_override_employee_id is not None:
                    if cmd.manager_override_employee_id == employee.id:
                        raise InvalidInputError("an employee cannot be their own manager override")
                    await self._require_employee(
                        uow, cmd.manager_override_employee_id, "manager_override_employee_id"
                    )
                employee.manager_override_employee_id = cmd.manager_override_employee_id
                changes["manager_override_employee_id"] = cmd.manager_override_employee_id
            if not changes:
                return employee
            employee.updated_at = self._clock.now()
            if {"first_name", "last_name", "locale"} & changes.keys():
                await self._identity.update_user(
                    employee.keycloak_sub,
                    first_name=employee.first_name,
                    last_name=employee.last_name,
                    locale=employee.locale.value,
                )
            await uow.employees.update(employee)
            await self._audit.record(
                uow,
                principal,
                "admin.employee.updated",
                resource_type="employee",
                resource_id=employee.id,
                details={"changes": changes},
            )
            await uow.commit()
        return employee

    async def deactivate(self, principal: Principal, employee_id: UUID) -> Employee:
        require(principal, Action.MANAGE_ORGANISATION)
        if employee_id == principal.employee_id:
            raise ConflictError("you cannot deactivate your own account")
        context = TenantContext.for_principal(principal)
        async with self._uow(context) as uow:
            employee = self._found(principal, await uow.employees.get(employee_id))
            if employee.status == EmployeeStatus.INACTIVE:
                return employee
            await self._identity.set_enabled(employee.keycloak_sub, False)
            employee.status = EmployeeStatus.INACTIVE
            employee.updated_at = self._clock.now()
            await uow.employees.update(employee)
            await self._audit.record(
                uow,
                principal,
                "admin.employee.deactivated",
                resource_type="employee",
                resource_id=employee.id,
            )
            await uow.commit()
        return employee

    async def set_roles(self, principal: Principal, employee_id: UUID, roles: frozenset[Role]) -> Employee:
        require(principal, Action.MANAGE_ORGANISATION)
        roles = _validate_roles(roles)
        if employee_id == principal.employee_id and Role.COMPANY_ADMIN not in roles:
            raise ConflictError("you cannot remove your own company_admin role")
        context = TenantContext.for_principal(principal)
        async with self._uow(context) as uow:
            employee = self._found(principal, await uow.employees.get(employee_id))
            if employee.roles == roles:
                return employee
            previous = employee.roles
            await self._identity.set_realm_roles(
                employee.keycloak_sub,
                frozenset(r.value for r in roles),
                managed=frozenset(r.value for r in TENANT_ROLES),
            )
            await uow.employees.set_roles(employee.id, roles)
            employee.roles = roles
            employee.updated_at = self._clock.now()
            await uow.employees.update(employee)
            await self._audit.record(
                uow,
                principal,
                "admin.role.changed",
                resource_type="employee",
                resource_id=employee.id,
                details={"from": sorted(r.value for r in previous), "to": sorted(r.value for r in roles)},
            )
            await uow.commit()
        return employee

    @staticmethod
    def _found(principal: Principal, employee: Employee | None) -> Employee:
        # RLS already hides other tenants' rows; the explicit tenant check is the application-layer belt.
        if employee is None or employee.tenant_id != principal.tenant_id:
            raise NotFoundError("employee not found")
        return employee

    @staticmethod
    async def _require_department(uow: UnitOfWork, department_id: UUID) -> None:
        if await uow.departments.get(department_id) is None:
            raise InvalidInputError(
                "unknown department", errors=[{"loc": ["body", "department_id"], "msg": "unknown department"}]
            )

    @staticmethod
    async def _require_employee(uow: UnitOfWork, employee_id: UUID, field: str) -> Employee:
        employee = await uow.employees.get(employee_id)
        if employee is None or not employee.is_active:
            raise InvalidInputError(
                f"unknown or inactive employee for {field}",
                errors=[{"loc": ["body", field], "msg": "unknown employee"}],
            )
        return employee
