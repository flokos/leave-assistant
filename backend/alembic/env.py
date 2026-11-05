"""Alembic environment: runs as `app_owner` (DATABASE_OWNER_URL), sync psycopg driver (ADR-0003)."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from leave_assistant.infrastructure.db.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = (
        config.attributes.get("url")
        or context.get_x_argument(as_dictionary=True).get("url")
        or os.environ.get("DATABASE_OWNER_URL")
    )
    if not url:
        raise RuntimeError("set DATABASE_OWNER_URL or pass `-x url=postgresql+psycopg://…` to alembic")
    return str(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(), target_metadata=target_metadata, literal_binds=True, include_schemas=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
