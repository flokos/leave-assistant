"""PeriodicTasks (M5): each task keeps its own interval; the composite runs only what is due."""

from __future__ import annotations

import time

from leave_assistant.worker.periodic import PeriodicTask, PeriodicTasks


async def test_tasks_run_when_due_only() -> None:
    calls: list[str] = []

    async def rollover() -> str:
        calls.append("rollover")
        return "ok"

    async def retention() -> str:
        calls.append("retention")
        return "scanned"

    tasks = PeriodicTasks(
        [PeriodicTask("rollover", rollover, 0.0), PeriodicTask("retention.scan", retention, 3600.0)]
    )
    assert tasks.interval == 0.0
    first = await tasks()
    assert first == {"rollover": "ok", "retention.scan": "scanned"}
    second = await tasks()
    assert second == {"rollover": "ok"} and calls == ["rollover", "retention", "rollover"]
    tasks._tasks[1].last_run = time.monotonic() - 3601  # the retention interval elapsed
    third = await tasks()
    assert "retention.scan" in third


def test_the_worker_schedules_retention_cleanup_and_proposal_expiry(services) -> None:  # type: ignore[no-untyped-def]
    from leave_assistant.config import Settings
    from leave_assistant.worker.periodic import build_periodic

    periodic = build_periodic(services, Settings())
    assert [t.name for t in periodic._tasks] == [
        "rollover",
        "retention.scan",
        "conversation.cleanup",
        "proposal.expire",
    ]
