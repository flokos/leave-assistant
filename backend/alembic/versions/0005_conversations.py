"""M6 agent core: conversations, messages (pseudonymised + provenance), agent runs, provider events; schema
`langgraph` for the checkpointer (tables are created by `AsyncPostgresSaver.setup()` as `langgraph_rw`).

Revision ID: 0005_conversations
Revises: 0004_security_privacy
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import apply_tenant_rls

revision = "0005_conversations"
down_revision = "0004_security_privacy"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)
CHECKPOINT_ROLE = "langgraph_rw"


def _tenant_fk(name: str) -> sa.Column:
    return sa.Column("tenant_id", UUID, sa.ForeignKey("platform.tenants.id", name=name), nullable=False)


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_conversations_tenant"),
        sa.Column(
            "employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_conversations_employee"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("locale", sa.String(8), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_activity_at", TS, nullable=False),
        sa.Column("run_lock_until", TS),
        sa.CheckConstraint("status IN ('open', 'archived')", name="ck_conversations_status"),
        schema="app",
    )
    op.create_index(
        "ix_conversations_employee",
        "conversations",
        ["tenant_id", "employee_id", "last_activity_at"],
        schema="app",
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_conversation_messages_tenant"),
        sa.Column(
            "conversation_id",
            UUID,
            sa.ForeignKey(
                "app.conversations.id", name="fk_conversation_messages_conversation", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content_pseudonymised", sa.Text, nullable=False),
        sa.Column("citations", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("provenance", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("run_id", UUID),
        sa.Column("outcome", sa.String(32)),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system_note')", name="ck_conversation_messages_role"
        ),
        schema="app",
    )
    op.create_index(
        "ix_conversation_messages_conversation",
        "conversation_messages",
        ["tenant_id", "conversation_id", "created_at"],
        schema="app",
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_agent_runs_tenant"),
        sa.Column(
            "conversation_id",
            UUID,
            sa.ForeignKey("app.conversations.id", name="fk_agent_runs_conversation", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn", sa.Integer, nullable=False),
        sa.Column("intent", sa.String(32)),
        sa.Column("language", sa.String(8)),
        sa.Column("route", sa.String(32)),
        sa.Column("provider_used", sa.String(32)),
        sa.Column("model_used", sa.String(80)),
        sa.Column("failovers", sa.Integer, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tool_trace", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("details", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("langfuse_trace_id", sa.String(64)),
        sa.Column("created_at", TS, nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_agent_runs_conversation", "agent_runs", ["tenant_id", "conversation_id", "turn"], schema="app"
    )
    op.create_table(
        "provider_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_provider_events_tenant"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("event", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("run_id", UUID),
        sa.Column("occurred_at", TS, nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_provider_events_occurred", "provider_events", ["tenant_id", "occurred_at"], schema="app"
    )
    for table in ("conversations", "conversation_messages", "agent_runs"):
        apply_tenant_rls("app", table)
    apply_tenant_rls("app", "provider_events", grants="SELECT, INSERT")

    # checkpointer schema: owned by app_owner, usable only by langgraph_rw (it cannot read app.*)
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph AUTHORIZATION app_owner")
    op.execute(f"GRANT USAGE, CREATE ON SCHEMA langgraph TO {CHECKPOINT_ROLE}")
    op.execute(f"REVOKE ALL ON SCHEMA app, audit FROM {CHECKPOINT_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
    for table in ("provider_events", "agent_runs", "conversation_messages", "conversations"):
        op.drop_table(table, schema="app")
