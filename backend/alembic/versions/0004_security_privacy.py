"""M5 content security and privacy: PII vault, security policies (+ platform defaults), injection findings,
supporting documents (envelope-encrypted) with access events and download tokens, retention policies and
review items.

Revision ID: 0004_security_privacy
Revises: 0003_documents
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import RUNTIME_ROLE, apply_tenant_rls

revision = "0004_security_privacy"
down_revision = "0003_documents"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)
DEFAULT_ALLOWED_MIME = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
)


def _tenant_fk(name: str, *, primary_key: bool = False) -> sa.Column:
    return sa.Column(
        "tenant_id",
        UUID,
        sa.ForeignKey("platform.tenants.id", name=name),
        nullable=False,
        primary_key=primary_key,
    )


def upgrade() -> None:
    # --- PII vault (ADR-0017) -------------------------------------------------------------------------------
    op.create_table(
        "pii_vault",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_pii_vault_tenant"),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("value_hmac", sa.LargeBinary, nullable=False),
        sa.Column("value_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("placeholder", sa.String(40), nullable=False),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("source_kinds", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column(
            "subject_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_pii_vault_subject", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("tenant_id", "value_hmac", name="uq_pii_vault_hmac"),
        sa.UniqueConstraint("tenant_id", "placeholder", name="uq_pii_vault_placeholder"),
        schema="app",
    )
    op.create_table(
        "pii_vault_counters",
        _tenant_fk("fk_pii_vault_counters_tenant", primary_key=True),
        sa.Column("entity_type", sa.String(24), primary_key=True),
        sa.Column("next_seq", sa.Integer, nullable=False, server_default="1"),
        schema="app",
    )

    # --- security policies: platform defaults (single row) + per-tenant row -----------------------------------
    op.create_table(
        "security_policy_defaults",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("risk_low_max", sa.Float, nullable=False),
        sa.Column("risk_medium_max", sa.Float, nullable=False),
        sa.Column("injection_llm_judge", sa.Boolean, nullable=False),
        sa.Column("user_input_block_threshold", sa.Float, nullable=False),
        sa.Column("max_upload_mb", sa.Integer, nullable=False),
        sa.Column("allowed_mime", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("threshold_min", sa.Float, nullable=False),
        sa.Column("threshold_max", sa.Float, nullable=False),
        sa.Column("max_upload_mb_limit", sa.Integer, nullable=False),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("id = 1", name="ck_security_policy_defaults_single_row"),
        schema="platform",
    )
    op.execute(
        sa.text(
            """
            INSERT INTO platform.security_policy_defaults
                (id, risk_low_max, risk_medium_max, injection_llm_judge, user_input_block_threshold, max_upload_mb,
                 allowed_mime, threshold_min, threshold_max, max_upload_mb_limit, updated_by, updated_at)
            VALUES (1, 0.3, 0.7, false, 0.85, 25, :mime, 0.05, 0.95, 50, 'migration', now())
            """
        ).bindparams(sa.bindparam("mime", list(DEFAULT_ALLOWED_MIME), type_=pg.ARRAY(sa.Text)))
    )
    op.execute(f"GRANT SELECT, UPDATE ON platform.security_policy_defaults TO {RUNTIME_ROLE}")
    op.create_table(
        "security_policies",
        _tenant_fk("fk_security_policies_tenant", primary_key=True),
        sa.Column("risk_low_max", sa.Float, nullable=False),
        sa.Column("risk_medium_max", sa.Float, nullable=False),
        sa.Column("injection_llm_judge", sa.Boolean, nullable=False),
        sa.Column("user_input_block_threshold", sa.Float, nullable=False),
        sa.Column("max_upload_mb", sa.Integer, nullable=False),
        sa.Column("allowed_mime", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("risk_low_max < risk_medium_max", name="ck_security_policies_ordering"),
        schema="app",
    )
    # back-fill: every existing tenant starts from the platform defaults (M5 decision 5)
    op.execute(
        """
        INSERT INTO app.security_policies
            (tenant_id, risk_low_max, risk_medium_max, injection_llm_judge, user_input_block_threshold,
             max_upload_mb, allowed_mime, updated_by, created_at, updated_at)
        SELECT t.id, d.risk_low_max, d.risk_medium_max, d.injection_llm_judge, d.user_input_block_threshold,
               d.max_upload_mb, d.allowed_mime, 'migration', now(), now()
        FROM platform.tenants t CROSS JOIN platform.security_policy_defaults d
        WHERE d.id = 1
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )
    op.create_table(
        "injection_findings",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_injection_findings_tenant"),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("detector", sa.String(32), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("excerpt_hash", sa.String(64), nullable=False),
        sa.Column("details", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", TS, nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_injection_findings_subject",
        "injection_findings",
        ["tenant_id", "subject_type", "subject_id"],
        schema="app",
    )

    # --- supporting documents (doc 08 §9) ----------------------------------------------------------------------
    op.create_table(
        "supporting_documents",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_supporting_documents_tenant"),
        sa.Column(
            "request_id",
            UUID,
            sa.ForeignKey("app.leave_requests.id", name="fk_supporting_documents_request"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_supporting_documents_employee"),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(400), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("encrypted_dek", sa.LargeBinary, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="stored"),
        sa.Column(
            "uploaded_by_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_supporting_documents_uploader"),
            nullable=False,
        ),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("retention_until", sa.Date),
        sa.Column("deleted_at", TS),
        sa.CheckConstraint(
            "status IN ('stored', 'retention_pending', 'deleted')", name="ck_supporting_documents_status"
        ),
        schema="app",
    )
    op.create_index(
        "ix_supporting_documents_request", "supporting_documents", ["tenant_id", "request_id"], schema="app"
    )
    op.create_index(
        "ix_supporting_documents_status", "supporting_documents", ["tenant_id", "status"], schema="app"
    )
    op.create_table(
        "supporting_document_access_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_supporting_access_tenant"),
        sa.Column(
            "supporting_document_id",
            UUID,
            sa.ForeignKey(
                "app.supporting_documents.id", name="fk_supporting_access_document", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_supporting_access_actor"),
            nullable=False,
        ),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("request_id", sa.String(64)),
        schema="app",
    )
    op.create_index(
        "ix_supporting_access_document",
        "supporting_document_access_events",
        ["tenant_id", "supporting_document_id", "occurred_at"],
        schema="app",
    )
    op.create_table(
        "supporting_download_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        _tenant_fk("fk_download_tokens_tenant"),
        sa.Column("supporting_document_id", UUID, nullable=False),
        sa.Column("actor_employee_id", UUID, nullable=False),
        sa.Column("actor_user_id", sa.String(120), nullable=False),
        sa.Column("actor_roles", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("used_at", TS),
        schema="platform",
    )
    op.create_index(
        "ix_supporting_download_tokens_expires",
        "supporting_download_tokens",
        ["expires_at"],
        schema="platform",
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON platform.supporting_download_tokens TO {RUNTIME_ROLE}"
    )

    # --- retention (doc 07 §7) ---------------------------------------------------------------------------------
    op.create_table(
        "retention_policies",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_retention_policies_tenant"),
        sa.Column("document_class", sa.String(32), nullable=False),
        sa.Column("retention_days", sa.Integer, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "document_class", name="uq_retention_policies_class"),
        sa.CheckConstraint("retention_days BETWEEN 0 AND 3650", name="ck_retention_policies_days"),
        schema="app",
    )
    op.create_table(
        "retention_review_items",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_retention_items_tenant"),
        sa.Column(
            "policy_id",
            UUID,
            sa.ForeignKey("app.retention_policies.id", name="fk_retention_items_policy"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", UUID, nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("eligible_since", sa.Date, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column(
            "reviewer_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_retention_items_reviewer"),
        ),
        sa.Column("decided_at", TS),
        sa.Column("comment", sa.Text),
        sa.CheckConstraint(
            "status IN ('pending', 'approved_delete', 'retained', 'deleted')",
            name="ck_retention_items_status",
        ),
        schema="app",
    )
    op.create_index(
        "ix_retention_review_items_status",
        "retention_review_items",
        ["tenant_id", "status", "created_at"],
        schema="app",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_retention_items_open ON app.retention_review_items (tenant_id, subject_type, subject_id) "
        "WHERE status = 'pending'"
    )

    # --- RLS and grants ----------------------------------------------------------------------------------------
    for table in (
        "pii_vault",
        "pii_vault_counters",
        "security_policies",
        "injection_findings",
        "supporting_documents",
        "retention_policies",
        "retention_review_items",
    ):
        apply_tenant_rls("app", table)
    apply_tenant_rls("app", "supporting_document_access_events", grants="SELECT, INSERT")


def downgrade() -> None:
    for table in (
        "retention_review_items",
        "retention_policies",
        "supporting_document_access_events",
        "supporting_documents",
        "injection_findings",
        "security_policies",
        "pii_vault_counters",
        "pii_vault",
    ):
        op.drop_table(table, schema="app")
    op.drop_table("supporting_download_tokens", schema="platform")
    op.drop_table("security_policy_defaults", schema="platform")
