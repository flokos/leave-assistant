"""Entry point: `python -m leave_assistant.worker [--drain] [--once]` (separate worker process, ADR-0001)."""

from __future__ import annotations

import argparse
import asyncio
import signal
import socket
import sys
import time

from leave_assistant.application.jobs.models import JobStatus
from leave_assistant.config import get_settings
from leave_assistant.infrastructure.observability.logging import configure_logging
from leave_assistant.infrastructure.runtime import build_runtime
from leave_assistant.worker.handlers import build_registry
from leave_assistant.worker.loop import run_worker_loop
from leave_assistant.worker.runner import JobRunner


async def _main(drain: bool, once: bool, wait_seconds: float) -> int:
    settings = get_settings()
    configure_logging(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    runtime = build_runtime(settings, role="worker")
    try:
        runner = JobRunner(
            runtime.services.jobs,
            build_registry(runtime.services),
            worker_id=f"{socket.gethostname()}:{time.time_ns() % 1_000_000}",
            timeout_seconds=settings.document_process_timeout_seconds,
            heartbeat_seconds=settings.job_heartbeat_seconds,
        )
        if drain:
            # keep draining until nothing is queued or running (retries with backoff included), or until `wait_seconds`
            deadline = time.monotonic() + wait_seconds
            while not stop.is_set():
                await run_worker_loop(settings, stop, periodic=None, runner=runner, drain=True)
                if await runtime.services.jobs.active_count() == 0 or time.monotonic() > deadline:
                    break
                await asyncio.sleep(min(settings.worker_poll_interval_seconds, 5.0))
            counts = await runtime.services.jobs.counts()
            sys.stdout.write(
                f"drained: processed={runner.stats.processed} succeeded={runner.stats.succeeded} "
                f"failed={runner.stats.failed} counts={counts}\n"
            )
            return (
                0
                if counts.get(JobStatus.QUEUED.value, 0) == 0 and counts.get(JobStatus.RUNNING.value, 0) == 0
                else 2
            )
        await run_worker_loop(
            settings,
            stop,
            max_iterations=1 if once else None,
            periodic=runtime.services.rollover.run_due,
            runner=runner,
        )
        return 0
    finally:
        await runtime.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Leave Assistant worker")
    parser.add_argument("--drain", action="store_true", help="process every claimable job, then exit")
    parser.add_argument("--once", action="store_true", help="run a single loop iteration, then exit")
    parser.add_argument(
        "--wait", type=float, default=1800.0, help="max seconds to wait for retries when draining"
    )
    args = parser.parse_args()
    return asyncio.run(_main(args.drain, args.once, args.wait))


if __name__ == "__main__":
    raise SystemExit(main())
