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
