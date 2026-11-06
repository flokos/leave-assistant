"""Cross-encoder reranker (ADR-0014): sentence-transformers `CrossEncoder`, sigmoid scores in [0, 1], loaded lazily.
Candidate A `BAAI/bge-reranker-v2-m3` and candidate B `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` are both
selectable by `RERANKER_MODEL`; the final choice is the M9 evaluation."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from functools import cached_property
from typing import Any

MAX_LENGTH = 512


class CrossEncoderReranker:
    def __init__(
        self, model: str, *, threads: int = 2, revision: str | None = None, max_length: int = MAX_LENGTH
    ) -> None:
        self._model_name = model
        self._max_length = max_length
        self._revision = revision
        self._threads = threads
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"{self._model_name}@{self._revision}" if self._revision else self._model_name

    @cached_property
    def _model(self) -> Any:
        import torch
        from sentence_transformers import CrossEncoder

        from leave_assistant.infrastructure.ml.safe_tanh import avoid_mkl_tanh

        torch.set_num_threads(self._threads)
        model = CrossEncoder(
            self._model_name,
            revision=self._revision,
            max_length=self._max_length,
            device="cpu",
            activation_fn=torch.nn.Sigmoid(),
        )
        avoid_mkl_tanh(
            model.model
        )  # the classification head's tanh crashed with SIGILL on AVX-only CPUs (R-27)
        return model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        with self._lock:
            scores = self._model.predict(
                [(query, p) for p in passages], batch_size=16, show_progress_bar=False
            )
        return [float(s) for s in scores]
