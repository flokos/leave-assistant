from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.ports import EmployeeFilter
from leave_assistant.domain.tenancy import Employee, EmployeeStatus, Locale, Role
from leave_assistant.infrastructure.db.models import EmployeeRoleRow, EmployeeRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _to_domain(row: EmployeeRow, roles: Iterable[str]) -> Employee:
    return Employee(
        id=row.id,
        tenant_id=row.tenant_id,
        keycloak_sub=row.keycloak_sub,
        employee_number=row.employee_number,
        first_name=row.first_name,
        last_name=row.last_name,
        email=row.email,
        department_id=row.department_id,
        manager_override_employee_id=row.manager_override_employee_id,
        hire_date=row.hire_date,
        locale=Locale(row.locale),
        status=EmployeeStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        roles=frozenset(Role(r) for r in roles),
    )


class SqlEmployeeRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[EmployeeRow]:
        return select(EmployeeRow).where(EmployeeRow.tenant_id == self._tenant)

    async def _roles_for(self, employee_ids: list[UUID]) -> dict[UUID, list[str]]:
        if not employee_ids:
            return {}
        stmt = select(EmployeeRoleRow.employee_id, EmployeeRoleRow.role).where(
            EmployeeRoleRow.employee_id.in_(employee_ids), EmployeeRoleRow.tenant_id == self._tenant
        )
        roles: dict[UUID, list[str]] = {}
        for employee_id, role in (await self._s.execute(stmt)).all():
            roles.setdefault(employee_id, []).append(role)
        return roles

    async def _one(self, stmt: Select[EmployeeRow]) -> Employee | None:
        if self._tenant is None:
            return None
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        roles = await self._roles_for([row.id])
        return _to_domain(row, roles.get(row.id, []))

    async def get(self, employee_id: UUID) -> Employee | None:
        return await self._one(self._base().where(EmployeeRow.id == employee_id))

    async def get_by_sub(self, keycloak_sub: str) -> Employee | None:
        return await self._one(self._base().where(EmployeeRow.keycloak_sub == keycloak_sub))

    async def get_by_email(self, email: str) -> Employee | None:
        return await self._one(self._base().where(EmployeeRow.email == email))

    async def list(
        self, *, filters: EmployeeFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[Employee]:
        if self._tenant is None:
            return []
        stmt = self._base().order_by(EmployeeRow.created_at, EmployeeRow.id).limit(limit)
        if filters.department_id is not None:
            stmt = stmt.where(EmployeeRow.department_id == filters.department_id)
        if filters.status is not None:
            stmt = stmt.where(EmployeeRow.status == filters.status)
        if filters.query:
            pattern = f"%{filters.query.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(EmployeeRow.first_name + " " + EmployeeRow.last_name).like(pattern),
                    func.lower(EmployeeRow.email).like(pattern),
                    func.lower(EmployeeRow.employee_number).like(pattern),
                )
            )
        if after is not None:
            stmt = stmt.where(tuple_(EmployeeRow.created_at, EmployeeRow.id) > after)
        rows = list((await self._s.execute(stmt)).scalars())
        roles = await self._roles_for([r.id for r in rows])
        return [_to_domain(r, roles.get(r.id, [])) for r in rows]

    async def count_in_department(self, department_id: UUID) -> int:
        if self._tenant is None:
            return 0
        stmt = (
            select(func.count())
            .select_from(EmployeeRow)
            .where(EmployeeRow.tenant_id == self._tenant, EmployeeRow.department_id == department_id)
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def add(self, employee: Employee) -> None:
        self._s.add(
            EmployeeRow(
                id=employee.id,
                tenant_id=employee.tenant_id,
                keycloak_sub=employee.keycloak_sub,
                employee_number=employee.employee_number,
                first_name=employee.first_name,
                last_name=employee.last_name,
                email=employee.email,
                department_id=employee.department_id,
                manager_override_employee_id=employee.manager_override_employee_id,
                hire_date=employee.hire_date,
                locale=employee.locale.value,
                status=employee.status.value,
                created_at=employee.created_at,
                updated_at=employee.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, employee: Employee) -> None:
        row = (await self._s.execute(self._base().where(EmployeeRow.id == employee.id))).scalar_one_or_none()
        if row is None:
            raise LookupError(f"employee {employee.id} does not exist")
        row.employee_number = employee.employee_number
        row.first_name = employee.first_name
        row.last_name = employee.last_name
        row.email = employee.email
        row.department_id = employee.department_id
        row.manager_override_employee_id = employee.manager_override_employee_id
        row.hire_date = employee.hire_date
        row.locale = employee.locale.value
        row.status = employee.status.value
        row.updated_at = employee.updated_at
        await flush_or_conflict(self._s)

    async def set_roles(self, employee_id: UUID, roles: frozenset[Role]) -> None:
        if self._tenant is None:
            return
        await self._s.execute(
            delete(EmployeeRoleRow).where(
                EmployeeRoleRow.employee_id == employee_id, EmployeeRoleRow.tenant_id == self._tenant
            )
        )
        now = datetime.now(UTC)
        for role in sorted(roles, key=lambda r: r.value):
            self._s.add(
                EmployeeRoleRow(
                    employee_id=employee_id, role=role.value, tenant_id=self._tenant, created_at=now
                )
            )
        await flush_or_conflict(self._s)
