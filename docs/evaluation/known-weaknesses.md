# Known weaknesses (M9)

What the M9 numbers do not show, and what they showed to be weak. Measured 2026-11-06; each item links to its
evidence. Risks with an owner and a contingency are in doc 13.

## Measurement limits
1. **Small dataset.** 50 retrievable chunks, 34 labelled retrieval queries, 18 RAGAS items, 11 live agent scenarios.
   Intervals are wide, and differences under about 3 points of nDCG@5 cannot be resolved. "No significant
   difference" (hybrid vs vector-only, Chroma vs pgvector) means "not detectable here", not "equal".
2. **Labels written by the developer** from the seed sources (pre-registered before any measurement, but one
   annotator, no agreement measure). The seed corpus is synthetic and cleaner than real policy documents.
3. **Few exact-term queries.** The full-text leg exists for codes, form names and exact phrases. `leave-eval-v1`
   is mostly natural-language questions, so it cannot show what the leg adds (§2 of the retrieval comparison).
4. **Gemini unmeasured live.** The free tier allows `gemini-3.6-flash` 20 requests per day, used up by the
   first answer run. RAGAS answers and the live agent scenarios ran on Mistral `ministral-14b-2512`, the
   production fallback. The production primary's answer quality and tool use are therefore not measured (doc 13 R-03).
5. **LLM judge.** RAGAS uses `mistral-large-4`, a larger model of the same family as the answer model; Medium and
   Small have no free quota. Shared-family bias is possible. Three repetitions at temperature 0 show the judge's
   own variance ([RAG quality](rag-quality.md)).
6. **One reference machine** (AVX-only i5-3320M, 7.3 GB, with swap in use). Latency figures do not transfer: on an
   AVX2/VNNI CPU the int8 reranker and candidate A would be re-evaluated.

## Product weaknesses found
7. **Verifier fallback counted as an answer** (R-36). When both drafts fail verification, users get a canned
   fallback, but the run outcome is `answered`: 2 of 18 live answers in both configurations. R02's draft cited
   sources it had not been given; R29 ("can I change an approved leave before it starts?") was classified as an
   action and the verifier demanded a `prepare_` tool.
8. **Tool use of the small answer model** (R-33). S13 ("3 days from 23 December") computed the working days but
   never prepared the request. S14/S15 asked for the leave type, so the scenario's validation failure was never
   reached. S19 passed a tool validation message to the user ("shorten the department name to 16
   characters"); the tool takes a department code and has no name lookup.
9. **Routing varies by provider.** S07 (a policy question, "how many days am I entitled to?") was answered from the
   user's balance (15) in one run, which had failed over from Gemini to Mistral, and from the policy (20) in the
   pinned run. The classifier's choice between `balance` and `policy_question` is not stable for this phrasing.
10. **Latency tail.** NFR-PERF-01's p50 passes (2.70 s), but p95 is 3.36 s. The first turn after a start takes about
    55 s (model loading); a warm-up at start-up hides it, unless a user arrives during the warm-up.
11. **ONNX memory.** The ONNX runtime needs more memory than PyTorch: peak 2.4 vs 1.6 GB for the reranker alone in
    the runtime harness, and 2.1 vs 2.0 GB for a whole chat process in `make chat-perf`. Two copies of the models in the 3 GB api container swapped heavily
    ([performance](performance.md) §1). Embedded-worker ingestion in the same container competes for the same
    memory.
12. **The hidden-footnote injection family is held for review, not quarantined** (2 of 2 fixtures). They never
    reach retrieval unreviewed, but they depend on an HR reviewer.
13. **No free judge for routine use.** `mistral-large-4` is metered at 20k tokens/min, so a full RAGAS run (2
    configurations × 3 repetitions) takes about two hours on the free plan.

## RAG quality findings
See [RAG quality](rag-quality.md) §3.
