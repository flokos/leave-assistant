"""M6/M7 against PostgreSQL: conversation tables under RLS, the checkpointer role boundary, a conversation that
continues after the checkpointer pool is replaced (restart test, doc 12 M6 exit), a confirmation that survives an
API restart and executes exactly once (S29), and the atomic proposal claim across connections."""

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
        tools=services.tools,
        proposals=services.proposals,
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


async def _drain(stream):  # type: ignore[no-untyped-def]
    return [e async for e in stream]


async def test_s29_a_pending_confirmation_survives_a_restart_and_executes_once(
    db_services: Services, seeded: dict[str, object], checkpoint_settings: Settings, owner_engine: AsyncEngine
) -> None:
    from uuid import UUID

    employees: dict[str, Employee] = seeded["employees"]["brightwater"]  # type: ignore[index]
    employee = employees["BWS-004"]
    principal = principal_for(employee)
    _, pool = await agent_for(db_services, checkpoint_settings)
    conversation = await db_services.chat.create(principal)
    events = await _drain(
        await db_services.chat.send(
            principal, conversation.id, "I want five days off starting December 14", request_id="r1"
        )
    )
    assert events[-1].name == "interrupt", [e.name for e in events]
    card = events[-1].data["card"]
    await pool.close()  # "api restart": the paused run lives only in PostgreSQL now

    agent, pool = await agent_for(db_services, checkpoint_settings)
    snapshot = await agent.graph.aget_state({"configurable": {"thread_id": str(conversation.id)}})
    assert snapshot.interrupts and snapshot.interrupts[0].value["proposal_id"] == card["proposal_id"]
    resume = dict(
        proposal_id=UUID(card["proposal_id"]),
        decision="confirm",
        payload_hash=card["payload_hash"],
        edits=None,
        request_id="r2",
    )
    done = await _drain(await db_services.chat.resume(principal, conversation.id, **resume))
    assert done[-1].name == "done" and done[-1].data["outcome"] == "executed"
    replay = await _drain(await db_services.chat.resume(principal, conversation.id, **resume))
    assert replay[-1].data["replayed"] is True
    await pool.close()
    async with owner_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(employee.tenant_id)}
        )
        requests = (
            await conn.execute(
                text(
                    "SELECT status, idempotency_key FROM app.leave_requests "
                    "WHERE employee_id = :e AND start_date = '2026-12-14'"
                ),
                {"e": employee.id},
            )
        ).all()
        proposal = (
            await conn.execute(
                text("SELECT status, executed_request_id FROM app.action_proposals WHERE id = :p"),
                {"p": card["proposal_id"]},
            )
        ).one()
    assert len(requests) == 1 and requests[0][0] == "PENDING_MANAGER"
    assert requests[0][1] == f"proposal:{card['proposal_id']}"
    assert proposal[0] == "executed" and proposal[1] is not None


async def test_the_claim_is_atomic_across_connections(
    db_services: Services, seeded: dict[str, object], checkpoint_settings: Settings
) -> None:
    import asyncio
    from uuid import UUID

    from leave_assistant.application.assistant.gateway import ToolContext
    from leave_assistant.application.llm.ports import ToolCall

    employees: dict[str, Employee] = seeded["employees"]["aegean"]  # type: ignore[index]
    principal = principal_for(employees["AEG-006"])
    conversation = await db_services.chat.create(principal)
    context = ToolContext(principal, conversation.id, UUID(int=1), db_services.clock.now().date(), "el")
    outcome = await db_services.tools.call(
        context,
        ToolCall(
            "prepare_leave_request", {"leave_type": "annual", "start_date": "2026-12-28", "working_days": 2}
        ),
        "c1",
    )
    assert outcome.proposal_id is not None, outcome.result
    results = await asyncio.gather(
        *(
            db_services.proposals.execute(principal, outcome.proposal_id, run_id=UUID(int=n))
            for n in (11, 12, 13)
        ),
        return_exceptions=True,
    )
    real = [r for r in results if not isinstance(r, BaseException) and not r.replayed]
    assert len(real) == 1, results
    page = await db_services.leave_requests.list(
        principal, statuses=None, date_from=None, date_to=None, limit=50, cursor=None
    )
    assert len([r for r in page.items if r.start_date.isoformat() == "2026-12-28"]) == 1
