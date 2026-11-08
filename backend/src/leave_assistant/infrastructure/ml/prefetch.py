"""Download the models once into the cache volume (`make models`): e5, the deployed reranker (and its ONNX export),
the Docling layout/TableFormer models. `--all` (`make models ALL=1`) also fetches the reserve reranker candidate.

M10: each model is released after its check, and `make models` runs in a one-off container — the clean-clone test
found that loading every model inside the running api exceeded its memory limit and the api was OOM-killed."""

from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

from leave_assistant.config import get_settings
from leave_assistant.infrastructure.ml.embedder import E5Embedder
from leave_assistant.infrastructure.ml.tokenizer import E5Tokenizer

RERANKERS = ("BAAI/bge-reranker-v2-m3", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")


def main() -> int:
    settings = get_settings()
    started = time.monotonic()
    tokenizer = E5Tokenizer(settings.embedding_model, settings.embedding_model_revision)
    sys.stdout.write(f"tokenizer: {tokenizer.count('warm-up')} token(s) for 'warm-up'\n")
    embedder = E5Embedder(settings.embedding_model, settings.embedding_model_revision)
    vector = embedder.embed_query("warm-up")
    sys.stdout.write(f"embedding model {embedder.model_id}: {len(vector)}-d\n")
    from leave_assistant.infrastructure.ml.reranker import CrossEncoderReranker

    del embedder
    gc.collect()
    every = "--all" in sys.argv[1:]
    models = dict.fromkeys([settings.reranker_model, *(RERANKERS if every else ())])  # ADR-0014 candidates
    for model in models:
        reranker = CrossEncoderReranker(model)
        sys.stdout.write(
            f"reranker {model}: {reranker.score('άδεια', ['κανονική άδεια', 'printer toner'])}\n"
        )
        del reranker
        gc.collect()
    if settings.reranker_backend != "torch":  # M9: export the ONNX graph once into $HF_HOME/onnx
        onnx = CrossEncoderReranker(settings.reranker_model, backend=settings.reranker_backend)
        sys.stdout.write(
            f"reranker {onnx.model_id}: {onnx.score('άδεια', ['κανονική άδεια', 'printer toner'])}\n"
        )
        del onnx
        gc.collect()
    artifacts = settings.docling_artifacts_dir
    if artifacts is not None:
        from docling.utils.model_downloader import download_models

        path = download_models(
            output_dir=Path(artifacts),
            progress=False,
            with_layout=True,
            with_tableformer=True,
            with_code_formula=False,
            with_picture_classifier=False,
            with_rapidocr=False,
        )
        sys.stdout.write(f"docling models: {path}\n")
    sys.stdout.write(f"done in {time.monotonic() - started:.1f} s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
