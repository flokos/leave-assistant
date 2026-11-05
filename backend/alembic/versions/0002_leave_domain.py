"""M2 leave domain: catalogue, requests, steps, events, ledger, balances view, notifications, outbox (doc 07 §4.3/§4.7).

Revision ID: 0002_leave_domain
Revises: 0001_baseline
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import RUNTIME_ROLE, apply_tenant_rls

revision = "0002_leave_domain"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)
DAYS = sa.Numeric(6, 2)


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(name=name, schema="app", create_type=False)


def _tenant_fk(name: str) -> sa.Column:
    return sa.Column("tenant_id", UUID, sa.ForeignKey("platform.tenants.id", name=name), nullable=False)


def upgrade() -> None:
    # trusted extension (PG 13+): GiST on uuid equality for the overlap exclusion constraint
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute("CREATE TYPE app.grant_mode AS ENUM ('upfront', 'monthly_accrual')")
    op.execute("CREATE TYPE app.step_role AS ENUM ('manager', 'hr')")
    op.execute(
        "CREATE TYPE app.step_status AS ENUM ('pending', 'approved', 'rejected', 'skipped', 'cancelled')"
    )
    op.execute(
        "CREATE TYPE app.request_status AS ENUM ('DRAFT', 'SUBMITTED', 'PENDING_MANAGER', 'PENDING_HR', 'APPROVED', "
        "'ACTIVE', 'COMPLETED', 'REJECTED', 'CANCELLED', 'WITHDRAWN', 'MODIFICATION_PENDING')"
    )
    op.execute("CREATE TYPE app.request_actor_kind AS ENUM ('employee', 'manager', 'hr', 'system', 'agent')")
    op.execute("CREATE TYPE app.decision_channel AS ENUM ('ui', 'agent')")
    op.execute(
        "CREATE TYPE app.ledger_entry_type AS ENUM ('GRANT', 'DEDUCTION', 'REVERSAL', 'ADJUSTMENT', 'CARRYOVER', 'EXPIRY')"
    )
    op.execute("CREATE TYPE app.email_status AS ENUM ('pending', 'sent', 'failed', 'skipped')")

    # --- catalogue -----------------------------------------------------------------------------------------
    op.create_table(
        "leave_types",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_leave_types_tenant"),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name_el", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("balance_bearing", sa.Boolean, nullable=False),
        sa.Column("allow_retroactive_days", sa.Integer, nullable=False),
        sa.Column("requires_document_above_days", sa.Integer),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_leave_types_tenant_code"),
        schema="app",
    )
    op.create_table(
        "leave_policies",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_leave_policies_tenant"),
        sa.Column(
            "leave_type_id",
            UUID,
            sa.ForeignKey("app.leave_types.id", name="fk_leave_policies_type"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("entitlement_days", DAYS, nullable=False),
        sa.Column("carryover_max_days", DAYS, nullable=False),
        sa.Column("carryover_expires_on", sa.Date),
        sa.Column("allow_negative", sa.Boolean, nullable=False),
        sa.Column("min_notice_days", sa.Integer, nullable=False),
        sa.Column("max_consecutive_days", sa.Integer),
        sa.Column("prorate_on_hire", sa.Boolean, nullable=False),
        sa.Column("grant_mode", _enum("grant_mode"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "leave_type_id", "year", name="uq_leave_policies_type_year"),
        schema="app",
    )
    op.create_table(
        "public_holidays",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_public_holidays_tenant"),
        sa.Column("holiday_date", sa.Date, nullable=False),
        sa.Column("name_el", sa.String(120), nullable=False),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("recurring", sa.Boolean, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "holiday_date", name="uq_public_holidays_tenant_date"),
        schema="app",
    )
    op.create_table(
        "approval_rules",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_approval_rules_tenant"),
        sa.Column("leave_type_id", UUID, sa.ForeignKey("app.leave_types.id", name="fk_approval_rules_type")),
        sa.Column("min_working_days", DAYS, nullable=False),
        sa.Column("max_working_days", DAYS),
        sa.Column("requires_document", sa.Boolean, nullable=False),
        sa.Column("steps", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("auto_acknowledge", sa.Boolean, nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_approval_rules_tenant_priority", "approval_rules", ["tenant_id", "priority"], schema="app"
    )
    op.create_table(
        "request_counters",
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_request_counters_tenant"),
            primary_key=True,
        ),
        sa.Column("year", sa.Integer, primary_key=True),
        sa.Column("next_seq", sa.Integer, nullable=False),
        schema="app",
    )

    # --- requests, steps, events ---------------------------------------------------------------------------
    op.create_table(
        "leave_requests",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_leave_requests_tenant"),
        sa.Column("request_number", sa.String(20), nullable=False),
        sa.Column(
            "employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_leave_requests_employee"),
            nullable=False,
        ),
        sa.Column(
            "leave_type_id",
            UUID,
            sa.ForeignKey("app.leave_types.id", name="fk_leave_requests_type"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("working_days", DAYS, nullable=False),
        sa.Column("status", _enum("request_status"), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("submitted_at", TS),
        sa.Column("decided_at", TS),
        sa.Column("cancelled_at", TS),
        sa.Column(
            "modification_of_request_id",
            UUID,
            sa.ForeignKey("app.leave_requests.id", name="fk_leave_requests_modification_of"),
        ),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "request_number", name="uq_leave_requests_number"),
        sa.CheckConstraint("end_date >= start_date", name="ck_leave_requests_dates"),
        schema="app",
    )
    op.create_index(
        "ix_leave_requests_tenant_employee", "leave_requests", ["tenant_id", "employee_id"], schema="app"
    )
    op.create_index(
        "ix_leave_requests_tenant_status", "leave_requests", ["tenant_id", "status"], schema="app"
    )
    op.create_index(
        "ix_leave_requests_dates", "leave_requests", ["tenant_id", "start_date", "end_date"], schema="app"
    )
    # overlapping open requests of one employee are impossible, whatever the application does (doc 07 §4.3)
    op.execute(
        "ALTER TABLE app.leave_requests ADD CONSTRAINT ex_leave_requests_no_overlap EXCLUDE USING gist "
        "(employee_id WITH =, daterange(start_date, end_date, '[]') WITH &&) "
        "WHERE (status NOT IN ('CANCELLED', 'REJECTED', 'WITHDRAWN', 'MODIFICATION_PENDING'))"
    )
    op.create_table(
        "leave_request_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_leave_request_events_tenant"),
        sa.Column(
            "request_id",
            UUID,
            sa.ForeignKey(
                "app.leave_requests.id", name="fk_leave_request_events_request", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("from_status", _enum("request_status")),
        sa.Column("to_status", _enum("request_status"), nullable=False),
        sa.Column(
            "actor_employee_id", UUID, sa.ForeignKey("app.employees.id", name="fk_leave_request_events_actor")
        ),
        sa.Column("actor_kind", _enum("request_actor_kind"), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("occurred_at", TS, nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_leave_request_events_request",
        "leave_request_events",
        ["tenant_id", "request_id", "occurred_at"],
        schema="app",
    )
    op.create_table(
        "approval_steps",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_approval_steps_tenant"),
        sa.Column(
            "request_id",
            UUID,
            sa.ForeignKey("app.leave_requests.id", name="fk_approval_steps_request", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("approver_role", _enum("step_role"), nullable=False),
        sa.Column(
            "designated_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_approval_steps_designee"),
        ),
        sa.Column("status", _enum("step_status"), nullable=False),
        sa.Column(
            "decided_by_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_approval_steps_decider"),
        ),
        sa.Column("decided_at", TS),
        sa.Column("comment", sa.Text),
        sa.Column("via", _enum("decision_channel")),
        sa.Column("fallback", sa.Boolean, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("request_id", "step_no", name="uq_approval_steps_request_step"),
        schema="app",
    )
    op.create_index(
        "ix_approval_steps_designee",
        "approval_steps",
        ["tenant_id", "designated_employee_id", "status"],
        schema="app",
    )

    # --- ledger (append-only for the runtime role) ---------------------------------------------------------
    op.create_table(
        "leave_ledger_entries",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_leave_ledger_tenant"),
        sa.Column(
            "employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_leave_ledger_employee"),
            nullable=False,
        ),
        sa.Column(
            "leave_type_id",
            UUID,
            sa.ForeignKey("app.leave_types.id", name="fk_leave_ledger_type"),
            nullable=False,
        ),
        sa.Column("period_year", sa.Integer, nullable=False),
        sa.Column("entry_type", _enum("ledger_entry_type"), nullable=False),
        sa.Column("quantity_days", DAYS, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("request_id", UUID, sa.ForeignKey("app.leave_requests.id", name="fk_leave_ledger_request")),
        sa.Column("reason", sa.Text),
        sa.Column(
            "created_by_employee_id", UUID, sa.ForeignKey("app.employees.id", name="fk_leave_ledger_creator")
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_leave_ledger_idempotency_key"),
        schema="app",
    )
    op.create_index(
        "ix_leave_ledger_balance_scope",
        "leave_ledger_entries",
        ["tenant_id", "employee_id", "leave_type_id", "period_year"],
        schema="app",
    )

    # --- notifications and outbox --------------------------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_notifications_tenant"),
        sa.Column(
            "recipient_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_notifications_recipient"),
            nullable=False,
        ),
        sa.Column("type", sa.String(60), nullable=False),
        sa.Column("params", pg.JSONB, nullable=False),
        sa.Column("channels", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("read_at", TS),
        sa.Column("email_status", _enum("email_status"), nullable=False),
        sa.Column("email_attempts", sa.Integer, nullable=False),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_notifications_dedupe"),
        schema="app",
    )
    op.create_index(
        "ix_notifications_recipient",
        "notifications",
        ["tenant_id", "recipient_employee_id", "created_at"],
        schema="app",
    )
    op.create_table(
        "integration_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_integration_events_tenant"),
        sa.Column("type", sa.String(60), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("delivered_at", TS),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("dead", sa.Boolean, nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_integration_events_undelivered",
        "integration_events",
        ["tenant_id", "delivered_at", "created_at"],
        schema="app",
    )

    # --- RLS and grants ------------------------------------------------------------------------------------
    for table in (
        "leave_types",
        "leave_policies",
        "public_holidays",
        "approval_rules",
        "request_counters",
        "leave_requests",
        "approval_steps",
        "notifications",
    ):
        apply_tenant_rls("app", table)
    for table in ("leave_request_events", "leave_ledger_entries", "integration_events"):
        apply_tenant_rls("app", table, grants="SELECT, INSERT")

    # balance view: RLS of the underlying tables applies to the caller (security_invoker, PG 15+)
    op.execute(
        """
        CREATE VIEW app.leave_balances WITH (security_invoker = true) AS
        WITH ledger AS (
            SELECT tenant_id, employee_id, leave_type_id, period_year, SUM(quantity_days) AS balance_days
            FROM app.leave_ledger_entries GROUP BY tenant_id, employee_id, leave_type_id, period_year
        ),
        pending AS (
            SELECT tenant_id, employee_id, leave_type_id, EXTRACT(YEAR FROM start_date)::int AS period_year, SUM(working_days) AS pending_days
            FROM app.leave_requests WHERE status IN ('SUBMITTED', 'PENDING_MANAGER', 'PENDING_HR')
            GROUP BY tenant_id, employee_id, leave_type_id, EXTRACT(YEAR FROM start_date)
        )
        SELECT COALESCE(l.tenant_id, p.tenant_id) AS tenant_id,
               COALESCE(l.employee_id, p.employee_id) AS employee_id,
               COALESCE(l.leave_type_id, p.leave_type_id) AS leave_type_id,
               COALESCE(l.period_year, p.period_year) AS period_year,
               COALESCE(l.balance_days, 0) AS balance_days,
               COALESCE(p.pending_days, 0) AS pending_days,
               COALESCE(l.balance_days, 0) - COALESCE(p.pending_days, 0) AS available_days
        FROM ledger l FULL OUTER JOIN pending p
          ON l.tenant_id = p.tenant_id AND l.employee_id = p.employee_id AND l.leave_type_id = p.leave_type_id AND l.period_year = p.period_year
        """
    )
    op.execute(f"GRANT SELECT ON app.leave_balances TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS app.leave_balances")
    for table in (
        "integration_events",
        "notifications",
        "leave_ledger_entries",
        "approval_steps",
        "leave_request_events",
        "leave_requests",
        "request_counters",
        "approval_rules",
        "public_holidays",
        "leave_policies",
        "leave_types",
    ):
        op.drop_table(table, schema="app")
    for enum in (
        "email_status",
        "ledger_entry_type",
        "decision_channel",
        "request_actor_kind",
        "request_status",
        "step_status",
        "step_role",
        "grant_mode",
    ):
        op.execute(f"DROP TYPE app.{enum}")
