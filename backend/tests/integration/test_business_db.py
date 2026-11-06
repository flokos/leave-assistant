"""M7 business scenarios against PostgreSQL with the real seed (doc 11 S12, S16, S17, S21, S25, S26): RLS, the SQL
repositories, the atomic claim and the idempotency index — with the fake LLM. Each test starts from a freshly
seeded `leave_test` database, so nothing touches the dev data."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.agent.graph import AgentDeps
from leave_assistant.agent.runner import LangGraphAgent
from leave_assistant.application.chat.service import ChatEvent
from leave_assistant.application.container import Services
from leave_assistant.application.leave.commands import SubmitRequest
from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.router import ProviderRouter
from leave_assistant.application.retrieval.context import EvidencePolicy
from leave_assistant.domain.tenancy import Employee, Principal
from leave_assistant.infrastructure.llm.fake import FakeChatModel
from tests.conftest import principal_for
from tests.fakes.documents import WordTokenizer

pytestmark = pytest.mark.integration


@pytest.fixture
def agent(db_services: Services, seeded: dict[str, object]) -> FakeChatModel:
    fake = FakeChatModel("fake")
    deps = AgentDeps(
        router=ProviderRouter([fake], WordTokenizer(), health=HealthTracker()),
        retrieval=db_services.retrieval,
        security=db_services.security,
        language=db_services.adapters.language,
        counter=WordTokenizer(),
        evidence=EvidencePolicy(tau=0.3),
        tools=db_services.tools,
        proposals=db_services.proposals,
    )
    db_services.chat.agent = LangGraphAgent(deps, InMemorySaver())
    return fake


def people(seeded: dict[str, object], tenant: str) -> dict[str, Employee]:
    return seeded["employees"][tenant]  # type: ignore[index, no-any-return]


async def turn(services: Services, principal: Principal, conversation_id: UUID, text: str) -> list[ChatEvent]:
    return [e async for e in await services.chat.send(principal, conversation_id, text, request_id="it")]


async def decide(
    services: Services,
    principal: Principal,
    conversation_id: UUID,
    events: list[ChatEvent],
    decision: str = "confirm",
) -> list[ChatEvent]:
    card = next(e for e in events if e.name == "interrupt").data["card"]
    stream = await services.chat.resume(
        principal,
        conversation_id,
        proposal_id=UUID(card["proposal_id"]),
        decision=decision,
        payload_hash=card["payload_hash"],
        edits=None,
        request_id="it",
    )
    return [e async for e in stream]


async def rows(engine: AsyncEngine, tenant_id: UUID, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)})
        return list((await conn.execute(text(sql), params)).all())


async def test_s12_and_s25_headline_on_postgres_executes_once(
    db_services: Services, seeded: dict[str, object], agent: FakeChatModel, owner_engine: AsyncEngine
) -> None:
    employee = people(seeded, "brightwater")["BWS-004"]
    principal = principal_for(employee)
    conversation = await db_services.chat.create(principal)
    events = await turn(
        db_services,
        principal,
        conversation.id,
        "How many vacation days do I have remaining? Can I request five days off starting December 14?",
    )
    assert [e.data["name"] for e in events if e.name == "tool_call" and e.data["status"] == "ok"] == [
        "get_my_balance",
        "prepare_leave_request",
    ]
    assert events[-1].name == "interrupt"
    first = await decide(db_services, principal, conversation.id, events)
    second = await decide(db_services, principal, conversation.id, events)
    assert first[-1].data["outcome"] == "executed" and second[-1].data["replayed"] is True
    created = await rows(
        owner_engine,
        employee.tenant_id,
        "SELECT status FROM app.leave_requests WHERE employee_id = :e AND start_date = '2026-12-14'",
        e=employee.id,
    )
    assert created == [("PENDING_MANAGER",)]
    events_with_origin = await rows(
        owner_engine,
        employee.tenant_id,
        "SELECT DISTINCT payload->>'via', request_id FROM app.leave_request_events WHERE payload ? 'proposal_id'",
    )
    assert (
        len(events_with_origin) == 1 and events_with_origin[0][0] == "agent"
    )  # submitted + routed, one request


async def test_s16_s17_manager_then_hr_approve_in_their_own_threads(
    db_services: Services, seeded: dict[str, object], agent: FakeChatModel, owner_engine: AsyncEngine
) -> None:
    aegean = people(seeded, "aegean")
    eleni, nikos, maria = (principal_for(aegean[n]) for n in ("AEG-004", "AEG-003", "AEG-002"))
    conversation = await db_services.chat.create(eleni)
    events = await turn(db_services, eleni, conversation.id, "Θέλω 7 μέρες άδεια από 7 Δεκεμβρίου")
    submitted = await decide(db_services, eleni, conversation.id, events)
    number = next(
        e.data["text"] for e in submitted if e.name == "token" and "LR-" in e.data["text"]
    )  # the outcome line names the request
    request = (
        await rows(
            owner_engine,
            eleni.tenant_id,  # type: ignore[arg-type]
            "SELECT request_number, status FROM app.leave_requests WHERE employee_id = :e AND start_date = '2026-12-07'",
            e=eleni.employee_id,
        )
    )[0]
    assert request[1] == "PENDING_MANAGER" and request[0][:3] == "LR-" and number

    for approver, expected in ((nikos, "PENDING_HR"), (maria, "APPROVED")):
        thread = await db_services.chat.create(approver)
        prepared = await turn(db_services, approver, thread.id, f"Approve {request[0]}")
        assert prepared[-1].name == "interrupt", [e.data for e in prepared if e.name == "tool_call"]
        done = await decide(db_services, approver, thread.id, prepared)
        assert done[-1].data["outcome"] == "executed"
        status = await rows(
            owner_engine,
            eleni.tenant_id,  # type: ignore[arg-type]
            "SELECT status FROM app.leave_requests WHERE request_number = :n",
            n=request[0],
        )
        assert status == [(expected,)]
    deductions = await rows(
        owner_engine,
        eleni.tenant_id,  # type: ignore[arg-type]
        "SELECT count(*) FROM app.leave_ledger_entries l JOIN app.leave_requests r ON r.id = l.request_id "
        "WHERE r.request_number = :n AND l.entry_type = 'DEDUCTION'",
        n=request[0],
    )
    assert deductions[0][0] >= 1
    via = await rows(
        owner_engine,
        eleni.tenant_id,  # type: ignore[arg-type]
        "SELECT s.via FROM app.approval_steps s JOIN app.leave_requests r ON r.id = s.request_id "
        "WHERE r.request_number = :n ORDER BY s.step_no",
        n=request[0],
    )
    assert via == [("agent",), ("agent",)]


async def test_s21_rls_keeps_the_other_tenant_invisible_to_tools(
    db_services: Services, seeded: dict[str, object], agent: FakeChatModel
) -> None:
    aegean_employee = principal_for(people(seeded, "aegean")["AEG-004"])
    brightwater_employee = principal_for(people(seeded, "brightwater")["BWS-004"])
    # a number that only exists in Aegean: create enough Aegean requests that the counter passes Brightwater's
    for day in (
        "2026-12-14",
        "2026-12-15",
        "2026-12-16",
        "2026-12-17",
        "2026-12-18",
        "2026-12-21",
        "2026-12-22",
    ):
        from datetime import date

        start = date.fromisoformat(day)
        await db_services.leave_requests.submit(
            aegean_employee, SubmitRequest(leave_type_code="annual", start_date=start, end_date=start)
        )
    page = await db_services.leave_requests.list(
        aegean_employee, statuses=None, date_from=None, date_to=None, limit=100, cursor=None
    )
    highest = max(r.request_number for r in page.items)
    conversation = await db_services.chat.create(brightwater_employee)
    agent.script["plan"].extend(
        [
            {"calls": [{"name": "prepare_cancellation", "arguments": {"request_number": highest}}]},
            {"text": "I could not find that request."},
        ]
    )
    events = await turn(db_services, brightwater_employee, conversation.id, f"Cancel request {highest}")
    statuses = [e.data["status"] for e in events if e.name == "tool_call" and e.data["status"] != "started"]
    assert statuses == ["not_found"]
    after = await db_services.leave_requests.list(
        aegean_employee, statuses=None, date_from=None, date_to=None, limit=100, cursor=None
    )
    assert {r.status.value for r in after.items if r.request_number == highest} == {"PENDING_MANAGER"}


async def test_s26_the_same_idempotency_key_creates_one_request(
    db_services: Services, seeded: dict[str, object]
) -> None:
    from datetime import date

    principal = principal_for(people(seeded, "aegean")["AEG-004"])
    command = SubmitRequest(
        leave_type_code="annual",
        start_date=date(2026, 12, 28),
        end_date=date(2026, 12, 29),
        idempotency_key="same-key-123",
    )
    first = await db_services.leave_requests.submit(principal, command)
    second = await db_services.leave_requests.submit(principal, command)
    assert first.request.id == second.request.id
    page = await db_services.leave_requests.list(
        principal,
        statuses=None,
        date_from=date(2026, 12, 28),
        date_to=date(2026, 12, 29),
        limit=10,
        cursor=None,
    )
    assert len(page.items) == 1
