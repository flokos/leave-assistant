"""RAGAS scoring of the live answer samples (M9, `make eval-ragas`; docs/evaluation/README.md §2).

    python -m leave_eval.ragas_run [--date YYYY-MM-DD] [--repetitions 3]

Judge: Mistral `EVAL_JUDGE_MODEL` (default `mistral-large-4`; temperature 0; paced by `EVAL_JUDGE_RPS` for the free
plan's 20k tokens/min), with `mistral-embed` for answer relevancy; Medium and Small have no free quota (0 req/min).
The answers are pinned to `ministral-14b-2512` (`answers --providers mistral`), so the judge is a different and much
larger model of the same family; the shared-family bias is reported. Inputs are what the answer
model saw: the pseudonymised question, the context blocks and the LLM-side answer, plus the reference answer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from leave_eval.common import RESULTS, answer_files, load_samples, scorable

JUDGE_MODEL = "mistral-large-4"
METRICS = ("faithfulness", "answer_relevancy", "llm_context_precision_with_reference", "context_recall")


def merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    """Token usage summed field by field, nested `*_tokens_details` included; anything else keeps the first value."""
    for key, value in usage.items():
        if key not in total or total[key] is None:
            total[key] = dict(value) if isinstance(value, dict) else value
        elif isinstance(value, dict) and isinstance(total[key], dict):
            merge_usage(total[key], value)
        elif isinstance(value, int | float) and isinstance(total[key], int | float):
            total[key] += value
    return total


def judge_chat(**kwargs: Any) -> Any:
    """ChatMistralAI whose multi-generation calls (answer relevancy asks for 3) survive nested usage: langchain-mistralai
    1.1.6 adds the usage dicts with `+=` and fails on Mistral's `prompt_tokens_details`."""
    from langchain_mistralai import ChatMistralAI

    class JudgeChat(ChatMistralAI):
        def _combine_llm_outputs(self, llm_outputs: list[dict[str, Any] | None]) -> dict[str, Any]:
            total: dict[str, Any] = {}
            for output in llm_outputs:
                if output and output.get("token_usage"):
                    merge_usage(total, output["token_usage"])
            return {"token_usage": total, "model_name": self.model}

    return JudgeChat(**kwargs)


def to_sample(sample: dict[str, Any]) -> Any:
    from ragas import SingleTurnSample

    return SingleTurnSample(
        user_input=sample.get("question_llm_safe") or sample["question"],
        retrieved_contexts=list(sample["contexts"]),
        response=sample["answer_llm_safe"],
        reference=sample["reference"],
    )


