"""Shared harness pieces (M9): the host-side runtime, principals, the corpus snapshot and its fingerprint, recording
wrappers around retrieval and the agent, and the live call budget.

The harness runs in the backend venv on the host (`make eval-*`), like the `scenario` tests: real models, the seeded
dev database through the published port, nothing in the request path is replaced — only observed."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from leave_assistant.application.chat.context import RunContext
from leave_assistant.application.chat.ports import AgentEvent, FinalEvent, InterruptEvent, ToolEvent
from leave_assistant.application.documents.views import RetrievalBundle
from leave_assistant.application.llm.router import ProviderEvent
from leave_assistant.application.retrieval.service import RetrievalService, ScoredHit
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import Settings
from leave_assistant.domain.privacy.types import LlmSafeText
from leave_assistant.domain.tenancy import Principal
from leave_assistant.evaluation.dataset import ChunkText
from leave_assistant.seed.data import AEGEAN, BRIGHTWATER
from leave_assistant.seed.run import employee_principal

REPO = Path(__file__).resolve().parents[4]
RESULTS = REPO / "docs" / "evaluation" / "results"


def host_settings(**overrides: Any) -> Settings:
    """Settings for the host: the stack's published ports (PostgreSQL 127.0.0.1:5432, Langfuse :3000)."""
    host = os.environ.get("EVAL_DB_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    rls = os.environ.get("LEAVE_DB_PASSWORD", "app-rls-dev")
    langgraph = os.environ.get("LANGGRAPH_DB_PASSWORD", "langgraph-dev")
    values: dict[str, Any] = {
        "database_url": f"postgresql+psycopg://app_rls:{rls}@{host}:{port}/leave",
        "langgraph_database_url": f"postgresql+psycopg://langgraph_rw:{langgraph}@{host}:{port}/leave",
        "s3_endpoint_url": os.environ.get("EVAL_S3_URL", "http://127.0.0.1:8333"),
        "langfuse_host": os.environ.get("EVAL_LANGFUSE_HOST", "http://localhost:3000"),
        "chroma_url": os.environ.get("EVAL_CHROMA_URL", "http://127.0.0.1:8100"),
        "torch_threads": int(os.environ.get("EVAL_TORCH_THREADS", "4")),
    }
    values.update(overrides)
    return Settings(**values)


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True)
        return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except OSError, subprocess.CalledProcessError:
        return "unknown"


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


# --- principals ----------------------------------------------------------------------------------------------------


class Principals:
    def __init__(self, services: Any) -> None:
        self._services = services
        self._cache: dict[tuple[str, str], Principal] = {}
        self.tenant_ids: dict[str, UUID] = {}

    async def tenant_id(self, slug: str) -> UUID:
        if slug not in self.tenant_ids:
            async with self._services.uow_factory(
                TenantContext(tenant_id=None, user_id="eval", platform_admin=True)
            ) as uow:
                tenant = await uow.tenants.get_by_slug(slug)
            if tenant is None:
                raise RuntimeError(f"tenant {slug} is not seeded")
            self.tenant_ids[slug] = tenant.id
        return self.tenant_ids[slug]

    async def get(self, slug: str, email: str) -> Principal:
        key = (slug, email)
        if key not in self._cache:
            tenant_id = await self.tenant_id(slug)
            async with self._services.uow_factory(
                TenantContext(tenant_id=None, user_id="eval", platform_admin=True)
            ) as uow:
                tenant = await uow.tenants.get(tenant_id)
            async with self._services.uow_factory(
                TenantContext(tenant_id=tenant_id, user_id="eval", roles=("system",))
            ) as uow:
                employee = await uow.employees.get_by_email(email)
            if tenant is None or employee is None:
                raise RuntimeError(f"seed user {email} missing")
            self._cache[key] = employee_principal(tenant, employee)
        return self._cache[key]


# --- the corpus ----------------------------------------------------------------------------------------------------

SEED_TITLES = {company.slug: {d.title for d in company.documents} for company in (AEGEAN, BRIGHTWATER)}


@dataclass(frozen=True, slots=True)
class Corpus:
    chunks: list[ChunkText]
    fingerprint: str
    documents: dict[str, list[str]]  # tenant → retrievable titles
    extra: dict[str, list[str]]  # retrievable documents that are not in the seed manifest


