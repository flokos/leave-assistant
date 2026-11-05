"""`python -m leave_assistant.leave.rollover` — run the leave rollover (statuses + ledger calendar) for all tenants now."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys

from leave_assistant.config import get_settings
from leave_assistant.infrastructure.observability.logging import configure_logging
from leave_assistant.infrastructure.runtime import build_runtime


async def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings)
    try:
        reports = await runtime.services.rollover.run_due()
    finally:
        await runtime.aclose()
    sys.stdout.write(json.dumps([dataclasses.asdict(r) for r in reports], indent=2, default=str) + "\n")
    return 1 if any(r.errors for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
