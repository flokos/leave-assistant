"""NFR-PERF-01 (`make chat-perf`): a policy question end to end **excluding LLM latency** (zero-latency fake provider)
on the reference machine — Presidio, input screen, retrieval with reranking of 20 candidates, graph, checkpoint,
re-identification and persistence. `--reranker` measures either ADR-0014 candidate."""

from __future__ import annotations

import argparse
import asyncio
import resource
import statistics
import sys
import time

from langgraph.checkpoint.memory import InMemorySaver

from leave_assistant.agent.graph import AgentDeps
from leave_assistant.agent.runner import LangGraphAgent
from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.router import ProviderRouter
from leave_assistant.application.retrieval.context import EvidencePolicy
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import get_settings
from leave_assistant.infrastructure.llm.fake import FakeChatModel
from leave_assistant.infrastructure.ml.reranker import CrossEncoderReranker
from leave_assistant.infrastructure.runtime import build_runtime
from leave_assistant.seed.run import employee_principal

QUESTIONS = (
    "Πόσες μέρες άδειας δικαιούμαι τον χρόνο;",
    "Μεταφέρονται οι μέρες που δεν πήρα;",
    "Τι ισχύει για την αναρρωτική άδεια;",
    "Can I carry over unused days?",
)


async def _main(runs: int, reranker: str | None) -> int:
    settings = get_settings()
    runtime = build_runtime(settings, role="api")
    services = runtime.services
    if reranker:
        services.retrieval._reranker = CrossEncoderReranker(  # noqa: SLF001
            reranker, threads=settings.torch_threads, max_length=settings.rerank_max_length
        )
    router = ProviderRouter([FakeChatModel("fake")], services.adapters.tokenizer, health=HealthTracker())
    services.chat.agent = LangGraphAgent(
        AgentDeps(
            router=router,
            retrieval=services.retrieval,
            security=services.security,
            language=services.adapters.language,
            counter=services.adapters.tokenizer,
            evidence=EvidencePolicy(tau=settings.retrieval_tau, budget_tokens=settings.llm_budget_context),
        ),
        InMemorySaver(),
    )
    try:
        async with services.uow_factory(
            TenantContext(tenant_id=None, user_id="perf", platform_admin=True)
        ) as uow:
            tenant = await uow.tenants.get_by_slug("aegean")
        assert tenant is not None
        async with services.uow_factory(
            TenantContext(tenant_id=tenant.id, user_id="perf", roles=("system",))
        ) as uow:
            employee = await uow.employees.get_by_email("eleni.dimitriou@aegean-logistics.example")
        assert employee is not None
        principal = employee_principal(tenant, employee)
        timings: list[float] = []
        for index in range(runs + 1):  # the first run warms the models and is reported separately
            conversation = await services.chat.create(principal)
            started = time.perf_counter()
            outcome = "?"
            async for event in await services.chat.send(
                principal, conversation.id, QUESTIONS[index % len(QUESTIONS)], request_id=f"perf-{index}"
            ):
                if event.name == "done":
                    outcome = str(event.data["outcome"])
            elapsed = time.perf_counter() - started
            await services.chat.archive(principal, conversation.id)
            if index == 0:
                sys.stdout.write(f"warm-up (model loads): {elapsed:.2f} s, outcome={outcome}\n")
            else:
                timings.append(elapsed)
        timings.sort()
        p95 = timings[max(0, int(round(0.95 * len(timings))) - 1)]
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        sys.stdout.write(
            f"reranker={services.retrieval._reranker.model_id if services.retrieval._reranker else None} "  # noqa: SLF001
            f"runs={len(timings)} p50={statistics.median(timings):.2f}s p95={p95:.2f}s max={timings[-1]:.2f}s "
            f"process max RSS={rss:.0f} MB\n"
        )
        return 0
    finally:
        await runtime.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="NFR-PERF-01: chat turn latency excluding the LLM")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--reranker", help="override RERANKER_MODEL")
    args = parser.parse_args()
    return asyncio.run(_main(args.runs, args.reranker))


if __name__ == "__main__":
    raise SystemExit(main())
