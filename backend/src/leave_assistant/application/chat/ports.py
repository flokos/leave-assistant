"""The agent as seen by the application: the LangGraph implementation lives in `leave_assistant.agent` and is wired
by the runtime (the application layer must not import LangGraph — import-linter contract)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from leave_assistant.application.chat.context import RunContext
from leave_assistant.domain.agent.types import AssistantAnswer
from leave_assistant.domain.privacy.types import LlmSafeText


@dataclass(frozen=True, slots=True)
class StatusEvent:
    stage: str  # classifying | retrieving | generating | verifying | summarising


@dataclass(frozen=True, slots=True)
class FinalEvent:
    answer: AssistantAnswer
    intent: str | None
    language: str | None
    route: str | None
    turn: int
    source_version_ids: tuple[str, ...] = ()


AgentEvent = StatusEvent | FinalEvent


class AgentRunner(Protocol):
    def run(self, context: RunContext, message: LlmSafeText) -> AsyncIterator[AgentEvent]: ...
    async def delete_threads(self, thread_ids: Sequence[UUID]) -> None: ...
