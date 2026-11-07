# RAG quality (M9)

Run 2026-11-06/07 on the reference machine. RAGAS 0.4.3 in the `evaluation/` project. Protocol: [README](README.md)
§2 (RAG quality) and §4 rule 6: RAGAS informs the report; it does not override the retrieval decision. Raw outputs:
`results/2026-11-06-ragas.{json,md}` (summary, paired comparison) and `results/2026-11-06-ragas.partial.jsonl`
(per-item scores of every repetition). Answers: `results/2026-11-06-answers-<config>.jsonl`.

## 1. Setup

| Item | Value |
|---|---|
| Configurations | `pgvector+B+hybrid+onnx` (deployed, τ 0.22) and `pgvector+A+vector` (best challenger by nDCG@5, τ 0.135) |
| Items | the 18 `ragas: true` items of `leave-eval-v1` (8 EL, 10 EN; both tenants). 17 scored per configuration: R29 ended in the verifier fallback with no evidence bundle, so RAGAS had no contexts to judge (§3.1) |
| Answers | live, through the full agent graph, pinned to Mistral (`ministral-14b-2512` answers, `ministral-8b-2512` classifies): 39 calls per configuration, 0 failovers (README amendment) |
| Contexts | exactly the evidence bundles the LLM received (`ContextRecorder`, both call sites), pseudonymised as the provider saw them |
| Metrics | faithfulness, answer relevancy, context precision with reference, context recall |
| Judge | `mistral-large-4`, temperature 0, embeddings `mistral-embed`; paced at 0.12 requests/s (20k tokens/min free limit); about 45 min per repetition |
| Repetitions | 3 per configuration (seeds 42–44); 0 judge errors, no NaN |
| Langfuse | dataset `leave-eval-v1` (71 items) and one experiment per configuration (`2026-11-06-<config>`, 18 items each), synced 2026-11-07 (`results/2026-11-06-langfuse.json`). Every item carries `answered`, `verifier_exhausted` and the four RAGAS means (34 of 36; R29 has none). Checked through the evaluator observations: in v4's events-only mode the public API does not list scores |

## 2. Results

