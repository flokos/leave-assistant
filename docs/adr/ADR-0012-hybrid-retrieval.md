# ADR-0012 — Hybrid retrieval: PostgreSQL FTS (greek/english/simple) + vector search + RRF + local reranker

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0011, ADR-0013, ADR-0014

## Context
Brief §7.7 selects vector similarity + keyword retrieval + fusion + local multilingual reranking + context selection,
evaluated in Greek and English.

## Problem
Provide a keyword leg that works for Greek and English without introducing another search engine, fuse it with the
vector leg robustly, and keep the whole query permission-filtered.

## Decision
Keyword leg = PostgreSQL full-text search with generated `tsvector` columns for the `greek`, `english` and
`simple` configurations (GIN indexes), `websearch_to_tsquery`, `ts_rank_cd`; accent folding for Greek queries.
Vector leg = pgvector HNSW (ADR-0011). Fusion = reciprocal rank fusion (k = 60) over the top 30 of each leg.
Reranking of ≤ 20 candidates by a local cross-encoder (ADR-0014), then context selection with a 1,800-token budget,
≤ 3 chunks per document and adjacency merging. The `greek` configuration was verified on PG 18 (stems and strips
accents, e.g. "εργαζόμενοι" → "εργαζομεν").

## Alternatives considered
- Elasticsearch/OpenSearch or ParadeDB for BM25: better lexical ranking, but another heavy service or extension.
- In-process BM25 (`rank_bm25`) over all chunks: no index, no permission predicate, memory-bound.
- Vector-only: misses exact codes, names and Greek morphology mismatches.

## Advantages
No new service; keyword and vector legs share predicates, RLS and transactions; cross-lingual recall from e5 plus
same-language precision from FTS.

## Disadvantages
PostgreSQL FTS ranking is weaker than BM25 implementations; the evaluation quantifies the keyword leg's contribution.

## Security implications
None beyond ADR-0011; all legs are filtered identically.

## Operational complexity
Generated columns and three GIN indexes; `hnsw.ef_search` tuned per session.

## Consequences
If evaluation shows the keyword leg adds little, it can be disabled by configuration without schema changes.
