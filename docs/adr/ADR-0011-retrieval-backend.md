# ADR-0011 — Retrieval backend: pgvector as default, ChromaDB as an evaluation profile behind one port

**Status:** Accepted, final (baseline v0.1, 2026-11-04; default confirmed by the M9 results, 2026-11-06) · **Related:** ADR-0003, ADR-0012

## Context
Brief §7.6 selects both ChromaDB and pgvector to compare retrieval approaches on the same corpus, embeddings and
dataset, asks for an abstraction to select and evaluate backends, forbids maintaining two independent production
systems, and wants the final default justified by evaluation results.

## Problem
Honour the comparison requirement without duplicating production capability or weakening tenant isolation.

## Decision
`VectorIndex` port with two adapters. **pgvector** (HNSW, cosine, `vector(384)`) is the default: vectors live next to
chunks, under RLS, joined with permission predicates and the FTS leg in one transaction. **Chroma** runs only in the
`eval` Compose profile with a collection per tenant and version-id filters computed in PostgreSQL. The evaluation
harness indexes the same corpus into both and reports hit@k/MRR/nDCG and latency; the default stays pgvector unless
Chroma shows a material quality advantage — which is not expected since both store identical vectors.

## Alternatives considered
- Chroma as default with pgvector for eval: inverts the isolation story (application-enforced only) and separates
  vectors from the keyword index and transactions.
- Both in production behind a runtime switch: duplicate operational burden, contrary to the brief.
- Dropping Chroma: violates the explicit selection; the comparison is a stated goal.

## Advantages
One system of record; database-level isolation for retrieval; no extra container in the core profile; the
comparison remains reproducible.

## Disadvantages
Chroma's native features (e.g., its own hybrid search in newer versions) are not used in production.

## Security implications
Retrieval inherits RLS and predicates (ADR-0003); Chroma isolation is application-only and therefore confined to
evaluation.

## Operational complexity
HNSW index maintenance in PostgreSQL; `eval` profile adds one container when needed.

## Consequences
The M9 report (`docs/evaluation/retrieval-comparison.md`) must be produced before this ADR's status changes from
"to be confirmed" to final.

## Results (M9, 2026-11-06)
`docs/evaluation/retrieval-comparison.md`. Built as decided: a `VectorIndex` port with `PgVectorIndex` (default) and
`ChromaVectorIndex` (eval profile; one collection per tenant, cosine; its version allow list is computed in
PostgreSQL under RLS, so Chroma never decides visibility), plus `ChromaSync` copying the exact pgvector embeddings.
On the 34 labelled queries of `leave-eval-v1`, Chroma returns **the same rankings** as pgvector in every
configuration, with 0 boundary violations, and a slower search (p50 123–179 ms vs 99–108 ms: the allow-list
round trip plus an HTTP hop). By the pre-registered rule (simpler option on a tie) **pgvector stays the default**.
Chroma remains available for evaluation (`RETRIEVAL_BACKEND=chroma`, `make eval-session`). The run also found that
the full-text leg had been matching almost nothing: an AND of every word of a whole question. It is fixed to OR
the stemmed terms; see the report §4.
