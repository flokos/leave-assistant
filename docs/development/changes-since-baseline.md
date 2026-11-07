# Architectural changes since the baseline

The architecture package (`docs/00`–`14`, ADRs) was written on 2026-11-04, before any code. During implementation
some decisions changed because measurements, a library or a free-tier limit said otherwise. This page lists the
changes that matter for understanding the system as built (brief §13 "Problems encountered, architectural
changes"). Each row links to where it was decided and amended; smaller implementation choices are in each
milestone's worklog ("Decisions" and "Problems" sections).

| Milestone | Baseline said | As built | Why | Recorded in |
|---|---|---|---|---|
| M0 | pnpm for the frontend | npm | `corepack pnpm` failed on the reference host | worklog M0 |
| M0 | TypeScript "5.9+" | held at 5.9 | TypeScript 6/7 fall outside the peer ranges of `openapi-typescript` 7 and `typescript-eslint` 8 | worklog M0, doc 02 §6 |
| M1 | the tenant's Keycloak group id is trusted once stored | re-verified on every provisioning run | a re-imported realm has no groups | worklog M1, doc 08 §4 |
| M2 | requests readable by owner, approvers and HR | company administrators may read a request too, masked to "absence" | the permission matrix wins over the endpoint table; masking protects the reason and type | worklog M2, doc 09 |
| M4 | `document_versions.parsed_structure jsonb` | the block tree is stored in object storage (`extracted_object_key`) | keeps large structures out of PostgreSQL | worklog M4, doc 07 |
| M4 | a `VectorIndex` port from the start | search lived in the chunk repository until M9 introduced the port with the Chroma adapter | the port was only needed for the comparison | worklog M4, M9, ADR-0011 |
| M4 | NFR-PERF-02: a 30-page text PDF in ≤ 3 min | 273.5 s measured, target not met | Docling's layout and table models on a 4-thread CPU | R-28 (open) |
| M5 | one benign reference set for injection scoring | separate sets for documents and user input (`benign_v1`, `benign_user_v1`, M7) | e5 conflates Greek imperatives in short user messages with injections | worklog M5/M7, doc 08 §8 |
| M5 | Presidio's built-in e-mail and IBAN recognisers | bilingual pattern recognisers added | the built-in ones are registered for English only | doc 08 §7.6 |
| M6 | Groq as the failover provider | **Mistral** (free plan) as the live second provider; Groq selectable but untested | the Groq signup was blocked (user decision) | ADR-0010 amendment, doc 02 §7 |
| M6 | answers streamed token by token | answers stream after citation verification | an unverified draft must never reach the user | worklog M6, doc 04 |
| M6 | 20 rerank candidates at 512 tokens | 10 at 256 | NFR-PERF-01 on the reference CPU | doc 05 §6.7 |
| M6 | — | answer language enforced deterministically (Greek question → Greek answer, even from English sources) | user requirement | doc 04, doc 05 §6.6 |
| M6 | — | a scalar-safe `tanh` replaces MKL's vector kernel in the models | SIGILL on the AVX-only CPU | R-27, worklog M6 |
| M7 | every role-permitted tool bound to the model | tools bound per role **and** intent group | JSON tool schemas exceeded the token budget (1,600 tokens) | worklog M7, doc 04 |
| M7 | — | deterministic answer guards for small models (stated balance, no phantom actions) | live `ministral-14b` behaviour | worklog M7, R-33 |
| M8 | notifications created by n8n | the backend creates every in-app notification; n8n adds e-mail and schedules | keeps business events in the backend's transaction | ADR-0021 amendment, doc 06 |
| M8 | OAuth state as a signed blob | a hashed single-use nonce; PKCE verifier derived, not stored | no bearer token on the callback; replay protection | doc 10 §9 |
| M8 | backfill job for a new calendar | reconciliation re-creates missing events | the same mechanism repairs everything | doc 10 §9 |
| M9 | reranker chosen by evaluation, default B (PyTorch) | B on **ONNX fp32**, τ 0.5 → 0.22 | pre-registered rule: A is better but fails NFR-PERF-01; ONNX is the only B runtime that passes | ADR-0014, docs/evaluation |
| M9 | the full-text leg as designed | the language leg ORs stemmed terms | AND-ing every word matched almost nothing | doc 05 §6.2 amendment |
| M9 | RAGAS judge from Gemini/Groq | `mistral-large-4` | free quotas (Gemini 20 requests/day; Mistral Medium/Small 0/min) | docs/evaluation/README amendment |
| M9 | embedded api limit 3 GB | 3.5 GB | the ONNX reranker plus the ingestion child peaked at 3.06 GB | doc 02 §10, ADR-0025 |
| M10 | verifier fallback recorded as `answered` | its own outcome `fallback` | run metrics and RAGAS counted fallbacks as answers (R-36) | doc 04, doc 09 |
| M10 | NER output used as is for documents | document precision filter for statistical PERSON/ADDRESS findings | 15 of 15 such findings in the seed policies were false positives (R-30) | doc 08 §7.6, ADR-0017 |
| M10 | `platform_settings` as a key/value table | one-row `platform.settings` with typed columns: the defaults of new companies | provider configuration stays in the environment; only tenant defaults need storage | doc 07 §4.1, doc 09 §2.9 |
| M10 | `tenants.settings.max_upload_mb` | superseded by the security policy's upload limit (M5); not editable by companies | one source of truth for upload limits | doc 07 §4.1 |
| M10 | `scripts/backup.sh` | `scripts/backup.sh` + `scripts/restore.sh`, tested with a clean-clone install | doc 03 §6.5 | docs/operations/backup-restore.md |

Recommended changes to the brief made before implementation (Mailpit instead of MailHog, Langfuse v4 with Valkey,
the evaluation-session mode, and others) are in [doc 14](../14-decision-log-and-open-questions.md).
