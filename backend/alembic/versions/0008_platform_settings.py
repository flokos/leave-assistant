"""M10 platform configuration: `platform.settings`, one row with what a new company gets when its creation leaves a
value out (default locale, timezone, the per-tenant settings of doc 07 §4.1). Platform administrators edit it through
`GET/PUT /platform/settings` (doc 09 §2.9).

Revision ID: 0008_platform_settings
Revises: 0007_integrations
Create Date: 2026-11-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from leave_assistant.infrastructure.db.rls import RUNTIME_ROLE

revision = "0008_platform_settings"
down_revision = "0007_integrations"
branch_labels = None
depends_on = None

DEFAULT_TENANT_SETTINGS = (
    '{"work_week": [0, 1, 2, 3, 4], "max_upload_mb": 10, "conversation_retention_days": 90, '
    '"proposal_ttl_minutes": 15, "allow_hr_fallback": true}'
)


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("default_locale", sa.String(2), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("tenant_settings", pg.JSONB, nullable=False),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_platform_settings_single_row"),
        sa.CheckConstraint("default_locale IN ('el', 'en')", name="ck_platform_settings_locale"),
        schema="platform",
    )
    op.execute(
        sa.text(
            "INSERT INTO platform.settings (id, default_locale, timezone, tenant_settings, updated_by, updated_at) "
            "VALUES (1, 'en', 'Europe/Athens', CAST(:settings AS jsonb), 'migration', now())"
        ).bindparams(settings=DEFAULT_TENANT_SETTINGS)
    )
    op.execute(f"GRANT SELECT, UPDATE ON platform.settings TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.drop_table("settings", schema="platform")
