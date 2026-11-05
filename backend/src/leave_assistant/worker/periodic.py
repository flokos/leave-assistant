"""Periodic maintenance run by the worker loop: leave rollover (M2) and the retention scan (M5), each on its own
interval. The loop calls the composite every `interval` seconds; tasks that are not due yet are skipped."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from leave_assistant.application.container import Services
from leave_assistant.config import Settings

Task = Callable[[], Awaitable[object]]


@dataclass(slots=True)
class PeriodicTask:
    name: str
    run: Task
    interval_seconds: float
    last_run: float | None = field(default=None)


class PeriodicTasks:
    def __init__(self, tasks: list[PeriodicTask]) -> None:
        self._tasks = tasks

    @property
    def interval(self) -> float:
        return min((t.interval_seconds for t in self._tasks), default=60.0)

    async def __call__(self) -> dict[str, str]:
        results: dict[str, str] = {}
        for task in self._tasks:
            now = time.monotonic()
            if task.last_run is not None and now - task.last_run < task.interval_seconds:
                continue
            task.last_run = now
            results[task.name] = str(await task.run())[:300]
        return results


def build_periodic(services: Services, settings: Settings) -> PeriodicTasks:
    return PeriodicTasks(
        [
            PeriodicTask("rollover", services.rollover.run_due, settings.rollover_interval_seconds),
            PeriodicTask(
                "retention.scan", services.retention.scan_all, settings.retention_scan_interval_seconds
            ),
        ]
    )
