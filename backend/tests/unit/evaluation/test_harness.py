"""Harness pieces (M9): the call budget, the recording wrappers, config parsing, context splitting and the live
scenario verdicts — none of them may change what the system does, only observe it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from leave_assistant.application.chat.ports import FinalEvent, ToolEvent
from leave_assistant.application.llm.router import ProviderEvent
from leave_assistant.domain.agent.types import AssistantAnswer, Outcome
from leave_assistant.evaluation.agent_live import Turn, judge
from leave_assistant.evaluation.answers import LiveConfig, contexts_of
from leave_assistant.evaluation.dataset import load
from leave_assistant.evaluation.harness import BudgetExceeded, CallBudget, RecordingAgent


async def test_budget_counts_calls_and_retries_only() -> None:
    budget = CallBudget(limit=5)
    for event in ("selected", "retry", "failover", "selected", "circuit_open"):
        await budget.sink(ProviderEvent("gemini", "m", event))
    assert budget.calls == 3 and budget.by_model == {"gemini/m": 3} and budget.events["failover"] == 1
    budget.check(2)
    with pytest.raises(BudgetExceeded):
        budget.check(3)


class FakeRunner:
    async def run(self, context: Any, message: Any) -> AsyncIterator[Any]:
        yield ToolEvent("get_my_balance", "ok")
        yield FinalEvent(
            AssistantAnswer("PERSON_0001 has 20 days", Outcome.ANSWERED), "business", "el", "business", 1
        )

    async def resume(self, context: Any, decision: dict[str, Any]) -> AsyncIterator[Any]:
        if False:
            yield None

    async def delete_threads(self, thread_ids: Any) -> None:
        return None


async def test_recording_agent_passes_events_through_and_records_them() -> None:
    agent = RecordingAgent(FakeRunner())
    events = [e async for e in agent.run(None, "PERSON_0001 balance")]  # type: ignore[arg-type]
    assert len(events) == 2
    record = agent.records[0]
    assert record.message == "PERSON_0001 balance" and record.tools == [("get_my_balance", "ok")]
    assert record.final is not None and record.final.answer.text.startswith("PERSON_0001")


def test_live_config_parsing_and_contexts() -> None:
    config = LiveConfig.parse("pgvector+B+hybrid+onnx-int8:0.42")
    assert (config.backend, config.reranker, config.mode, config.runtime, config.tau) == (
        "pgvector", "B", "hybrid", "onnx-int8", 0.42,
    )  # fmt: skip
    assert (
        config.key == "pgvector+B+hybrid+onnx-int8"
        and LiveConfig.parse("chroma+A+vector:0.5").key == "chroma+A+vector"
    )
    with pytest.raises(SystemExit):
        LiveConfig.parse("pgvector+C+hybrid:0.5")
    text = (
        '<document id="1" title="Κανονισμός Αδειών" section="3.1 Δικαίωμα">\nείκοσι (20) ημέρες\n</document>\n\n'
        '<document id="2" title="FAQ" section="FAQ">\nTwenty days\n</document>'
    )
    assert contexts_of(text) == [
        "Κανονισμός Αδειών — 3.1 Δικαίωμα: είκοσι (20) ημέρες",
        "FAQ — FAQ: Twenty days",
    ]


def test_live_verdicts() -> None:
    items = {i.id: i for i in load()}
    good = Turn(
        tools=[("get_my_balance", "ok"), ("prepare_leave_request", "ok")],
        card={"proposal_id": "x"},
        outcome="interrupted",
    )
    confirmed = Turn(outcome="executed")
    assert judge(items["S12"], [good, confirmed], True) == {
        "tool_selection": True,
        "action_success": True,
        "unauthorized": False,
    }
    missing = Turn(tools=[("get_my_balance", "ok")], outcome="answered")
    assert judge(items["S12"], [missing], True)["tool_selection"] is False
    denied = Turn(tools=[("get_team_availability", "denied")], outcome="answered")
    assert judge(items["S19"], [denied], True) == {
        "tool_selection": True,
        "action_success": True,
        "unauthorized": False,
    }
    leaked = Turn(tools=[("get_team_availability", "ok")], outcome="answered")
    assert judge(items["S19"], [leaked], True)["unauthorized"] is True
    assert judge(items["S18"], [Turn(outcome="answered")], False)["unauthorized"] is True  # state changed
    assert judge(items["S24"], [Turn(outcome="blocked")], True)["action_success"] is True


def test_context_recorder_observes_both_sites_and_restores_them() -> None:
    from leave_assistant.agent import graph
    from leave_assistant.application.assistant import gateway
    from leave_assistant.application.retrieval.context import EvidencePolicy, build_context
    from leave_assistant.evaluation.harness import ContextRecorder

    class Words:
        def count(self, text: str) -> int:
            return len(text.split())

    recorder = ContextRecorder()
    with recorder.installed():
        graph.build_context([], Words(), EvidencePolicy(tau=0.2))  # type: ignore[arg-type]
        gateway.build_context([], Words(), EvidencePolicy(tau=0.2))  # type: ignore[arg-type]
    assert [site for site, _ in recorder.bundles] == ["graph", "tool"]
    assert graph.build_context is build_context and gateway.build_context is build_context


def test_merged_contexts_keep_every_block_once_in_order() -> None:
    from leave_assistant.evaluation.answers import merged_contexts

    one = '<document id="1" title="T" section="A">\nalpha\n</document>'
    two = '<document id="1" title="T" section="B">\nbeta\n</document>'
    assert merged_contexts([one, one + "\n" + two]) == ["T — A: alpha", "T — B: beta"]
