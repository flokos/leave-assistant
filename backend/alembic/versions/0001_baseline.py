"""M1 baseline: schemas, enums, tenants, departments, employees, employee_roles, idempotency keys, audit log, RLS.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import RUNTIME_ROLE, TENANT_PREDICATE, apply_tenant_rls

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)


def _enum(name: str, schema: str) -> pg.ENUM:
    return pg.ENUM(name=name, schema=schema, create_type=False)


def upgrade() -> None:
    for schema in ("platform", "app", "audit"):
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION app_owner")
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {RUNTIME_ROLE}")

    op.execute("CREATE TYPE platform.tenant_status AS ENUM ('active', 'suspended')")
    op.execute("CREATE TYPE platform.locale AS ENUM ('el', 'en')")
    op.execute("CREATE TYPE app.employee_status AS ENUM ('active', 'inactive')")
    op.execute("CREATE TYPE app.app_role AS ENUM ('employee', 'manager', 'hr_admin', 'company_admin')")
    op.execute("CREATE TYPE audit.actor_kind AS ENUM ('user', 'automation', 'system', 'agent')")
    op.execute("CREATE TYPE audit.outcome AS ENUM ('success', 'denied', 'error')")

    # --- platform (no tenant RLS; protected by application policy) ----------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", _enum("tenant_status", "platform"), nullable=False),
        sa.Column("default_locale", _enum("locale", "platform"), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("settings", pg.JSONB, nullable=False),
        sa.Column("keycloak_group_id", sa.String(64)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        schema="platform",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON platform.tenants TO {RUNTIME_ROLE}")

    op.create_table(
        "idempotency_keys",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID
        ),  # informational only: rows are private to user_id (doc 07 §4.5, M1 note)
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("route", sa.String(200), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer, nullable=False),
        sa.Column("response_body", pg.JSONB),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.UniqueConstraint("user_id", "route", "key", name="uq_idempotency_user_route_key"),
        schema="platform",
    )
    op.create_index("ix_idempotency_expires_at", "idempotency_keys", ["expires_at"], schema="platform")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON platform.idempotency_keys TO {RUNTIME_ROLE}")

    # --- app (tenant tables, forced RLS) -------------------------------------------------------------------
    op.create_table(
        "departments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_departments_tenant"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("manager_employee_id", UUID),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_code"),
        schema="app",
    )
    op.create_table(
        "employees",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_employees_tenant"),
            nullable=False,
        ),
        sa.Column("keycloak_sub", sa.String(64), nullable=False, unique=True),
        sa.Column("employee_number", sa.String(32), nullable=False),
        sa.Column("first_name", sa.String(80), nullable=False),
        sa.Column("last_name", sa.String(80), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column(
            "department_id",
            UUID,
            sa.ForeignKey("app.departments.id", name="fk_employees_department"),
            nullable=False,
        ),
        sa.Column(
            "manager_override_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_employees_manager_override", ondelete="SET NULL"),
        ),
        sa.Column("hire_date", sa.Date, nullable=False),
        sa.Column("locale", _enum("locale", "platform"), nullable=False),
        sa.Column("status", _enum("employee_status", "app"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "employee_number", name="uq_employees_tenant_number"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_employees_tenant_email"),
        schema="app",
    )
    op.create_index(
        "ix_employees_tenant_department", "employees", ["tenant_id", "department_id"], schema="app"
    )
    op.create_foreign_key(
        "fk_departments_manager",
        "departments",
        "employees",
        ["manager_employee_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "employee_roles",
        sa.Column(
            "employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_employee_roles_employee", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", _enum("app_role", "app"), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_employee_roles_tenant"),
            nullable=False,
        ),
        sa.Column("created_at", TS, nullable=False),
        schema="app",
    )
    for table in ("departments", "employees", "employee_roles"):
        apply_tenant_rls("app", table)

    # --- audit (append-only; tenant rows visible to the tenant, NULL-tenant rows to platform admins) -------
    op.create_table(
        "audit_log",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("actor_kind", _enum("actor_kind", "audit"), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_roles", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(64)),
        sa.Column("outcome", _enum("outcome", "audit"), nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column("thread_id", UUID),
        sa.Column("details", pg.JSONB, nullable=False),
        schema="audit",
    )
    op.create_index("ix_audit_log_tenant_occurred", "audit_log", ["tenant_id", "occurred_at"], schema="audit")
    op.create_index("ix_audit_log_action", "audit_log", ["action"], schema="audit")
    op.execute("ALTER TABLE audit.audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit.audit_log FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON audit.audit_log FOR SELECT USING ({TENANT_PREDICATE})")
    op.execute(
        "CREATE POLICY platform_read ON audit.audit_log FOR SELECT "
        "USING (tenant_id IS NULL AND current_setting('app.platform_admin', true) = 'true')"
    )
    op.execute(
        "CREATE POLICY audit_insert ON audit.audit_log FOR INSERT WITH CHECK ("
        f"(tenant_id IS NOT NULL AND {TENANT_PREDICATE}) OR "
        "(tenant_id IS NULL AND NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "AND NULLIF(current_setting('app.user_id', true), '') IS NOT NULL))"
    )
    op.execute(f"GRANT SELECT, INSERT ON audit.audit_log TO {RUNTIME_ROLE}")

    # readiness check compares the applied revision with the code's head (doc 03 §6)
    op.execute(f"GRANT SELECT ON public.alembic_version TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.drop_table("audit_log", schema="audit")
    op.drop_table("employee_roles", schema="app")
    op.drop_constraint("fk_departments_manager", "departments", schema="app", type_="foreignkey")
    op.drop_table("employees", schema="app")
    op.drop_table("departments", schema="app")
    op.drop_table("idempotency_keys", schema="platform")
    op.drop_table("tenants", schema="platform")
    for enum in (
        "audit.outcome",
        "audit.actor_kind",
        "app.app_role",
        "app.employee_status",
        "platform.locale",
        "platform.tenant_status",
    ):
        op.execute(f"DROP TYPE {enum}")
    for schema in ("audit", "app", "platform"):
        op.execute(f"DROP SCHEMA {schema}")
