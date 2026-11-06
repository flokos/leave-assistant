"""An agent on the in-memory stack: fake providers, in-memory checkpointer, real graph."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from leave_assistant.agent.graph import AgentDeps
from leave_assistant.agent.runner import LangGraphAgent
from leave_assistant.application.chat.service import ChatEvent
from leave_assistant.application.container import Services
from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.router import ProviderRouter
from leave_assistant.application.retrieval.context import EvidencePolicy
from leave_assistant.domain.agent.budget import TokenBudgets
from leave_assistant.domain.tenancy import Principal
from leave_assistant.infrastructure.llm.fake import FakeChatModel
from tests.conftest import principal_for
from tests.fakes.documents import WordTokenizer
from tests.fakes.world import World
from tests.unit.documents.conftest import ENGLISH_HANDBOOK_MD, GREEK_POLICY_MD, create_markdown


async def no_sleep(_: float) -> None:
    return None


@dataclass
class AgentWorld:
    services: Services
    primary: FakeChatModel
    secondary: FakeChatModel
    saver: InMemorySaver
    agent: LangGraphAgent


def build_agent(services: Services, *, budgets: TokenBudgets | None = None, tau: float = 0.3) -> AgentWorld:
    primary, secondary = FakeChatModel("gemini"), FakeChatModel("groq")
    budgets = budgets or TokenBudgets()
    router = ProviderRouter(
        [primary, secondary], WordTokenizer(), health=HealthTracker(), budgets=budgets, sleep=no_sleep
    )
    saver = InMemorySaver()
    deps = AgentDeps(
        router=router,
        retrieval=services.retrieval,
        security=services.security,
        language=services.adapters.language,
        counter=WordTokenizer(),
        evidence=EvidencePolicy(tau=tau, min_evidence_chunks=1, budget_tokens=budgets.context),
    )
    agent = LangGraphAgent(deps, saver)
    services.chat.agent = agent
    services.chat.budgets = budgets
    return AgentWorld(services, primary, secondary, saver, agent)


@pytest.fixture
async def agent_world(services: Services, world: World, tmp_path: Path) -> AgentWorld:
    """Tenant A has the Greek regulation and the English handbook indexed and active."""
    hr = principal_for(world.a.employees["hr_admin"])
    for title, content, name in (
        ("Κανονισμός Αδειών", GREEK_POLICY_MD, "kanonismos.md"),
        ("Employee Leave Handbook", ENGLISH_HANDBOOK_MD, "handbook.md"),
    ):
        created = await create_markdown(services, hr, tmp_path, title=title, content=content, name=name)
        assert created.latest_version is not None
        await services.ingestion.process(created.document.tenant_id, created.latest_version.id)
    return build_agent(services)


async def ask(services: Services, principal: Principal, conversation_id, text: str) -> list[ChatEvent]:  # type: ignore[no-untyped-def]
    stream = await services.chat.send(principal, conversation_id, text, request_id="req-test")
    return [event async for event in stream]


def answer_text(events: list[ChatEvent]) -> str:
    return "".join(e.data["text"] for e in events if e.name == "token")
