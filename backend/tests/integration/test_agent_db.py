"""M6 against PostgreSQL: conversation tables under RLS, the checkpointer role boundary, and a conversation that
continues after the checkpointer pool is replaced (restart test, doc 12 M6 exit)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.agent.checkpoint import open_saver
from leave_assistant.agent.graph import AgentDeps
from leave_assistant.agent.runner import LangGraphAgent
from leave_assistant.application.container import Services
from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.router import ProviderRouter
from leave_assistant.application.retrieval.context import EvidencePolicy
from leave_assistant.config import Settings
from leave_assistant.domain.tenancy import Employee
from leave_assistant.infrastructure.llm.fake import FakeChatModel
from tests.conftest import principal_for
from tests.fakes.documents import WordTokenizer
from tests.integration.conftest import HOST, PORT, TEST_DB

pytestmark = pytest.mark.integration
CHECKPOINT_URL = f"postgresql+psycopg://langgraph_rw:{os.environ.get('LANGGRAPH_DB_PASSWORD', 'langgraph-dev')}@{HOST}:{PORT}/{TEST_DB}"


@pytest.fixture
async def checkpoint_settings(migrated_database: str) -> AsyncIterator[Settings]:
    settings = Settings(langgraph_database_url=CHECKPOINT_URL)
    saver, pool = await open_saver(settings)
    await saver.setup()
    await pool.close()
    yield settings


async def agent_for(services: Services, settings: Settings) -> tuple[LangGraphAgent, object]:
    saver, pool = await open_saver(settings)
    router = ProviderRouter([FakeChatModel("fake")], WordTokenizer(), health=HealthTracker())
    deps = AgentDeps(
        router=router,
        retrieval=services.retrieval,
        security=services.security,
        language=services.adapters.language,
        counter=WordTokenizer(),
        evidence=EvidencePolicy(tau=0.3),
    )
    agent = LangGraphAgent(deps, saver)
    services.chat.agent = agent
    return agent, pool


async def test_conversation_survives_a_checkpointer_restart(
    db_services: Services, seeded: dict[str, object], checkpoint_settings: Settings, owner_engine: AsyncEngine
) -> None:
    employees: dict[str, Employee] = seeded["employees"]["aegean"]  # type: ignore[index]
    principal = principal_for(employees["AEG-004"])
    _, pool = await agent_for(db_services, checkpoint_settings)
    conversation = await db_services.chat.create(principal)
    events = [
        e async for e in await db_services.chat.send(principal, conversation.id, "Hello!", request_id="r1")
    ]
    assert events[-1].name == "done"
    await pool.close()  # "api restart": a new pool and saver, same database
    agent, pool = await agent_for(db_services, checkpoint_settings)
    events = [
        e
        async for e in await db_services.chat.send(principal, conversation.id, "Ευχαριστώ!", request_id="r2")
    ]
    assert events[-1].name == "done" and events[0].data["turn"] == 2
    state = await agent.graph.aget_state({"configurable": {"thread_id": str(conversation.id)}})
    assert state.values["turn"] == 2 and [m["role"] for m in state.values["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    history = await db_services.chat.messages(principal, conversation.id)
    assert [m.message.role.value for m in history] == ["user", "assistant", "user", "assistant"]
    # retention removes the app rows and the checkpoints
    await db_services.chat.archive(principal, conversation.id)
    assert await db_services.chat.cleanup_expired() == {"conversations": 1}
    assert not (await agent.graph.aget_state({"configurable": {"thread_id": str(conversation.id)}})).values
    await pool.close()
    async with owner_engine.connect() as conn:
        tables = {
            r[0]
            for r in (
                await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'langgraph'"))
            ).all()
        }
    assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= tables


async def test_checkpointer_role_cannot_read_application_tables(checkpoint_settings: Settings) -> None:
    url = CHECKPOINT_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM app.conversations")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM app.pii_vault")
