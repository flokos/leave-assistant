from __future__ import annotations

import asyncio
from pathlib import Path

from leave_assistant.config import Settings
from leave_assistant.infrastructure.observability.logging import get_logger

log = get_logger("worker")


def touch_heartbeat(path: str) -> None:
    Path(path).touch()


async def run_worker_loop(
    settings: Settings, stop: asyncio.Event, *, max_iterations: int | None = None
) -> int:
    """Poll loop. Returns the number of completed iterations (used by tests and by embedded mode)."""
    iterations = 0
    log.info("worker.start", mode=settings.worker_mode, interval=settings.worker_poll_interval_seconds)
    while not stop.is_set():
        touch_heartbeat(settings.worker_heartbeat_path)
        # Job claiming (SELECT ... FOR UPDATE SKIP LOCKED) is implemented in M4.
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
        except TimeoutError:
            continue
    log.info("worker.stop", iterations=iterations)
    return iterations
