"""ProviderRouter (ADR-0010, doc 04 §9): budgets, retries with jitter, failover on transient errors only, one
same-provider repair on invalid output, no retry on content blocks, circuit breaker per provider+model."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.ports import (
    ChatModel,
    ModelRole,
    Prompt,
    PromptMessage,
    TokenCounter,
    ToolSpec,
    ToolStep,
    Usage,
    prompt_text,
    tool_schema_text,
)
from leave_assistant.domain.agent.budget import TokenBudgets, estimate
from leave_assistant.domain.agent.types import (
    AllProvidersFailed,
    BudgetExceeded,
    ProviderError,
    ProviderErrorKind,
)
from leave_assistant.domain.privacy.types import LlmSafeText

T = TypeVar("T", bound=BaseModel)
REPAIR = LlmSafeText(
    "Your previous reply did not match the required JSON schema. Reply again with valid JSON only, "
    "following the schema exactly."
)
REPAIR_TOOLS = LlmSafeText(
    "Your previous reply was not a valid tool call. Call one of the available tools with valid arguments, "
    "or answer in plain text."
)
Invoke = Callable[[ChatModel, Prompt, float], Awaitable[tuple[Any, Usage]]]


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    provider: str
    model: str
    event: str  # selected | retry | failover | repair | circuit_open | circuit_close | error
    reason: str = ""
    latency_ms: int | None = None


@dataclass(slots=True)
class CallRecord:
    """What happened during one routed call — attached to `agent_runs` and traces."""

    provider: str | None = None
    model: str | None = None
    attempts: int = 0
    failovers: int = 0
    repairs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    events: list[ProviderEvent] = field(default_factory=list)


EventSink = Callable[[ProviderEvent], Awaitable[None]]


async def _ignore(_: ProviderEvent) -> None:
    return None


class ProviderRouter:
    def __init__(
        self,
        providers: Sequence[ChatModel],
        counter: TokenCounter,
        *,
        health: HealthTracker | None = None,
        budgets: TokenBudgets | None = None,
        timeout_seconds: float = 25.0,
        max_retries: int = 2,
        max_backoff_seconds: float = 4.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        sink: EventSink = _ignore,
    ) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = list(providers)
        self._counter = counter
        self.health = health or HealthTracker()
        self.budgets = budgets or TokenBudgets()
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._max_backoff = max_backoff_seconds
        self._sleep = sleep
        self._sink = sink

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    def count(self, prompt: Prompt) -> int:
        return estimate(self._counter.count(prompt_text(prompt.messages)))

    def count_tools(self, tools: Sequence[ToolSpec]) -> int:
        return estimate(self._counter.count(tool_schema_text(tools))) if tools else 0

    def check_budget(self, prompt: Prompt, extra_tokens: int = 0) -> int:
        tokens = self.count(prompt) + extra_tokens
        if tokens > self.budgets.total:
            raise BudgetExceeded(f"{prompt.id}: {tokens} input tokens > {self.budgets.total}")
        return tokens

    async def _event(self, record: CallRecord, event: ProviderEvent) -> None:
        record.events.append(event)
        await self._sink(event)

    def _backoff(self, attempt: int) -> float:
        return float(min(self._max_backoff, 0.5 * 2**attempt)) * (0.5 + random.random() / 2)  # noqa: S311 - jitter only

    async def structured(
        self, prompt: Prompt, schema: type[T], *, role: ModelRole, record: CallRecord | None = None
    ) -> T:
        async def invoke(provider: ChatModel, current: Prompt, deadline: float) -> tuple[Any, Usage]:
            return await provider.structured(current, schema, role=role, deadline_seconds=deadline)

        value: T = await self._route(prompt, role, record, invoke, repair=REPAIR)
        return value

    async def tool_step(
        self,
        prompt: Prompt,
        tools: Sequence[ToolSpec],
        *,
        role: ModelRole,
        record: CallRecord | None = None,
    ) -> ToolStep:
        """One planning step with `tools` bound natively (M7). Same budget, retry, failover and repair rules."""
        tools = tuple(tools)

        async def invoke(provider: ChatModel, current: Prompt, deadline: float) -> tuple[Any, Usage]:
            return await provider.tool_step(current, tools, role=role, deadline_seconds=deadline)

        step: ToolStep = await self._route(
            prompt, role, record, invoke, repair=REPAIR_TOOLS, extra_tokens=self.count_tools(tools)
        )
        return step

    async def _route(
        self,
        prompt: Prompt,
        role: ModelRole,
        record: CallRecord | None,
        invoke: Invoke,
        *,
        repair: LlmSafeText,
        extra_tokens: int = 0,
    ) -> Any:
        record = record if record is not None else CallRecord()
        tokens = self.check_budget(prompt, extra_tokens)
        errors: list[ProviderError] = []
        tried = 0
        for provider in self._providers:
            model = provider.model_for(role)
            if not self.health.available(provider.name, model):
                errors.append(
                    ProviderError(ProviderErrorKind.TRANSIENT, provider.name, model, "circuit open")
                )
                continue
            if tried:
                record.failovers += 1
                await self._event(
                    record, ProviderEvent(provider.name, model, "failover", errors[-1].kind.value)
                )
            tried += 1
            try:
                value, usage = await self._call_provider(
                    provider, model, prompt, invoke, record, repair=repair, extra_tokens=extra_tokens
                )
            except ProviderError as error:
                errors.append(error)
                if error.kind in (ProviderErrorKind.CONTENT_BLOCKED, ProviderErrorKind.INVALID_OUTPUT):
                    # a safety block is about the content; invalid output after one repair is surfaced (doc 04 §9)
                    await self._event(record, ProviderEvent(provider.name, model, "error", error.kind.value))
                    raise
                fatal = error.kind is ProviderErrorKind.FATAL
                state = self.health.failure(provider.name, model, fatal=fatal)
                await self._event(
                    record,
                    ProviderEvent(provider.name, model, "error", f"{error.kind.value}: {error.detail[:120]}"),
                )
                if state.value == "open":
                    await self._event(
                        record, ProviderEvent(provider.name, model, "circuit_open", error.kind.value)
                    )
                continue
            previous = self.health.success(provider.name, model)
            if previous.value != "closed":
                await self._event(record, ProviderEvent(provider.name, model, "circuit_close"))
            record.provider, record.model = provider.name, model
            record.input_tokens += usage.input_tokens or tokens
            record.output_tokens += usage.output_tokens
            return value
        raise AllProvidersFailed(errors)

    async def _call_provider(
        self,
        provider: ChatModel,
        model: str,
        prompt: Prompt,
        invoke: Invoke,
        record: CallRecord,
        *,
        repair: LlmSafeText,
        extra_tokens: int,
    ) -> tuple[Any, Usage]:
        """Retries transient errors on this provider; repairs invalid output once; raises the final error."""
        repaired = False
        current = prompt
        attempt = 0
        while True:
            record.attempts += 1
            started = time.monotonic()
            await self._event(record, ProviderEvent(provider.name, model, "selected", prompt.id))
            try:
                value, usage = await asyncio.wait_for(
                    invoke(provider, current, self._timeout), timeout=self._timeout
                )
                record.latency_ms += int((time.monotonic() - started) * 1000)
                return value, usage
            except TimeoutError:
                error = ProviderError(ProviderErrorKind.TRANSIENT, provider.name, model, "timeout")
            except ProviderError as exc:
                error = exc
            record.latency_ms += int((time.monotonic() - started) * 1000)
            if error.kind is ProviderErrorKind.INVALID_OUTPUT and not repaired:
                repaired = True
                record.repairs += 1
                await self._event(record, ProviderEvent(provider.name, model, "repair", error.detail[:120]))
                current = Prompt(
                    id=prompt.id,
                    messages=(*prompt.messages, PromptMessage("user", repair)),
                    max_output_tokens=prompt.max_output_tokens,
                )
                self.check_budget(current, extra_tokens)
                continue
            if error.kind is ProviderErrorKind.TRANSIENT and attempt < self._max_retries:
                attempt += 1
                await self._event(record, ProviderEvent(provider.name, model, "retry", error.detail[:120]))
                await self._sleep(self._backoff(attempt))
                continue
            raise error
