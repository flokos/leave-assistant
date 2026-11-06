# Performance — 2026-11-06 (commit 7f7ba88-dirty)

## Live answers, per stage (ms)

Warm turns only (the first turn of a configuration loads the models and is listed as `warmup_total_ms`);
search and rerank over the turns that retrieved. The LLM time is the provider's, on the free tier.

| Configuration | stage | n | p50 | p95 |
|---|---|---|---|---|
| `pgvector+A+vector` | search_ms | 16 | 160 | 198 |
| `pgvector+A+vector` | rerank_ms | 16 | 35170 | 42884 |
| `pgvector+A+vector` | llm_ms | 17 | 1840 | 4350 |
| `pgvector+A+vector` | total_ms | 17 | 36980 | 44968 |
| `pgvector+A+vector` | warmup_total_ms | 1 | 75810 | — |
| `pgvector+A+vector` | total_minus_llm_ms | 17 | 35408 | 43459 |
| `pgvector+B+hybrid+onnx` | search_ms | 16 | 174 | 208 |
| `pgvector+B+hybrid+onnx` | rerank_ms | 16 | 2109 | 2890 |
| `pgvector+B+hybrid+onnx` | llm_ms | 17 | 1610 | 5047 |
| `pgvector+B+hybrid+onnx` | total_ms | 17 | 4530 | 6566 |
| `pgvector+B+hybrid+onnx` | warmup_total_ms | 1 | 59490 | — |
| `pgvector+B+hybrid+onnx` | total_minus_llm_ms | 17 | 2614 | 3443 |

## Ingestion per document type (job claim → finish, s)

| Type | n | p50 | max |
|---|---|---|---|
| docx | 3 | 35.7 | 47.8 |
| md | 58 | 57.2 | 163.7 |
| pdf | 7 | 91.4 | 312.7 |
| scan | 1 | 52.2 | 52.2 |
| txt | 3 | 103.4 | 132.1 |
