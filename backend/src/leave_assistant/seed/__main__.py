"""`python -m leave_assistant.seed` — seed both companies into Keycloak and the database (idempotent)."""

from __future__ import annotations

import asyncio
import json
import sys

from leave_assistant.config import get_settings
from leave_assistant.infrastructure.observability.logging import configure_logging, get_logger
from leave_assistant.infrastructure.runtime import build_runtime
from leave_assistant.seed.run import run_seed


async def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("seed")
    runtime = build_runtime(settings)
    try:
        report = await run_seed(runtime.services, settings.seed_user_password.get_secret_value())
    finally:
        await runtime.aclose()
    log.info(
        "seed.done", created=len(report.created), updated=len(report.updated), unchanged=len(report.unchanged)
    )
    sys.stdout.write(
        json.dumps(
            {"created": report.created, "updated": report.updated, "unchanged": report.unchanged}, indent=2
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
