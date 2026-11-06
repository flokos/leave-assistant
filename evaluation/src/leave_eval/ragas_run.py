"""RAGAS scoring of the live answer samples (M9, `make eval-ragas`; docs/evaluation/README.md §2).

    python -m leave_eval.ragas_run [--date YYYY-MM-DD] [--repetitions 3]

Judge: Mistral (`EVAL_JUDGE_MODEL`, default ministral-14b-2512, temperature 0) with `mistral-embed` for answer
relevancy — a different provider from the primary answer model (Gemini). Inputs are what the answer model saw: the
pseudonymised question, the context blocks and the LLM-side answer, plus the reference answer from the dataset.
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
from typing import Any

from leave_eval.common import RESULTS, answer_files, load_samples, scorable

METRICS = ("faithfulness", "answer_relevancy", "llm_context_precision_with_reference", "context_recall")


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


def score(path_samples: list[dict[str, Any]], repetitions: int) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from langchain_mistralai import ChatMistralAI, MistralAIEmbeddings
        from ragas import EvaluationDataset, RunConfig, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            Faithfulness,
            LLMContextPrecisionWithReference,
            LLMContextRecall,
            ResponseRelevancy,
        )

    model = os.environ.get("EVAL_JUDGE_MODEL", "ministral-14b-2512")
    judge = LangchainLLMWrapper(ChatMistralAI(model=model, temperature=0, max_retries=6, timeout=120))
    embeddings = LangchainEmbeddingsWrapper(MistralAIEmbeddings(model="mistral-embed"))
    samples = [s for s in path_samples if scorable(s)]
    dataset = EvaluationDataset(samples=[to_sample(s) for s in samples])
    per_item: dict[str, dict[str, list[float]]] = {s["id"]: {m: [] for m in METRICS} for s in samples}
    for rep in range(repetitions):
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
            run_config=RunConfig(max_workers=2, timeout=180, max_retries=8, max_wait=60, seed=42 + rep),
            show_progress=False,
        )
        frame = result.to_pandas()
        for sample, (_, row) in zip(samples, frame.iterrows(), strict=True):
            for metric in METRICS:
                value = row.get(metric)
                per_item[sample["id"]][metric].append(float(value) if value is not None else float("nan"))
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
    args = parser.parse_args(argv)
    files = answer_files(args.date)
    if not files:
        raise SystemExit(f"no answer samples for {args.date} in {RESULTS}")
    out: dict[str, Any] = {"date": args.date, "configs": {}}
    for path in files:
        samples = load_samples(path)
        config = samples[0]["config"] if samples else path.stem
        sys.stdout.write(f"{config}: {sum(scorable(s) for s in samples)}/{len(samples)} scorable\n")
        out["configs"][config] = score(samples, args.repetitions)
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
    target.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write(f"written {target.name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
