"""Chat endpoints through HTTP: conversations, SSE stream format, problems, history; prompt budgets."""

from __future__ import annotations

import json
from uuid import uuid4

from httpx import AsyncClient

from leave_assistant.agent import prompts
from tests.conftest import TokenFactory, bearer
from tests.fakes.documents import WordTokenizer
from tests.fakes.world import World
from tests.unit.agent.conftest import AgentWorld


def parse_sse(body: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if name and data is not None:
            events.append((name, json.loads(data)))
    return events


async def test_conversation_lifecycle_and_sse_stream(
    api: AsyncClient, agent_world: AgentWorld, world: World, tokens: TokenFactory
) -> None:
    auth = bearer(tokens.for_employee(world.a.employees["employee"]))
    created = await api.post("/api/v1/chat/conversations", headers={**auth, "Idempotency-Key": str(uuid4())})
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    response = await api.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"text": "How many working days of annual leave per calendar year?"},
        headers=auth,
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [n for n, _ in events]
    assert names[0] == "run_started" and names[-1] == "done" and "token" in names and "citations" in names
    assert "twenty working days" in "".join(d["text"] for n, d in events if n == "token")
    history = await api.get(f"/api/v1/chat/conversations/{conversation_id}/messages", headers=auth)
    assert [m["role"] for m in history.json()] == ["user", "assistant"]
    assert history.json()[1]["citations"][0]["title"] == "Employee Leave Handbook"
    listed = await api.get("/api/v1/chat/conversations", headers=auth)
    assert listed.json()["items"][0]["title"].startswith("How many working days")
    other = bearer(tokens.for_employee(world.a.employees["employee2"]))
    assert (await api.get(f"/api/v1/chat/conversations/{conversation_id}", headers=other)).status_code == 404
    denied = await api.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages", json={"text": "hi"}, headers=other
    )
    assert denied.status_code == 404 and denied.json()["type"].endswith("not-found")
    empty = await api.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages", json={"text": ""}, headers=auth
    )
    assert empty.status_code == 422
    assert (
        await api.get("/api/v1/chat/conversations", headers=bearer(tokens.platform_admin()))
    ).status_code == 403
    deleted = await api.delete(
        f"/api/v1/chat/conversations/{conversation_id}", headers={**auth, "Idempotency-Key": str(uuid4())}
    )
    assert deleted.status_code == 204
    assert (await api.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth)).status_code == 404


def test_static_prompts_fit_their_budgets() -> None:
    counter = WordTokenizer()
    for name in ("system", "classify", "condense", "generate", "summarise"):
        file = prompts.load(name)
        body = prompts.system_prompt("el", hardened=True) if name == "system" else file.body
        assert counter.count(body) * 1.15 <= file.budget, name
    assert prompts.load("system").budget <= 700


async def test_resume_card_history_runs_and_problems_through_http(
    api: AsyncClient,
    services,
    leave_world: World,
    tokens: TokenFactory,  # type: ignore[no-untyped-def]
) -> None:
    from tests.unit.agent.conftest import build_agent

    build_agent(services)
    employee = leave_world.b.employees["employee"]
    auth = bearer(tokens.for_employee(employee))
    created = await api.post("/api/v1/chat/conversations", headers={**auth, "Idempotency-Key": str(uuid4())})
    conversation_id = created.json()["id"]
    response = await api.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"text": "I want five days off starting December 14"},
        headers=auth,
    )
    events = parse_sse(response.text)
    assert [n for n, _ in events][-1] == "interrupt"
    assert ("tool_call", {"name": "prepare_leave_request", "status": "ok"}) in events
    interrupt = next(d for n, d in events if n == "interrupt")
    card = interrupt["card"]
    assert card["status"] == "pending" and card["actions"] == ["confirm", "reject", "edit"]

    detail = await api.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth)
    assert detail.json()["pending_proposal"]["proposal_id"] == interrupt["proposal_id"]
    url = f"/api/v1/chat/conversations/{conversation_id}/resume"
    body = {
        "proposal_id": interrupt["proposal_id"],
        "decision": "confirm",
        "payload_hash": card["payload_hash"],
    }
    bad = await api.post(url, json={**body, "payload_hash": "f" * 64}, headers=auth)
    assert bad.status_code == 409 and bad.json()["type"].endswith("chat/proposal-hash-mismatch")
    malformed = await api.post(url, json={**body, "decision": "approve_everything"}, headers=auth)
    assert malformed.status_code == 422
    intruder = bearer(tokens.for_employee(leave_world.b.employees["employee2"]))
    assert (await api.post(url, json=body, headers=intruder)).status_code == 404

    done = parse_sse((await api.post(url, json=body, headers=auth)).text)
    assert done[-1][0] == "done" and done[-1][1]["outcome"] == "executed"
    replay = parse_sse((await api.post(url, json=body, headers=auth)).text)
    assert replay[-1][1]["replayed"] is True

    history = (await api.get(f"/api/v1/chat/conversations/{conversation_id}/messages", headers=auth)).json()
    assert [m["outcome"] for m in history] == [None, "interrupted", "executed"]
    assert history[1]["card"]["status"] == "executed" and history[2]["card"] is None
    assert (await api.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth)).json()[
        "pending_proposal"
    ] is None

    runs = (await api.get(f"/api/v1/chat/conversations/{conversation_id}/runs", headers=auth)).json()
    assert [r["outcome"] for r in runs] == ["interrupted", "executed"]
    assert [t["name"] for t in runs[0]["tool_trace"]] == ["prepare_leave_request"]
    assert runs[0]["proposals"][0]["status"] == "executed" and "arguments" not in json.dumps(runs)
    hr = bearer(tokens.for_employee(leave_world.b.employees["hr_admin"]))
    assert (
        await api.get(f"/api/v1/chat/conversations/{conversation_id}/runs", headers=hr)
    ).status_code == 200
    colleague = bearer(tokens.for_employee(leave_world.b.employees["manager"]))
    assert (
        await api.get(f"/api/v1/chat/conversations/{conversation_id}/runs", headers=colleague)
    ).status_code == 404


def test_business_prompts_and_tool_schemas_fit_their_budgets() -> None:
    from leave_assistant.application.assistant.tools import allowed_tools, spec_for
    from leave_assistant.application.llm.ports import tool_schema_text
    from leave_assistant.domain.agent.budget import TokenBudgets
    from leave_assistant.domain.tenancy import Role

    counter = WordTokenizer()
    for name in ("business", "plan"):
        file = prompts.load(name)
        body = prompts.business_prompt("el", hardened=True) if name == "business" else file.body
        assert counter.count(body) * 1.15 <= file.budget, name
    widest = frozenset({Role.EMPLOYEE, Role.MANAGER, Role.HR_ADMIN, Role.COMPANY_ADMIN})
    schemas = tool_schema_text([spec_for(t) for t in allowed_tools(widest)])
    assert counter.count(schemas) * 1.15 <= TokenBudgets().tools
