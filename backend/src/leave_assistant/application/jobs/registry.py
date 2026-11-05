"""Handler registry: job type → coroutine; `isolated` handlers run in a child process (ML work, M4 decision 3)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from leave_assistant.application.jobs.models import Job

Handler = Callable[[Job], Awaitable[object]]
FailureHook = Callable[[Job, str, bool], Awaitable[None]]  # (job, error, final)


@dataclass(frozen=True, slots=True)
class JobHandler:
    type: str
    run: Handler
    isolated: bool = False
    on_failure: FailureHook | None = None


class JobRegistry:
    def __init__(self, handlers: list[JobHandler] | None = None) -> None:
        self._handlers: dict[str, JobHandler] = {}
        for handler in handlers or []:
            self.register(handler)

    def register(self, handler: JobHandler) -> None:
        self._handlers[handler.type] = handler

    def get(self, type: str) -> JobHandler:
        try:
            return self._handlers[type]
        except KeyError:
            raise KeyError(f"no handler registered for job type {type!r}") from None

    def types(self) -> list[str]:
        return sorted(self._handlers)
