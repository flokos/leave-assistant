"""Entry point: `python -m leave_assistant.worker` (separate worker process, ADR-0001)."""

from __future__ import annotations

import asyncio
import signal

from leave_assistant.config import get_settings
from leave_assistant.infrastructure.observability.logging import configure_logging
from leave_assistant.worker.loop import run_worker_loop


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_worker_loop(settings, stop)


if __name__ == "__main__":
    asyncio.run(_main())
