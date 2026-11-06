"""The agent as seen by the application: the LangGraph implementation lives in `leave_assistant.agent` and is wired
by the runtime (the application layer must not import LangGraph — import-linter contract)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from leave_assistant.application.chat.context import RunContext
from leave_assistant.domain.agent.types import AssistantAnswer
from leave_assistant.domain.privacy.types import LlmSafeText


@dataclass(frozen=True, slots=True)
class StatusEvent:
    stage: str  # classifying | retrieving | generating | verifying | summarising | planning | executing | …


@dataclass(frozen=True, slots=True)
class ToolEvent:
    name: str
    status: str  # started | ok | denied | invalid | not_found | error


@dataclass(frozen=True, slots=True)
class InterruptEvent:
    """The run paused on a confirmation card (doc 09 §3 `interrupt`); the proposal is a database row."""

    proposal_id: UUID
    kind: str
    text: str  # pseudonymised, deterministic (templates + tool facts)
    intent: str | None
    language: str | None
    turn: int
    placeholders: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FinalEvent:
    answer: AssistantAnswer
    intent: str | None
    language: str | None
    route: str | None
    turn: int
    source_version_ids: tuple[str, ...] = ()
    placeholders: dict[str, dict[str, Any]] = field(default_factory=dict)  # tool-result provenance (M7)
    proposal_id: UUID | None = None  # the proposal this answer concludes (resume runs)
    tool_steps: int = 0


AgentEvent = StatusEvent | ToolEvent | InterruptEvent | FinalEvent


class AgentRunner(Protocol):
    def run(self, context: RunContext, message: LlmSafeText) -> AsyncIterator[AgentEvent]: ...
    def resume(self, context: RunContext, decision: dict[str, Any]) -> AsyncIterator[AgentEvent]: ...
    async def delete_threads(self, thread_ids: Sequence[UUID]) -> None: ...
