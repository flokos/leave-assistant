# Retrieval comparison and the reranker decision (M9)

Run 2026-11-06 on the reference machine (i5-3320M, 4 threads, AVX without AVX2, 7.3 GB), commit `7f7ba88` plus the
working tree of the M9 branch, seed corpus fingerprint `99e6750960b45e4b` (50 retrievable chunks, two tenants),
dataset `leave-eval-v1` (34 labelled queries, 11 unanswerable items). Protocol and decision rule: [README](README.md)
§2–§4, written before the first run. Raw outputs: `results/2026-11-06-retrieval-grid.*`,
`results/2026-11-06-reranker-runtimes.*`.

## 1. Result

| Setting | Before M9 | After M9 | Why (rule in README §4) |
|---|---|---|---|
| Vector backend | pgvector | **pgvector** | Chroma returns the same rankings (identical vectors) and is slower; rule 2, simpler option |
| Candidates | hybrid (vector + full-text, RRF) | **hybrid** | vector-only is +0.009 nDCG@5, interval [0.000, 0.025] touches 0; rule 2, baseline stays |
| Reranker | B `mmarco-mMiniLMv2-L12-H384-v1` | **B** | A is better (+0.034 [0.005, 0.074]) but fails the latency gate; rule 3 |
| Reranker runtime | PyTorch fp32 | **ONNX fp32** | same rankings as PyTorch; the only B runtime that passes NFR-PERF-01; rule 3 |
| τ (evidence threshold) | 0.5 | **0.22** | recalibrated for B; rule 5. S01–S09 pass with it (12/12) |

Also found and fixed: the full-text leg matched almost nothing (§4).

## 2. Retrieval grid (12 configurations, after the full-text fix)

Means over the 34 labelled queries with 95 % bootstrap intervals; Δ is the paired difference to the baseline
`pgvector+B+hybrid`. Boundary violations were 0 for every configuration (S09, S20, R16, R24: HR-restricted
document, other tenant, department notice).

| Configuration | hit@1 | hit@5 | MRR | nDCG@5 | ΔnDCG@5 | search p50 ms |
|---|---|---|---|---|---|---|
| `pgvector+none+hybrid` | 0.853 | 0.971 | 0.903 | 0.829 [0.746, 0.898] | −0.055 [−0.132, 0.021] | 108 |
| `pgvector+none+vector` | 0.853 | 1.000 | 0.910 | 0.882 [0.824, 0.933] | −0.001 [−0.080, 0.087] | 99 |
| `pgvector+A+hybrid` | 0.912 | 1.000 | 0.946 | 0.917 [0.862, 0.966] | **+0.034 [0.005, 0.074]** | 108 |
| `pgvector+A+vector` | 0.912 | 1.000 | 0.951 | 0.927 [0.878, 0.969] | **+0.044 [0.011, 0.086]** | 107 |
| `pgvector+B+hybrid` (baseline) | 0.882 | 0.971 | 0.923 | 0.883 [0.803, 0.948] | — | 108 |
| `pgvector+B+vector` | 0.882 | 0.971 | 0.926 | 0.892 [0.815, 0.953] | +0.009 [0.000, 0.025] | 106 |

The Chroma rows are equal to their pgvector rows to the third decimal on every metric: the same e5 vectors are
copied into both stores and both rank by cosine similarity. Chroma's search p50 is 123–179 ms against 99–108 ms
for pgvector, because the version allow list still comes from PostgreSQL (row-level security) and Chroma adds an HTTP
hop. Full table: `results/2026-11-06-retrieval-grid.md`.

Reading the table:
- **Reranker A** is the only change whose interval lies above 0, by 3–4 points of nDCG@5. It puts a relevant chunk first on one
  more query than B (31 vs 30 of 34).
