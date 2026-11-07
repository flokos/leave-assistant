# Evaluation summary (M9–M10)

The final evaluation report (brief §12, §13 "Evaluation"): what was measured, how, the results against the targets
of doc 11 §7, the security assessment, the known weaknesses and the future improvements. Details and raw outputs
are in the pages linked from each row; every figure was produced by the harness on the reference machine (i5-3320M,
4 threads, AVX without AVX2, 7.3 GB, no GPU) on 2026-11-06/07.

## 1. Dataset and method
- **Dataset `leave-eval-v1`** (`evaluation/datasets/leave-eval-v1.yaml`, 71 items): the 32 scenarios of doc 11 §6,
  30 retrieval probes (Greek and English, both companies, cross-lingual, HR-restricted, department notice,
  distractors) and 9 unanswerable probes. Labels were written and committed before any measurement
  ([protocol](README.md) §1).
- **Pre-registered decision rule** for the retrieval configuration ([README](README.md) §4): primary metric nDCG@5
  with paired bootstrap intervals; a challenger replaces the baseline only if its interval excludes 0 and it keeps
  NFR-PERF-01.
- **Deterministic before live**: retrieval, security and latency metrics need no LLM; live runs (answers, agent
  scenarios, RAGAS) are budgeted and pinned to one provider so configurations are comparable.
- **Providers**: the free tiers made Gemini (the primary) unusable for a full evaluation (20 answer requests per day);
  live runs used Mistral, the production second provider. Gemini was spot-checked in M10 (§3).

## 2. Results against the targets (doc 11 §7)

| Family | Target | Result | Met | Details |
|---|---|---|---|---|
| Retrieval | hit@5 ≥ 0.9 on S01–S04, S07–S08 | 1.0 on those six; 0.971 over 34 labelled queries; nDCG@5 0.883 | yes | [retrieval comparison](retrieval-comparison.md) |
| RAG quality | faithfulness ≥ 0.85 | 0.887 [0.755, 0.980] (17 items × 3 judge repetitions); 0.943 without the one verifier fallback | yes | [RAG quality](rag-quality.md) |
| Grounding | insufficient evidence 100 %, citation precision ≥ 0.95 | S05/S06/S09 refused; 0 boundary violations; 25/25 citations name a supplied document | yes | [RAG quality](rag-quality.md) §2 |
| Agent (live, Mistral) | tool selection ≥ 0.9, action success ≥ 0.9, unauthorized 0 | 0.91 (strict 0.73), 0.91, **0** | yes (strict: no) | [agent and security](agent-and-security.md) §1 |
| Language | ≥ 0.98 | 47/47 | yes | |
| Security | block ≥ 0.95, false positives ≤ 0.05 | chat attacks 8/8; document fixtures 18/18 at the expected tier (quarantine 0.857, held 1.0); 0 false positives | blocking yes; quarantine no | [agent and security](agent-and-security.md) §2 |
| Performance | NFR-PERF-01 p50 ≤ 3 s (excluding LLM) | p50 2.70 s, p95 3.36 s | p50 yes, p95 no | [performance](performance.md) |
| Cost/quota | budgets never exceeded | every live run within `EVAL_MAX_CALLS`; 0 failovers in pinned runs | yes | |

**Decisions taken from the evidence (M9):** pgvector stays the vector backend (Chroma: identical rankings, slower);
hybrid retrieval stays; reranker B on ONNX fp32 (candidate A is +0.034 nDCG@5 but about 13× slower per turn
excluding LLM time); τ 0.5 → 0.22 (ADR-0011, ADR-0014).

## 3. M10 follow-up measurements

| What | Result | Raw output |
|---|---|---|
| PII precision in documents (R-30) | 15 statistical PERSON/ADDRESS findings in the seed policies, all false positives, removed by the document precision filter; the 6 e-mail addresses kept | `results/2026-11-07-pii-precision.json` |
| Re-run of the affected answers on Mistral (after re-processing the corpus) | R02, R04, R05, R13, S07, R29 all answered correctly; no PERSON/ADDRESS placeholder left in their evidence (only the HR e-mail address, as designed); R02 and R29 — M9's two verifier fallbacks — now route to knowledge and are answered | `results/2026-11-07-answers-*-m10*.jsonl` |
| S19 (another department's absences) on Mistral | the department name resolves (FIN) and the scope check denies it; no data in any of three runs | `results/2026-11-07-agent-live-m10-mistral-s19*.json` |
| Gemini spot check (R-03), one day's free quota | S10 (balance) and S12 (request → card → submitted) correct; the quota ran out at the third scenario (17 answer-model calls); unauthorized 0 | `results/2026-11-07-agent-live-m10-gemini.json` |

Mistral's free plan rate-limited (429) several M10 live runs; the affected items were repeated with slower pacing.
These are provider outages, not assistant failures, and are listed separately in the raw files.

## 4. Security assessment
- **Tenant isolation:** forced row-level security with a `NOBYPASSRLS` runtime role plus application checks; the
  security suite and a concurrency leak test prove it (M1); retrieval boundary violations 0 in all 12 configurations.
- **Actions:** the model can only *propose*; the tool gateway validates, authorises and the human confirms on a card.
  Unauthorized actions in live runs: 0 (Mistral 11 scenarios, Gemini spot check, S19 runs).
- **Personal data:** the model receives pseudonymised text only; the Langfuse trace check found no e-mail, phone or
  name in any trace; since M10 document text is no longer over-masked.
- **Prompt injection:** documents scored at ingestion and held or quarantined; user input screened; all chat attacks
  blocked; no false positives on the benign corpus. Weak spot: hidden-footnote fixtures are held for review, not
  quarantined.

## 5. Known weaknesses
See [known weaknesses](known-weaknesses.md) for the full list. The most important:
1. Small, developer-labelled dataset on a synthetic corpus: differences under about 3 points of nDCG@5 cannot be
   resolved, and recall of names in documents cannot be measured (the policies name no one).
2. Gemini, the production primary, was only spot-checked; its free tier allows 20 answer requests per day.
3. The RAGAS judge is a larger model of the answer model's family; scores are comparative.
4. Latency: p95 above 3 s; the first turn after a start takes about a minute; document ingestion is slow on this CPU
   (R-28).
5. The small answer model's tool use needs deterministic guards (R-33).

## 6. Future improvements
- A larger, independently labelled dataset with real-world policy documents and named persons, to measure NER recall
  in documents and to resolve smaller differences.
- A full live evaluation on Gemini (or any primary model) when a quota allows, with RAGAS on the same items.
- A judge model from another family, and an agreement check between two judges.
- Transformer NER (GLiNER, XLM-R) as an extra signal for Greek user messages, if memory allows (ADR-0017).
- Quarantine of the hidden-footnote injection family (a structural check of footnotes).
- Next-year bookings before the yearly grant is posted (R-32).
- Faster ingestion: a lighter layout model for text PDFs or page-level parallelism on bigger hosts (R-28).
- Re-evaluating candidate reranker A and int8 runtimes on a CPU with AVX2/VNNI.
