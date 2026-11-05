from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from leave_assistant.config import Settings
from leave_assistant.infrastructure.observability.logging import get_logger
from leave_assistant.worker.runner import JobRunner

log = get_logger("worker")
PeriodicTask = Callable[[], Awaitable[object]]


def touch_heartbeat(path: str) -> None:
    Path(path).touch()


async def run_worker_loop(
    settings: Settings,
    stop: asyncio.Event,
    *,
    max_iterations: int | None = None,
    periodic: PeriodicTask | None = None,
    periodic_interval_seconds: float | None = None,
    runner: JobRunner | None = None,
    drain: bool = False,
) -> int:
    """Poll loop. Returns the number of completed iterations (used by tests and by embedded mode).

    `periodic` (M2: leave rollover) runs on the first iteration and then every `periodic_interval_seconds`;
    `runner` (M4) claims and runs up to `worker_max_jobs_per_iteration` jobs per iteration. With `drain=True`
    the loop ends as soon as an iteration finds nothing to run (`make ingest`). Errors are logged, never raised —
    the loop keeps the heartbeat alive.
    """
    iterations = 0
    interval = (
        periodic_interval_seconds
        if periodic_interval_seconds is not None
        else settings.rollover_interval_seconds
    )
    last_periodic: float | None = None
    log.info("worker.start", mode=settings.worker_mode, interval=settings.worker_poll_interval_seconds)
    while not stop.is_set():
        touch_heartbeat(settings.worker_heartbeat_path)
        if periodic is not None and (last_periodic is None or time.monotonic() - last_periodic >= interval):
            try:
                result = await periodic()
                log.info("worker.periodic.done", result=str(result)[:500])
            except Exception as exc:  # noqa: BLE001 - never stop the heartbeat
                log.error("worker.periodic.failed", error=f"{type(exc).__name__}: {exc}")
            last_periodic = time.monotonic()
        processed = 0
        if runner is not None:
            while processed < settings.worker_max_jobs_per_iteration and not stop.is_set():
                try:
                    if not await runner.run_next():
                        break
                except Exception as exc:  # noqa: BLE001 - a broken claim must not kill the loop
                    log.error("worker.job.error", error=f"{type(exc).__name__}: {exc}")
                    break
                processed += 1
                touch_heartbeat(settings.worker_heartbeat_path)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        if drain and processed == 0:
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
        except TimeoutError:
            continue
    log.info("worker.stop", iterations=iterations)
    return iterations
