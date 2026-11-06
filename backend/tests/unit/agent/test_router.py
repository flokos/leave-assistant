"""ProviderRouter (doc 04 §9): retries, failover, repair, content blocks, circuits, budgets, events."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.ports import ModelRole, Prompt, PromptMessage
from leave_assistant.application.llm.router import CallRecord, ProviderEvent, ProviderRouter
from leave_assistant.domain.agent.budget import TokenBudgets
from leave_assistant.domain.agent.types import (
    AllProvidersFailed,
    BudgetExceeded,
    ProviderError,
    ProviderErrorKind,
)
from leave_assistant.domain.privacy.types import LlmSafeText
from leave_assistant.infrastructure.llm.fake import FakeChatModel
from tests.fakes.documents import WordTokenizer


class Out(BaseModel):
    query: str


def prompt(text: str = "how many days") -> Prompt:
    return Prompt(
        "condense@1",
        (
            PromptMessage("system", LlmSafeText("sys")),
            PromptMessage("user", LlmSafeText(f"<user>{text}</user>")),
        ),
    )


def transient(name: str = "gemini") -> ProviderError:
    return ProviderError(ProviderErrorKind.TRANSIENT, name, "m", "429")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def no_sleep(_: float) -> None:
    return None


def make(
    *providers: FakeChatModel, clock: Clock | None = None, events: list[ProviderEvent] | None = None, **kw
):  # type: ignore[no-untyped-def]
    async def sink(event: ProviderEvent) -> None:
        if events is not None:
            events.append(event)

    health = HealthTracker(now=clock or Clock(), open_seconds=60.0)
    return ProviderRouter(list(providers), WordTokenizer(), health=health, sleep=no_sleep, sink=sink, **kw)


async def test_primary_success_records_usage_and_selection() -> None:
    gemini = FakeChatModel("gemini")
    events: list[ProviderEvent] = []
    record = CallRecord()
    value = await make(gemini, events=events).structured(prompt(), Out, role=ModelRole.FAST, record=record)
    assert value.query == "how many days" and record.provider == "gemini" and record.model == "fake-fast"
    assert record.attempts == 1 and record.failovers == 0 and [e.event for e in events] == ["selected"]


async def test_transient_errors_retry_then_fail_over_and_open_the_circuit() -> None:
    gemini, groq = FakeChatModel("gemini"), FakeChatModel("groq")
    gemini.fail_next(transient(), transient(), transient())  # 1 call + 2 retries
    events: list[ProviderEvent] = []
    record = CallRecord()
    router = make(gemini, groq, events=events)
    value = await router.structured(prompt(), Out, role=ModelRole.FAST, record=record)
    assert value.query == "how many days" and record.provider == "groq" and record.failovers == 1
    assert [e.event for e in events].count("retry") == 2 and "failover" in [e.event for e in events]
    assert len(gemini.calls) == 3 and len(groq.calls) == 1
    # two more exhausted calls on gemini open its circuit; then it is skipped entirely
    for _ in range(2):
        gemini.fail_next(transient(), transient(), transient())
        await router.structured(prompt(), Out, role=ModelRole.FAST)
    assert router.health.snapshot()["gemini/fake-fast"] == "open"
    calls = len(gemini.calls)
    await router.structured(prompt(), Out, role=ModelRole.FAST)
    assert len(gemini.calls) == calls  # circuit open → not called


async def test_half_open_probe_closes_the_circuit_after_the_open_period() -> None:
    clock = Clock()
    gemini, groq = FakeChatModel("gemini"), FakeChatModel("groq")
    router = make(gemini, groq, clock=clock)
    gemini.fail_next(ProviderError(ProviderErrorKind.FATAL, "gemini", "m", "401"))
    record = CallRecord()
    await router.structured(prompt(), Out, role=ModelRole.FAST, record=record)
    assert record.provider == "groq" and len(gemini.calls) == 1  # fatal: no retry, immediate failover
    assert router.health.snapshot()["gemini/fake-fast"] == "open"
    clock.t += 61
    await router.structured(prompt(), Out, role=ModelRole.FAST)
    assert len(gemini.calls) == 1  # fatal errors keep the circuit open longer (10 min)
    clock.t += 600
    events: list[ProviderEvent] = []
    router2 = make(gemini, groq, clock=clock, events=events)
    router2.health = router.health
    record = CallRecord()
    await router2.structured(prompt(), Out, role=ModelRole.FAST, record=record)
    assert record.provider == "gemini" and "circuit_close" in [e.event for e in events]


async def test_invalid_output_is_repaired_once_on_the_same_provider_then_surfaced() -> None:
    gemini, groq = FakeChatModel("gemini"), FakeChatModel("groq")
    invalid = ProviderError(ProviderErrorKind.INVALID_OUTPUT, "gemini", "m", "bad json")
    gemini.fail_next(invalid)
    record = CallRecord()
    router = make(gemini, groq)
    await router.structured(prompt(), Out, role=ModelRole.FAST, record=record)
    assert record.provider == "gemini" and record.repairs == 1 and len(gemini.calls) == 2
    assert "did not match the required JSON schema" in gemini.calls[1][2]
    gemini.fail_next(invalid, invalid)
    with pytest.raises(ProviderError) as surfaced:
        await router.structured(prompt(), Out, role=ModelRole.FAST)
    assert surfaced.value.kind is ProviderErrorKind.INVALID_OUTPUT and len(groq.calls) == 0


async def test_content_blocks_are_not_retried_or_failed_over() -> None:
    gemini, groq = FakeChatModel("gemini"), FakeChatModel("groq")
    gemini.fail_next(ProviderError(ProviderErrorKind.CONTENT_BLOCKED, "gemini", "m", "SAFETY"))
    with pytest.raises(ProviderError) as blocked:
        await make(gemini, groq).structured(prompt(), Out, role=ModelRole.FAST)
    assert (
        blocked.value.kind is ProviderErrorKind.CONTENT_BLOCKED and len(gemini.calls) == 1 and not groq.calls
    )


async def test_all_providers_failing_raises_with_every_error() -> None:
    gemini, groq = FakeChatModel("gemini"), FakeChatModel("groq")
    gemini.fail_next(*[transient()] * 3)
    groq.fail_next(*[transient("groq")] * 3)
    with pytest.raises(AllProvidersFailed) as failed:
        await make(gemini, groq).structured(prompt(), Out, role=ModelRole.FAST)
    assert len(failed.value.errors) == 2


async def test_budget_is_asserted_before_any_provider_call() -> None:
    gemini = FakeChatModel("gemini")
    router = make(gemini, budgets=TokenBudgets(total=10))
    with pytest.raises(BudgetExceeded):
        await router.structured(prompt("word " * 20), Out, role=ModelRole.FAST)
    assert not gemini.calls


def test_prompt_messages_refuse_plain_strings() -> None:
    with pytest.raises(TypeError):
        PromptMessage("user", "raw text with a name")  # type: ignore[arg-type]
