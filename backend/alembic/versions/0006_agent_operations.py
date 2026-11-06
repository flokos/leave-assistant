"""M7 agentic operations: action proposals (the confirm-before-execute records), the pending-interrupt mirror,
the card reference on assistant messages, and two exactly-once guards (doc 04 §6, doc 07 §4.3/§4.6).

Revision ID: 0006_agent_operations
Revises: 0005_conversations
Create Date: 2026-11-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import apply_tenant_rls

revision = "0006_agent_operations"
down_revision = "0005_conversations"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)
KINDS = "'submit', 'modify', 'cancel', 'approve', 'reject'"
STATUSES = "'pending', 'confirmed', 'executed', 'rejected', 'expired', 'failed'"


def upgrade() -> None:
    op.create_table(
        "action_proposals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_action_proposals_tenant"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            UUID,
            sa.ForeignKey(
                "app.conversations.id", name="fk_action_proposals_conversation", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_action_proposals_employee"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("run_id", UUID),
        sa.Column("claimed_by_run", UUID),
        sa.Column("claimed_at", TS),
        sa.Column("decided_at", TS),
        sa.Column(
            "executed_request_id",
            UUID,
            sa.ForeignKey("app.leave_requests.id", name="fk_action_proposals_request", ondelete="SET NULL"),
        ),
        sa.Column("outcome", pg.JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_action_proposals_kind"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_action_proposals_status"),
        schema="app",
    )
    op.create_index(
        "ix_action_proposals_conversation",
        "action_proposals",
        ["tenant_id", "conversation_id", "created_at"],
        schema="app",
    )
    op.create_index(
        "ix_action_proposals_pending_expiry",
        "action_proposals",
        ["tenant_id", "expires_at"],
        schema="app",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "pending_interrupts",
        sa.Column(
            "conversation_id",
            UUID,
            sa.ForeignKey(
                "app.conversations.id", name="fk_pending_interrupts_conversation", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_pending_interrupts_tenant"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey(
                "app.action_proposals.id", name="fk_pending_interrupts_proposal", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        schema="app",
    )
    op.add_column(
        "conversation_messages",
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey(
                "app.action_proposals.id", name="fk_conversation_messages_proposal", ondelete="SET NULL"
            ),
        ),
        schema="app",
    )
    # exactly-once guards: a submit carrying a known key returns the existing request instead of a second one, and
    # the request event an agent execution wrote is findable by its proposal id (stale-claim recovery)
    op.create_index(
        "uq_leave_requests_idempotency",
        "leave_requests",
        ["tenant_id", "employee_id", "idempotency_key"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.execute(
        "CREATE INDEX ix_leave_request_events_proposal ON app.leave_request_events "
        "(tenant_id, (payload->>'proposal_id')) WHERE payload ? 'proposal_id'"
    )
    for table in ("action_proposals", "pending_interrupts"):
        apply_tenant_rls("app", table)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.ix_leave_request_events_proposal")
    op.drop_index("uq_leave_requests_idempotency", table_name="leave_requests", schema="app")
    op.drop_column("conversation_messages", "proposal_id", schema="app")
    op.drop_table("pending_interrupts", schema="app")
    op.drop_table("action_proposals", schema="app")
