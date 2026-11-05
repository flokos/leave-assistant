"""SQLAlchemy models for the M1 schema (doc 07 §4.1, §4.2, §4.5 idempotency, §4.8 audit).

Enums are created by the baseline migration (`create_type=False` here). Tenant tables carry `tenant_id` and are
protected by forced RLS policies — see `alembic/rls.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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


GRANT_MODE = ENUM("upfront", "monthly_accrual", name="grant_mode", schema="app", create_type=False)
STEP_ROLE = ENUM("manager", "hr", name="step_role", schema="app", create_type=False)
STEP_STATUS = ENUM(
    "pending",
    "approved",
    "rejected",
    "skipped",
    "cancelled",
    name="step_status",
    schema="app",
    create_type=False,
)
REQUEST_STATUS = ENUM(
    "DRAFT",
    "SUBMITTED",
    "PENDING_MANAGER",
    "PENDING_HR",
    "APPROVED",
    "ACTIVE",
    "COMPLETED",
    "REJECTED",
    "CANCELLED",
    "WITHDRAWN",
    "MODIFICATION_PENDING",
    name="request_status",
    schema="app",
    create_type=False,
)
REQUEST_ACTOR_KIND = ENUM(
    "employee", "manager", "hr", "system", "agent", name="request_actor_kind", schema="app", create_type=False
)
DECISION_CHANNEL = ENUM("ui", "agent", name="decision_channel", schema="app", create_type=False)
LEDGER_ENTRY_TYPE = ENUM(
    "GRANT",
    "DEDUCTION",
    "REVERSAL",
    "ADJUSTMENT",
    "CARRYOVER",
    "EXPIRY",
    name="ledger_entry_type",
    schema="app",
    create_type=False,
)
EMAIL_STATUS = ENUM(
    "pending", "sent", "failed", "skipped", name="email_status", schema="app", create_type=False
)
DAYS = Numeric(6, 2)


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


# --- M2: leave domain (doc 07 §4.3), notifications and outbox (§4.7) -----------------------------------------
class LeaveTypeRow(Base):
    __tablename__ = "leave_types"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_leave_types_tenant_code"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_leave_types_tenant"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name_el: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    balance_bearing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_retroactive_days: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_document_above_days: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeavePolicyRow(Base):
    __tablename__ = "leave_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "leave_type_id", "year", name="uq_leave_policies_type_year"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_leave_policies_tenant"),
        nullable=False,
    )
    leave_type_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.leave_types.id", name="fk_leave_policies_type"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    entitlement_days: Mapped[Decimal] = mapped_column(DAYS, nullable=False)
    carryover_max_days: Mapped[Decimal] = mapped_column(DAYS, nullable=False)
    carryover_expires_on: Mapped[date | None] = mapped_column(Date)
    allow_negative: Mapped[bool] = mapped_column(Boolean, nullable=False)
    min_notice_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_consecutive_days: Mapped[int | None] = mapped_column(Integer)
    prorate_on_hire: Mapped[bool] = mapped_column(Boolean, nullable=False)
    grant_mode: Mapped[str] = mapped_column(GRANT_MODE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicHolidayRow(Base):
    __tablename__ = "public_holidays"
    __table_args__ = (
        UniqueConstraint("tenant_id", "holiday_date", name="uq_public_holidays_tenant_date"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_public_holidays_tenant"),
        nullable=False,
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name_el: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    recurring: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRuleRow(Base):
    __tablename__ = "approval_rules"
    __table_args__ = (Index("ix_approval_rules_tenant_priority", "tenant_id", "priority"), {"schema": "app"})

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_approval_rules_tenant"),
        nullable=False,
    )
    leave_type_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.leave_types.id", name="fk_approval_rules_type")
    )
    min_working_days: Mapped[Decimal] = mapped_column(DAYS, nullable=False)
    max_working_days: Mapped[Decimal | None] = mapped_column(DAYS)
    requires_document: Mapped[bool] = mapped_column(Boolean, nullable=False)
    steps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    auto_acknowledge: Mapped[bool] = mapped_column(Boolean, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RequestCounterRow(Base):
    __tablename__ = "request_counters"
    __table_args__ = {"schema": "app"}

    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_request_counters_tenant"),
        primary_key=True,
    )
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class LeaveRequestRow(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "request_number", name="uq_leave_requests_number"),
        Index("ix_leave_requests_tenant_employee", "tenant_id", "employee_id"),
        Index("ix_leave_requests_tenant_status", "tenant_id", "status"),
        Index("ix_leave_requests_dates", "tenant_id", "start_date", "end_date"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_leave_requests_tenant"),
        nullable=False,
    )
    request_number: Mapped[str] = mapped_column(String(20), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_leave_requests_employee"),
        nullable=False,
    )
    leave_type_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.leave_types.id", name="fk_leave_requests_type"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    working_days: Mapped[Decimal] = mapped_column(DAYS, nullable=False)
    status: Mapped[str] = mapped_column(REQUEST_STATUS, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modification_of_request_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.leave_requests.id", name="fk_leave_requests_modification_of")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeaveRequestEventRow(Base):
    __tablename__ = "leave_request_events"
    __table_args__ = (
        Index("ix_leave_request_events_request", "tenant_id", "request_id", "occurred_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_leave_request_events_tenant"),
        nullable=False,
    )
    request_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.leave_requests.id", name="fk_leave_request_events_request", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(REQUEST_STATUS)
    to_status: Mapped[str] = mapped_column(REQUEST_STATUS, nullable=False)
    actor_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_leave_request_events_actor")
    )
    actor_kind: Mapped[str] = mapped_column(REQUEST_ACTOR_KIND, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalStepRow(Base):
    __tablename__ = "approval_steps"
    __table_args__ = (
        UniqueConstraint("request_id", "step_no", name="uq_approval_steps_request_step"),
        Index("ix_approval_steps_designee", "tenant_id", "designated_employee_id", "status"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_approval_steps_tenant"),
        nullable=False,
    )
    request_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.leave_requests.id", name="fk_approval_steps_request", ondelete="CASCADE"),
        nullable=False,
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_role: Mapped[str] = mapped_column(STEP_ROLE, nullable=False)
    designated_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_approval_steps_designee")
    )
    status: Mapped[str] = mapped_column(STEP_STATUS, nullable=False)
    decided_by_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_approval_steps_decider")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    via: Mapped[str | None] = mapped_column(DECISION_CHANNEL)
    fallback: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeaveLedgerEntryRow(Base):
    __tablename__ = "leave_ledger_entries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_leave_ledger_idempotency_key"),
        Index("ix_leave_ledger_balance_scope", "tenant_id", "employee_id", "leave_type_id", "period_year"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_leave_ledger_tenant"), nullable=False
    )
    employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_leave_ledger_employee"), nullable=False
    )
    leave_type_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.leave_types.id", name="fk_leave_ledger_type"), nullable=False
    )
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(LEDGER_ENTRY_TYPE, nullable=False)
    quantity_days: Mapped[Decimal] = mapped_column(DAYS, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.leave_requests.id", name="fk_leave_ledger_request")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_by_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_leave_ledger_creator")
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_notifications_dedupe"),
        Index("ix_notifications_recipient", "tenant_id", "recipient_employee_id", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_notifications_tenant"),
        nullable=False,
    )
    recipient_employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_notifications_recipient"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    channels: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_status: Mapped[str] = mapped_column(EMAIL_STATUS, nullable=False)
    email_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationEventRow(Base):
    __tablename__ = "integration_events"
    __table_args__ = (
        Index("ix_integration_events_undelivered", "tenant_id", "delivered_at", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_integration_events_tenant"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    dead: Mapped[bool] = mapped_column(Boolean, nullable=False)
