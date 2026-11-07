# 15 — Technology justification

Why each technology and tool in this repository was chosen, what was considered instead, what it costs, and what
the implementation milestones (M0–M10) showed about it. This is the one place to read the justification for the
whole stack.

- Exact versions, licences and the resolver evidence are in [doc 02](02-technology-stack.md).
- The larger decisions have their own ADR ([adr/](adr/README.md)), which this page links instead of repeating.
- "Evidence" lines cite measurements and incidents recorded in the worklogs
  ([development/worklog/](development/worklog/)) and the evaluation ([evaluation/](evaluation/README.md)), all on the
  reference machine: i5-3320M, 2 cores / 4 threads, AVX without AVX2, 7.3 GB RAM, no GPU.

Selection principles, in order (brief §1, §4, §15): the brief's explicit selections are kept unless evidence argues
against them; local and free (no paid service, no cloud dependency at runtime); open licences that allow internal
use; fits the reference machine; the smallest number of moving parts that meets the requirement; mature and
maintained.

Each entry has the same shape: **Role** · **Why** · **Alternatives** · **+** advantages · **−** disadvantages ·
**Operations** · **Evidence**.

## Contents
1. [Platform and infrastructure](#1-platform-and-infrastructure)
2. [Backend runtime](#2-backend-runtime)
3. [Agent and LLM providers](#3-agent-and-llm-providers)
4. [Retrieval, documents and machine learning](#4-retrieval-documents-and-machine-learning)
5. [Security and privacy](#5-security-and-privacy)
6. [Integrations and automation](#6-integrations-and-automation)
7. [Frontend](#7-frontend)
8. [Observability and evaluation](#8-observability-and-evaluation)
9. [Quality tooling](#9-quality-tooling)
10. [Development tooling and process](#10-development-tooling-and-process)
11. [Technologies considered and not used](#11-technologies-considered-and-not-used)

---

## 1. Platform and infrastructure

### Docker Compose (with profiles)
- **Role:** the whole system runs locally as one Compose project (`deploy/compose.yaml`), in profiles `core`,
  `automation`, `observability`, `eval` ([ADR-0025](adr/ADR-0025-compose-profiles-and-resources.md)).
- **Why:** the brief requires local Docker Compose; profiles let the 7 GB machine run only what a task needs.
- **Alternatives:** everything always on (does not fit the machine); Kubernetes with kind/k3s (cluster operations
  for a single host).
- **+** one command per profile; healthchecks and `depends_on` order the start; memory limits per container.
- **−** no self-healing beyond restart policies; memory limits are caps, not reservations, so a full host still
  swaps or OOM-kills (seen in M8 and M9).
- **Operations:** `make up` / `make up-staged` (embedded worker, low-memory hosts); `make stats`, `make peaks`.
- **Evidence:** `core` idle ≈ 0.4 GB at M0, about 3 GB with models resident; M9 raised the embedded api limit to
  3.5 GB after one OOM kill (ONNX peak 3.06 GB). The project name is fixed (`leave-assistant`), so a second checkout
  reuses the same volumes (used by the M10 clean-clone test).

### PostgreSQL 18 (image `pgvector/pgvector:0.8.7-pg18`)
- **Role:** the system of record: four logical databases (`leave`, `keycloak`, `n8n`, `langfuse`), row-level
  security for tenant isolation, the job queue, the outbox, full-text search, vectors and the LangGraph checkpoints.
- **Why:** the brief selects PostgreSQL; one engine for transactions, RLS, FTS (with a Greek configuration) and
  vectors keeps permissions, keyword and vector search in one transaction and one backup.
- **Alternatives:** schema- or database-per-tenant ([ADR-0003](adr/ADR-0003-multitenancy-rls.md)); a separate
  search engine and vector store; Redis/Celery for queues ([ADR-0019](adr/ADR-0019-n8n-scope-and-job-queue.md)).
- **+** forced RLS with a `NOBYPASSRLS` runtime role is a second line of isolation under the application checks;
  `SELECT … FOR UPDATE SKIP LOCKED` makes a simple, durable job queue; the Greek FTS configuration exists.
- **−** RLS needs care (`SET LOCAL` per transaction, `NULLIF('')` policies, the owner role is forced too);
  one instance is a single point of failure (acceptable locally).
- **Operations:** `make migrate` (Alembic), `make backup` / `make restore` (pg_dump per database, M10).
- **Evidence:** RLS and the Greek FTS configuration verified empirically before any code (M0); a concurrency leak
  test proves the tenant context never leaks across pooled connections (M1); 50–130 MB in normal use.

### pgvector 0.8.7
- **Role:** the default vector index of document chunks (HNSW, cosine) behind the `VectorIndex` port.
- **Why:** vectors next to the rows they belong to, under the same RLS and transactions
  ([ADR-0011](adr/ADR-0011-retrieval-backend.md)).
- **Alternatives:** ChromaDB as the default (isolation enforced only in the application); a dedicated vector service.
- **+** permissions and visibility are SQL predicates in the same query; no extra service; backed up with the database.
- **−** tuning knobs are fewer than in dedicated engines; HNSW builds are memory-hungry for large corpora.
- **Evidence (M9):** identical rankings to Chroma on the same vectors for all 49 evaluation queries, and faster
  (search p50 99–108 ms against Chroma's 123–179 ms) — so it stays the default.

### Keycloak 26.8
- **Role:** identity provider: one realm, tenants as groups with a `tenant_id` claim, roles per workspace, PKCE
  login for the SPA, service accounts for provisioning and n8n ([ADR-0004](adr/ADR-0004-keycloak-single-realm.md)).
- **Why:** the brief selects Keycloak; it gives OIDC, an Admin API for dynamic tenant creation, and separation of
  platform and company administration.
- **Alternatives:** a realm per tenant (per-tenant discovery in the SPA, multiplied Admin API work); a custom user
  store (re-implementing password policies, PKCE, sessions).
- **+** standards-based tokens validated offline via JWKS; temporary passwords and password policies for free.
- **−** the heaviest core service after the api; realm import subtleties (`${VAR}` resolution at import time, existing
  realms never overwritten, `view-realm` needed by the admin client).
- **Operations:** the realm export is in the repository (`deploy/keycloak/`); `make token EMAIL=…` for tooling.
- **Evidence:** 210–528 MB (M0, M10); about 110 s to healthy on a first start.

### SeaweedFS 4.52 (S3 API)
- **Role:** object storage for uploaded documents, extracted structures, encrypted supporting documents and
  Langfuse's blobs, behind the `ObjectStorage` port ([ADR-0016](adr/ADR-0016-object-storage-and-encryption.md)).
- **Why:** the brief selects SeaweedFS; any S3-compatible store works with the same boto3 adapter.
- **Alternatives:** MinIO (equivalent through S3); `bytea` in PostgreSQL (bloats the database and its backups).
- **+** small (11–34 MB idle, about 200 MB under load); one container (`weed server -s3 -filer`); per-identity
  bucket permissions (Langfuse has its own identity limited to its bucket, M9).
- **−** less common than MinIO, so fewer answers online; health checks need both the master and the S3 port.
- **Evidence:** M9: when the host's OOM killer stopped SeaweedFS, Langfuse answered 500 until it was restarted —
  recorded in the troubleshooting guide.

### nginx (frontend container)
- **Role:** serves the built SPA and proxies `/api` to the backend, so the browser sees one origin.
- **Why:** the smallest production-grade static server and reverse proxy; no CORS configuration needed.
- **Alternatives:** Vite's preview server (not for production); serving the SPA from FastAPI (couples the two).
- **Evidence:** 5–20 MB.

## 2. Backend runtime

### Python 3.14
- **Role:** the backend, the worker, the evaluation harness and the tooling scripts.
- **Why:** the newest release on which the complete dependency set resolves, installs and imports (verified with
  uv at M0); free-threading is not used ([ADR-0026](adr/ADR-0026-python-version-and-dependency-pinning.md)).
- **Alternatives:** 3.12/3.13 "to be safe" — 3.13 is kept as a zero-cost fallback with an identical lock.
- **−** a few compiled dependencies lagged (recorded as R-21); none blocked the project.

### FastAPI 0.162 + Uvicorn + sse-starlette
- **Role:** the REST API (`/api/v1`), RFC 9457 problem details, idempotency middleware, and the chat stream as
  server-sent events.
- **Why:** the brief selects FastAPI; typed request/response models generate the OpenAPI contract that the frontend
  types are built from (`make contract`).
- **Alternatives:** Django REST framework (heavier, ORM-coupled); Litestar (smaller ecosystem); WebSockets for chat
  (bidirectional not needed; SSE passes proxies and reconnects simply).
- **+** async end to end; dependency injection for principals and services; OpenAPI for free.
- **−** version-specific details matter (0.162 returns plain `Response` for JSON; `_IncludedRouter` entries).

### Pydantic 2 and pydantic-settings
- **Role:** API schemas, tool argument models (the gateway validates every LLM tool call), settings from the
  environment.
- **Why:** FastAPI's native model layer; strict validation is the boundary between the LLM and the business code.
- **Evidence:** M9 found that an empty variable in `.env` overrode a default; settings now use
  `env_ignore_empty=True`.

### SQLAlchemy 2.1 (async) + psycopg 3 + Alembic
- **Role:** repositories behind the application ports, one async driver for SQLAlchemy and the LangGraph
  checkpointer, schema migrations (0001–0008).
- **Why:** mature, typed (`Mapped[...]`), and works with RLS through `SET LOCAL` per transaction.
- **Alternatives:** asyncpg (would need a second driver for the checkpointer); an ORM-free query layer (more code);
  Django ORM (framework coupling).
- **−** async sessions need discipline (no lazy loading); migrations are hand-written for RLS policies and grants.

### structlog
- **Role:** JSON logs with request, tenant, thread and job ids; a scrubbing processor masks e-mails, phones, AMKA,
  AFM and IBANs in free text (doc 08 §7.5).
- **Why:** structured logs are greppable and the processor chain is the natural place for PII scrubbing.
- **Alternatives:** the standard `logging` module with formatters (no processor chain).

### httpx, tenacity, PyJWT, cryptography, boto3
- **httpx:** async HTTP client for Keycloak, the internal API tests and the harness (also the test transport).
- **tenacity:** bounded retries with backoff around provider and storage calls; the router owns failover itself.
- **PyJWT (+crypto):** offline JWT validation against Keycloak's JWKS; no call to Keycloak per request.
- **cryptography:** AES-256-GCM envelope encryption (supporting documents, OAuth refresh tokens, vault originals).
- **boto3:** the S3 client for SeaweedFS (the de-facto standard; path-style addressing).

### Modular monolith with a separate worker process
- **Role:** one backend codebase, two process roles: the API and the job worker (Docling, OCR, embeddings), or the
  worker embedded in the API on small hosts ([ADR-0001](adr/ADR-0001-modular-monolith.md)).
- **Why:** strong module boundaries (import-linter) without the operations of microservices; heavy ML work isolated
  from request handling.
- **Evidence:** ingestion runs in a child process so a SIGILL or OOM in Docling never takes the API down (M4).

## 3. Agent and LLM providers

### LangGraph 1.2 (with langgraph-checkpoint-postgres)
- **Role:** the agent: an orchestrator with knowledge, business and approval subgraphs, interrupts for
  human-in-the-loop confirmation, checkpoints in PostgreSQL ([ADR-0007](adr/ADR-0007-langgraph-topology.md),
  [ADR-0008](adr/ADR-0008-langgraph-checkpointer.md)).
- **Why:** the brief selects LangGraph; explicit nodes make the deterministic steps (screening, pseudonymisation,
  grounding checks, the tool gateway) visible and testable, and interrupts implement "propose → confirm → execute".
- **Alternatives:** a free-form ReAct agent (no demonstrable routing); several autonomous agents and a plain chat
  loop (both rejected by the brief).
- **+** durable interrupts survive restarts; per-node tests with a fake provider.
- **−** API details bit twice: subgraphs need an `output_schema` (an M6 history-duplication bug), and new input on
  an interrupted thread drops the interrupt (M7).
- **Evidence:** M10 added a `fallback` outcome when the verifier rejects every draft, so run metrics no longer count
  it as an answer (R-36).

### langchain-core and the provider packages (langchain-google-genai, langchain-mistralai, langchain-groq)
- **Role:** one chat-model interface with native tool calling and streaming across providers, used by the provider
  router ([ADR-0010](adr/ADR-0010-llm-provider-router.md)).
- **Why:** tool-calling schemas and streaming differ per provider; the LangChain wrappers normalise them, and
  LangGraph and RAGAS use the same interface.
- **Alternatives:** native SDKs (three tool-call formats to maintain); a gateway proxy such as LiteLLM or OpenRouter
  (an extra service or cloud dependency).
- **−** wrapper bugs: langchain-mistralai 1.1.6 crashes when it sums nested usage (patched locally in the evaluation
  project, M9); Gemini 3 thought signatures and Mistral's 9-character tool ids needed handling (M7).

### Gemini (Google AI Studio free tier) — primary provider
- **Role:** `gemini-3.6-flash` answers and plans; `gemini-3.5-flash-lite` classifies.
- **Why:** the brief selects Gemini; strong multilingual (Greek) quality and native tool calling at no cost.
- **Alternatives:** paid tiers (forbidden by the brief and ADR-0029).
- **−** the free tier allows **20 requests per day** for the answer model (measured in M9), content may be used by
  Google (acceptable only because all data is fictional), thinking tokens count against the output cap (M6).
- **Evidence:** M6 hit `429 RESOURCE_EXHAUSTED` after about ten calls; M9 could not run the evaluation on Gemini; M10
  ran a small spot check within one day's quota (docs/evaluation/summary.md).

### Mistral (free "Experiment" plan) — second provider
- **Role:** failover provider (`ministral-14b-2512` answers, `ministral-8b-2512` classifies) and, for comparability,
  the provider of the M9 evaluation; `mistral-large-4` is the RAGAS judge.
- **Why:** the Groq signup was blocked during M6, and Mistral's free plan offers tool calling and enough requests
  per minute; it replaced Groq as the live second provider (user decision, ADR-0010 amendment).
- **Alternatives:** Groq (selected by the brief, kept selectable but untested live); OpenRouter free models (another
  intermediary).
- **−** small models need deterministic guards (grounded numbers, stated balance, no phantom "I prepared…");
  Medium and Small have 0 requests/minute on the free plan, so the judge is a large model of the same family.
- **Evidence (M9):** live agent tool selection 10/11, action success 10/11, unauthorized actions 0; RAGAS
  faithfulness 0.887 on its answers.

### Groq — selected by the brief, not used live
- **Role:** a configured provider (`LLM_PROVIDER_ORDER` accepts it; models `llama-3.1-8b-instant` and
  `llama-3.3-70b-versatile`).
- **Why it is not live:** the account signup was blocked during M6. Its free caps (8–12K tokens/minute) also shaped
  the token budget of every prompt (doc 04 §8), which stays.

### The fake provider
- **Role:** a scripted, deterministic chat model for unit, integration and end-to-end tests
  ([ADR-0023](adr/ADR-0023-testing-and-evaluation.md)).
- **Why:** correctness tests must not depend on quota, latency or model drift.
- **Evidence:** the 14 end-to-end specs and the agent unit suites run with it (`LLM_PROVIDER_ORDER=fake`).

## 4. Retrieval, documents and machine learning

### Hybrid retrieval: PostgreSQL FTS + vectors + reciprocal rank fusion + a cross-encoder reranker
- **Role:** candidates from the Greek/English/simple full-text indexes and from pgvector, fused by RRF, rescored by a
  local reranker, cut by an evidence threshold τ ([ADR-0012](adr/ADR-0012-hybrid-retrieval.md)).
- **Why:** vectors find paraphrases across languages; full text finds exact codes, form names and phrases; the
  reranker orders both.
- **Alternatives:** Elasticsearch/OpenSearch or ParadeDB (another heavy service); in-process BM25 (no index, no
  permission predicate); vector-only.
- **Evidence (M9):** the full-text leg had matched almost nothing (it ANDed every word); fixed. On the natural-language
  evaluation queries vector-only was +0.009 nDCG@5 (interval touching 0), so hybrid stays for exact terms the dataset
  under-represents. τ recalibrated from 0.5 to 0.22.

### Sentence Transformers 5.7 + `intfloat/multilingual-e5-small`
- **Role:** passage and query embeddings (384 dimensions), CPU only, with index generations for model changes
  ([ADR-0013](adr/ADR-0013-embeddings-e5-small.md)).
- **Why:** the brief prefers multilingual-e5-small; it handles Greek and cross-lingual queries at CPU cost the
  reference machine can carry.
- **Alternatives:** e5-base/large or bge-m3 dense (3–10× CPU); hosted embeddings (forbidden).
- **−** sentence-transformers 6 requires transformers 5, which Docling does not support, so 5.7 is pinned (doc 02 §9).
- **Evidence:** baseline nDCG@5 0.883 and hit@5 0.971 on 34 labelled queries (M9).

### Cross-encoder reranker `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (candidate B) on ONNX Runtime
- **Role:** rescores 10 candidates per question ([ADR-0014](adr/ADR-0014-reranker-selection.md)).
- **Why:** chosen by the pre-registered M9 evaluation: candidate A (`BAAI/bge-reranker-v2-m3`) is better
  (+0.034 nDCG@5) but about 13× slower per turn excluding LLM time (35.4 vs 2.6 s), which fails NFR-PERF-01.
- **Alternatives:** no reranker (−0.055 nDCG@5); LLM reranking (more content to providers, quota); Jina rerankers
  (licence excluded them, ADR-0014).
- **Evidence:** ONNX fp32 gives the same scores as PyTorch, rerank p50 1.88 s vs 2.72 s, and a chat turn p50 2.70 s
  (NFR-PERF-01 met); int8 is slower on this AVX-only CPU.

### ONNX Runtime 1.31 (through sentence-transformers' ONNX backend)
- **Role:** the reranker's runtime (`RERANKER_BACKEND=onnx`); the model is exported once by `make models`.
- **Why:** 31 % faster than PyTorch at the same scores on the reference CPU (M9).
- **−** about 800 MB more peak memory than PyTorch for the reranker; the embedded api limit was raised to 3.5 GB.

### PyTorch 2.15 (CPU index)
- **Role:** runtime of the embedding model, Docling's layout and table models, and the PyTorch reranker variant.
- **Why:** required by sentence-transformers and Docling; the CPU wheel index avoids gigabytes of CUDA libraries.
- **−** MKL's vector `tanh` kernel crashes with SIGILL on this AVX-only CPU (R-27); a scalar-safe `tanh` patch
  (`infrastructure/ml/safe_tanh.py`) fixed it in M5. torchvision must come from the same index.

### Docling 2.147 + Tesseract OCR
- **Role:** parses PDF, DOCX, Markdown, text and scans into structured blocks (headings, tables, pages); Tesseract
  (CLI, `ell` + `eng`) reads bitmap pages ([ADR-0015](adr/ADR-0015-docling-and-chunking.md)).
- **Why:** the brief selects Docling; its structure feeds the structure-aware chunker (no fixed-size chunks, as the
  brief requires). Tesseract is smaller and more deterministic than Docling's default EasyOCR, with good Greek.
- **Alternatives:** Unstructured or PyMuPDF (less table fidelity); EasyOCR (heavier torch models); RapidOCR (weak Greek).
- **−** slow on this CPU: about 60 s per document including the child start; a 30-page text PDF took 273.5 s against a
  3-minute target (R-28, open); Docling raises at start when OCR is enabled and Tesseract is missing.
- **Operations:** runs only in the worker (child process, lazy model load); `make ingest`, `make reindex`.

### lingua-language-detector
- **Role:** detects Greek or English per message and per document; drives the answer language and the FTS
  configuration.
- **Why:** accurate on short texts, offline, deterministic.
- **Evidence:** answer language matched the question's in 47 of 47 live turns (M9).

### ChromaDB 1.5.9 — evaluation only
- **Role:** the second implementation of the `VectorIndex` port, in the `eval` profile, for the comparison the brief
  asks for.
- **Why kept:** the brief selects it; the port keeps the comparison honest (same corpus, same vectors).
- **Why not the default:** isolation would be enforced only in the application, and vectors would leave the
  database's transactions and backups. M9 measured the same rankings and slower searches.

## 5. Security and privacy

### Microsoft Presidio (analyzer + anonymizer) with spaCy `en_core_web_lg` and `el_core_news_md`
- **Role:** PII detection for user messages and document chunks (tool results map employees through the directory); custom recognisers for Greek
  identifiers (AMKA, AFM, ID card, phones) and a known-entity layer for every employee
  ([ADR-0017](adr/ADR-0017-pii-pseudonymisation-vault.md)).
- **Why:** the brief selects Presidio; it is pluggable, so the Greek recognisers and the directory layer fit in.
- **Alternatives:** transformer NER (GLiNER, XLM-R) — better recall possibly, much heavier; kept as a swap path.
- **−** Presidio's e-mail and IBAN recognisers are English-only (bilingual ones were added, M5); spaCy NER tags
  ordinary policy words as persons and places. M9 measured it in the evidence the LLM reads; M10 measured 15 of 15
  statistical PERSON/ADDRESS findings in the seed policy documents as false positives and added a document precision
  filter (doc 08 §7.6, R-30).
- **Operations:** about 20 s lazy load in the worker child; `make rescan` / `make reindex` re-apply it.

### The pseudonym vault (application code)
- **Role:** stable placeholders per tenant (`PERSON_0042`), HMAC lookup keys, AES-GCM-encrypted originals, and a
  provenance-aware re-identification step.
- **Why:** the model reasons about consistent placeholders without ever seeing a name, and re-identification
  follows the role matrix of doc 08 §7.4.
- **Evidence:** the M9 Langfuse trace check found no e-mail, phone or name in any trace — only placeholders.

### Prompt-injection defence (heuristics + an e5 contrastive classifier + risk tiers)
- **Role:** documents are scored at ingestion (low → published, medium → review, high → quarantine); user messages
  are screened; tools are allow-listed and validated ([ADR-0018](adr/ADR-0018-prompt-injection-defence.md)).
- **Why:** defence in depth; no available classifier is Greek-trained, so the multilingual embedding model plus
  Greek heuristics is the practical choice.
- **Alternatives:** Llama Prompt Guard 2 or ProtectAI DeBERTa (English-centric, gated); LLM-only judging (costs quota,
  is itself injectable).
- **Evidence (M9):** 18/18 document fixtures at the expected tier (hidden footnotes held for review, not
  quarantined), 0 false positives on the benign corpus, 8/8 chat attacks blocked, 0/10 ordinary commands blocked.

## 6. Integrations and automation

### n8n 2.46 Community Edition (profile `automation`)
- **Role:** e-mail notification workflows, reminders and the provider-health digest, consuming the backend's
  transactional outbox through an internal API ([ADR-0019](adr/ADR-0019-n8n-scope-and-job-queue.md),
  [ADR-0021](adr/ADR-0021-notifications-mailpit.md)).
- **Why:** the brief selects n8n for automation; polling an outbox keeps business rules and credentials in the backend.
- **Alternatives:** the backend sending SMTP itself; webhooks into n8n (inbound auth and retries on the backend).
- **−** the Sustainable Use License allows internal use only (re-verified, no `.ee.` features used); the default
  JavaScript heap crashed under 512 MB (now 1 GB / 768 MB heap); 140–560 MB.
- **Evidence:** M8 found that a workflow path without output withheld acknowledgements, and that fire-and-forget
  sub-workflows fail; both fixed, with unit tests over the workflow JSON.

### Mailpit 1.33
- **Role:** captures every e-mail locally (UI on :8025).
- **Why:** the brief names MailHog, which is unmaintained; Mailpit is its maintained successor with the same ports.
- **Evidence:** 5–30 MB.

### Google Calendar API (google-api-python-client, google-auth, google-auth-oauthlib) + a mock adapter
- **Role:** approved absences are written to a company calendar and, optionally, personal calendars through an
  outbox with deterministic event ids and reconciliation ([ADR-0020](adr/ADR-0020-calendar-sync.md)).
- **Why:** the brief requires Google Calendar; the official client handles discovery, OAuth and retries; the mock
  adapter is the default so nothing depends on a Google account.
- **Alternatives:** synchronous calls during approval (couples the transaction to Google); n8n doing the Google calls
  (credentials outside the backend).
- **−** OAuth clients in *Testing* status issue refresh tokens that expire after 7 days (R-34); the client uses
  httplib2, so tests use its `HttpMockSequence` instead of respx.
- **Evidence:** the manual Google checklist was deferred by the user to the end of M10 (doc 10 §9).

## 7. Frontend

### React 19 + TypeScript 5.9 + Vite 8
- **Role:** the single-page application with the five workspaces of brief §11
  ([ADR-0024](adr/ADR-0024-frontend-stack.md)).
- **Why:** the brief selects React with TypeScript instead of Streamlit; Vite is the standard fast build tool.
- **Alternatives:** Next.js (server rendering not needed, adds a server runtime).
- **−** TypeScript is held at 5.9: TypeScript 7 is outside `typescript-eslint` 8's peer range and TypeScript 6
  outside `openapi-typescript` 7's (found in M0).

### Ant Design 6
- **Role:** components (tables, forms, drawers, date pickers) with built-in Greek and English locales.
- **Why:** dense tables and forms suit administration-heavy screens.
- **Alternatives:** MUI (comparable); shadcn/ui + Tailwind (more assembly for enterprise widgets).
- **−** under jsdom it needs `matchMedia`/`ResizeObserver` stubs and is slow (4–6 s per table or modal test); some
  components drop `data-testid` (wrapped in a `div`).

### openapi-typescript + openapi-fetch
- **Role:** the frontend's API types are generated from the backend's OpenAPI snapshot; the client adds the bearer
  token, an `Idempotency-Key` on every mutation and RFC 9457 error mapping.
- **Why:** the contract is checked in CI (`make contract`); a backend change that breaks the UI fails at type-check.

### TanStack Query 5, React Router, react-i18next / i18next, react-oidc-context / oidc-client-ts, dayjs
- **TanStack Query:** server state, caching and invalidation; no global store needed.
- **React Router:** lazy routes per workspace, guarded by roles (UX only; the API re-authorises every call).
- **react-i18next:** every string in Greek and English; a test fails when a key is missing in either language.
- **react-oidc-context / oidc-client-ts:** PKCE login against Keycloak and silent token renewal; the API client
  reads the current access token through a getter (`auth/token.ts`).
- **dayjs:** date handling for the pickers (Ant Design's date library).

## 8. Observability and evaluation

### Langfuse v4 (self-hosted, profile `observability`) with ClickHouse 26.3 and Valkey 8.1
- **Role:** masked traces of agent runs, the evaluation dataset `leave-eval-v1` and experiments with RAGAS scores
  ([ADR-0022](adr/ADR-0022-langfuse-optional-profile.md)).
- **Why:** the brief selects Langfuse; self-hosting keeps traces on the machine.
- **Alternatives:** Langfuse Cloud (traces leave the host); OpenTelemetry with Jaeger/Tempo (no LLM views or
  datasets); Langfuse v2 (end-of-life).
- **−** v4 needs ClickHouse, Redis-compatible storage and S3; a low-memory ClickHouse configuration was required;
  v4 removed the trace list API and, in events-only mode, score listing (M9).
- **Operations:** optional; started only for evaluation sessions on the 7 GB machine.
- **Evidence:** about 1.3 GB at peak for web, worker, ClickHouse and Valkey (M9).

### RAGAS 0.4.3 (separate `evaluation/` uv project)
- **Role:** faithfulness, answer relevancy, context precision and recall of live answers, judged by an LLM
  ([ADR-0023](adr/ADR-0023-testing-and-evaluation.md)).
- **Why:** the brief selects RAGAS; a separate project keeps its heavy dependency tree out of the backend image.
- **Alternatives:** DeepEval and similar frameworks.
- **−** needs a judge with quota: three repetitions of two configurations took about 4.5 hours on the free plan;
  scores are comparative, not absolute (judge bias).
- **Evidence (M9):** faithfulness 0.887 for the deployed configuration (target 0.85); judge variance small (sd ≤ 0.018).

### The evaluation harness (application code, `leave_assistant.evaluation`)
- **Role:** retrieval metrics with bootstrap intervals, live answer capture with the exact evidence the model saw,
  live agent metrics, security figures, latency, PII precision (M10), the Langfuse trace check.
- **Why:** the decision rule was pre-registered before any measurement; deterministic metrics need no LLM.

### Langfuse Python SDK 5.1
- **Role:** the backend's LangChain callback with a `mask` hook (second line of defence) and the evaluation sync.

## 9. Quality tooling

### pytest (+ pytest-asyncio, pytest-cov, hypothesis, respx, freezegun)
- **Role:** unit tests on in-memory fakes, integration tests against the real PostgreSQL, Keycloak and SeaweedFS,
  security suites, scenario tests on the seed corpus.
- **Why:** the brief selects pytest. hypothesis checks the working-day and ledger arithmetic with generated inputs;
  respx fakes HTTP providers; freezegun fixes time for rollover and retention.
- **Evidence:** M9: 915 backend unit and 82 integration tests; the M10 figures are in worklog M10.

### Vitest + Testing Library + jsdom
- **Role:** component and page tests of the SPA.
- **Why:** shares Vite's configuration and TypeScript transform; Testing Library tests what the user sees.

### Playwright
- **Role:** end-to-end tests through the real Keycloak login and the running stack.
- **Why:** reliable cross-browser automation with downloads, traces and screenshots.
- **Evidence:** 14 specs at M9, extended with the settings and export flows in M10.

### Ruff, mypy (strict), import-linter, ESLint, Prettier, pip-audit
- **Ruff:** linting and formatting (line length 110), one fast tool instead of flake8 + isort + black.
- **mypy strict:** type discipline matters here — `LlmSafeText` is a distinct type, so passing raw text to a
  prompt builder is a type error.
- **import-linter:** enforces the layers (API → application → domain; agent outside application) of
  [ADR-0002](adr/ADR-0002-layered-architecture.md); four contracts.
- **ESLint + Prettier:** the frontend equivalents.
- **pip-audit:** in the dev group for checking the locked set against known vulnerabilities
  (`uv run --no-sync pip-audit`); not part of the gates.

## 10. Development tooling and process

### uv (and its lock files)
- **Role:** Python version management, dependency resolution with a universal lock, virtual environments, the CPU
  wheel index for torch ([ADR-0026](adr/ADR-0026-python-version-and-dependency-pinning.md)).
- **Why:** one fast tool; the resolver is what verified the version set (doc 02 §1), rather than "latest of each".
- **Alternatives:** pip-tools, Poetry.
- **−** adding a package to the large lock forces a long image rebuild (about 10 minutes).

### npm and Node 24 (build image) / 22 (host)
- **Role:** frontend dependencies and builds. `corepack pnpm` failed on the host, so npm is used (M3).

### Make
- **Role:** the developer interface: every operation in the documentation is a `make` target (`make help`).
- **Why:** available everywhere, no extra runtime; targets are thin wrappers over Compose and the tools.

### GitHub and GitHub Actions (optional CI)
- **Role:** the private repository, one branch and pull request per milestone, CI mirroring `make lint`,
  `make test-unit` and `make contract` ([ADR-0029](adr/ADR-0029-operating-decisions.md)).
- **Why:** the repository must pass locally first; hosted CI is a convenience, not a dependency.

### Claude Code (development assistant only)
- **Role:** proposes plans and changes during development; the engineer reviews and keeps accountability
  ([development/process.md](development/process.md)).
- **Why:** the brief describes an AI-assisted process; it is not a runtime component and the application never
  calls it.

## 11. Technologies considered and not used

| Technology | Considered for | Why not |
|---|---|---|
| Microservices | backend structure | operations and duplicated auth/tenancy with no benefit at local scale (ADR-0001) |
| Celery / RQ + Redis | background jobs | a broker and a second durability model next to PostgreSQL (ADR-0019) |
| Schema- or database-per-tenant | isolation | migrations × N and runtime DDL for dynamic tenants (ADR-0003) |
| Realm per tenant (Keycloak) | identity | per-tenant discovery in the SPA, multiplied admin work (ADR-0004) |
| MinIO | object storage | the brief selects SeaweedFS; same S3 adapter either way (ADR-0016) |
| Elasticsearch / OpenSearch / ParadeDB | keyword search | another heavy service or extension (ADR-0012) |
| ChromaDB as the default | vectors | isolation in the application only; measured slower with identical rankings (ADR-0011, M9) |
| Larger embedding models (e5-base/large, bge-m3) | embeddings | 3–10× CPU cost on the reference machine (ADR-0013) |
| `BAAI/bge-reranker-v2-m3` (candidate A) | reranking | better but about 13× slower per turn; fails NFR-PERF-01 (ADR-0014, M9) |
| Jina rerankers | reranking | licence (ADR-0014) |
| int8 quantised reranker | latency | slower than fp32 on an AVX-only CPU (M9) |
| EasyOCR / RapidOCR | OCR | heavier models / weak Greek (ADR-0015) |
| GLiNER / XLM-R NER | PII detection | heavier; the measured errors were removable by deterministic filters (ADR-0017, M10) |
| Llama Prompt Guard 2, ProtectAI DeBERTa | injection detection | English-centric, gated (ADR-0018) |
| LiteLLM / OpenRouter | provider routing | an extra service or cloud intermediary (ADR-0010) |
| Paid LLM tiers | providers | the brief forbids paid services (ADR-0029) |
| Langfuse Cloud / v2, Jaeger/Tempo | observability | traces leave the host / end-of-life / no LLM views (ADR-0022) |
| DeepEval | evaluation | the brief selects RAGAS (ADR-0023) |
| MailHog | e-mail capture | unmaintained; Mailpit is the drop-in successor (ADR-0021) |
| Next.js, MUI, shadcn/ui + Tailwind, Streamlit | frontend | SSR not needed / comparable / more assembly / rejected by the brief (ADR-0024) |
| Kubernetes (kind/k3s) | deployment | cluster operations for one host (ADR-0025) |
| Poetry, pip-tools | Python packaging | uv is faster and handles the universal lock and the CPU index (ADR-0026) |
