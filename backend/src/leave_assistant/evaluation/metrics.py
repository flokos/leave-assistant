"""Retrieval and statistics metrics (pure; doc 11 §7, docs/evaluation/README.md §2)."""

from __future__ import annotations

import math
import random
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass

SEED = 2026
RESAMPLES = 10_000


def hit_at(ranked: Sequence[Hashable], grades: Mapping[Hashable, int], k: int) -> float:
    return 1.0 if any(grades.get(c, 0) > 0 for c in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[Hashable], grades: Mapping[Hashable, int]) -> float:
    for position, candidate in enumerate(ranked, 1):
        if grades.get(candidate, 0) > 0:
            return 1.0 / position
    return 0.0


def ndcg_at(ranked: Sequence[Hashable], grades: Mapping[Hashable, int], k: int) -> float:
    """Graded nDCG@k with gain 2^g − 1; the ideal ranking uses every relevant chunk of the query."""

    def dcg(gains: Sequence[int]) -> float:
        return float(sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(gains[:k])))

    ideal = dcg(sorted((g for g in grades.values() if g > 0), reverse=True))
    if ideal == 0:
        return 0.0
    return dcg([grades.get(c, 0) for c in ranked]) / ideal


@dataclass(frozen=True, slots=True)
class Interval:
    mean: float
    low: float
    high: float

    def __str__(self) -> str:
        return f"{self.mean:.3f} [{self.low:.3f}, {self.high:.3f}]"


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    position = (len(sorted_values) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def percentile(values: Sequence[float], q: float) -> float:
    return _percentile(sorted(values), q)


def bootstrap_mean(
    values: Sequence[float], *, resamples: int = RESAMPLES, seed: int = SEED, alpha: float = 0.05
) -> Interval:
    """Percentile bootstrap interval of the mean (resampling queries with replacement)."""
    if not values:
        return Interval(float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return Interval(sum(values) / n, _percentile(means, alpha / 2), _percentile(means, 1 - alpha / 2))


def paired_bootstrap(
    challenger: Sequence[float],
    baseline: Sequence[float],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
    alpha: float = 0.05,
) -> Interval:
    """Interval of the mean per-query difference challenger − baseline (same queries, same order)."""
    if len(challenger) != len(baseline):
        raise ValueError("paired samples must have the same length")
    return bootstrap_mean(
        [c - b for c, b in zip(challenger, baseline, strict=True)],
        resamples=resamples,
        seed=seed,
        alpha=alpha,
    )


@dataclass(frozen=True, slots=True)
class Threshold:
    tau: float
    balanced_accuracy: float
    true_positive_rate: float
    true_negative_rate: float


def balanced_threshold(positives: Sequence[float], negatives: Sequence[float]) -> Threshold:
    """τ maximising balanced accuracy (positives ≥ τ, negatives < τ); among equal candidates the midpoint of the
    widest gap between consecutive observed scores."""
    if not positives or not negatives:
        raise ValueError("both answerable and unanswerable scores are needed")
    observed = sorted(set(positives) | set(negatives))
    candidates = (
        [observed[0]]
        + [(a + b) / 2 for a, b in zip(observed, observed[1:], strict=False)]
        + [observed[-1] + 1e-9]
    )
    best: tuple[float, float, Threshold] | None = None
    for tau in candidates:
        tpr = sum(p >= tau for p in positives) / len(positives)
        tnr = sum(n < tau for n in negatives) / len(negatives)
        accuracy = (tpr + tnr) / 2
        above = min((v for v in observed if v >= tau), default=tau)
        below = max((v for v in observed if v < tau), default=tau)
        gap = above - below
        key = (accuracy, gap)
        if best is None or key > (best[0], best[1]):
            best = (accuracy, gap, Threshold(round(tau, 4), accuracy, tpr, tnr))
    assert best is not None
    return best[2]
