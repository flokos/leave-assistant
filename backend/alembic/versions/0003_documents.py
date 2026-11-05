"""M4 document platform: documents, versions, chunks (pgvector + FTS), ACL, review events, index generations, jobs.

Revision ID: 0003_documents
Revises: 0002_leave_domain
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import RUNTIME_ROLE, apply_tenant_rls

revision = "0003_documents"
down_revision = "0002_leave_domain"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)
EMBEDDING_DIMENSION = 384


def _enum(name: str, schema: str = "app") -> pg.ENUM:
    return pg.ENUM(name=name, schema=schema, create_type=False)


def _tenant_fk(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column("tenant_id", UUID, sa.ForeignKey("platform.tenants.id", name=name), nullable=nullable)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    # unaccent() is STABLE; generated columns need IMMUTABLE — the dictionary rules file does not change at runtime
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.f_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
        """
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION app.f_unaccent(text) TO {RUNTIME_ROLE}")

    op.execute(
        "CREATE TYPE app.document_category AS ENUM "
        "('policy_general', 'policy_hr_restricted', 'department_notice', 'admin_internal')"
    )
    op.execute(
        "CREATE TYPE app.document_version_status AS ENUM ('UPLOADED', 'PROCESSING', 'FAILED', 'ACTIVE', "
        "'PENDING_REVIEW', 'QUARANTINED', 'SUPERSEDED', 'RETIRED', 'DELETED')"
    )
    op.execute("CREATE TYPE app.risk_tier AS ENUM ('low', 'medium', 'high')")
    op.execute("CREATE TYPE app.chunk_type AS ENUM ('text', 'table')")
    op.execute("CREATE TYPE app.acl_principal_type AS ENUM ('role', 'department', 'employee')")
    op.execute("CREATE TYPE app.review_action AS ENUM ('release', 'reject', 'delete', 'inspect')")
    op.execute("CREATE TYPE app.generation_status AS ENUM ('building', 'active', 'retired')")
    op.execute("CREATE TYPE platform.job_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'dead')")

    # --- documents and versions ----------------------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_documents_tenant"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("category", _enum("document_category"), nullable=False),
        sa.Column(
            "department_id",
            UUID,
            sa.ForeignKey("app.departments.id", name="fk_documents_department", ondelete="SET NULL"),
        ),
        sa.Column(
            "owner_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_documents_owner"),
            nullable=False,
        ),
        sa.Column("active_version_id", UUID),
        sa.Column("language_hint", sa.String(8)),
        sa.Column("deleted_at", TS),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        schema="app",
    )
    op.create_index("ix_documents_tenant_created", "documents", ["tenant_id", "created_at"], schema="app")
    op.create_table(
        "document_versions",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_document_versions_tenant"),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("app.documents.id", name="fk_document_versions_document", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("object_key", sa.String(400), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", _enum("document_version_status"), nullable=False),
        sa.Column("pages", sa.Integer),
        sa.Column("language", sa.String(8)),
        sa.Column("extracted_object_key", sa.String(400)),
        sa.Column("risk_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("risk_tier", _enum("risk_tier"), nullable=False, server_default="low"),
        sa.Column("risk_findings", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("pii_summary", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("processing_error", sa.Text),
        sa.Column("processed_at", TS),
        sa.Column("index_generation", sa.String(200)),
        sa.Column("retrievable", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "uploaded_by_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_document_versions_uploader"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("document_id", "version_no", name="uq_document_versions_number"),
        schema="app",
    )
    op.create_index(
        "ix_document_versions_tenant_status", "document_versions", ["tenant_id", "status"], schema="app"
    )
    op.create_foreign_key(
        "fk_documents_active_version",
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )

    # --- chunks: vectors + three FTS configurations ----------------------------------------------------------
    op.create_table(
        "document_chunks",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_document_chunks_tenant"),
        sa.Column(
            "version_id",
            UUID,
            sa.ForeignKey("app.document_versions.id", name="fk_document_chunks_version", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("chunk_type", _enum("chunk_type"), nullable=False),
        sa.Column("heading_path", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("page_start", sa.Integer),
        sa.Column("page_end", sa.Integer),
        sa.Column("language", sa.String(8)),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_llm_safe", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION)),
        sa.Column("embedding_model_id", sa.String(200), nullable=False),
        sa.Column("tsv_el", pg.TSVECTOR, sa.Computed("to_tsvector('greek', content)", persisted=True)),
        sa.Column("tsv_en", pg.TSVECTOR, sa.Computed("to_tsvector('english', content)", persisted=True)),
        sa.Column(
            "tsv_simple",
            pg.TSVECTOR,
            sa.Computed("to_tsvector('simple', app.f_unaccent(content))", persisted=True),
        ),
        sa.Column("risk_flags", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("has_pii", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("retrievable", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_document_chunks_ordinal"),
        schema="app",
    )
    op.create_index(
        "ix_document_chunks_tenant_version",
        "document_chunks",
        ["tenant_id", "version_id", "ordinal"],
        schema="app",
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON app.document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WHERE retrievable"
    )
    for config in ("el", "en", "simple"):
        op.execute(
            f"CREATE INDEX ix_document_chunks_tsv_{config} ON app.document_chunks USING gin (tsv_{config})"
        )

    # --- ACL, review events, index generations -----------------------------------------------------------------
    op.create_table(
        "document_acl",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_document_acl_tenant"),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("app.documents.id", name="fk_document_acl_document", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_type", _enum("acl_principal_type"), nullable=False),
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column("permission", sa.String(16), nullable=False, server_default="read"),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint(
            "document_id", "principal_type", "principal_id", name="uq_document_acl_principal"
        ),
        schema="app",
    )
    op.create_table(
        "document_review_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_document_review_events_tenant"),
        sa.Column(
            "version_id",
            UUID,
            sa.ForeignKey(
                "app.document_versions.id", name="fk_document_review_events_version", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "reviewer_employee_id",
            UUID,
            sa.ForeignKey("app.employees.id", name="fk_document_review_events_reviewer"),
            nullable=False,
        ),
        sa.Column("action", _enum("review_action"), nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("occurred_at", TS, nullable=False),
        schema="app",
    )
    op.create_index(
        "ix_document_review_events_version", "document_review_events", ["version_id"], schema="app"
    )
    op.create_table(
        "index_generations",
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("platform.tenants.id", name="fk_index_generations_tenant"),
            primary_key=True,
        ),
        sa.Column("generation", sa.String(200), primary_key=True),
        sa.Column("embedding_model_id", sa.String(200), nullable=False),
        sa.Column("chunking_rules_version", sa.String(32), nullable=False),
        sa.Column("status", _enum("generation_status"), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("completed_at", TS),
        schema="app",
    )

    # --- jobs (platform schema, no RLS — M4 decision 2) --------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk("fk_jobs_tenant", nullable=True),
        sa.Column("type", sa.String(60), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("status", _enum("job_status", "platform"), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("run_after", TS),
        sa.Column("locked_by", sa.String(120)),
        sa.Column("locked_at", TS),
        sa.Column("heartbeat_at", TS),
        sa.Column("last_error", sa.Text),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column("finished_at", TS),
        sa.Column("extra", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        schema="platform",
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_after"], schema="platform")
    op.create_index("ix_jobs_tenant_created", "jobs", ["tenant_id", "created_at"], schema="platform")
    op.execute(
        "CREATE UNIQUE INDEX uq_jobs_active_dedupe ON platform.jobs (dedupe_key) "
        "WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON platform.jobs TO {RUNTIME_ROLE}")

    # --- RLS and grants --------------------------------------------------------------------------------------
    for table in ("documents", "document_versions", "document_chunks", "document_acl", "index_generations"):
        apply_tenant_rls("app", table)
    apply_tenant_rls("app", "document_review_events", grants="SELECT, INSERT")


def downgrade() -> None:
    op.drop_table("jobs", schema="platform")
    for table in (
        "index_generations",
        "document_review_events",
        "document_acl",
        "document_chunks",
    ):
        op.drop_table(table, schema="app")
    op.drop_constraint("fk_documents_active_version", "documents", schema="app", type_="foreignkey")
    op.drop_table("document_versions", schema="app")
    op.drop_table("documents", schema="app")
    op.execute("DROP TYPE platform.job_status")
    for enum in (
        "generation_status",
        "review_action",
        "acl_principal_type",
        "chunk_type",
        "risk_tier",
        "document_version_status",
        "document_category",
    ):
        op.execute(f"DROP TYPE app.{enum}")
    op.execute("DROP FUNCTION IF EXISTS app.f_unaccent(text)")
