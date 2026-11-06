"""Checkpointer (ADR-0008): `AsyncPostgresSaver` on its own pool as `langgraph_rw` with `search_path=langgraph`.
`python -m leave_assistant.agent.checkpoint` runs `setup()` once (part of `make migrate`)."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from leave_assistant.config import Settings, get_settings


def conninfo(settings: Settings) -> str:
    url = settings.langgraph_database_url
    if not url:
        raise RuntimeError("LANGGRAPH_DATABASE_URL is not configured")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


async def open_saver(settings: Settings) -> tuple[Any, Any]:
    """Returns `(saver, pool)`; the caller closes the pool."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo(settings),
        min_size=1,
        max_size=4,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=langgraph",
        },
    )
    await pool.open()
    return AsyncPostgresSaver(pool), pool  # type: ignore[arg-type]


async def _setup() -> int:
    saver, pool = await open_saver(get_settings())
    try:
        await saver.setup()
        sys.stdout.write("langgraph checkpoint tables ready (schema langgraph)\n")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_setup()))
