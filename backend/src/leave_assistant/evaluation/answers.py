"""Live answer samples for RAGAS (M9, `make eval-live`; docs/evaluation/README.md §2).

    python -m leave_assistant.evaluation.answers --config pgvector+B+hybrid:0.5 --config pgvector+A+hybrid:0.42

Each `--config` is `backend+reranker+mode[+runtime]:τ`. For every `ragas: true` item the full chat path runs with the
real providers (the production router, Gemini first): input screening, pseudonymisation, retrieval, the agent,
re-identification, persistence (the conversation is archived afterwards). Recorded per sample: what the provider saw
(the LLM-safe question, the context blocks, the LLM-side answer), the user-visible answer and citations, the outcome,
provider/model/tokens, and timings per stage. Budget: `EVAL_MAX_CALLS`; pacing: `EVAL_PACE_SECONDS`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.evaluation.dataset import Item, load
from leave_assistant.evaluation.harness import (
    RESULTS,
    BudgetExceeded,
    CallBudget,
    ContextRecorder,
    Principals,
    RecordingAgent,
    TimedRetrieval,
    git_commit,
    host_settings,
    snapshot,
    stamp,
)
from leave_assistant.infrastructure.db.models import AgentRunRow
from leave_assistant.infrastructure.runtime import Runtime, build_runtime

RERANKERS = {"A": "BAAI/bge-reranker-v2-m3", "B": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"}
BLOCK = re.compile(r'<document id="\d+" title="([^"]*)" section="([^"]*)">\n(.*?)\n</document>', re.S)


@dataclass(frozen=True, slots=True)
class LiveConfig:
    backend: str
    reranker: str
    mode: str
    runtime: str
    tau: float

    @classmethod
    def parse(cls, spec: str) -> LiveConfig:
        key, _, tau = spec.partition(":")
        parts = key.split("+")
        if len(parts) not in (3, 4) or parts[1] not in RERANKERS or not tau:
            raise SystemExit(
                f"bad --config {spec!r}: expected backend+A|B+hybrid|vector[+torch|onnx|onnx-int8]:τ"
            )
        return cls(parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else "torch", float(tau))

    @property
    def key(self) -> str:
        runtime = "" if self.runtime == "torch" else f"+{self.runtime}"
        return f"{self.backend}+{self.reranker}+{self.mode}{runtime}"

    def settings(self, providers: str) -> Any:
        return host_settings(
            llm_provider_order=providers,
            retrieval_backend=self.backend,
            reranker_model=RERANKERS[self.reranker],
            reranker_backend=self.runtime,
            retrieval_hybrid=self.mode == "hybrid",
            retrieval_tau=self.tau,
            langfuse_enabled=os.environ.get("EVAL_TRACE", "1") != "0",
        )


def contexts_of(text: str) -> list[str]:
    return [f"{title} — {section}: {body}".strip() for title, section, body in BLOCK.findall(text)]


def merged_contexts(texts: list[str]) -> list[str]:
    """Every evidence block the LLM saw in one turn (knowledge node and tool calls), first occurrence kept."""
    return list(dict.fromkeys(block for text in texts for block in contexts_of(text)))


async def run_config(
    config: LiveConfig, items: list[Item], budget: CallBudget, pace: float, providers: str
) -> list[dict[str, Any]]:
    settings = config.settings(providers)
    runtime = build_runtime(settings, role="api")
    services = runtime.services
    principals = Principals(services)
    corpus = await snapshot(services, principals)
    if corpus.extra:
        await runtime.aclose()
        raise SystemExit(f"the corpus is not the seed corpus: {corpus.extra}")
    timed = TimedRetrieval(services.retrieval)
    services.retrieval = timed  # type: ignore[assignment]  # the agent's dependency, observed only
    services.tools._retrieval = timed  # type: ignore[assignment]  # `search_policies` holds its own reference
    recorder = ContextRecorder()
    samples: list[dict[str, Any]] = []
    try:
        with recorder.installed():
            await _answer_items(config, items, budget, pace, runtime, principals, timed, recorder, samples)
    except BudgetExceeded as exc:
        sys.stdout.write(f"budget reached: {exc}\n")
    finally:
        await runtime.aclose()
    return samples


async def _answer_items(
    config: LiveConfig,
    items: list[Item],
    budget: CallBudget,
    pace: float,
    runtime: Runtime,
    principals: Principals,
    timed: TimedRetrieval,
    recorder: ContextRecorder,
    samples: list[dict[str, Any]],
) -> None:
    services = runtime.services
    if not await runtime.start_agent(extra_sink=budget.sink):
        raise SystemExit("no LLM provider configured")
    agent = RecordingAgent(services.chat.agent)
    services.chat.agent = agent
    for item in items:
        budget.check()
        assert item.tenant and item.user and item.question
        principal = await principals.get(item.tenant, item.user)
        conversation = await services.chat.create(principal)
        calls_before, records_before = len(timed.calls), len(agent.records)
        bundles_before = len(recorder.bundles)
        started = time.perf_counter()
        text, citations, done, error = "", [], {}, None
        async for event in await services.chat.send(
            principal, conversation.id, item.question, request_id=f"eval-{item.id}"
        ):
            if event.name == "token":
                text += str(event.data["text"])
            elif event.name == "citations":
                citations = [str(c["title"]) for c in event.data["items"]]
            elif event.name == "done":
                done = dict(event.data)
            elif event.name == "error":
                error = event.data
        total_s = time.perf_counter() - started
        async with services.uow_factory(TenantContext.for_principal(principal)) as uow:
            run = (
                await uow.session.execute(  # type: ignore[attr-defined]
                    select(AgentRunRow).where(AgentRunRow.conversation_id == conversation.id)
                )
            ).scalar_one_or_none()
            run_outcome = run.outcome if run is not None else None
            run_language = run.language if run is not None else None
            details = dict(run.details) if run is not None else {}
        await services.chat.archive(principal, conversation.id)
        retrievals = timed.calls[calls_before:]
        records = agent.records[records_before:]
        final = records[-1].final if records else None
        bundles = recorder.bundles[bundles_before:]
        sample = {
            "id": item.id,
            "config": config.key,
            "tau": config.tau,
            "tenant": item.tenant,
            "question": item.question,
            "question_llm_safe": records[-1].message if records else None,
            "expected_language": item.language,
            "answer": text,
            "answer_llm_safe": final.answer.text if final else None,
            "contexts": merged_contexts([text for _, text in bundles]),
            "context_sites": [site for site, _ in bundles],
            "tools": records[-1].tools if records else [],
            "reference": item.reference,
            "citations": citations,
            "outcome": done.get("outcome") or run_outcome,
            "language": run_language,
            "provider": (done.get("usage") or {}).get("provider"),
            "model": (done.get("usage") or {}).get("model"),
            "input_tokens": (done.get("usage") or {}).get("input_tokens"),
            "output_tokens": (done.get("usage") or {}).get("output_tokens"),
            "failovers": details.get("failovers"),
            "top_score": details.get("top_score"),
            "timings": {
                "search_s": round(sum(r.search_s for r in retrievals), 3),
                "rerank_s": round(sum(r.rerank_s for r in retrievals), 3),
                "llm_ms": details.get("llm_latency_ms"),
                "total_s": round(total_s, 2),
            },
            "conversation_id": str(conversation.id),
            "error": error,
            # the verifier's rejections per draft; since M10 an exhausted verifier is the outcome `fallback` (R-36)
            "verifier_problems": details.get("grounding_problems") or [],
            "verifier_exhausted": (done.get("outcome") or run_outcome) == "fallback",
        }
        samples.append(sample)
        sys.stdout.write(
            f"{config.key} {item.id}: {sample['outcome']} {sample['provider']}/{sample['model']} "
            f"{total_s:.1f}s calls={budget.calls}\n"
        )
        sys.stdout.flush()
        await asyncio.sleep(pace)


async def run(
    configs: list[LiveConfig], only: set[str] | None, providers: str, tag: str = ""
) -> dict[str, Any]:
    items = [i for i in load() if i.ragas and (not only or i.id in only)]
    budget = CallBudget.from_env()
    pace = float(os.environ.get("EVAL_PACE_SECONDS", "6"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"date": stamp(), "commit": git_commit(), "providers": providers, "configs": {}}
    for config in configs:
        samples = await run_config(config, items, budget, pace, providers)
        path = RESULTS / f"{summary['date']}-answers-{config.key}{tag}.jsonl"
        path.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), encoding="utf-8")
        summary["configs"][config.key] = {"samples": len(samples), "file": path.name, "tau": config.tau}
    summary["calls"] = budget.calls
    summary["by_model"] = budget.by_model
    summary["provider_events"] = budget.events
    (RESULTS / f"{summary['date']}-answers{tag}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M9 live answer samples for RAGAS")
    parser.add_argument(
        "--config", action="append", required=True, help="backend+A|B+hybrid|vector[+runtime]:τ"
    )
    parser.add_argument("--only", help="comma-separated item ids (default: every ragas item)")
    parser.add_argument(
        "--providers",
        default="gemini,mistral",
        help="provider order (default: production's); one provider pins every answer to it, for comparable configs",
    )
    parser.add_argument(
        "--tag", default="", help="suffix of the result files, e.g. -gemini (M10 spot checks)"
    )
    args = parser.parse_args(argv)
    configs = [LiveConfig.parse(c) for c in args.config]
    only = set(args.only.split(",")) if args.only else None
    summary = asyncio.run(run(configs, only, args.providers, args.tag))
    sys.stdout.write(json.dumps({k: summary[k] for k in ("date", "calls", "by_model")}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
