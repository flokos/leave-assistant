# ADR-0026 — Python 3.14 and a resolver-verified, uv-locked dependency set

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0013, ADR-0015

## Context
The brief asks to check current versions and compatibility (§15.6). The naive "latest of each" set is not mutually
compatible; the review of this package required the latest versions *that work together*.

## Problem
Pick the newest Python and library versions that resolve and install together on the reference machine, and keep
them pinned reproducibly.

## Decision
- **Python 3.14** (`python:3.14-slim`): the intersection of all `requires_python` ranges is 3.11–3.14 (Presidio
  caps at `<3.15`); every compiled dependency ships cp314 wheels; `uv lock` produces an identical resolution for
  3.13, which is the documented fallback.
- Resolver-verified set (uv 0.13.7, 2026-11-04): langgraph 1.2.19, langgraph-checkpoint 4.3.1,
  langgraph-checkpoint-postgres 3.2.0, langchain-core 1.7.5, langchain-google-genai 4.4.x (explicit `>=4.4` lower bound),
  langchain-groq 1.1.3, langfuse 5.1.0, torch 2.15.0+cpu, **transformers 4.57.6**, **sentence-transformers 5.7.0**,
  docling 2.147.0 (docling-core 2.106.0, docling-ibm-models 4.0.4, docling-parse 7.24.2), spacy 3.8.18 (+ 3.8.0 models),
  presidio-analyzer/anonymizer 2.2.366 (numpy pinned to 2.4.x by it), chromadb 1.5.9, ragas 0.4.3, sqlalchemy 2.1.8,
  psycopg 3.3.8, alembic 1.20.1, fastapi 0.162.0, pydantic 2.14.2, onnxruntime 1.31.1, optimum-onnx 0.1.0.
- Hard ceilings discovered by probing: `docling-ibm-models` and `optimum-onnx` require `transformers<4.58`, so
  sentence-transformers 6.x (requires transformers ≥5) is excluded until Docling supports transformers 5.
- `uv` manages `pyproject.toml` + `uv.lock`; `torch` **and** `torchvision` are both *direct* dependencies mapped to the
  PyTorch CPU index (`tool.uv.sources` applies to direct dependencies only; a transitive torchvision resolves from PyPI and
  its CUDA-linked wheel fails against `torch+cpu` with `operator torchvision::nms does not exist`); monthly upgrade PRs run
  the import smoke test; model revisions are pinned separately.

## Alternatives considered
- Python 3.12/3.13 "to be safe": 3.14 is verified; 3.13 remains a zero-cost fallback.
- pip-tools/poetry: uv is faster and handles the universal lock and the CPU index cleanly.
- Newest transformers/sentence-transformers: incompatible with Docling (verified), see above.

## Advantages
Reproducible installs; compatibility proven by a resolver and an install/import smoke test rather than by reading
release notes.

## Disadvantages
Python 3.14 is young; some upstream classifiers (langgraph) do not list it yet (pure Python, resolves and imports).

## Security implications
Pinned, auditable dependencies (`pip-audit` in CI).

## Operational complexity
Lockfile discipline; CPU wheel index configuration.

## Consequences
Doc 02 lists the full pinned set and the evidence; re-verification happens at M0 when the real `pyproject.toml` is
created.
