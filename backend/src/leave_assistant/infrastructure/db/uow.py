"""SQLAlchemy unit of work implementing `application.ports.UnitOfWork` (one session, one transaction)."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leave_assistant.application.errors import ConflictError
from leave_assistant.application.ports import (
    AuditRepository,
    DepartmentRepository,
    EmployeeRepository,
    IdempotencyRepository,
    TenantRepository,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.infrastructure.db.repositories.audit import SqlAuditRepository
from leave_assistant.infrastructure.db.repositories.departments import SqlDepartmentRepository
from leave_assistant.infrastructure.db.repositories.employees import SqlEmployeeRepository
from leave_assistant.infrastructure.db.repositories.idempotency import SqlIdempotencyRepository
from leave_assistant.infrastructure.db.repositories.tenants import SqlTenantRepository
from leave_assistant.infrastructure.db.session import CONTEXT_KEY


class SqlAlchemyUnitOfWork:
    tenants: TenantRepository
    departments: DepartmentRepository
    employees: EmployeeRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], context: TenantContext | None
    ) -> None:
        self._factory = session_factory
        self.context = context
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    async def __aenter__(self) -> Self:
        session = self._factory()
        session.info[CONTEXT_KEY] = self.context
        self._session = session
        tenant_id = self.context.tenant_id if self.context else None
        self.tenants = SqlTenantRepository(session)
        self.departments = SqlDepartmentRepository(session, tenant_id)
        self.employees = SqlEmployeeRepository(session, tenant_id)
        self.audit = SqlAuditRepository(session, self.context)
        self.idempotency = SqlIdempotencyRepository(session)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            await session.rollback()  # no-op after commit; discards uncommitted work otherwise
        finally:
            await session.close()

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("the change conflicts with existing data") from exc

    async def rollback(self) -> None:
        await self.session.rollback()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    def __call__(self, context: TenantContext | None) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._factory, context)
