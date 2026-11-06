"""Retrieval and statistics metrics (docs/evaluation/README.md §2) against hand-computed values."""

from __future__ import annotations

import math

import pytest

from leave_assistant.evaluation.metrics import (
    balanced_threshold,
    bootstrap_mean,
    hit_at,
    ndcg_at,
    paired_bootstrap,
    percentile,
    reciprocal_rank,
)

GRADES = {"a": 2, "b": 1, "c": 2}


def test_hit_and_reciprocal_rank() -> None:
    assert hit_at(["x", "a"], GRADES, 1) == 0.0 and hit_at(["x", "a"], GRADES, 5) == 1.0
    assert reciprocal_rank(["x", "y", "b"], GRADES) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x", "y"], GRADES) == 0.0


def test_graded_ndcg_matches_the_formula() -> None:
    # ranking x, a, b: DCG = 0 + 3/log2(3) + 1/log2(4); ideal a, c, b: 3/log2(2) + 3/log2(3) + 1/log2(4)
    dcg = 3 / math.log2(3) + 1 / math.log2(4)
    ideal = 3 + 3 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at(["x", "a", "b"], GRADES, 5) == pytest.approx(dcg / ideal)
    assert ndcg_at(["a", "c", "b"], GRADES, 5) == pytest.approx(1.0)
    assert ndcg_at(["x"], {}, 5) == 0.0
    assert ndcg_at(["b", "a"], GRADES, 1) == pytest.approx(1 / 3)  # cut-off at k: only b (gain 1) vs ideal 3


def test_bootstrap_is_deterministic_and_brackets_the_mean() -> None:
    values = [0.0, 1.0, 1.0, 0.5, 0.75, 1.0, 0.25]
    first, second = bootstrap_mean(values), bootstrap_mean(values)
    assert first == second
    assert first.low <= first.mean <= first.high and first.mean == pytest.approx(sum(values) / len(values))
    assert bootstrap_mean([1.0] * 5).low == 1.0


def test_paired_bootstrap_detects_a_consistent_improvement_only() -> None:
    baseline = [0.5, 0.6, 0.7, 0.4, 0.5, 0.6, 0.55, 0.65]
    better = [b + 0.1 for b in baseline]
    noisy = [b + (0.1 if i % 2 else -0.1) for i, b in enumerate(baseline)]
    assert paired_bootstrap(better, baseline).low > 0
    interval = paired_bootstrap(noisy, baseline)
    assert interval.low < 0 < interval.high
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [1.0, 2.0])


def test_balanced_threshold_separates_scores() -> None:
    result = balanced_threshold([0.9, 0.8, 0.7], [0.1, 0.3, 0.5])
    assert 0.5 < result.tau <= 0.7 and result.balanced_accuracy == 1.0
    overlapping = balanced_threshold([0.9, 0.4], [0.5, 0.1])
    assert overlapping.balanced_accuracy == pytest.approx(0.75)
    assert percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
