"""`python -m leave_assistant.retention.scan`: run the retention scan for every tenant now (`make retention-scan`)."""

from __future__ import annotations

import asyncio
import sys

from leave_assistant.config import get_settings
from leave_assistant.infrastructure.observability.logging import configure_logging
from leave_assistant.infrastructure.runtime import build_runtime


async def _main() -> int:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings, role="worker")
    try:
        reports = await runtime.services.retention.scan_all()
        for report in reports:
            sys.stdout.write(
                f"tenant={report.tenant_id} eligible={report.eligible} created={report.created}\n"
            )
        return 0
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
