"""Risk score and tiers (doc 08 §8.2): `max(heuristic, classifier, judge) + 0.1 × min(flagged blocks, 3)`, clipped."""

from __future__ import annotations

from dataclasses import dataclass

from leave_assistant.domain.documents.types import RiskTier


@dataclass(frozen=True, slots=True)
class RiskThresholds:
    low_max: float = 0.3  # score < low_max → low
    medium_max: float = 0.7  # low_max ≤ score < medium_max → medium; ≥ medium_max → high

    def validate(self) -> None:
        if not 0.0 < self.low_max < self.medium_max <= 1.0:
            raise ValueError("thresholds must satisfy 0 < low_max < medium_max <= 1")


def combine(heuristic: float, classifier: float | None, judge: float | None, flagged_blocks: int) -> float:
    detectors = [heuristic] + [s for s in (classifier, judge) if s is not None]
    base = max(detectors) if detectors else 0.0
    score = base + 0.1 * min(max(flagged_blocks, 0), 3) if base > 0 else 0.0
    return round(min(max(score, 0.0), 1.0), 4)


def tier_for(score: float, thresholds: RiskThresholds) -> RiskTier:
    if score >= thresholds.medium_max:
        return RiskTier.HIGH
    if score >= thresholds.low_max:
        return RiskTier.MEDIUM
    return RiskTier.LOW
