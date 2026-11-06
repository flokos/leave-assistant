"""M8 integrations: calendar connections (encrypted refresh tokens), the calendar sync outbox, event mapping,
reconciliation items, the mock provider's events, one-time OAuth states (platform), and a delivery lease on the
integration-event outbox polled by n8n (doc 06, doc 07 §4.7, doc 10).

Revision ID: 0007_integrations
Revises: 0006_agent_operations
Create Date: 2026-11-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import RUNTIME_ROLE, apply_tenant_rls

revision = "0007_integrations"
down_revision = "0006_agent_operations"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)


def _tenant(name: str) -> sa.Column:
    return sa.Column("tenant_id", UUID, sa.ForeignKey("platform.tenants.id", name=name), nullable=False)


def upgrade() -> None:
    op.create_table(
        "calendar_connections",
        sa.Column("id", UUID, primary_key=True),
        _tenant("fk_calendar_connections_tenant"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "employee_id", UUID, sa.ForeignKey("app.employees.id", name="fk_calendar_connections_employee")
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_calendar_id", sa.String(255)),
        sa.Column("refresh_token_encrypted", sa.LargeBinary),
        sa.Column("refresh_token_dek", sa.LargeBinary),
        sa.Column("scopes", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "authorised_by_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_calendar_connections_authoriser"),
            nullable=False,
        ),
        sa.Column("last_success_at", TS),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.CheckConstraint("kind IN ('company_shared', 'personal')", name="ck_calendar_connections_kind"),
        sa.CheckConstraint("provider IN ('google', 'mock')", name="ck_calendar_connections_provider"),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'revoked')", name="ck_calendar_connections_status"
        ),
        sa.CheckConstraint(
            "(kind = 'personal') = (employee_id IS NOT NULL)", name="ck_calendar_connections_owner"
        ),
        schema="app",
    )
    # one live connection per company calendar and per employee (revoked rows stay as history)
    op.create_index(
        "uq_calendar_connections_company",
        "calendar_connections",
        ["tenant_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("kind = 'company_shared' AND status <> 'revoked'"),
    )
    op.create_index(
        "uq_calendar_connections_personal",
        "calendar_connections",
        ["tenant_id", "employee_id"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("kind = 'personal' AND status <> 'revoked'"),
    )
    op.create_table(
        "calendar_sync_jobs",
        sa.Column("id", UUID, primary_key=True),
        _tenant("fk_calendar_sync_jobs_tenant"),
        sa.Column(
            "request_id",
            UUID,
            sa.ForeignKey("app.leave_requests.id", name="fk_calendar_sync_jobs_request"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            UUID,
            sa.ForeignKey("app.calendar_connections.id", name="fk_calendar_sync_jobs_connection"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unknown_errors", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TS),
        sa.Column("error_class", sa.String(16)),
        sa.Column("last_error", sa.Text),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.CheckConstraint(
            "operation IN ('create', 'update', 'delete')", name="ck_calendar_sync_jobs_operation"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'parked', 'dead')",
            name="ck_calendar_sync_jobs_status",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_calendar_sync_jobs_idempotency"),
        schema="app",
    )
    op.create_index(
        "ix_calendar_sync_jobs_status",
        "calendar_sync_jobs",
        ["tenant_id", "status", "next_attempt_at"],
        schema="app",
    )
    op.create_index(
        "ix_calendar_sync_jobs_request", "calendar_sync_jobs", ["tenant_id", "request_id"], schema="app"
    )
    op.create_table(
        "calendar_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant("fk_calendar_events_tenant"),
        sa.Column(
            "request_id",
            UUID,
            sa.ForeignKey("app.leave_requests.id", name="fk_calendar_events_request"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            UUID,
            sa.ForeignKey("app.calendar_connections.id", name="fk_calendar_events_connection"),
            nullable=False,
        ),
        sa.Column("external_event_id", sa.String(1024), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("last_verified_at", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(
            "status IN ('present', 'deleted', 'orphaned', 'missing')", name="ck_calendar_events_status"
        ),
        sa.UniqueConstraint("connection_id", "request_id", name="uq_calendar_events_connection_request"),
        schema="app",
    )
    op.create_table(
        "calendar_reconciliation_items",
        sa.Column("id", UUID, primary_key=True),
        _tenant("fk_calendar_reconciliation_items_tenant"),
        sa.Column(
            "connection_id",
            UUID,
            sa.ForeignKey("app.calendar_connections.id", name="fk_calendar_reconciliation_items_connection"),
            nullable=False,
        ),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("run_at", TS, nullable=False),
        sa.Column("finding", sa.String(24), nullable=False),
        sa.Column("request_id", UUID),
        sa.Column("external_event_id", sa.String(1024)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("action", sa.String(24)),
        sa.Column("details", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "resolved_by_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_calendar_reconciliation_items_resolver"),
        ),
        sa.Column("resolved_at", TS),
        sa.CheckConstraint(
            "finding IN ('missing', 'mismatched', 'orphaned', 'unknown_external_id')",
            name="ck_calendar_reconciliation_items_finding",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'ignored')", name="ck_calendar_reconciliation_items_status"
        ),
        schema="app",
    )
    op.create_index(
        "ix_calendar_reconciliation_items_run",
        "calendar_reconciliation_items",
        ["tenant_id", "run_at"],
        schema="app",
    )
    op.create_table(
        "mock_calendar_events",
        _tenant("fk_mock_calendar_events_tenant"),
        sa.Column("calendar_id", sa.String(255), primary_key=True),
        sa.Column("event_id", sa.String(1024), primary_key=True),
        sa.Column("body", pg.JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        schema="app",
    )
    op.create_table(
        "oauth_states",
        sa.Column("nonce_hash", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_oauth_states_tenant"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("employee_id", UUID, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("roles", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("used_at", TS),
        schema="platform",
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON platform.oauth_states TO {RUNTIME_ROLE}")
    op.add_column("integration_events", sa.Column("leased_until", TS), schema="app")
    # the outbox was append-only for the runtime role (M2); delivery now updates only its bookkeeping columns, and
    # housekeeping deletes delivered rows (M8 decision 8)
    op.execute(
        f"GRANT UPDATE (delivered_at, attempts, dead, leased_until) ON app.integration_events TO {RUNTIME_ROLE}"
    )
    op.execute(f"GRANT DELETE ON app.integration_events TO {RUNTIME_ROLE}")
    for table in (
        "calendar_connections",
        "calendar_sync_jobs",
        "calendar_events",
        "calendar_reconciliation_items",
        "mock_calendar_events",
    ):
        apply_tenant_rls("app", table)


def downgrade() -> None:
    op.execute(f"REVOKE DELETE ON app.integration_events FROM {RUNTIME_ROLE}")
    op.execute(
        f"REVOKE UPDATE (delivered_at, attempts, dead, leased_until) ON app.integration_events FROM {RUNTIME_ROLE}"
    )
    op.drop_column("integration_events", "leased_until", schema="app")
    op.drop_table("oauth_states", schema="platform")
    for table in (
        "mock_calendar_events",
        "calendar_reconciliation_items",
        "calendar_events",
        "calendar_sync_jobs",
        "calendar_connections",
    ):
        op.drop_table(table, schema="app")
