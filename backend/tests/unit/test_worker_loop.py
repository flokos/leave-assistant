from __future__ import annotations

import asyncio
from pathlib import Path

from leave_assistant.config import Settings
from leave_assistant.worker.loop import run_worker_loop


async def test_loop_touches_heartbeat_and_stops_after_max_iterations(tmp_path: Path) -> None:
    heartbeat = tmp_path / "hb"
    settings = Settings(
        _env_file=None, worker_poll_interval_seconds=0.001, worker_heartbeat_path=str(heartbeat)
    )
    iterations = await run_worker_loop(settings, asyncio.Event(), max_iterations=3)
    assert iterations == 3
    assert heartbeat.exists()


async def test_stop_event_ends_the_loop(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, worker_poll_interval_seconds=5, worker_heartbeat_path=str(tmp_path / "hb")
    )
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker_loop(settings, stop))
    await asyncio.sleep(0.01)
    stop.set()
    assert await asyncio.wait_for(task, timeout=1) == 1
