"""SQLAlchemy models for the M1 schema (doc 07 §4.1, §4.2, §4.5 idempotency, §4.8 audit).

Enums are created by the baseline migration (`create_type=False` here). Tenant tables carry `tenant_id` and are
protected by forced RLS policies — see `alembic/rls.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TENANT_STATUS = ENUM("active", "suspended", name="tenant_status", schema="platform", create_type=False)
LOCALE = ENUM("el", "en", name="locale", schema="platform", create_type=False)
EMPLOYEE_STATUS = ENUM("active", "inactive", name="employee_status", schema="app", create_type=False)
APP_ROLE = ENUM(
    "employee", "manager", "hr_admin", "company_admin", name="app_role", schema="app", create_type=False
)
ACTOR_KIND = ENUM(
    "user", "automation", "system", "agent", name="actor_kind", schema="audit", create_type=False
)
OUTCOME = ENUM("success", "denied", "error", name="outcome", schema="audit", create_type=False)


class Base(DeclarativeBase):
    pass


class TenantRow(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "platform"}

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(TENANT_STATUS, nullable=False)
    default_locale: Mapped[str] = mapped_column(LOCALE, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    keycloak_group_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "route", "key", name="uq_idempotency_user_route_key"),
        Index("ix_idempotency_expires_at", "expires_at"),
        {"schema": "platform"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    route: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DepartmentRow(Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_code"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_departments_tenant"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    manager_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "app.employees.id",
            name="fk_departments_manager",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
            ondelete="SET NULL",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmployeeRow(Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("tenant_id", "employee_number", name="uq_employees_tenant_number"),
        UniqueConstraint("tenant_id", "email", name="uq_employees_tenant_email"),
        Index("ix_employees_tenant_department", "tenant_id", "department_id"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_employees_tenant"), nullable=False
    )
    keycloak_sub: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    department_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.departments.id", name="fk_employees_department"), nullable=False
    )
    manager_override_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_employees_manager_override", ondelete="SET NULL"),
    )
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    locale: Mapped[str] = mapped_column(LOCALE, nullable=False)
    status: Mapped[str] = mapped_column(EMPLOYEE_STATUS, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmployeeRoleRow(Base):
    __tablename__ = "employee_roles"
    __table_args__ = {"schema": "app"}

    employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_employee_roles_employee", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(APP_ROLE, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_employee_roles_tenant"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLogRow(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_audit_log_action", "action"),
        {"schema": "audit"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))  # NULL = platform-level event
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_kind: Mapped[str] = mapped_column(ACTOR_KIND, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(OUTCOME, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    thread_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