- **Hybrid vs vector-only**: without a reranker, the full-text leg costs 5 points (RRF lifts chunks that share
  words with the question but don't answer it). After reranking the difference is under 1 point and not
  significant. On this dataset the leg adds nothing measurable; it is kept for exact-term lookups (policy codes,
  form names), which `leave-eval-v1` barely covers (see [known weaknesses](known-weaknesses.md)).
- **The dataset is small** (50 chunks, 34 queries): intervals are wide, and differences under about 3 points cannot
  be resolved.

## 3. Reranker runtimes (B; 10 and 20 candidates)

One warm `score()` per query on the same candidate lists; peak RSS of the measuring process.

| Runtime | candidates | nDCG@5 | p50 | p95 | peak RSS | τ | balanced acc. |
|---|---|---|---|---|---|---|---|
| PyTorch fp32 | 10 | 0.8835 | 2.72 s | 3.46 s | 1583 MB | 0.220 | 0.985 |
| **ONNX fp32** | 10 | 0.8835 | **1.88 s** | 2.47 s | 2381 MB | 0.220 | 0.985 |
| ONNX int8 (avx2 config) | 10 | 0.8937 | 2.78 s | 4.05 s | 1664 MB | 0.195 | 0.985 |
| PyTorch fp32 | 20 | 0.8914 | 5.51 s | 5.75 s | 1583 MB | 0.533 | 0.941 |
| ONNX fp32 | 20 | 0.8914 | 3.96 s | 5.85 s | 2381 MB | 0.533 | 0.941 |
| ONNX int8 | 20 | 0.9060 | 6.67 s | 7.31 s | 1664 MB | 0.471 | 0.956 |

- ONNX fp32 gives the same scores as PyTorch and is 31 % faster at p50. It uses about 800 MB more peak memory in this
  process (ONNX Runtime arena plus the exported graph).
- int8 is *slower* than fp32 on this CPU: the quantised kernels need AVX2/VNNI, and the i5-3320M has only AVX.
  Its +0.01 nDCG@5 is within the noise of 34 queries.
- 20 candidates cost twice the time for +0.008 nDCG@5, and their τ separates worse. 10 stays.

**End-to-end gate (NFR-PERF-01, `make chat-perf`, 20 turns, LLM time excluded, on the host with 2 threads):**
PyTorch p50 3.60 s / p95 4.67 s fails. ONNX fp32 p50 2.58 s / p95 3.33 s passes. The containerised re-check is in
[performance](performance.md).

**Reranker A** was not timed in this table. In M6 a turn with A in PyTorch took about 73 s. This session a PyTorch
run of A was stopped after 52 minutes without finishing 34 queries. A (568 M parameters) is about 5× B's
size. Even a 2× ONNX speed-up cannot bring it under 3 s per turn on this machine, so it fails rule 3 whatever the
runtime, and timing it further would not change the decision.

## 4. Finding: the full-text leg matched almost nothing

The first grid run (`results/2026-11-06-retrieval-grid-before-fts-fix.md`) showed hybrid and vector-only producing
**identical rankings for all 49 queries**. Cause: the language-specific leg used `plainto_tsquery`, which joins every
word of the question with AND. A full natural-language question almost never has all its words in one chunk, so
the leg returned nothing and RRF passed the vector leg through unchanged. Fixed in
`ChunkRepository.keyword_search`: the language leg now ORs the stemmed terms (`ts_rank` still prefers chunks matching
more terms); the `simple` leg keeps `websearch_to_tsquery` for quoted phrases and codes. The fix is covered by an
integration test that sends a whole question. The grid in §2 is the re-run after the fix. M6's hybrid
retrieval had, in effect, been vector-only.

## 5. τ calibration

The top reranker score separates answerable from unanswerable items. B (10 candidates): the highest unanswerable
score is 0.199 (U04), the lowest answerable scores are 0.020 (one outlier) and then 0.241.
τ = 0.2199 (balanced accuracy 0.985; TPR 0.971, TNR 1.0). With the old τ = 0.5, 15 % of answerable questions would
have been refused (TPR 0.853). Deployed as `RETRIEVAL_TAU=0.22`. A would calibrate to τ = 0.135 with perfect separation.

## 6. Reproduce
```bash
make eval-session && make eval-offline     # grid + B runtimes (EVAL_RUNTIME_VARIANTS to choose variants)
cd backend && uv run --no-sync python -m leave_assistant.evaluation.retrieval runtimes-report   # rebuild §3 table
```
