"""Shared paths and loaders for the evaluation project (RAGAS + Langfuse; doc 11 §8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "evaluation" / "datasets" / "leave-eval-v1.yaml"
RESULTS = REPO / "docs" / "evaluation" / "results"
DATASET_NAME = "leave-eval-v1"


def dataset_items() -> list[dict[str, Any]]:
    return list(yaml.safe_load(DATASET.read_text(encoding="utf-8"))["items"])


def answer_files(date: str) -> list[Path]:
    return sorted(RESULTS.glob(f"{date}-answers-*.jsonl"))


def load_samples(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scorable(sample: dict[str, Any]) -> bool:
    """RAGAS needs an answer grounded in contexts; refusals and errors are reported separately."""
    return (
        bool(sample.get("answer_llm_safe"))
        and bool(sample.get("contexts"))
        and sample.get("outcome") == "answered"
    )
