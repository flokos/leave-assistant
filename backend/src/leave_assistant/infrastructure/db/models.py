"""SQLAlchemy models for the M1 schema (doc 07 §4.1, §4.2, §4.5 idempotency, §4.8 audit).

Enums are created by the baseline migration (`create_type=False` here). Tenant tables carry `tenant_id` and are
protected by forced RLS policies — see `alembic/rls.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, TSVECTOR
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


# --- documents, retrieval and jobs (M4: doc 07 §4.4 / §4.7, migration 0003) -----------------------------------
DOCUMENT_CATEGORY = ENUM(
    "policy_general",
    "policy_hr_restricted",
    "department_notice",
    "admin_internal",
    name="document_category",
    schema="app",
    create_type=False,
)
VERSION_STATUS = ENUM(
    "UPLOADED",
    "PROCESSING",
    "FAILED",
    "ACTIVE",
    "PENDING_REVIEW",
    "QUARANTINED",
    "SUPERSEDED",
    "RETIRED",
    "DELETED",
    name="document_version_status",
    schema="app",
    create_type=False,
)
RISK_TIER = ENUM("low", "medium", "high", name="risk_tier", schema="app", create_type=False)
CHUNK_TYPE = ENUM("text", "table", name="chunk_type", schema="app", create_type=False)
ACL_PRINCIPAL_TYPE = ENUM(
    "role", "department", "employee", name="acl_principal_type", schema="app", create_type=False
)
REVIEW_ACTION = ENUM(
    "release", "reject", "delete", "inspect", name="review_action", schema="app", create_type=False
)
GENERATION_STATUS = ENUM(
    "building", "active", "retired", name="generation_status", schema="app", create_type=False
)
JOB_STATUS = ENUM(
    "queued",
    "running",
    "succeeded",
    "failed",
    "dead",
    name="job_status",
    schema="platform",
    create_type=False,
)
EMBEDDING_DIMENSION = 384


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_tenant_created", "tenant_id", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_documents_tenant"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(DOCUMENT_CATEGORY, nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.departments.id", name="fk_documents_department", ondelete="SET NULL"),
    )
    owner_employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_documents_owner"), nullable=False
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "app.document_versions.id",
            name="fk_documents_active_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
            ondelete="SET NULL",
        ),
    )
    language_hint: Mapped[str | None] = mapped_column(String(8))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentVersionRow(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_document_versions_number"),
        Index("ix_document_versions_tenant_status", "tenant_id", "status"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_document_versions_tenant"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.documents.id", name="fk_document_versions_document", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(400), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(VERSION_STATUS, nullable=False)
    pages: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(8))
    extracted_object_key: Mapped[str | None] = mapped_column(String(400))
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_tier: Mapped[str] = mapped_column(RISK_TIER, nullable=False)
    risk_findings: Mapped[Any] = mapped_column(JSONB, nullable=False)
    pii_summary: Mapped[Any] = mapped_column(JSONB, nullable=False)
    processing_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_generation: Mapped[str | None] = mapped_column(String(200))
    retrievable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    uploaded_by_employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_document_versions_uploader"),
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentChunkRow(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_document_chunks_ordinal"),
        Index("ix_document_chunks_tenant_version", "tenant_id", "version_id", "ordinal"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_document_chunks_tenant"),
        nullable=False,
    )
    version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.document_versions.id", name="fk_document_chunks_version", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_type: Mapped[str] = mapped_column(CHUNK_TYPE, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(8))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_llm_safe: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=True)
    embedding_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    tsv_el: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('greek', content)", persisted=True))
    tsv_en: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('english', content)", persisted=True))
    tsv_simple: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', app.f_unaccent(content))", persisted=True)
    )
    risk_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    has_pii: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retrievable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentAclRow(Base):
    __tablename__ = "document_acl"
    __table_args__ = (
        UniqueConstraint("document_id", "principal_type", "principal_id", name="uq_document_acl_principal"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_document_acl_tenant"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.documents.id", name="fk_document_acl_document", ondelete="CASCADE"),
        nullable=False,
    )
    principal_type: Mapped[str] = mapped_column(ACL_PRINCIPAL_TYPE, nullable=False)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentReviewEventRow(Base):
    __tablename__ = "document_review_events"
    __table_args__ = (Index("ix_document_review_events_version", "version_id"), {"schema": "app"})

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_document_review_events_tenant"),
        nullable=False,
    )
    version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.document_versions.id", name="fk_document_review_events_version", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_document_review_events_reviewer"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(REVIEW_ACTION, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IndexGenerationRow(Base):
    __tablename__ = "index_generations"
    __table_args__ = {"schema": "app"}

    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_index_generations_tenant"),
        primary_key=True,
    )
    generation: Mapped[str] = mapped_column(String(200), primary_key=True)
    embedding_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    chunking_rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(GENERATION_STATUS, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobRow(Base):
    """`platform.jobs` (M4 decision 2): claimed across tenants; handlers open tenant-scoped transactions."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        Index("ix_jobs_tenant_created", "tenant_id", "created_at"),
        {"schema": "platform"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_jobs_tenant")
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(JOB_STATUS, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- M5: privacy vault, security policies, injection findings (doc 07 §4.5, ADR-0017) ----------------------------


class PiiVaultRow(Base):
    """Tenant-stable placeholders: HMAC lookup, AES-GCM original (ADR-0017); the LLM never sees this table."""

    __tablename__ = "pii_vault"
    __table_args__ = (
        UniqueConstraint("tenant_id", "value_hmac", name="uq_pii_vault_hmac"),
        UniqueConstraint("tenant_id", "placeholder", name="uq_pii_vault_placeholder"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_pii_vault_tenant"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    value_hmac: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    placeholder: Mapped[str] = mapped_column(String(40), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_kinds: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    subject_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_pii_vault_subject", ondelete="SET NULL")
    )


class PiiVaultCounterRow(Base):
    __tablename__ = "pii_vault_counters"
    __table_args__ = {"schema": "app"}

    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_pii_vault_counters_tenant"),
        primary_key=True,
    )
    entity_type: Mapped[str] = mapped_column(String(24), primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class SecurityPolicyDefaultsRow(Base):
    """`platform.security_policy_defaults`: one row (id = 1) — tenant defaults and the ranges tenants may use."""

    __tablename__ = "security_policy_defaults"
    __table_args__ = {"schema": "platform"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    risk_low_max: Mapped[float] = mapped_column(Float, nullable=False)
    risk_medium_max: Mapped[float] = mapped_column(Float, nullable=False)
    injection_llm_judge: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_input_block_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    max_upload_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_mime: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    threshold_min: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_max: Mapped[float] = mapped_column(Float, nullable=False)
    max_upload_mb_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SecurityPolicyRow(Base):
    __tablename__ = "security_policies"
    __table_args__ = {"schema": "app"}

    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_security_policies_tenant"),
        primary_key=True,
    )
    risk_low_max: Mapped[float] = mapped_column(Float, nullable=False)
    risk_medium_max: Mapped[float] = mapped_column(Float, nullable=False)
    injection_llm_judge: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_input_block_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    max_upload_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_mime: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InjectionFindingRow(Base):
    __tablename__ = "injection_findings"
    __table_args__ = (
        Index("ix_injection_findings_subject", "tenant_id", "subject_type", "subject_id"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_injection_findings_tenant"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detector: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    excerpt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- M5: supporting documents (doc 07 §4.7, doc 08 §9) -------------------------------------------------------------


class SupportingDocumentRow(Base):
    __tablename__ = "supporting_documents"
    __table_args__ = (
        Index("ix_supporting_documents_request", "tenant_id", "request_id"),
        Index("ix_supporting_documents_status", "tenant_id", "status"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_supporting_documents_tenant"),
        nullable=False,
    )
    request_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.leave_requests.id", name="fk_supporting_documents_request"),
        nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_supporting_documents_employee"),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(400), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    uploaded_by_employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_supporting_documents_uploader"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_until: Mapped[date | None] = mapped_column(Date)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportingAccessEventRow(Base):
    __tablename__ = "supporting_document_access_events"
    __table_args__ = (
        Index("ix_supporting_access_document", "tenant_id", "supporting_document_id", "occurred_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_supporting_access_tenant"),
        nullable=False,
    )
    supporting_document_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.supporting_documents.id", name="fk_supporting_access_document", ondelete="CASCADE"),
        nullable=False,
    )
    actor_employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.employees.id", name="fk_supporting_access_actor"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))


class DownloadTokenRow(Base):
    """`platform.supporting_download_tokens`: hashed single-use tokens; the actor is re-bound when presented."""

    __tablename__ = "supporting_download_tokens"
    __table_args__ = (Index("ix_supporting_download_tokens_expires", "expires_at"), {"schema": "platform"})

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_download_tokens_tenant"),
        nullable=False,
    )
    supporting_document_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    actor_employee_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- M5: retention (doc 07 §7, Q-08) ------------------------------------------------------------------------------


class RetentionPolicyRow(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_class", name="uq_retention_policies_class"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_retention_policies_tenant"),
        nullable=False,
    )
    document_class: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetentionReviewItemRow(Base):
    __tablename__ = "retention_review_items"
    __table_args__ = (
        Index("ix_retention_review_items_status", "tenant_id", "status", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_retention_items_tenant"),
        nullable=False,
    )
    policy_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.retention_policies.id", name="fk_retention_items_policy"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    eligible_since: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewer_employee_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_retention_items_reviewer")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)


# --- M6: conversations and agent runs (doc 07 §4.6) ---------------------------------------------------------------


class ConversationRow(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_employee", "tenant_id", "employee_id", "last_activity_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_conversations_tenant"),
        nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("app.employees.id", name="fk_conversations_employee"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_lock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index("ix_conversation_messages_conversation", "tenant_id", "conversation_id", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_conversation_messages_tenant"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.conversations.id", name="fk_conversation_messages_conversation", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_pseudonymised: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[Any] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[Any] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    outcome: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_conversation", "tenant_id", "conversation_id", "turn"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("platform.tenants.id", name="fk_agent_runs_tenant"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("app.conversations.id", name="fk_agent_runs_conversation", ondelete="CASCADE"),
        nullable=False,
    )
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(8))
    route: Mapped[str | None] = mapped_column(String(32))
    provider_used: Mapped[str | None] = mapped_column(String(32))
    model_used: Mapped[str | None] = mapped_column(String(80))
    failovers: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_trace: Mapped[Any] = mapped_column(JSONB, nullable=False)
    details: Mapped[Any] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderEventRow(Base):
    __tablename__ = "provider_events"
    __table_args__ = (Index("ix_provider_events_occurred", "tenant_id", "occurred_at"), {"schema": "app"})

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("platform.tenants.id", name="fk_provider_events_tenant"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    event: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
