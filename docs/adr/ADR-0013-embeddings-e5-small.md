# ADR-0013 — Local embeddings with intfloat/multilingual-e5-small and index generations

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0011, ADR-0012

## Context
Brief §7.5 selects Sentence Transformers with `intfloat/multilingual-e5-small`, local execution, Greek and English,
consistent model for indexing and querying, correct prefixes and reproducible evaluation.

## Problem
Keep embeddings consistent across the corpus lifetime and across backends, on CPU, with a clear path to model
changes.

## Decision
Single `EmbeddingModel` adapter (sentence-transformers 5.7 / transformers 4.57 / torch 2.15 CPU — the resolver-verified
set) with pinned model revision, normalised 384-d outputs, enforced `query:`/`passage:` prefixes, batch encoding in the
worker and query encoding in the API. `embedding_model_id` is stored per chunk; `index_generations` track model and
chunking-rule versions; a model change builds a new generation and swaps atomically.

## Alternatives considered
- Larger multilingual models (e5-base/large, bge-m3 dense): better quality, 3–10× CPU cost; e5-small is the
  brief's stated preference and fits the machine.
- Hosted embedding APIs: forbidden by the brief.

## Advantages
Small (118M), fast on CPU, cross-lingual, MIT-licensed; deterministic given pinned revisions.

## Disadvantages
512-token input bound shapes chunk sizes; quality ceiling lower than larger models (acceptable for policy text).

## Security implications
Embeddings are computed locally; original text never leaves the host for embedding.

## Operational complexity
Model files cached in a named volume; first start downloads ~470 MB.

## Consequences
Chunking targets 300 tokens (max 450) to stay well within 512 with the context header.
