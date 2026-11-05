from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID


class FixedClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 11, 5, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def tick(self, seconds: float = 1.0) -> None:
        self.current += timedelta(seconds=seconds)


class SequentialIds:
    """Deterministic, ascending UUIDs (keyset pagination tests rely on the order)."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def new_id(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value
