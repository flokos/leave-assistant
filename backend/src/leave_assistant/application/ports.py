"""Ports (interfaces) implemented by infrastructure adapters (doc 03 §4.3). Application code depends on these only."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from leave_assistant.application.audit.entry import AuditEntry
from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Department, Employee, Role, Tenant


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...


class TenantRepository(Protocol):
    async def get(self, tenant_id: UUID) -> Tenant | None: ...
    async def get_by_slug(self, slug: str) -> Tenant | None: ...
    async def list(self, *, limit: int, after: tuple[datetime, UUID] | None) -> list[Tenant]: ...
    async def add(self, tenant: Tenant) -> None: ...
    async def update(self, tenant: Tenant) -> None: ...


@dataclass(frozen=True, slots=True)
class EmployeeFilter:
    department_id: UUID | None = None
    status: str | None = None
    query: str | None = None


class DepartmentRepository(Protocol):
    """Tenant-scoped: every method applies the unit of work's tenant context (belt) on top of RLS (braces)."""

    async def get(self, department_id: UUID) -> Department | None: ...
    async def get_by_code(self, code: str) -> Department | None: ...
    async def list(self) -> list[Department]: ...
    async def add(self, department: Department) -> None: ...
    async def update(self, department: Department) -> None: ...
    async def delete(self, department_id: UUID) -> None: ...


class EmployeeRepository(Protocol):
    async def get(self, employee_id: UUID) -> Employee | None: ...
    async def get_by_sub(self, keycloak_sub: str) -> Employee | None: ...
    async def get_by_email(self, email: str) -> Employee | None: ...
    async def list(
        self, *, filters: EmployeeFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[Employee]: ...
    async def count_in_department(self, department_id: UUID) -> int: ...
    async def add(self, employee: Employee) -> None: ...
    async def update(self, employee: Employee) -> None: ...
    async def set_roles(self, employee_id: UUID, roles: frozenset[Role]) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditFilter:
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    action: str | None = None
    platform_only: bool = False


class AuditRepository(Protocol):
    async def add(self, entry: AuditEntry) -> None: ...
    async def list(
        self, *, filters: AuditFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[AuditEntry]: ...


class IdempotencyRepository(Protocol):
    async def get(self, user_id: str, route: str, key: str) -> IdempotencyRecord | None: ...
    async def add(self, record: IdempotencyRecord) -> None: ...
    async def purge_expired(self, now: datetime) -> int: ...


class UnitOfWork(Protocol):
    """One database transaction. Entering opens a session bound to `context` (SET LOCAL app.* — ADR-0003)."""

    context: TenantContext | None
    tenants: TenantRepository
    departments: DepartmentRepository
    employees: EmployeeRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext | None) -> UnitOfWork: ...


@dataclass(frozen=True, slots=True)
class IdentityUser:
    sub: str
    username: str
    enabled: bool
    required_actions: tuple[str, ...] = ()
    realm_roles: frozenset[str] = frozenset()
    group_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewIdentityUser:
    username: str
    email: str
    first_name: str
    last_name: str
    locale: str
    password: str
    temporary_password: bool
    realm_roles: frozenset[str]
    group_path: str
    attributes: dict[str, Sequence[str]] = field(default_factory=dict)


class IdentityAdmin(Protocol):
    """Keycloak Admin API operations used by provisioning (ADR-0004). Implemented by Keycloak and a fake."""

    async def ensure_tenant_group(self, slug: str, tenant_id: UUID) -> str: ...
    async def find_user_by_username(self, username: str) -> IdentityUser | None: ...
    async def get_user(self, sub: str) -> IdentityUser | None: ...
    async def create_user(self, spec: NewIdentityUser) -> str: ...
    async def update_user(self, sub: str, *, first_name: str, last_name: str, locale: str) -> None: ...
    async def set_enabled(self, sub: str, enabled: bool) -> None: ...
    async def set_realm_roles(self, sub: str, roles: frozenset[str], *, managed: frozenset[str]) -> None: ...
    async def reset_password(self, sub: str, password: str, *, temporary: bool) -> None: ...
    async def delete_user(self, sub: str) -> None: ...
