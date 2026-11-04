# ADR-0014 — Reranker chosen by evaluation between two Apache-2.0 candidates; Jina excluded

**Status:** Proposed (candidates fixed; decision to be recorded with M9 results) · **Related:** ADR-0012

## Context
Brief §7.7 asks for a suitable local multilingual reranker without predetermining the model, evaluated in Greek and
English. Licences must permit self-hosted use.

## Problem
Pick a reranker that handles Greek, runs on a 4-core CPU within the latency budget, and is permissively licensed.

## Decision
Evaluate exactly two candidates on the M9 dataset in both languages: **A** `BAAI/bge-reranker-v2-m3` (568M, Apache-2.0,
multilingual incl. Greek; int8 ONNX export via `optimum-onnx`, which is compatible with the pinned transformers 4.57)
and **B** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (~118M, Apache-2.0, multilingual base). Selection criteria:
nDCG@5/MRR gain over fused ranking, p95 latency for 20 candidates on the reference machine, memory. `jinaai/jina-reranker-v2-base-multilingual`
and newer Jina rerankers are **excluded** (CC BY-NC 4.0). `mixedbread-ai/mxbai-rerank-base-v2` is a reserve candidate
(Apache-2.0) if both fail quality or latency criteria.

## Alternatives considered
- No reranker (RRF only): fastest; evaluated as the baseline.
- LLM-based reranking via providers: sends more content out and burns quota.

## Advantages
Decision made on measured evidence in the languages that matter; both candidates are licence-safe.

## Disadvantages
Candidate A is heavy on CPU without quantisation; calibration of the evidence threshold is model-specific.

## Security implications
Local inference only.

## Operational complexity
Model download (A: ~2.2 GB fp32 or ~600 MB int8); lazy loading in the API process.

## Consequences
This ADR is amended with the results table and the chosen default at M9.
