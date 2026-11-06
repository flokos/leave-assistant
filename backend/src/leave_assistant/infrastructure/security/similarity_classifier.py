"""Similarity classifier (doc 08 §8.2): contrastive nearest-exemplar scoring on e5 embeddings.

e5-small puts every leave-related sentence in a narrow band (cosine 0.83–0.95 even for benign policy text versus
injection phrases), so a raw "max similarity to injection exemplars" cannot separate the classes. The score is
therefore the *margin* between the nearest injection exemplar and the nearest benign reference sentence
(`benign_v1.json`, curated HR/leave sentences), mapped linearly to 0–1 between `floor` and `ceiling`. Constants are
calibrated on the benign seed corpus (held out from both lists) — see worklog M5.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path

from leave_assistant.application.ports import EmbeddingModel

DEFAULT_PATH = Path(__file__).with_name("exemplars_v1.json")
BENIGN_PATH = Path(__file__).with_name("benign_v1.json")
USER_BENIGN_PATH = Path(__file__).with_name("benign_user_v1.json")  # M7: screening user input only
DEFAULT_FLOOR = 0.025  # margin at which a block starts to look like an injection (benign corpus max: +0.035)
DEFAULT_CEILING = 0.065  # margin from which the classifier is certain (paraphrased injections: +0.037…+0.14)


def load_exemplars(path: Path = DEFAULT_PATH) -> tuple[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data["version"]), [str(item["text"]) for item in data["exemplars"]]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b, strict=False)))


class SimilarityClassifier:
    def __init__(
        self,
        embedder: EmbeddingModel,
        *,
        path: Path = DEFAULT_PATH,
        benign_path: Path = BENIGN_PATH,
        floor: float = DEFAULT_FLOOR,
        ceiling: float = DEFAULT_CEILING,
    ) -> None:
        self._embedder = embedder
        self._path = path
        self._benign_path = benign_path
        self._floor = floor
        self._ceiling = ceiling

    @cached_property
    def _exemplars(self) -> tuple[str, list[list[float]], list[list[float]]]:
        version, texts = load_exemplars(self._path)
        _, benign = load_exemplars(self._benign_path)
        return version, self._embedder.embed_passages(texts), self._embedder.embed_passages(benign)

    @property
    def version(self) -> str:
        return self._exemplars[0]

    def margins(self, texts: Sequence[str]) -> list[tuple[float, float]]:
        """`(nearest injection exemplar, nearest benign reference)` cosines per text (used by the calibration test)."""
        if not texts:
            return []
        _, injection, benign = self._exemplars
        out: list[tuple[float, float]] = []
        for vector in self._embedder.embed_passages(list(texts)):
            out.append((max(_dot(vector, e) for e in injection), max(_dot(vector, b) for b in benign)))
        return out

    def calibrate(self, margin: float) -> float:
        return max(0.0, min(1.0, (margin - self._floor) / (self._ceiling - self._floor)))

    def score_blocks(self, texts: Sequence[str]) -> list[float]:
        return [round(self.calibrate(inj - ben), 4) for inj, ben in self.margins(texts)]
