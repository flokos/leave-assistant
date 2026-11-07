# Reranker runtimes — 2026-11-06 (commit 7f7ba88-dirty)

Candidates from pgvector + hybrid; latency = one `score()` call per query (warm), on the reference machine.

| Model | Runtime | load s | peak RSS MB | candidates | nDCG@5 | p50 ms | p95 ms | τ | balanced acc. |
|---|---|---|---|---|---|---|---|---|---|
| B | torch | 11.55 | 1583 | 10 | 0.8835 | 2719.5 | 3456.0 | 0.2199 | 0.985 |
| B | torch | 11.55 | 1583 | 20 | 0.8914 | 5509.4 | 5747.2 | 0.5329 | 0.941 |
| B | onnx | 20.46 | 2381 | 10 | 0.8835 | 1880.7 | 2473.0 | 0.2199 | 0.985 |
| B | onnx | 20.46 | 2381 | 20 | 0.8914 | 3957.1 | 5852.3 | 0.5329 | 0.941 |
| B | onnx-int8 | 11.75 | 1664 | 10 | 0.8937 | 2784.6 | 4052.7 | 0.195 | 0.985 |
| B | onnx-int8 | 11.75 | 1664 | 20 | 0.906 | 6672.5 | 7306.5 | 0.4713 | 0.956 |

Reranker A was not measured here: in torch it took about 73 s per turn in M6, and a torch run this session was stopped after 52 minutes without finishing. That rules it out under NFR-PERF-01 whatever the runtime (docs/evaluation/retrieval-comparison.md).
