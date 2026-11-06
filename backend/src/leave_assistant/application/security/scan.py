"""InjectionScanService: heuristics + similarity classifier (+ judge in M6) → risk assessment and user-input screening."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from leave_assistant.application.ports import LlmJudge, RiskAssessment, SimilarityClassifier
from leave_assistant.domain.security.heuristics import scan_blocks, score_text
from leave_assistant.domain.security.scoring import RiskThresholds, combine, tier_for

CLASSIFIER_FLAG_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class InputAssessment:
    score: float
    blocked: bool
    families: tuple[str, ...]


def excerpt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:32]


class InjectionScanService:
    def __init__(
        self,
        classifier: SimilarityClassifier | None = None,
        judge: LlmJudge | None = None,
        *,
        input_classifier: SimilarityClassifier | None = None,
    ) -> None:
        self._classifier = classifier
        self._judge = judge
        # what users type is screened against its own benign set (their leave commands are not forgery, M7)
        self._input_classifier = input_classifier or classifier

    @property
    def classifier_version(self) -> str | None:
        return self._classifier.version if self._classifier is not None else None

    def assess_blocks(
        self, texts: Sequence[str], *, thresholds: RiskThresholds, structural_flags: Sequence[str] = ()
    ) -> RiskAssessment:
        """Blocking (the classifier embeds); the judge is reserved for the medium band once a provider exists (M6)."""
        heuristic = scan_blocks(texts)
        classifier_scores: list[float] | None = None
        if self._classifier is not None and texts:
            classifier_scores = self._classifier.score_blocks(texts)
        findings: list[dict[str, Any]] = [
            {
                "detector": "heuristic",
                "family": flag.family,
                "block": flag.block_index,
                "score": flag.weight,
                "excerpt": flag.excerpt,
            }
            for flag in heuristic.flags
        ]
        flagged: set[int] = {flag.block_index for flag in heuristic.flags}
        classifier_score: float | None = None
        if classifier_scores:
            classifier_score = max(classifier_scores)
            for index, value in enumerate(classifier_scores):
                if value >= CLASSIFIER_FLAG_THRESHOLD:
                    flagged.add(index)
                    findings.append(
                        {
                            "detector": "classifier",
                            "family": "similarity",
                            "block": index,
                            "score": round(value, 3),
                            "excerpt": texts[index][:160],
                        }
                    )
        for flag in structural_flags:
            findings.append(
                {"detector": "structural", "family": flag, "block": None, "score": 1.0, "excerpt": ""}
            )
        score = combine(heuristic.score, classifier_score, None, len(flagged))
        if structural_flags:
            score = 1.0  # active content is a hard flag (doc 08 §8.2)
        chunk_flags: dict[int, tuple[str, ...]] = {}
        for finding in findings:
            block = finding["block"]
            if isinstance(block, int):
                chunk_flags[block] = (*chunk_flags.get(block, ()), str(finding["family"]))
        return RiskAssessment(
            score=score,
            tier=tier_for(score, thresholds),
            findings=tuple(findings),
            chunk_flags=chunk_flags,
            heuristic_score=heuristic.score,
            classifier_score=classifier_score,
            structural_flags=tuple(structural_flags),
        )

    def screen_input(self, text: str, *, block_threshold: float) -> InputAssessment:
        """User-input screening (doc 08 §8.3): heuristics + classifier; the chat ingress (M6/M7) acts on `blocked`."""
        result = score_text(text)
        classifier_score = (
            self._input_classifier.score_blocks([text])[0]
            if self._input_classifier is not None and text
            else None
        )
        score = combine(result.score, classifier_score, None, 1 if result.flags else 0)
        families = tuple(sorted({f.family for f in result.flags}))
        return InputAssessment(score=score, blocked=score >= block_threshold, families=families)
