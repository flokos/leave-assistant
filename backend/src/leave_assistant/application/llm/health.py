"""Per provider+model circuit breaker (doc 04 §9): open after repeated transient failures or a fatal error,
half-open probe after `open_seconds`, closed again on success."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _Circuit:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    fatal: bool = False


@dataclass
class HealthTracker:
    open_seconds: float = 60.0
    fatal_open_seconds: float = 600.0
    failure_threshold: int = 3
    now: Callable[[], float] = time.monotonic
    _circuits: dict[tuple[str, str], _Circuit] = field(default_factory=dict)

    def _get(self, provider: str, model: str) -> _Circuit:
        return self._circuits.setdefault((provider, model), _Circuit())

    def available(self, provider: str, model: str) -> bool:
        circuit = self._get(provider, model)
        if circuit.state is CircuitState.CLOSED:
            return True
        limit = self.fatal_open_seconds if circuit.fatal else self.open_seconds
        if circuit.state is CircuitState.OPEN and self.now() - circuit.opened_at >= limit:
            circuit.state = CircuitState.HALF_OPEN  # one probe call is allowed
            return True
        return circuit.state is CircuitState.HALF_OPEN

    def success(self, provider: str, model: str) -> CircuitState:
        circuit = self._get(provider, model)
        previous = circuit.state
        circuit.state, circuit.failures, circuit.fatal = CircuitState.CLOSED, 0, False
        return previous

    def failure(self, provider: str, model: str, *, fatal: bool = False) -> CircuitState:
        circuit = self._get(provider, model)
        circuit.failures += 1
        if fatal or circuit.state is CircuitState.HALF_OPEN or circuit.failures >= self.failure_threshold:
            circuit.state, circuit.opened_at, circuit.fatal = CircuitState.OPEN, self.now(), fatal
        return circuit.state

    def snapshot(self) -> dict[str, str]:
        return {f"{p}/{m}": c.state.value for (p, m), c in self._circuits.items()}
