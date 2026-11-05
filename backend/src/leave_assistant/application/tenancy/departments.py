"""Department administration (doc 09 §2.8; Q-11: one manager per department)."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.errors import ConflictError, InvalidInputError, NotFoundError
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWorkFactory
from leave_assistant.application.tenancy.commands import CreateDepartment, UpdateDepartment
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.application.tenancy.validation import validate_code, validate_name
from leave_assistant.domain.tenancy import Department, Principal, Role


class DepartmentService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, ids: IdGenerator, clock: Clock, audit: AuditService
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit

    async def list(self, principal: Principal) -> list[Department]:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.departments.list()

    async def get(self, principal: Principal, department_id: UUID) -> Department:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            department = await uow.departments.get(department_id)
        return self._found(principal, department)

    async def create(self, principal: Principal, cmd: CreateDepartment) -> Department:
        require(principal, Action.MANAGE_ORGANISATION)
        code = validate_code(cmd.code)
        name = validate_name(cmd.name, "name")
        now = self._clock.now()
        assert principal.tenant_id is not None  # guaranteed by require() for tenant-scoped actions
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            if await uow.departments.get_by_code(code) is not None:
                raise ConflictError(f"department code {code!r} already exists")
            department = Department(
                id=self._ids.new_id(),
                tenant_id=principal.tenant_id,
                code=code,
                name=name,
                created_at=now,
                updated_at=now,
            )
            await uow.departments.add(department)
            await self._audit.record(
                uow,
                principal,
                "admin.department.created",
                resource_type="department",
                resource_id=department.id,
                details={"code": code},
            )
            await uow.commit()
        return department

    async def update(self, principal: Principal, department_id: UUID, cmd: UpdateDepartment) -> Department:
        require(principal, Action.MANAGE_ORGANISATION)
        changes: dict[str, object] = {}
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            department = self._found(principal, await uow.departments.get(department_id))
            if cmd.code is not None and validate_code(cmd.code) != department.code:
                code = validate_code(cmd.code)
                if await uow.departments.get_by_code(code) is not None:
                    raise ConflictError(f"department code {code!r} already exists")
                department.code = code
                changes["code"] = code
            if cmd.name is not None:
                department.name = validate_name(cmd.name, "name")
                changes["name"] = department.name
            if not changes:
                return department
            department.updated_at = self._clock.now()
            await uow.departments.update(department)
            await self._audit.record(
                uow,
                principal,
                "admin.department.updated",
                resource_type="department",
                resource_id=department.id,
                details={"changes": changes},
            )
            await uow.commit()
        return department

    async def delete(self, principal: Principal, department_id: UUID) -> None:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            department = self._found(principal, await uow.departments.get(department_id))
            if await uow.employees.count_in_department(department.id) > 0:
                raise ConflictError("department still has employees", problem_type="department/not-empty")
            await uow.departments.delete(department.id)
            await self._audit.record(
                uow,
                principal,
                "admin.department.deleted",
                resource_type="department",
                resource_id=department.id,
                details={"code": department.code},
            )
            await uow.commit()

    async def set_manager(
        self, principal: Principal, department_id: UUID, employee_id: UUID | None
    ) -> Department:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            department = self._found(principal, await uow.departments.get(department_id))
            if employee_id is not None:
                manager = await uow.employees.get(employee_id)
                if manager is None or not manager.is_active:
                    raise InvalidInputError(
                        "unknown or inactive employee",
                        errors=[{"loc": ["body", "employee_id"], "msg": "unknown employee"}],
                    )
                if manager.department_id != department.id:
                    raise InvalidInputError("the manager must be a member of the department")
                if Role.MANAGER not in manager.roles:
                    raise InvalidInputError("the employee does not hold the manager role")
            if department.manager_employee_id == employee_id:
                return department
            previous = department.manager_employee_id
            department.manager_employee_id = employee_id
            department.updated_at = self._clock.now()
            await uow.departments.update(department)
            await self._audit.record(
                uow,
                principal,
                "admin.department.manager.changed",
                resource_type="department",
                resource_id=department.id,
                details={"from": previous, "to": employee_id},
            )
            await uow.commit()
        return department

    @staticmethod
    def _found(principal: Principal, department: Department | None) -> Department:
        if department is None or department.tenant_id != principal.tenant_id:
            raise NotFoundError("department not found")
        return department
