"""Programmatic access to the Alembic scripts: head revision for readiness, upgrade for tooling and tests."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

import leave_assistant

BACKEND_ROOT = Path(leave_assistant.__file__).resolve().parents[2]


def alembic_config(url: str | None = None) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    if url:
        config.attributes["url"] = url
    return config


def expected_head() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("no Alembic revisions found")
    return head


def upgrade_to_head(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


def downgrade_to_base(url: str) -> None:
    command.downgrade(alembic_config(url), "base")
