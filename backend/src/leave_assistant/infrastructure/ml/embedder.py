"""E5Embedder (ADR-0013): sentence-transformers on CPU, normalised fp32 vectors, prefix discipline in one place."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from functools import cached_property
from typing import Any

PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "


class E5Embedder:
    def __init__(
        self, model: str, revision: str | None = None, *, batch_size: int = 32, threads: int | None = None
    ) -> None:
        self._model_name = model
        self._revision = revision
        self._batch_size = batch_size
        self._threads = threads
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"{self._model_name}@{self._revision or 'main'}"

    @property
    def dimension(self) -> int:
        return 384

    @cached_property
    def _model(self) -> Any:
        import torch
        from sentence_transformers import SentenceTransformer

        if self._threads:
            torch.set_num_threads(self._threads)
        model = SentenceTransformer(self._model_name, revision=self._revision, device="cpu")
        # e5 uses mean pooling: BERT's pooler (dense + tanh) is computed and discarded. Dropping it saves work and
        # keeps MKL's vector tanh — the kernel behind the SIGILLs on AVX-only CPUs (R-27, worklog M6) — off this path.
        auto_model = getattr(model[0], "auto_model", None)
        if auto_model is not None and getattr(auto_model, "pooler", None) is not None:
            auto_model.pooler = None
        from leave_assistant.infrastructure.ml.safe_tanh import avoid_mkl_tanh

        avoid_mkl_tanh(model)  # belt and braces: nothing should be left once the pooler is gone
        model.max_seq_length = 512
        return model

    def load(self) -> None:
        _ = self._model

    @staticmethod
    def _check(texts: Sequence[str]) -> None:
        for text in texts:
            if text.startswith((PASSAGE_PREFIX, QUERY_PREFIX)):
                raise ValueError("texts must not be prefixed by the caller; the embedder adds the e5 prefix")

    def _encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:  # torch modules are not safe for concurrent encode calls from several threads
            vectors = self._model.encode(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return [[float(x) for x in row] for row in vectors]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        self._check(texts)
        if not texts:
            return []
        return self._encode([PASSAGE_PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        self._check([text])
        return self._encode([QUERY_PREFIX + text])[0]
