# ADR-0014 — Reranker chosen by evaluation between two Apache-2.0 candidates; Jina excluded

**Status:** Accepted (decided on the M9 results, 2026-11-06): candidate B, ONNX fp32 runtime · **Related:** ADR-0012

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

## Implementation note (M6, 2026-11-05)
M6: both candidates are wired behind the `Reranker` port and downloaded by `make models`; the running default is candidate B because candidate A in fp32 does not fit the api memory limit on the reference host (measurements in worklog M6). The M9 evaluation still selects the final model (possibly A in int8).

## Results and decision (M9, 2026-11-06)
`docs/evaluation/retrieval-comparison.md` §2–§3, `docs/evaluation/performance.md` §1.

| | nDCG@5 (34 queries) | Δ vs B, paired 95 % CI | rerank p50 / p95 (10 candidates) | chat turn p50 excl. LLM |
|---|---|---|---|---|
| no reranker (RRF only) | 0.829 | −0.055 [−0.132, 0.021] | — | — |
| **B** `mmarco-mMiniLMv2-L12-H384-v1`, PyTorch fp32 | 0.883 | — | 2.72 / 3.46 s | 3.79 s (fails NFR-PERF-01) |
| **B, ONNX fp32** | 0.883 (same scores) | 0.000 | **1.88 / 2.47 s** | **2.70 s (passes)** |
| B, ONNX int8 (avx2 kernels) | 0.894 | — | 2.78 / 4.05 s | — |
| **A** `bge-reranker-v2-m3`, PyTorch fp32 | 0.917 | **+0.034 [0.005, 0.074]** | ≈ 35 s per turn (live) | 35.4 s (fails) |

**Decision: B with the ONNX fp32 runtime** (`RERANKER_BACKEND=onnx`, exported by `make models` into
`/models/onnx/`), 10 candidates, τ recalibrated to **0.22**. A is the only candidate with a significant quality
gain, but it is about 13× slower per turn excluding LLM time (35.4 vs 2.6 s; rerank 35 vs 2.1 s). No runtime could bring it under the 3 s target on this CPU (rule 3 of the
pre-registered protocol), so its ONNX/int8 variants were not timed. int8 is slower than fp32 here: the i5-3320M
has no AVX2, and the quantised kernels need it. 20 candidates double the latency for +0.008 nDCG@5. The reserve
candidate (`mxbai-rerank-base-v2`) was not needed: B passes both criteria. Re-evaluate A on a host with AVX2/VNNI or
more cores. The harness reruns with `make eval-offline`.
