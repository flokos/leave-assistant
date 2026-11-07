"""Performance figures for the M9 report (doc 11 §7 "Performance"): latency per stage of the live answers, and
ingestion time per document type from the job history. NFR-PERF-01 itself is `make chat-perf` (in the api
container, zero-latency fake provider) and is recorded next to these figures.

    python -m leave_assistant.evaluation.perf [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from typing import Any
from uuid import UUID

from leave_assistant.application.jobs.models import JOB_DOCUMENT_PROCESS, JobStatus
from leave_assistant.application.ports import JobFilter
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.evaluation import metrics
from leave_assistant.evaluation.harness import RESULTS, git_commit, host_settings, stamp
from leave_assistant.infrastructure.runtime import build_runtime
from leave_assistant.seed.data import AEGEAN, BRIGHTWATER

# the seed manifest knows each document's source format (`scan` = image-only PDF, OCR)
SEED_FORMATS = {d.title: d.format for company in (AEGEAN, BRIGHTWATER) for d in company.documents}
# uploads outside the seed manifest: the MIME subtype, named as the manifest names formats
MIME_FORMATS = {
    "markdown": "md",
    "plain": "txt",
    "vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def stage_latency(date: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in sorted(RESULTS.glob(f"{date}-answers-*.jsonl")):
        samples = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not samples:
            continue
        config = samples[0]["config"]
        stages: dict[str, list[float]] = defaultdict(list)
        warmup, samples = samples[0], samples[1:]  # the first turn loads the models: reported on its own
        for s in samples:
            t = s["timings"]
            if t["search_s"] > 0:  # turns that retrieved (a clarification or a fallback may not)
                stages["search_ms"].append(t["search_s"] * 1000)
                stages["rerank_ms"].append(t["rerank_s"] * 1000)
            if t.get("llm_ms") is not None:
                stages["llm_ms"].append(float(t["llm_ms"]))
            stages["total_ms"].append(t["total_s"] * 1000)
        out[config] = {
            stage: {
                "n": len(values),
                "p50": round(metrics.percentile(values, 0.5)),
                "p95": round(metrics.percentile(values, 0.95)),
            }
            for stage, values in stages.items()
        }
        out[config]["warmup_total_ms"] = {
            "n": 1,
            "p50": round(warmup["timings"]["total_s"] * 1000),
            "p95": None,
        }
        excluding = [
            s["timings"]["total_s"] * 1000 - float(s["timings"]["llm_ms"])
            for s in samples
            if s["timings"].get("llm_ms") is not None
        ]
        if excluding:
            out[config]["total_minus_llm_ms"] = {
                "n": len(excluding),
                "p50": round(metrics.percentile(excluding, 0.5)),
                "p95": round(metrics.percentile(excluding, 0.95)),
            }
    return out


async def ingestion() -> dict[str, Any]:
    runtime = build_runtime(host_settings(), role="api")
    services = runtime.services
    by_type: dict[str, list[float]] = defaultdict(list)
    try:
        async with services.uow_factory(None) as uow:
            jobs = await uow.jobs.list(
                filters=JobFilter(status=JobStatus.SUCCEEDED, type=JOB_DOCUMENT_PROCESS), limit=500
            )
        for job in jobs:
            if not (job.locked_at and job.finished_at and job.tenant_id):
                continue
            version_id = job.payload.get("version_id")
            async with services.uow_factory(
                TenantContext(tenant_id=job.tenant_id, user_id="eval", roles=("system",))
            ) as uow:
                version = await uow.document_versions.get(UUID(str(version_id))) if version_id else None
                document = await uow.documents.get(version.document_id) if version else None
            if version is None:
                continue
            title = document.title if document else ""
            subtype = version.mime.rsplit("/", 1)[-1]
            kind = SEED_FORMATS.get(title) or MIME_FORMATS.get(subtype, subtype)
            by_type[kind].append((job.finished_at - job.locked_at).total_seconds())
    finally:
        await runtime.aclose()
    return {
        kind: {
            "n": len(values),
            "p50_s": round(metrics.percentile(values, 0.5), 1),
            "max_s": round(max(values), 1),
        }
        for kind, values in sorted(by_type.items())
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M9 performance figures")
    parser.add_argument("--date", default=stamp())
    args = parser.parse_args(argv)
    summary = {
        "date": args.date,
        "commit": git_commit(),
        "stage_latency": stage_latency(args.date),
        "ingestion": asyncio.run(ingestion()),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    prefix = RESULTS / f"{args.date}-performance"
    prefix.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"# Performance — {args.date} (commit {summary['commit']})",
        "",
        "## Live answers, per stage (ms)",
        "",
        "Warm turns only (the first turn of a configuration loads the models and is listed as `warmup_total_ms`);",
        "search and rerank over the turns that retrieved. The LLM time is the provider's, on the free tier.",
        "",
    ]
    lines += ["| Configuration | stage | n | p50 | p95 |", "|---|---|---|---|---|"]
    for config, stages in summary["stage_latency"].items():
        for stage, values in stages.items():
            p95 = "—" if values["p95"] is None else values["p95"]
            lines.append(f"| `{config}` | {stage} | {values['n']} | {values['p50']} | {p95} |")
    lines += [
        "",
        "## Ingestion per document type (job claim → finish, s)",
        "",
        "| Type | n | p50 | max |",
        "|---|---|---|---|",
    ]
    for kind, values in summary["ingestion"].items():
        lines.append(f"| {kind} | {values['n']} | {values['p50_s']} | {values['max_s']} |")
    prefix.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(summary["ingestion"]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