async def snapshot(services: Any, principals: Principals) -> Corpus:
    """Every retrievable chunk of both seed tenants (system context, all visibilities) and a SHA-256 fingerprint."""
    chunks: list[ChunkText] = []
    documents: dict[str, list[str]] = {}
    for slug in SEED_TITLES:
        tenant_id = await principals.tenant_id(slug)
        async with services.uow_factory(
            TenantContext(tenant_id=tenant_id, user_id="eval", roles=("system",))
        ) as uow:
            titles: dict[UUID, str] = {}
            for row in await uow.chunks.index_rows():
                if row.document_id not in titles:
                    document = await uow.documents.get(row.document_id)
                    titles[row.document_id] = document.title if document else "?"
                chunk = await uow.chunks.get(row.chunk_id)
                if chunk is not None:
                    chunks.append(ChunkText(chunk.id, slug, titles[row.document_id], chunk.content))
        documents[slug] = sorted(set(titles.values()))
    digest = hashlib.sha256()
    for chunk in sorted(chunks, key=lambda c: (c.tenant, c.title, str(c.chunk_id))):
        digest.update(f"{chunk.tenant}|{chunk.title}|{chunk.chunk_id}|{chunk.content}\n".encode())
    extra = {slug: [t for t in titles if t not in SEED_TITLES[slug]] for slug, titles in documents.items()}
    return Corpus(chunks, digest.hexdigest()[:16], documents, {k: v for k, v in extra.items() if v})


# --- recording wrappers -----------------------------------------------------------------------------------------------


@dataclass(slots=True)
class RetrievalCall:
    query: str
    hits: list[ScoredHit]
    search_s: float
    rerank_s: float


class TimedRetrieval:
    """Stands in for `RetrievalService` in the agent's dependencies: same results, stage timings recorded."""

    def __init__(self, inner: RetrievalService) -> None:
        self.inner = inner
        self.calls: list[RetrievalCall] = []

    async def retrieve(self, principal: Principal, query: str) -> list[ScoredHit]:
        started = time.perf_counter()
        bundle: RetrievalBundle = await self.inner.search(principal, query, k=self.inner.rerank_candidates)
        searched = time.perf_counter()
        scored = await self.inner.rescore(bundle)
        self.calls.append(RetrievalCall(query, scored, searched - started, time.perf_counter() - searched))
        return scored

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass(slots=True)
class AgentRecord:
    message: str
    tools: list[tuple[str, str]] = field(default_factory=list)
    final: FinalEvent | None = None
    interrupt: InterruptEvent | None = None


class RecordingAgent:
    """Wraps the `AgentRunner`: what the agent received (LLM-safe), the tools it called and its LLM-side answer."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.records: list[AgentRecord] = []

    async def _record(
        self, record: AgentRecord, events: AsyncIterator[AgentEvent]
    ) -> AsyncIterator[AgentEvent]:
        self.records.append(record)
        async for event in events:
            if isinstance(event, ToolEvent):
                record.tools.append((event.name, event.status))
            elif isinstance(event, FinalEvent):
                record.final = event
            elif isinstance(event, InterruptEvent):
                record.interrupt = event
            yield event

    def run(self, context: RunContext, message: LlmSafeText) -> AsyncIterator[AgentEvent]:
        return self._record(AgentRecord(str(message)), self.inner.run(context, message))

    def resume(self, context: RunContext, decision: dict[str, Any]) -> AsyncIterator[AgentEvent]:
        return self._record(
            AgentRecord(f"[resume {decision.get('decision')}]"), self.inner.resume(context, decision)
        )

    async def delete_threads(self, thread_ids: Sequence[UUID]) -> None:
        await self.inner.delete_threads(thread_ids)


# --- the live budget ---------------------------------------------------------------------------------------------------


class BudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class CallBudget:
    """Counts provider calls (`selected` and `retry` events) against `EVAL_MAX_CALLS`."""

    limit: int
    calls: int = 0
    by_model: dict[str, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_env(cls, default: int = 250) -> CallBudget:
        return cls(int(os.environ.get("EVAL_MAX_CALLS", str(default))))

    async def sink(self, event: ProviderEvent) -> None:
        self.events[event.event] = self.events.get(event.event, 0) + 1
        if event.event in ("selected", "retry"):
            self.calls += 1
            key = f"{event.provider}/{event.model}"
            self.by_model[key] = self.by_model.get(key, 0) + 1

    def check(self, upcoming: int = 4) -> None:
        if self.calls + upcoming > self.limit:
            raise BudgetExceeded(f"{self.calls} calls used of EVAL_MAX_CALLS={self.limit}")

    @property
    def remaining(self) -> int:
        return self.limit - self.calls


Pacer = Callable[[], Awaitable[None]]
