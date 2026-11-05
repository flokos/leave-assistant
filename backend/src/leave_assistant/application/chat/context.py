"""RunContext (doc 04 §1): trusted, immutable, built by the API layer from the verified principal and passed to
LangGraph as `context` — never part of the checkpointed state. `metrics` is a per-run collector (not state)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from leave_assistant.application.llm.router import CallRecord
from leave_assistant.domain.agent.budget import TokenBudgets
from leave_assistant.domain.tenancy import Principal


@dataclass(slots=True)
class RunMetrics:
    calls: list[CallRecord] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def record(self) -> CallRecord:
        record = CallRecord()
        self.calls.append(record)
        return record

    @property
    def provider(self) -> str | None:
        return next((c.provider for c in reversed(self.calls) if c.provider), None)

    @property
    def model(self) -> str | None:
        return next((c.model for c in reversed(self.calls) if c.model), None)

    @property
    def failovers(self) -> int:
        return sum(c.failovers for c in self.calls)

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def llm_latency_ms(self) -> int:
        return sum(c.latency_ms for c in self.calls)


@dataclass(frozen=True, slots=True)
class RunContext:
    principal: Principal
    tenant_id: UUID
    employee_id: UUID
    thread_id: UUID
    locale: str
    request_id: str | None
    block_threshold: float
    budgets: TokenBudgets
    metrics: RunMetrics = field(default_factory=RunMetrics)
