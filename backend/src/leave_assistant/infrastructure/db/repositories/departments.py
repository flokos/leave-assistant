from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.domain.tenancy import Department
from leave_assistant.infrastructure.db.models import DepartmentRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _to_domain(row: DepartmentRow) -> Department:
    return Department(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        manager_employee_id=row.manager_employee_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlDepartmentRepository:
    """Tenant-scoped: queries add `tenant_id = :tenant` explicitly on top of RLS; no tenant → nothing."""

    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[DepartmentRow]:
        return select(DepartmentRow).where(DepartmentRow.tenant_id == self._tenant)

    async def get(self, department_id: UUID) -> Department | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(DepartmentRow.id == department_id))
        ).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def get_by_code(self, code: str) -> Department | None:
        if self._tenant is None:
            return None
        row = (await self._s.execute(self._base().where(DepartmentRow.code == code))).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list(self) -> list[Department]:
        if self._tenant is None:
            return []
        rows = (await self._s.execute(self._base().order_by(DepartmentRow.code))).scalars()
        return [_to_domain(r) for r in rows]

    async def add(self, department: Department) -> None:
        self._s.add(
            DepartmentRow(
                id=department.id,
                tenant_id=department.tenant_id,
                code=department.code,
                name=department.name,
                manager_employee_id=department.manager_employee_id,
                created_at=department.created_at,
                updated_at=department.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, department: Department) -> None:
        row = (
            await self._s.execute(self._base().where(DepartmentRow.id == department.id))
        ).scalar_one_or_none()
        if row is None:
            raise LookupError(f"department {department.id} does not exist")
        row.code = department.code
        row.name = department.name
        row.manager_employee_id = department.manager_employee_id
        row.updated_at = department.updated_at
        await flush_or_conflict(self._s)

    async def delete(self, department_id: UUID) -> None:
        await self._s.execute(
            delete(DepartmentRow).where(
                DepartmentRow.id == department_id, DepartmentRow.tenant_id == self._tenant
            )
        )
