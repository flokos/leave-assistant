"""S01–S09 (doc 11 §6, deterministic part) against the seeded dev database with the real retrieval stack (e5, FTS,
mmarco reranker, lingua, Presidio, vault) and the fake LLM. Requires `make up-staged && make bootstrap && make ingest`
(run by `make test-integration`, marker `scenario`)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from leave_assistant.agent.graph import AgentDeps
from leave_assistant.agent.runner import LangGraphAgent
from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.router import ProviderRouter
from leave_assistant.application.retrieval.context import EvidencePolicy
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import Settings
from leave_assistant.infrastructure.llm.fake import FakeChatModel
from leave_assistant.infrastructure.runtime import Runtime, build_runtime
from leave_assistant.seed.run import employee_principal
from tests.fakes.identity import FakeIdentityAdmin

pytestmark = [pytest.mark.scenario, pytest.mark.asyncio(loop_scope="module")]
# M9: the scenarios live in the evaluation dataset (evaluation/datasets/leave-eval-v1.yaml, `scenario_test: true`)
SCENARIOS = [
    item
    for item in yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "evaluation/datasets/leave-eval-v1.yaml").read_text(encoding="utf-8")
    )["items"]
    if item.get("scenario_test")
]
HOST = os.environ.get("TEST_DATABASE_HOST", "127.0.0.1")
PORT = os.environ.get("POSTGRES_PORT", "5432")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime() -> AsyncIterator[tuple[Runtime, FakeChatModel]]:
    settings = Settings(
        database_url=f"postgresql+psycopg://app_rls:{os.environ.get('LEAVE_DB_PASSWORD', 'app-rls-dev')}@{HOST}:{PORT}/leave",
        torch_threads=4,
    )
    rt = build_runtime(settings, role="api", identity=FakeIdentityAdmin())
    fake = FakeChatModel("fake")
    services = rt.services
    router = ProviderRouter([fake], services.adapters.tokenizer, health=HealthTracker())
    deps = AgentDeps(
        router=router,
        retrieval=services.retrieval,
        security=services.security,
        language=services.adapters.language,
        counter=services.adapters.tokenizer,
        evidence=EvidencePolicy(tau=settings.retrieval_tau, budget_tokens=settings.llm_budget_context),
    )
    services.chat.agent = LangGraphAgent(deps, InMemorySaver())
    yield rt, fake
    await rt.aclose()


async def ask(rt: Runtime, scenario: dict[str, Any]) -> dict[str, Any]:
    services = rt.services
    async with services.uow_factory(TenantContext(tenant_id=None, user_id="t", platform_admin=True)) as uow:
        tenant = await uow.tenants.get_by_slug(scenario["tenant"])
    assert tenant is not None
    async with services.uow_factory(
        TenantContext(tenant_id=tenant.id, user_id="t", roles=("system",))
    ) as uow:
        employee = await uow.employees.get_by_email(scenario["user"])
    assert employee is not None, scenario["user"]
    principal = employee_principal(tenant, employee)
    conversation = await services.chat.create(principal)
    result: dict[str, Any] = {"text": "", "citations": [], "principal": principal}
    async for event in await services.chat.send(
        principal, conversation.id, scenario["question"], request_id=scenario["id"]
    ):
        if event.name == "token":
            result["text"] += event.data["text"]
        elif event.name == "citations":
            result["citations"] = event.data["items"]
        elif event.name in ("done", "error"):
            result[event.name] = event.data
    async with services.uow_factory(TenantContext.for_principal(principal)) as uow:
        from sqlalchemy import select

        from leave_assistant.infrastructure.db.models import AgentRunRow

        run = (
            await uow.session.execute(
                select(AgentRunRow).where(AgentRunRow.conversation_id == conversation.id)
            )
        ).scalar_one()
        result["run"] = SimpleNamespace(outcome=run.outcome, language=run.language, details=dict(run.details))
    await services.chat.archive(principal, conversation.id)
    return result


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
async def test_scenario(runtime: tuple[Runtime, FakeChatModel], scenario: dict[str, Any]) -> None:
    rt, _ = runtime
    result = await ask(rt, scenario)
    run = result["run"]
    print(
        f"{scenario['id']}: outcome={run.outcome} lang={run.language} top={run.details.get('top_score')} "
        f"evidence={run.details.get('evidence')} cites={[c['title'] for c in result['citations']]}"
    )
    assert "error" not in result, result.get("error")
    if "outcome" in scenario:
        assert run.outcome == scenario["outcome"]
    assert run.language == scenario["language"]
    titles = [c["title"] for c in result["citations"]]
    if scenario.get("cite_any"):
        assert titles and titles[0] in scenario["cite_any"], titles
    for banned in scenario.get("never_cite", []):
        assert banned not in titles
    if scenario.get("chunk_has"):
        hit = await rt.services.retrieval.citation(
            result["principal"], __import__("uuid").UUID(result["citations"][0]["chunk_id"])
        )
        assert any(n in hit.chunk.content for n in scenario["chunk_has"]), hit.chunk.content[:300]
    if scenario.get("active_version_only"):
        for citation in result["citations"]:
            hit = await rt.services.retrieval.citation(
                result["principal"], __import__("uuid").UUID(citation["chunk_id"])
            )
            assert hit.version.retrievable and hit.version.status.value == "ACTIVE"
    if scenario["id"] == "S08":
        assert "twenty-five" not in result["text"] and "25" not in result["text"]
