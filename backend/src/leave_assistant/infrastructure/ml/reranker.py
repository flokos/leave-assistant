"""Cross-encoder reranker (ADR-0014): sentence-transformers `CrossEncoder`, sigmoid scores in [0, 1], loaded lazily.
Candidate A `BAAI/bge-reranker-v2-m3` and candidate B `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` are both
selectable by `RERANKER_MODEL`; the runtime by `RERANKER_BACKEND` (M9): PyTorch fp32, ONNX fp32 or ONNX dynamic int8.
ONNX models are exported once into `$HF_HOME/onnx/<model>` (`make models`) with the sigmoid form of tanh (R-27)."""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

MAX_LENGTH = 512
Backend = Literal["torch", "onnx", "onnx-int8"]
FP32_FILE = "onnx/model.onnx"
INT8_CONFIG = "avx2"  # the closest dynamic-int8 configuration for AVX-only CPUs (none exists for plain AVX)
INT8_FILE = f"onnx/model_qint8_{INT8_CONFIG}.onnx"


def onnx_root() -> Path:
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(home) / "onnx"


def export_onnx(
    model: str, *, revision: str | None, quantize: bool, root: Path | None = None
) -> tuple[Path, str]:
    """Exports (once) the ONNX fp32 graph and, if asked, its dynamic int8 quantisation; returns (dir, file)."""
    from sentence_transformers import CrossEncoder

    from leave_assistant.infrastructure.ml.safe_tanh import patched_tanh

    target = (root or onnx_root()) / model.replace("/", "--")
    if not (target / FP32_FILE).exists():
        with patched_tanh():
            exported = CrossEncoder(model, revision=revision, backend="onnx", device="cpu")
            exported.save_pretrained(str(target))
    if not quantize:
        return target, FP32_FILE
    if not (target / INT8_FILE).exists():
        from sentence_transformers.backend import export_dynamic_quantized_onnx_model

        fp32 = CrossEncoder(str(target), backend="onnx", device="cpu", model_kwargs={"file_name": FP32_FILE})
        export_dynamic_quantized_onnx_model(fp32, INT8_CONFIG, str(target))
    return target, INT8_FILE


class CrossEncoderReranker:
    def __init__(
        self,
        model: str,
        *,
        threads: int = 2,
        revision: str | None = None,
        max_length: int = MAX_LENGTH,
        backend: Backend = "torch",
    ) -> None:
        self._model_name = model
        self._max_length = max_length
        self._revision = revision
        self._threads = threads
        self._backend: Backend = backend
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        base = f"{self._model_name}@{self._revision}" if self._revision else self._model_name
        return base if self._backend == "torch" else f"{base}[{self._backend}]"

    @property
    def backend(self) -> Backend:
        return self._backend

    @cached_property
    def _model(self) -> Any:
        import torch
        from sentence_transformers import CrossEncoder

        torch.set_num_threads(self._threads)
        if self._backend != "torch":
            import onnxruntime

            path, file_name = export_onnx(
                self._model_name, revision=self._revision, quantize=self._backend == "onnx-int8"
            )
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = self._threads
            return CrossEncoder(
                str(path),
                backend="onnx",
                device="cpu",
                max_length=self._max_length,
                activation_fn=torch.nn.Sigmoid(),
                model_kwargs={"file_name": file_name, "session_options": options},
            )

        from leave_assistant.infrastructure.ml.safe_tanh import avoid_mkl_tanh

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

    def load(self) -> None:
        _ = self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        with self._lock:
            scores = self._model.predict(
                [(query, p) for p in passages], batch_size=16, show_progress_bar=False
            )
        return [float(s) for s in scores]
