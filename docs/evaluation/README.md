# Evaluation — protocol, pre-registration and reproduction (M9)

Doc 11 §6–§11, ADR-0011/0014/0022/0023. Results in this folder are produced only by the harness, with the commit,
the configuration, the date and a fingerprint of the corpus they ran on (doc 11 §1 principle 5).

| File | Contents |
|------|----------|
| `README.md` | this protocol: dataset, metrics, configurations, the decision rule (written before any measurement) |
| `retrieval-comparison.md` | pgvector vs Chroma × rerankers × hybrid/vector-only; reranker runtimes; τ calibration; the decision |
| `rag-quality.md` | RAGAS on the live answers of the two best configurations |
| `agent-and-security.md` | tool selection, action success, unauthorized actions (live and deterministic); injection and PII figures |
| `performance.md` | latency per stage, NFR-PERF-01, ingestion time per document type, memory of the evaluation session |
| `known-weaknesses.md` | what the numbers do not show and what is known to be weak |
| `results/` | the raw outputs (Markdown + CSV/JSONL) behind every table |

## 1. Dataset `leave-eval-v1`
`evaluation/datasets/leave-eval-v1.yaml` — the 32 scenarios of doc 11 §6, 28 retrieval probes (R01–R30 minus the
two boundary checks), 3 access-boundary checks (S09, R16, R24) plus S20 (cross-tenant), and 11 unanswerable items
(S05, S06, P01–P03, U01–U06). Labels were written from the seed corpus sources on 2026-11-06 and committed before
any configuration was measured. Relevance is graded (2 = states the answer, 1 = supporting), resolved to chunk ids
at run time by document title and anchor phrases (NFKC, case-folded, whitespace removed). The same file is uploaded
to Langfuse as the dataset `leave-eval-v1`.

The corpus is the frozen seed corpus in the dev database (doc 05 §10): 50 retrievable chunks in two tenants.
Leftover test documents are removed before a run; each result records the corpus fingerprint (SHA-256 over the
retrievable chunk ids and contents) so runs on a different corpus are recognisable.

## 2. Metrics
- **Retrieval (deterministic, no LLM)**, per labelled query and then averaged: hit@1, hit@5, MRR (first relevant
  chunk), nDCG@5 with the graded labels (gain 2^g − 1, ideal DCG from all relevant chunks of that query). The
  ranking evaluated is what the agent receives: the fused list (hybrid) or the vector list (vector-only), rescored
  by the reranker when one is configured, after the per-document cap.
- **Uncertainty**: 95 % percentile bootstrap intervals (10,000 resamples of queries, seed 2026) for means, and the
  paired bootstrap of the per-query difference for comparisons.
- **Boundaries**: a boundary item fails if any chunk of a `never_retrieve` document (or tenant) appears in the
  candidate list. Must be 0 for every configuration.
- **Evidence threshold τ**: the top reranker score of answerable vs unanswerable items; τ maximises balanced
  accuracy (answerable ≥ τ, unanswerable < τ); ties → the midpoint of the widest gap.
- **RAG quality (live, LLM judge)**: RAGAS faithfulness, answer relevancy, context precision (with reference),
  context recall on the 18 `ragas: true` items; 3 judge repetitions; mean ± standard deviation over repetitions and
  the bootstrap interval over items.
- **Agent (live)**: tool-selection accuracy (every `expected_tools` called, no `forbidden_tools` executed), action
  success (expected outcome reached), unauthorized-action rate (state changes that the user was not allowed to make);
  answer-language fidelity on every live answer (lingua).
- **Performance**: p50/p95 per stage (embedding + legs + fusion, rerank, LLM, total) over repeated runs.

## 3. Configurations
Retrieval grid (12): vector backend {pgvector, Chroma} × reranker {none, A `BAAI/bge-reranker-v2-m3`, B
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`} × candidates {hybrid (vector + full-text, RRF), vector-only}. Fixed:
e5-small embeddings (identical vectors in both stores), leg size 30, 10 rerank candidates at 256 tokens (the M6
defaults), 3 chunks per document. Reranker runtime table: {A, B} × {PyTorch fp32, ONNX fp32, ONNX int8} × {10, 20}
candidates: quality on the same candidate lists, p50/p95 latency and peak memory.

## 4. Decision rule (pre-registered 2026-11-06, before any measurement)
1. **Baseline** = the running default: pgvector, hybrid, reranker B (PyTorch fp32), 10 candidates, 256 tokens.
2. A challenger replaces the baseline only if the paired bootstrap 95 % interval of its per-query **nDCG@5**
   difference lies entirely above 0. Otherwise the baseline (or, between equivalent challengers, the simpler and
   cheaper one: pgvector over Chroma, fewer models, fewer candidates) stays.
3. **Latency gate**: a reranker variant is only eligible if a chat turn excluding LLM time keeps NFR-PERF-01
   (`make chat-perf` p50 ≤ 3 s on the reference machine). If no variant passes, the fastest variant whose nDCG@5 is
   not significantly worse than the baseline's (interval of the difference includes 0 or lies above it) is chosen,
   and R-31 is revised with the measured figure.
4. **Boundaries** must be clean (0 violations) for a configuration to be eligible at all.
5. **τ** is recalibrated (§2) whenever the reranker or its runtime changes, and the S01–S09 scenario test must pass
   with the new τ before the change is adopted.
6. RAGAS runs on the baseline and the best challenger (by mean nDCG@5); RAGAS informs the report but does not
   override rule 2 (judge noise, doc 11 §11).

## 5. Reproduction
```bash
make eval-session        # stop frontend, n8n, worker, api, keycloak; start chroma (eval) and Langfuse (observability)
make eval-offline        # retrieval grid, reranker runtimes, τ calibration → docs/evaluation/results/
make eval-live           # live answers (budget EVAL_MAX_CALLS) and live agent scenarios → samples + metrics
make eval-ragas          # RAGAS in the separate evaluation/ project; Langfuse dataset + experiments
make eval-report         # tables in docs/evaluation/*.md from results/
make eval-session-end    # back to `make up-staged`
```
Everything except the live steps is deterministic for a given corpus fingerprint and model revisions.
