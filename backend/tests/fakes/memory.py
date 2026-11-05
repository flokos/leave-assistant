"""In-memory unit of work emulating the transactional and RLS behaviour of the real adapter.

- Each entered unit of work operates on a snapshot; `commit()` publishes it to the shared store (rollback = discard).
- Tenant-scoped repositories only see rows of the context's tenant; no tenant → nothing (fail closed like RLS).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from leave_assistant.application.audit.entry import AuditEntry
from leave_assistant.application.errors import ConflictError
from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.application.ports import (
    AuditFilter,
    AuditRepository,
    DepartmentRepository,
    EmployeeFilter,
    EmployeeRepository,
    IdempotencyRepository,
    TenantRepository,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Department, Employee, Role, Tenant


@dataclass
class MemoryStore:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    departments: dict[UUID, Department] = field(default_factory=dict)
    employees: dict[UUID, Employee] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    idempotency: dict[tuple[str, str, str], IdempotencyRecord] = field(default_factory=dict)


def _after(key: tuple[datetime, UUID], after: tuple[datetime, UUID] | None) -> bool:
    return after is None or key > after


class _Tenants:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return copy.deepcopy(self._s.tenants.get(tenant_id))

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return copy.deepcopy(next((t for t in self._s.tenants.values() if t.slug == slug), None))

    async def list(self, *, limit: int, after: tuple[datetime, UUID] | None) -> list[Tenant]:
        rows = sorted(self._s.tenants.values(), key=lambda t: (t.created_at, t.id))
        return [copy.deepcopy(t) for t in rows if _after((t.created_at, t.id), after)][:limit]

    async def add(self, tenant: Tenant) -> None:
        if any(t.slug == tenant.slug for t in self._s.tenants.values()):
            raise ConflictError("duplicate slug")
        self._s.tenants[tenant.id] = copy.deepcopy(tenant)

    async def update(self, tenant: Tenant) -> None:
        self._s.tenants[tenant.id] = copy.deepcopy(tenant)


class _Departments:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    def _rows(self) -> list[Department]:
        return [
            d
            for d in self._s.departments.values()
            if self._tenant is not None and d.tenant_id == self._tenant
        ]

    async def get(self, department_id: UUID) -> Department | None:
        return copy.deepcopy(next((d for d in self._rows() if d.id == department_id), None))

    async def get_by_code(self, code: str) -> Department | None:
        return copy.deepcopy(next((d for d in self._rows() if d.code == code), None))

    async def list(self) -> list[Department]:
        return [copy.deepcopy(d) for d in sorted(self._rows(), key=lambda d: d.code)]

    async def add(self, department: Department) -> None:
        if department.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        if any(d.code == department.code for d in self._rows()):
            raise ConflictError("duplicate code")
        self._s.departments[department.id] = copy.deepcopy(department)

    async def update(self, department: Department) -> None:
        if department.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        self._s.departments[department.id] = copy.deepcopy(department)

    async def delete(self, department_id: UUID) -> None:
        if any(d.id == department_id for d in self._rows()):
            del self._s.departments[department_id]


class _Employees:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    def _rows(self) -> list[Employee]:
        return [
            e for e in self._s.employees.values() if self._tenant is not None and e.tenant_id == self._tenant
        ]

    async def get(self, employee_id: UUID) -> Employee | None:
        return copy.deepcopy(next((e for e in self._rows() if e.id == employee_id), None))

    async def get_by_sub(self, keycloak_sub: str) -> Employee | None:
        return copy.deepcopy(next((e for e in self._rows() if e.keycloak_sub == keycloak_sub), None))

    async def get_by_email(self, email: str) -> Employee | None:
        return copy.deepcopy(next((e for e in self._rows() if e.email == email), None))

    async def list(
        self, *, filters: EmployeeFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[Employee]:
        rows = self._rows()
        if filters.department_id is not None:
            rows = [e for e in rows if e.department_id == filters.department_id]
        if filters.status is not None:
            rows = [e for e in rows if e.status.value == filters.status]
        if filters.query:
            q = filters.query.lower()
            rows = [
                e
                for e in rows
                if q in e.display_name.lower() or q in e.email or q in e.employee_number.lower()
            ]
        rows = sorted(rows, key=lambda e: (e.created_at, e.id))
        return [copy.deepcopy(e) for e in rows if _after((e.created_at, e.id), after)][:limit]

    async def count_in_department(self, department_id: UUID) -> int:
        return sum(1 for e in self._rows() if e.department_id == department_id)

    async def add(self, employee: Employee) -> None:
        if employee.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        if any(
            e.email == employee.email or e.employee_number == employee.employee_number for e in self._rows()
        ):
            raise ConflictError("duplicate employee")
        if any(e.keycloak_sub == employee.keycloak_sub for e in self._s.employees.values()):
            raise ConflictError("duplicate keycloak_sub")
        self._s.employees[employee.id] = copy.deepcopy(employee)

    async def update(self, employee: Employee) -> None:
        if employee.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        self._s.employees[employee.id] = copy.deepcopy(employee)

    async def set_roles(self, employee_id: UUID, roles: frozenset[Role]) -> None:
        row = next((e for e in self._rows() if e.id == employee_id), None)
        if row is not None:
            row.roles = frozenset(roles)


class _Audit:
    def __init__(self, store: MemoryStore, context: TenantContext | None) -> None:
        self._s = store
        self._ctx = context

    async def add(self, entry: AuditEntry) -> None:
        tenant = self._ctx.tenant_id if self._ctx else None
        if entry.tenant_id != tenant:
            raise ConflictError("RLS: audit WITH CHECK failed")
        self._s.audit.append(entry)

    async def list(
        self, *, filters: AuditFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[AuditEntry]:
        tenant = self._ctx.tenant_id if self._ctx else None
        platform_admin = bool(self._ctx and self._ctx.platform_admin)
        rows = [
            a
            for a in self._s.audit
            if (a.tenant_id is not None and a.tenant_id == tenant) or (a.tenant_id is None and platform_admin)
        ]
        if filters.platform_only:
            rows = [a for a in rows if a.tenant_id is None]
        if filters.action:
            rows = [a for a in rows if a.action == filters.action]
        if filters.occurred_from:
            rows = [a for a in rows if a.occurred_at >= filters.occurred_from]
        if filters.occurred_to:
            rows = [a for a in rows if a.occurred_at < filters.occurred_to]
        rows = sorted(rows, key=lambda a: (a.occurred_at, a.id), reverse=True)
        if after is not None:
            rows = [a for a in rows if (a.occurred_at, a.id) < after]
        return rows[:limit]


class _Idempotency:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    async def get(self, user_id: str, route: str, key: str) -> IdempotencyRecord | None:
        return self._s.idempotency.get((user_id, route, key))

    async def add(self, record: IdempotencyRecord) -> None:
        self._s.idempotency[(record.user_id, record.route, record.key)] = record

    async def purge_expired(self, now: datetime) -> int:
        expired = [k for k, r in self._s.idempotency.items() if r.expires_at <= now]
        for k in expired:
            del self._s.idempotency[k]
        return len(expired)


class MemoryUnitOfWork:
    tenants: TenantRepository
    departments: DepartmentRepository
    employees: EmployeeRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository

    def __init__(self, store: MemoryStore, context: TenantContext | None) -> None:
        self._shared = store
        self.context = context
        self._committed = False

    async def __aenter__(self) -> Self:
        self._work = copy.deepcopy(self._shared)
        self._committed = False
        tenant = self.context.tenant_id if self.context else None
        self.tenants = _Tenants(self._work)
        self.departments = _Departments(self._work, tenant)
        self.employees = _Employees(self._work, tenant)
        self.audit = _Audit(self._work, self.context)
        self.idempotency = _Idempotency(self._work)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        self._shared.tenants = self._work.tenants
        self._shared.departments = self._work.departments
        self._shared.employees = self._work.employees
        self._shared.audit = self._work.audit
        self._shared.idempotency = self._work.idempotency
        self._committed = True

    async def rollback(self) -> None:
        self._work = copy.deepcopy(self._shared)


class MemoryUnitOfWorkFactory:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or MemoryStore()

    def __call__(self, context: TenantContext | None) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self.store, context)
