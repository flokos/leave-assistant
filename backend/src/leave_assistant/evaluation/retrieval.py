"""Offline retrieval evaluation (M9, `make eval-offline`; docs/evaluation/README.md §2–§4).

    python -m leave_assistant.evaluation.retrieval grid       # 12 configurations, τ calibration
    python -m leave_assistant.evaluation.retrieval runtimes   # reranker A/B × torch/onnx/onnx-int8 × 10/20 candidates
    python -m leave_assistant.evaluation.retrieval runtime --model B --backend onnx-int8   # one variant (a child)

No LLM is involved. Rankings come from `RetrievalService.search` + `rescore` — the agent's own code path — with
the reranker's scores cached per (model, query, passage) so the grid scores each pair once.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import resource
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from leave_assistant.application.retrieval.service import RetrievalService, ScoredHit
from leave_assistant.application.retrieval.vector_index import PgVectorIndex
from leave_assistant.evaluation import metrics
from leave_assistant.evaluation.dataset import Item, grades_for, load, unresolved
from leave_assistant.evaluation.harness import (
    RESULTS,
    Corpus,
    Principals,
    git_commit,
    host_settings,
    snapshot,
    stamp,
)
from leave_assistant.infrastructure.ml.reranker import CrossEncoderReranker
from leave_assistant.infrastructure.runtime import Runtime, build_runtime

RERANKERS = {"A": "BAAI/bge-reranker-v2-m3", "B": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"}
BACKENDS = ("pgvector", "chroma")
CANDIDATES = 10  # the M6 default (doc 05 §6.7)
MAX_LENGTH = 256
LEG_SIZE = 30
BASELINE = ("pgvector", "B", "hybrid")


@dataclass(frozen=True, slots=True)
class Config:
    backend: str
    reranker: str | None  # "A" | "B" | None
    mode: str  # hybrid | vector

    @property
    def key(self) -> str:
        return f"{self.backend}+{self.reranker or 'none'}+{self.mode}"


def grid() -> list[Config]:
    return [Config(b, r, m) for r in (None, "A", "B") for b in BACKENDS for m in ("hybrid", "vector")]


class CachingReranker:
    """`Reranker` port with a (query, passage) cache — identical scores, each pair computed once."""

    def __init__(self, inner: CrossEncoderReranker) -> None:
        self.inner = inner
        self.cache: dict[tuple[str, str], float] = {}
        self.computed = 0

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        missing = [p for p in dict.fromkeys(passages) if (query, p) not in self.cache]
        if missing:
            for passage, score in zip(missing, self.inner.score(query, missing), strict=True):
                self.cache[(query, passage)] = score
            self.computed += len(missing)
        return [self.cache[(query, p)] for p in passages]


@dataclass(slots=True)
class Row:
    config: str
    item: str
    kind: str
    language: str | None
    hit1: float | None = None
    hit5: float | None = None
    rr: float | None = None
    ndcg5: float | None = None
    top_score: float | None = None
    violations: int = 0
    search_ms: float = 0.0
    ranked: list[str] = field(default_factory=list)


async def _prepare(allow_extra: bool) -> tuple[Runtime, Principals, Corpus, list[Item]]:
    runtime = build_runtime(host_settings(), role="api")
    principals = Principals(runtime.services)
    corpus = await snapshot(runtime.services, principals)
    items = load()
    if corpus.extra and not allow_extra:
        await runtime.aclose()
        raise SystemExit(f"the corpus is not the seed corpus — remove these documents first: {corpus.extra}")
    missing = unresolved([i for i in items if i.labelled], corpus.chunks)
    if missing:
        await runtime.aclose()
        raise SystemExit(f"labels do not resolve on this corpus: {missing}")
    return runtime, principals, corpus, items


def _service(
    runtime: Runtime, index: Any, reranker: Any, mode: str, candidates: int = CANDIDATES
) -> RetrievalService:
    adapters = runtime.services.adapters
    return RetrievalService(
        runtime.services.uow_factory,
        adapters.embedder,
        adapters.language,
        leg_size=LEG_SIZE,
        default_k=candidates,
        reranker=reranker,
        rerank_candidates=candidates,
        vector_index=index,
        hybrid=mode == "hybrid",
    )


async def _rank(
    service: RetrievalService, principal: Any, question: str, k: int
) -> tuple[list[ScoredHit], float]:
    started = time.perf_counter()
    bundle = await service.search(principal, question, k=k)
    searched = time.perf_counter() - started
    return await service.rescore(bundle), searched


def _score_row(
    config: str, item: Item, scored: list[ScoredHit], corpus: Corpus, searched: float, tenants: dict[str, Any]
) -> Row:
    row = Row(config, item.id, item.kind, item.language, search_ms=round(searched * 1000, 1))
    ranked = [h.hit.chunk.id for h in scored]
    row.ranked = [str(c) for c in ranked]
    row.top_score = round(scored[0].rerank_score, 4) if scored else None
    if item.labelled:
        grades: dict[Any, int] = dict(grades_for(item, corpus.chunks))
        row.hit1 = metrics.hit_at(ranked, grades, 1)
        row.hit5 = metrics.hit_at(ranked, grades, 5)
        row.rr = metrics.reciprocal_rank(ranked, grades)
        row.ndcg5 = round(metrics.ndcg_at(ranked, grades, 5), 4)
    forbidden = set(item.never_retrieve)
    foreign = tenants.get(item.never_retrieve_tenant) if item.never_retrieve_tenant else None
    row.violations = sum(
        1
        for h in scored
        if h.hit.document.title in forbidden or (foreign is not None and h.hit.chunk.tenant_id == foreign)
    )
    return row


async def run_grid(allow_extra: bool) -> dict[str, Any]:
    runtime, principals, corpus, items = await _prepare(allow_extra)
    services = runtime.services
    try:
        from leave_assistant.infrastructure.retrieval.chroma import ChromaSync, ChromaVectorIndex, connect

        client = connect(runtime.settings.chroma_url)
        sync = ChromaSync(client, services.uow_factory)
        synced = {
            slug: await sync.sync_tenant(await principals.tenant_id(slug))
            for slug in ("aegean", "brightwater")
        }
        indexes = {"pgvector": PgVectorIndex(), "chroma": ChromaVectorIndex(client)}
        targets = [
            i for i in items if i.kind in {"knowledge", "probe", "unanswerable", "boundary"} and i.question
        ]
        rows: list[Row] = []
        for reranker_key in (None, "A", "B"):
            cached = (
                CachingReranker(
                    CrossEncoderReranker(RERANKERS[reranker_key], threads=4, max_length=MAX_LENGTH)
                )
                if reranker_key
                else None
            )
            for config in [c for c in grid() if c.reranker == reranker_key]:
                service = _service(runtime, indexes[config.backend], cached, config.mode)
                for item in targets:
                    assert item.tenant and item.user and item.question
                    principal = await principals.get(item.tenant, item.user)
                    scored, searched = await _rank(service, principal, item.question, CANDIDATES)
                    rows.append(_score_row(config.key, item, scored, corpus, searched, principals.tenant_ids))
                sys.stdout.write(f"{config.key}: done\n")
                sys.stdout.flush()
            del cached
        summary = summarise(rows, corpus, synced)
        _write_grid(rows, summary)
        return summary
    finally:
        await runtime.aclose()


def _per_config(rows: list[Row], metric: str) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        value = getattr(row, metric)
        if value is not None:
            out.setdefault(row.config, []).append((row.item, float(value)))
    return {k: sorted(v) for k, v in out.items()}


def summarise(rows: list[Row], corpus: Corpus, synced: dict[str, int]) -> dict[str, Any]:
    baseline_key = Config(*BASELINE).key
    configs: dict[str, Any] = {}
    ndcg = _per_config(rows, "ndcg5")
    for key in [c.key for c in grid()]:
        mine = [r for r in rows if r.config == key]
        labelled = [r for r in mine if r.ndcg5 is not None]
        entry: dict[str, Any] = {"n": len(labelled), "boundary_violations": sum(r.violations for r in mine)}
        for metric in ("hit1", "hit5", "rr", "ndcg5"):
            values = [float(getattr(r, metric)) for r in labelled]
            interval = metrics.bootstrap_mean(values)
            entry[metric] = {
                "mean": round(interval.mean, 4),
                "low": round(interval.low, 4),
                "high": round(interval.high, 4),
            }
        searches = [r.search_ms for r in mine]
        entry["search_ms_p50"] = round(metrics.percentile(searches, 0.5), 1)
        entry["search_ms_p95"] = round(metrics.percentile(searches, 0.95), 1)
        if key != baseline_key:
            mine_n, base_n = dict(ndcg[key]), dict(ndcg[baseline_key])
            ids = sorted(mine_n)
            diff = metrics.paired_bootstrap([mine_n[i] for i in ids], [base_n[i] for i in ids])
            entry["ndcg5_vs_baseline"] = {
                "mean": round(diff.mean, 4),
                "low": round(diff.low, 4),
                "high": round(diff.high, 4),
            }
        configs[key] = entry
    by_language: dict[str, Any] = {}
    for key in [baseline_key, Config("pgvector", "A", "hybrid").key, Config("pgvector", None, "hybrid").key]:
        for language in ("el", "en"):
            values = [
                float(r.ndcg5)
                for r in rows
                if r.config == key and r.ndcg5 is not None and r.language == language
            ]
            if values:
                by_language[f"{key}|{language}"] = {
                    "n": len(values),
                    "ndcg5": round(sum(values) / len(values), 4),
                }
    calibration: dict[str, Any] = {}
    for reranker in ("A", "B"):
        key = Config("pgvector", reranker, "hybrid").key
        positives = [
            float(r.top_score)
            for r in rows
            if r.config == key and r.ndcg5 is not None and r.top_score is not None
        ]
        negatives = [
            float(r.top_score)
            for r in rows
            if r.config == key and r.kind == "unanswerable" and r.top_score is not None
        ]
        threshold = metrics.balanced_threshold(positives, negatives)
        at_current = {
            "tpr": round(sum(p >= 0.5 for p in positives) / len(positives), 3),
            "tnr": round(sum(n < 0.5 for n in negatives) / len(negatives), 3),
        }
        calibration[reranker] = {
            "tau": threshold.tau,
            "balanced_accuracy": round(threshold.balanced_accuracy, 3),
            "tpr": round(threshold.true_positive_rate, 3),
            "tnr": round(threshold.true_negative_rate, 3),
            "at_tau_0.5": at_current,
            "answerable_min": round(min(positives), 4),
            "unanswerable": {
                r.item: r.top_score for r in rows if r.config == key and r.kind == "unanswerable"
            },
        }
    return {
        "date": stamp(),
        "commit": git_commit(),
        "corpus_fingerprint": corpus.fingerprint,
        "corpus_documents": corpus.documents,
        "chroma_synced_chunks": synced,
        "candidates": CANDIDATES,
        "max_length": MAX_LENGTH,
        "leg_size": LEG_SIZE,
        "baseline": baseline_key,
        "configs": configs,
        "by_language": by_language,
        "calibration": calibration,
    }


def _write_grid(rows: list[Row], summary: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    prefix = RESULTS / f"{summary['date']}-retrieval-grid"
    with prefix.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "config",
                "item",
                "kind",
                "language",
                "hit1",
                "hit5",
                "rr",
                "ndcg5",
                "top_score",
                "violations",
                "search_ms",
                "ranked",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.config,
                    r.item,
                    r.kind,
                    r.language,
                    r.hit1,
                    r.hit5,
                    r.rr,
                    r.ndcg5,
                    r.top_score,
                    r.violations,
                    r.search_ms,
                    " ".join(r.ranked),
                ]
            )
    prefix.with_suffix(".json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Retrieval grid — {summary['date']} (commit {summary['commit']}, corpus {summary['corpus_fingerprint']})",
        "",
        f"Candidates {summary['candidates']} at {summary['max_length']} tokens, leg size {summary['leg_size']}; "
        f"baseline `{summary['baseline']}`. Means with 95 % bootstrap intervals over the labelled queries.",
        "",
        "| Configuration | n | hit@1 | hit@5 | MRR | nDCG@5 | ΔnDCG@5 vs baseline | boundary | search p50 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    def cell(entry: dict[str, Any]) -> str:
        return f"{entry['mean']:.3f} [{entry['low']:.3f}, {entry['high']:.3f}]"

    for key, entry in summary["configs"].items():
        delta = entry.get("ndcg5_vs_baseline")
        lines.append(
            f"| `{key}` | {entry['n']} | {cell(entry['hit1'])} | {cell(entry['hit5'])} | {cell(entry['rr'])} | "
            f"{cell(entry['ndcg5'])} | {cell(delta) if delta else '—'} | {entry['boundary_violations']} | {entry['search_ms_p50']} |"
        )
    lines += [
        "",
        "## τ calibration (pgvector + hybrid)",
        "",
        "| Reranker | τ | balanced accuracy | TPR | TNR | at τ = 0.5 (TPR / TNR) |",
        "|---|---|---|---|---|---|",
    ]
    for reranker, entry in summary["calibration"].items():
        lines.append(
            f"| {reranker} | {entry['tau']} | {entry['balanced_accuracy']} | {entry['tpr']} | {entry['tnr']} | "
            f"{entry['at_tau_0.5']['tpr']} / {entry['at_tau_0.5']['tnr']} |"
        )
    prefix.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- reranker runtimes (one child process per variant: memory and crashes stay isolated) ----------------------------


async def run_variant(model: str, backend: str, candidate_counts: Sequence[int]) -> dict[str, Any]:
    runtime, principals, corpus, items = await _prepare(allow_extra=False)
    try:
        base = _service(runtime, PgVectorIndex(), None, "hybrid", candidates=max(candidate_counts))
        labelled = [i for i in items if i.labelled]
        unanswerable = [i for i in items if i.kind == "unanswerable"]
        reranker = CrossEncoderReranker(RERANKERS[model], threads=4, max_length=MAX_LENGTH, backend=backend)  # type: ignore[arg-type]
        started = time.perf_counter()
        reranker.load()
        load_s = time.perf_counter() - started
        lists: dict[str, tuple[str, list[Any]]] = {}
        for item in [*labelled, *unanswerable]:
            assert item.tenant and item.user and item.question
            principal = await principals.get(item.tenant, item.user)
            bundle = await base.search(principal, item.question, k=max(candidate_counts))
            lists[item.id] = (bundle.query, bundle.hits)
        first = next(iter(lists.values()))
        reranker.score(
            first[0], [f"{h.document.title}: {h.chunk.content_llm_safe}" for h in first[1][:2]]
        )  # warm-up
        out: dict[str, Any] = {
            "model": model,
            "backend": backend,
            "load_s": round(load_s, 2),
            "by_candidates": {},
        }
        for n in candidate_counts:
            latencies: list[float] = []
            ndcgs: list[float] = []
            tops: dict[str, float] = {}
            for item in [*labelled, *unanswerable]:
                query, hits = lists[item.id]
                hits = hits[:n]
                passages = [f"{h.document.title}: {h.chunk.content_llm_safe}" for h in hits]
                t0 = time.perf_counter()
                scores = reranker.score(query, passages)
                latencies.append((time.perf_counter() - t0) * 1000)
                order = sorted(zip(hits, scores, strict=True), key=lambda pair: pair[1], reverse=True)
                if item.labelled:
                    grades: dict[Any, int] = dict(grades_for(item, corpus.chunks))
                    ndcgs.append(metrics.ndcg_at([h.chunk.id for h, _ in order], grades, 5))
                tops[item.id] = round(order[0][1], 4) if order else 0.0
            positives = [tops[i.id] for i in labelled]
            negatives = [tops[i.id] for i in unanswerable]
            threshold = metrics.balanced_threshold(positives, negatives)
            out["by_candidates"][str(n)] = {
                "ndcg5": round(sum(ndcgs) / len(ndcgs), 4),
                "ndcg5_items": dict(zip([i.id for i in labelled], [round(v, 4) for v in ndcgs], strict=True)),
                "p50_ms": round(statistics.median(latencies), 1),
                "p95_ms": round(metrics.percentile(latencies, 0.95), 1),
                "tau": threshold.tau,
                "balanced_accuracy": round(threshold.balanced_accuracy, 3),
            }
        out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        out["corpus_fingerprint"] = corpus.fingerprint
        return out
    finally:
        await runtime.aclose()


def run_variants(candidate_counts: Sequence[int]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for model in ("A", "B"):
        for backend in ("torch", "onnx", "onnx-int8"):
            started = time.perf_counter()
            proc = subprocess.run(
                [sys.executable, "-m", "leave_assistant.evaluation.retrieval", "runtime", "--model", model,
                 "--backend", backend, "--candidates", ",".join(map(str, candidate_counts))],
                capture_output=True, text=True,
            )  # fmt: skip
            wall = round(time.perf_counter() - started, 1)
            lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
            if proc.returncode == 0 and lines:
                entry = json.loads(lines[-1])
            else:
                entry = {
                    "model": model,
                    "backend": backend,
                    "error": f"exit {proc.returncode}: {proc.stderr[-600:]}",
                }
            entry["wall_s"] = wall
            results.append(entry)
            sys.stdout.write(f"{model}/{backend}: {'error' if 'error' in entry else 'ok'} in {wall}s\n")
            sys.stdout.flush()
    summary = {"date": stamp(), "commit": git_commit(), "max_length": MAX_LENGTH, "variants": results}
    RESULTS.mkdir(parents=True, exist_ok=True)
    prefix = RESULTS / f"{summary['date']}-reranker-runtimes"
    prefix.with_suffix(".json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Reranker runtimes — {summary['date']} (commit {summary['commit']})",
        "",
        "Candidates from pgvector + hybrid; latency = one `score()` call per query (warm), on the reference machine.",
        "",
        "| Model | Runtime | load s | peak RSS MB | candidates | nDCG@5 | p50 ms | p95 ms | τ | balanced acc. |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in results:
        if "error" in entry:
            lines.append(
                f"| {entry['model']} | {entry['backend']} | — | — | — | failed: {entry['error'][:120]} | | | | |"
            )
            continue
        for n, values in entry["by_candidates"].items():
            lines.append(
                f"| {entry['model']} | {entry['backend']} | {entry['load_s']} | {entry['peak_rss_mb']} | {n} | "
                f"{values['ndcg5']} | {values['p50_ms']} | {values['p95_ms']} | {values['tau']} | {values['balanced_accuracy']} |"
            )
    prefix.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M9 offline retrieval evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grid")
    g.add_argument(
        "--allow-extra", action="store_true", help="run although non-seed documents are retrievable"
    )
    r = sub.add_parser("runtimes")
    r.add_argument("--candidates", default="10,20")
    one = sub.add_parser("runtime")
    one.add_argument("--model", choices=sorted(RERANKERS), required=True)
    one.add_argument("--backend", choices=("torch", "onnx", "onnx-int8"), required=True)
    one.add_argument("--candidates", default="10,20")
    args = parser.parse_args(argv)
    if args.command == "grid":
        summary = asyncio.run(run_grid(args.allow_extra))
        sys.stdout.write(json.dumps({k: summary[k] for k in ("date", "commit", "corpus_fingerprint")}) + "\n")
    elif args.command == "runtimes":
        run_variants([int(n) for n in args.candidates.split(",")])
    else:
        result = asyncio.run(
            run_variant(args.model, args.backend, [int(n) for n in args.candidates.split(",")])
        )
        sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