Mean over items of the per-item mean over 3 repetitions; 95 % bootstrap CI over items; standard deviation of the
three repetition means (the judge's own variance).

| Metric | B (deployed) | A (challenger) | A − B paired [95 % CI] | doc 11 target |
|---|---|---|---|---|
| Faithfulness | **0.887** [0.755, 0.980], sd 0.018 | 0.912 [0.784, 0.990], sd 0.008 | +0.025 [−0.007, 0.059] | ≥ 0.85 — **met** |
| Answer relevancy | 0.862 [0.744, 0.932], sd 0.007 | 0.865 [0.747, 0.935], sd 0.013 | +0.004 [−0.012, 0.017] | — |
| Context precision (with reference) | 0.990 [0.971, 1.0], sd 0 | 1.0, sd 0 | +0.010 [0.0, 0.029] | — |
| Context recall | 1.0, sd 0 | 1.0, sd 0 | 0.0 | — |

Without R02, the verifier fallback scored 0/0 in both configurations (§3.1): B faithfulness 0.943 and relevancy 0.916;
A 0.969 and 0.920.

Citation precision (doc 11 §7, grounding): every citation in a delivered answer names a document from the evidence
the LLM received: B 25/25, A 22/22 (document level; the verifier enforces it per turn).

**Reading.**
- The deployed configuration meets the faithfulness target.
- A is not significantly better on any RAGAS metric: every paired interval includes 0. The context precision
  difference comes from one item, S07 (§3.4).
- This agrees with the retrieval decision (A +0.034 nDCG@5 at about 13× the time per turn excluding LLM, 17× the rerank time,
  [retrieval comparison](retrieval-comparison.md)). Answer quality gives no reason to revisit it.
- Judge variance (sd ≤ 0.018) is small next to the item-level spread. The intervals are wide because there are 17
  items, not because of the judge.

## 3. Findings

### 3.1 Verifier fallbacks count as answers (R-36)

R02 and R29 ended in the verifier fallback in both configurations (`verifier_exhausted`).

| Item | Question | Why both drafts were rejected | Effect on RAGAS |
|---|---|---|---|
| R02 | "Who approves a leave request that is longer than five days?" | classified as an *action*; the verifier demanded a `prepare_` tool | the fallback "I could not find enough information…" was scored: faithfulness 0, relevancy 0. That alone lowers each configuration's mean by about 0.055 |
| R29 | "Μπορώ να αλλάξω μια εγκεκριμένη άδεια πριν ξεκινήσει;" (can I change an approved leave before it starts?) | the draft cited sources that were not provided; no evidence bundle was recorded | not scorable (no contexts); excluded |

Both are policy questions phrased around an action verb. Both runs were recorded as `answered`, which overstates
answer quality in run metrics. Carried to M10 under R-36: give the fallback its own outcome, and route questions
about permissions ("who approves…", "can I change…") to knowledge before action.

### 3.2 Over-pseudonymisation of policy text (R-30)

The pseudonymiser runs on document text as well as on user input. In the bundles the LLM received, ordinary words
were replaced by placeholders:
- **B:** 4 of 18 items (R02, R04, R05, R13), 20 placeholders.
- **A:** 3 items, 13 placeholders.

Examples from the Aegean corpus:

| Source text | What the LLM saw |
|---|---|
| "## Who approves my request?" | "PERSON_0002 my request?" |
| "…shows you a proposal and submits it only after you confirm." | "…shows you a proposal ADDRESS_0007." |
| "…απαιτείται έγκριση του Τμήματος Ανθρώπινου Δυναμικού" (approval by the HR department) | "…απαιτείται έγκριση του ADDRESS_0004." |
| "Το Τμήμα Ανθρώπινου Δυναμικού ελέγχει τη βεβαίωση…" | "Το ADDRESS_0003 ελέγχει τη βεβαίωση…" |
| the HR department's e-mail address | `EMAIL_0001` (by design: e-mail addresses are always masked) |

Readers see the restored text, because answers are re-identified. The model, however, reasons over the
placeholders. For R02 the answer-bearing entity ("the HR department") reached the LLM as `ADDRESS_0004`.
- RAGAS cannot see this, because the judge receives the same masked contexts and the reference answer.
- Retrieval metrics do not see it either: they work on chunk ids.

This confirms R-30's M5 observation ("generic places as ADDRESS") on the live path. GLiNER and XLM-R recognisers were
not evaluated in M9; this is carried to M10. Options:
- an allow list of organisational terms per tenant (department names, "Human Resources");
- skipping NER on approved policy documents, whose text an HR reviewer has already read, while keeping the
  e-mail, phone and known-employee layers;
- measuring precision on the corpus first.

### 3.3 Cross-lingual judging lowers faithfulness

S03 is a Greek question to Brightwater, whose corpus is English. The answer is a faithful Greek rendering of the
handbook ("up to five (5) unused days…, by 31 March; days not taken by then expire"), yet it scored faithfulness 0.56
(B) and 0.78 (A). S08 (English, the same chunk) scored 0.75 / 0.83 for "The Employee Leave Handbook states… twenty
(20) working days", which is also a faithful answer.

Both are judge artefacts: claims checked across languages, and an attribution phrase counted as a claim. They are
not answer errors. A minor wording slip in S03 ("άχρηστες", useless, for "αχρησιμοποίητες", unused) does not change
the meaning. Without R02, the lowest faithfulness scores are these two items, so the cross-lingual case is where the
metric is least reliable.

### 3.4 Context precision: one extra chunk

B's only context precision below 1 is S07, "Πόσες ημέρες κανονικής άδειας δικαιούμαι;" (how many days of annual leave
am I entitled to?), at 0.83. The hybrid candidates put the FAQ answer first, followed by the sick-leave paragraph,
the leave-type table and the carry-over section; A put the policy paragraph first. Both answers were correct (20
days). Context recall was 1.0 for every item in both configurations, which on this clean seed corpus mostly shows
that the labels and the corpus are small (known weaknesses §1–2).

## 4. Caveats

- **The judge** is a larger model of the answer model's family (Medium and Small have no free quota), so
  shared-family bias is possible. Scores are comparative between the two configurations, not absolute (doc 11 §8).
- **The production primary (Gemini) was not measured** (R-03): its free tier allows 20 requests per day.
- **17 items** per configuration, 7 of them Greek. Differences under about 0.06 in faithfulness cannot be resolved.
- **Cost:** 3 repetitions × 2 configurations took about 4.5 hours of paced judge calls on the free plan (known
  weaknesses §13).

## 5. Reproduce

```bash
make eval-live EVAL_ANSWER_PROVIDERS=mistral   # answers for both configurations (≈ 80 provider calls)
make eval-ragas                                # 3 repetitions each; resumes from results/<date>-ragas.partial.jsonl
```
