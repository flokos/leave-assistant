# Performance (M9)

Measured 2026-11-06 on the reference machine: i5-3320M (2 cores / 4 threads, AVX without AVX2),
7.3 GB RAM, desktop applications running, host load average 1.5–3. Raw outputs:
`results/2026-11-06-chat-perf.md`, `results/2026-11-06-performance.*`, `results/2026-11-06-reranker-runtimes.*`.

## 1. NFR-PERF-01: policy answer ≤ 3 s excluding LLM time

| Measurement | p50 | p95 | Verdict |
|---|---|---|---|
| `make chat-perf`, one-off api container, reranker B **ONNX fp32** (deployed) | **2.70 s** | 3.36 s | **passes** |
| same, reranker B PyTorch fp32 (the M6–M8 default) | 3.79 s | 4.75 s | fails |
| live answers on the host, B ONNX, 17 warm turns, total minus provider time | 2.61 s | 3.44 s | passes |
| live answers on the host, A PyTorch, 17 warm turns, total minus provider time | 35.4 s | 43.5 s | fails |

The p50 target is met. The p95 (3.4 s) is above 3 s; NFR-PERF-01 states a p50 target, but the tail is noted in
[known weaknesses](known-weaknesses.md). R-31 is closed for the p50 target.

**Measurement fix.** Up to M8, `make chat-perf` ran the measuring process *inside* the running api container, beside
the api server that had already loaded every model, so two copies shared the 3 GB limit. On 2026-11-06 the cgroup
reached that limit 265,835 times, and the ONNX run measured 13.9 s of swapping. The target now starts a one-off
container (same image, environment and limit). The M6/M7 figures (p50 3.12–3.97 s with PyTorch) were measured
the old way. Today's one-off PyTorch figure, 3.79 s, is close to them, so they were not far off for PyTorch.

## 2. Latency per stage (live answers, warm turns)

From `results/2026-11-06-performance.md`; 17 warm turns per configuration (the first turn loads the models).
Search and rerank are over the 16 turns that retrieved. The provider was `ministral-14b-2512` on the free tier.

| Stage | B (pgvector, hybrid, ONNX) p50 / p95 | A (pgvector, vector-only, PyTorch) p50 / p95 |
|---|---|---|
| embedding + vector and full-text legs + fusion | 174 / 208 ms | 160 / 198 ms |
| rerank (10 candidates, 256 tokens) | 2109 / 2890 ms | 35170 / 42884 ms |
| LLM (provider time) | 1610 / 5047 ms | 1840 / 4350 ms |
| total | 4530 / 6566 ms | 36980 / 44968 ms |
| first turn after start (model loading) | 59.5 s | 75.8 s |

The reranker dominates. A live turn usually calls the provider twice (classification on `ministral-8b-2512`, answer on
`ministral-14b-2512`). With Gemini as the answer model, M6 measured 3–10 s of provider time (thinking tokens).
The first turn after start takes about a minute because the models load lazily in the process that serves it.
The api runs a warm-up turn at start-up, so users don't see this delay (R-31 mitigation).

## 3. Ingestion time per document type

Job claim → finish for every successful `document.process` job in the dev database (seed corpus, re-ingestions and
test uploads, M4–M9). Each figure includes parsing, chunking, PII screening, injection screening, embedding and
indexing. The history mixes jobs run under very different host load (some during test suites), so the spread is wide.

| Type | n | p50 | max |
|---|---|---|---|
| Markdown | 58 | 57.2 s | 163.7 s |
| Word (docx) | 3 | 35.7 s | 47.8 s |
| PDF (text) | 7 | 91.4 s | 312.7 s |
| PDF (scanned, OCR) | 1 | 52.2 s | 52.2 s |
| plain text | 3 | 103.4 s | 132.1 s |

Doc 11 sets no ingestion target. Documents become searchable within minutes, and users see the processing state
(M4).

## 4. Reranker runtimes

See [retrieval comparison §3](retrieval-comparison.md#3-reranker-runtimes-b-10-and-20-candidates): ONNX fp32 is 31 %
faster than PyTorch at p50 with identical scores. int8 is slower on this AVX-only CPU.