def bootstrap(values: list[float], resamples: int = 10_000, seed: int = 2026) -> tuple[float, float, float]:
    clean = [v for v in values if not math.isnan(v)]
    if not clean:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(clean)
    means = sorted(sum(clean[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return sum(clean) / n, means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def item_means(summary: dict[str, Any], metric: str) -> dict[str, float]:
    """Per item: the mean over repetitions (NaN-free items only)."""
    out = {}
    for item, values in summary["items"].items():
        clean = [x for x in values[metric] if not math.isnan(x)]
        if clean:
            out[item] = statistics.fmean(clean)
    return out


def paired(baseline: dict[str, Any], challenger: dict[str, Any]) -> dict[str, Any]:
    """Challenger − baseline per metric over the items both scored: mean difference with a 95 % bootstrap interval."""
    result: dict[str, Any] = {}
    for metric in METRICS:
        a, b = item_means(baseline, metric), item_means(challenger, metric)
        common = sorted(a.keys() & b.keys())
        mean, low, high = bootstrap([b[i] - a[i] for i in common])
        result[metric] = {
            "delta": round(mean, 4),
            "low": round(low, 4),
            "high": round(high, 4),
            "n": len(common),
        }
    return result


def load_checkpoint(path: Path, config: str) -> dict[int, dict[str, dict[str, float]]]:
    """Finished repetitions of `config` from earlier (interrupted) runs: {rep: {item: {metric: value}}}."""
    done: dict[int, dict[str, dict[str, float]]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line) if line.strip() else None
            if entry and entry["config"] == config:
                done[int(entry["rep"])] = entry["items"]
    return done


def score(
    path_samples: list[dict[str, Any]], repetitions: int, checkpoint: Path | None = None
) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from langchain_core.rate_limiters import InMemoryRateLimiter
        from langchain_mistralai import MistralAIEmbeddings
        from ragas import EvaluationDataset, RunConfig, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
            ResponseRelevancy,
        )

    model = os.environ.get("EVAL_JUDGE_MODEL", JUDGE_MODEL)
    # the free plan meters mistral-large-4 at 20k tokens/min: pace the judge so 429s don't turn into NaN scores
    limiter = InMemoryRateLimiter(requests_per_second=float(os.environ.get("EVAL_JUDGE_RPS", "0.12")))
    judge = LangchainLLMWrapper(
        judge_chat(model=model, temperature=0, max_retries=8, timeout=180, rate_limiter=limiter)
    )
    embeddings = LangchainEmbeddingsWrapper(MistralAIEmbeddings(model="mistral-embed"))
    samples = [s for s in path_samples if scorable(s)]
    dataset = EvaluationDataset(samples=[to_sample(s) for s in samples])
    per_item: dict[str, dict[str, list[float]]] = {s["id"]: {m: [] for m in METRICS} for s in samples}
    config = path_samples[0]["config"] if path_samples else ""
    finished = load_checkpoint(checkpoint, config) if checkpoint else {}
    for rep in range(repetitions):
        if rep in finished:  # a repetition takes ~1 h on the free plan: never redo one
            for item, values in finished[rep].items():
                for metric in METRICS:
                    per_item[item][metric].append(float(values[metric]))
            sys.stdout.write(f"  repetition {rep + 1}/{repetitions} restored from {checkpoint}\n")
            continue
        result = evaluate(
            dataset,
            metrics=[
                Faithfulness(),
                ResponseRelevancy(),
                LLMContextPrecisionWithReference(),
                LLMContextRecall(),
            ],
            llm=judge,
            embeddings=embeddings,
            run_config=RunConfig(max_workers=2, timeout=900, max_retries=8, max_wait=90, seed=42 + rep),
            show_progress=False,
        )
        frame = result.to_pandas()
        scores: dict[str, dict[str, float]] = {}
        for sample, (_, row) in zip(samples, frame.iterrows(), strict=True):
            scores[sample["id"]] = {}
            for metric in METRICS:
                value = row.get(metric)
                scores[sample["id"]][metric] = float(value) if value is not None else float("nan")
                per_item[sample["id"]][metric].append(scores[sample["id"]][metric])
        if checkpoint:
            with checkpoint.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"config": config, "rep": rep, "judge": model, "items": scores}) + "\n"
                )
        sys.stdout.write(f"  repetition {rep + 1}/{repetitions} done\n")
        sys.stdout.flush()
    summary: dict[str, Any] = {"judge": model, "embeddings": "mistral-embed", "repetitions": repetitions}
    summary["scored"] = len(samples)
    summary["not_scored"] = {s["id"]: s.get("outcome") for s in path_samples if not scorable(s)}
    for metric in METRICS:
        item_means = [
            statistics.fmean(v[metric]) if any(not math.isnan(x) for x in v[metric]) else float("nan")
            for v in per_item.values()
        ]
        rep_means = [
            statistics.fmean([v[metric][r] for v in per_item.values() if not math.isnan(v[metric][r])])
            for r in range(repetitions)
            if any(not math.isnan(v[metric][r]) for v in per_item.values())
        ]
        mean, low, high = bootstrap(item_means)
        summary[metric] = {
            "mean": round(mean, 4),
            "low": round(low, 4),
            "high": round(high, 4),
            "repetition_sd": round(statistics.pstdev(rep_means), 4) if len(rep_means) > 1 else 0.0,
            "nan": sum(math.isnan(x) for x in item_means),
        }
    summary["items"] = {k: {m: [round(x, 4) for x in v[m]] for m in METRICS} for k, v in per_item.items()}
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAGAS scoring of the M9 live answers")
    parser.add_argument("--date", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--baseline", default="pgvector+B+hybrid+onnx", help="configuration the others are compared to"
    )
    args = parser.parse_args(argv)
    files = answer_files(args.date)
    if not files:
        raise SystemExit(f"no answer samples for {args.date} in {RESULTS}")
    out: dict[str, Any] = {"date": args.date, "configs": {}}
    for path in files:
        samples = load_samples(path)
        config = samples[0]["config"] if samples else path.stem
        sys.stdout.write(f"{config}: {sum(scorable(s) for s in samples)}/{len(samples)} scorable\n")
        out["configs"][config] = score(
            samples, args.repetitions, RESULTS / f"{args.date}-ragas.partial.jsonl"
        )
    if args.baseline in out["configs"]:
        out["paired"] = {
            config: paired(out["configs"][args.baseline], summary)
            for config, summary in out["configs"].items()
            if config != args.baseline
        }
    target = RESULTS / f"{args.date}-ragas.json"
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        f"# RAGAS — {args.date}",
        "",
        "| Configuration | metric | mean [95 % CI over items] | sd over repetitions | n |",
        "|---|---|---|---|---|",
    ]
    for config, summary in out["configs"].items():
        for metric in METRICS:
            m = summary[metric]
            lines.append(
                f"| `{config}` | {metric} | {m['mean']} [{m['low']}, {m['high']}] | {m['repetition_sd']} | {summary['scored'] - m['nan']} |"
            )
    for config, deltas in out.get("paired", {}).items():
        lines += [
            "",
            f"## `{config}` − `{args.baseline}` (paired, items scored in both)",
            "",
            "| metric | Δ mean [95 % CI] | n |",
            "|---|---|---|",
        ]
        lines += [f"| {m} | {d['delta']} [{d['low']}, {d['high']}] | {d['n']} |" for m, d in deltas.items()]
    target.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write(f"written {target.name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
