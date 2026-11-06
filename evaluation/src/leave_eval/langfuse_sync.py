"""Langfuse: the dataset `leave-eval-v1` and one experiment per configuration (M9; doc 11 §8, ADR-0022/0023).

    python -m leave_eval.langfuse_sync [--date YYYY-MM-DD]

Items are upserted with their dataset ids. Each experiment's task looks up the answer the harness produced for that
item and configuration (the answers come from the real chat path, traced separately under the conversation id); the
evaluators attach the RAGAS scores (mean over the judge repetitions) and the outcome, so Langfuse shows scores per item
and per run next to the dataset.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import UTC, datetime
from typing import Any

from leave_eval.common import DATASET_NAME, RESULTS, answer_files, dataset_items, load_samples

METRICS = ("faithfulness", "answer_relevancy", "llm_context_precision_with_reference", "context_recall")


def item_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Dataset item: question, tenant, user and language as input; outcome, reference and labels as expected output."""
    return {
        "id": item["id"],
        "input": {
            k: item.get(k) for k in ("question", "tenant", "user", "language") if item.get(k) is not None
        },
        "expected_output": {
            k: v
            for k, v in {
                "outcome": item.get("outcome") or item.get("expected_outcome"),
                "reference": item.get("reference"),
                "relevant": [r["doc"] for r in item.get("relevant", [])] or None,
                "expected_tools": item.get("expected_tools"),
                "expected": item.get("expected"),
            }.items()
            if v is not None
        },
        "metadata": {k: item.get(k) for k in ("kind", "category", "type", "test") if item.get(k) is not None},
    }


def main(argv: list[str] | None = None) -> int:
    from langfuse import Evaluation, Langfuse

    parser = argparse.ArgumentParser(description="Langfuse dataset and experiments for M9")
    parser.add_argument("--date", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    client = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("EVAL_LANGFUSE_HOST", "http://localhost:3000"),
    )
    if not client.auth_check():
        raise SystemExit("Langfuse rejected the project keys")
    client.create_dataset(
        name=DATASET_NAME, description="doc 11 §6 scenarios + M9 retrieval probes", metadata={"version": 1}
    )
    items = dataset_items()
    for item in items:
        payload = item_payload(item)
        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=payload["id"],
            input=payload["input"],
            expected_output=payload["expected_output"],
            metadata=payload["metadata"],
        )
    sys.stdout.write(f"dataset {DATASET_NAME}: {len(items)} items upserted\n")

    ragas_path = RESULTS / f"{args.date}-ragas.json"
    ragas = json.loads(ragas_path.read_text(encoding="utf-8")) if ragas_path.exists() else {"configs": {}}
    dataset = client.get_dataset(DATASET_NAME)
    runs: dict[str, Any] = {}
    for path in answer_files(args.date):
        samples = {s["id"]: s for s in load_samples(path)}
        if not samples:
            continue
        config = next(iter(samples.values()))["config"]
        scores = ragas["configs"].get(config, {}).get("items", {})

        def task(*, item: Any, samples: dict[str, Any] = samples, **_: Any) -> Any:
            sample = samples[item.id]
            return {
                "answer": sample["answer"],
                "outcome": sample["outcome"],
                "citations": sample["citations"],
            }

        def evaluator(
            *,
            input: Any,
            output: Any,
            expected_output: Any,
            metadata: Any,
            scores: dict[str, Any] = scores,
            **kwargs: Any,
        ) -> list[Any]:
            item_id = output.get("id") if isinstance(output, dict) else None
            result = [Evaluation(name="answered", value=1.0 if output.get("outcome") == "answered" else 0.0)]
            for metric, values in (scores.get(item_id) or {}).items():
                clean = [v for v in values if not math.isnan(v)]
                if clean:
                    result.append(
                        Evaluation(
                            name=metric,
                            value=sum(clean) / len(clean),
                            comment=f"{len(clean)} judge repetitions",
                        )
                    )
            return result

        data = [i for i in dataset.items if i.id in samples]
        result = dataset.run_experiment(
            name=f"rag-{config}",
            run_name=f"{args.date}-{config}",
            description="M9 live answers (Gemini first) scored with RAGAS (judge Mistral)",
            task=task,
            evaluators=[evaluator],
            max_concurrency=4,
            metadata={
                "config": config,
                "date": args.date,
                "judge": ragas["configs"].get(config, {}).get("judge", ""),
            },
        )
        runs[config] = {
            "items": len(data),
            "run": f"{args.date}-{config}",
            "dataset_run_id": getattr(result, "dataset_run_id", None),
        }
        sys.stdout.write(f"experiment {config}: {len(data)} items\n")
    client.flush()
    (RESULTS / f"{args.date}-langfuse.json").write_text(
        json.dumps({"dataset": DATASET_NAME, "items": len(items), "runs": runs}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
