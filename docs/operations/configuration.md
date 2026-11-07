# Configuration reference

Generated from [`deploy/.env.example`](../../deploy/.env.example) by `scripts/env_reference.py` (`make docs-env`);
`make contract` fails when this page is out of date. Copy the example with `make env`, which also generates every
empty key and password into the git-ignored `deploy/.env`. The backend reads the variables through
`backend/src/leave_assistant/config.py` (pydantic-settings: an empty value means "use the default", unknown values of
enumerated flags fail at start-up); Compose passes them to the containers (`deploy/compose.yaml`).

Secrets are never shown here: *(your key)* is a key from an external account you add yourself; *(generated)* means
`make env` fills an empty value with a random one; *(dev value)*
means the example carries a development value (for example `postgres-dev`) that must be replaced on anything but a
local, non-exposed machine. Neither belongs in git. The milestone in each section title says when the variables were
introduced.


## PostgreSQL [M0]

| Variable | Example | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | *(dev value)* | (dev) superuser; used only by init scripts and local tooling |
| `LEAVE_DB_PASSWORD` | *(dev value)* | (dev) role app_rls — runtime role, NOBYPASSRLS (ADR-0003) |
| `LEAVE_DB_OWNER_PASSWORD` | *(dev value)* | (dev) role app_owner — migrations [M1] |
| `LANGGRAPH_DB_PASSWORD` | *(dev value)* | (dev) role langgraph_rw — checkpointer schema [M6] |
| `KEYCLOAK_DB_PASSWORD` | *(dev value)* | (dev) Keycloak's own database |
| `N8N_DB_PASSWORD` | *(dev value)* | (dev) [M8] |
| `LANGFUSE_DB_PASSWORD` | *(dev value)* | (dev) [M9] |

## Keycloak [M0/M1]

| Variable | Example | Purpose |
|---|---|---|
| `KC_BOOTSTRAP_ADMIN_USERNAME` | `admin` |  |
| `KC_BOOTSTRAP_ADMIN_PASSWORD` | *(dev value)* | (dev) console at http://localhost:8081 |
| `KC_PUBLIC_URL` | `http://localhost:8081` | issuer of tokens; what the browser uses |
| `KEYCLOAK_ADMIN_CLIENT_SECRET` | *(dev value)* | (dev) backend-admin service account (provisioning) [M1] |
| `N8N_CLIENT_SECRET` | *(dev value)* | (dev) n8n-automation service account [M8] |
| `SEED_USER_PASSWORD` | *(dev value)* | (dev) password of platform.admin and every seeded user [M1] |

## SeaweedFS S3 [M0]

| Variable | Example | Purpose |
|---|---|---|
| `S3_ACCESS_KEY` | *(dev value)* |  |
| `S3_SECRET_KEY` | *(dev value)* | (dev) |
| `S3_BUCKET_DOCUMENTS` | `leave-documents` |  |

## Application [M0]

| Variable | Example | Purpose |
|---|---|---|
| `APP_ENV` | `dev` | dev \| test \| prod |
| `LOG_LEVEL` | `INFO` |  |
| `WORKER_MODE` | `separate` | embedded on low-memory hosts (then: make up-staged) |
| `API_PORT` | `8000` | published only by compose.dev.yaml |
| `FRONTEND_PORT` | `8080` |  |
| `KEYCLOAK_PORT` | `8081` |  |
| `MAILPIT_PORT` | `8025` |  |
| `POSTGRES_PORT` | `5432` | published on 127.0.0.1 only (host-side tests, psql) [M1] |

## Documents and ingestion [M4] (doc 05)

| Variable | Example | Purpose |
|---|---|---|
| `MAX_UPLOAD_MB` | `25` | per file; pages are capped by MAX_DOCUMENT_PAGES (500) in the backend |
| `DOCUMENT_PROCESS_TIMEOUT_SECONDS` | `900` | wall clock per document job (Docling + OCR run in a child process) |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-small` | revision pinned in config.py (ADR-0013) |
| `OCR_LANGUAGES` | `ell,eng` | Tesseract language packs installed in the image |
| `TORCH_THREADS` | `2` | api: query embeddings only |
| `WORKER_TORCH_THREADS` | `4` | worker child: parsing + passage embeddings |
| `MKL_CBWR` | `AUTO` | set AVX on CPUs without AVX2 (illegal-instruction crashes, R-27) |
| `API_MEMORY_LIMIT` | `2G` | make up-staged uses 3584M (embedded worker; M9 measured a 3.06 GB peak) |

## Feature flags [M4+] (doc 03 §7)

| Variable | Example | Purpose |
|---|---|---|
| `RETRIEVAL_BACKEND` | `pgvector` | pgvector \| chroma (eval profile) |
| `CALENDAR_PROVIDER` | `mock` | mock \| google |
| `LANGFUSE_ENABLED` | `false` |  |
| `LLM_PROVIDER_ORDER` | `gemini,mistral,groq` | decided Q-04: Gemini primary; Mistral second (Groq signup blocked, worklog M6); providers without a key are skipped |

## LLM providers [M6] — free tiers, fictional data only (ADR-0029)

| Variable | Example | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *(your key)* | aistudio.google.com → Get API key (free tier; content may be used by Google) |
| `GROQ_API_KEY` | *(your key)* | console.groq.com → API Keys (free tier) |
| `MISTRAL_API_KEY` | *(your key)* | console.mistral.ai → API Keys (free Experiment plan, phone verification) |
| `GEMINI_MODEL_FAST` | `gemini-3.5-flash-lite` | classify / condense / summarise (stable, verified 2026-11-05) |
| `GEMINI_MODEL` | `gemini-3.6-flash` | grounded answers |
| `GEMINI_THINKING_LEVEL` | `low` | Gemini 3 thinking depth (minimal\|low\|medium\|high); thoughts count against the output cap |
| `GEMINI_THINKING_TOKENS` | *(dev value)* | allowance for thoughts on top of the 700-token answer budget |
| `GROQ_MODEL_FAST` | `llama-3.1-8b-instant` |  |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` |  |
| `MISTRAL_MODEL_FAST` | `ministral-8b-2512` | free plan: 188 req/min |
| `MISTRAL_MODEL` | `ministral-14b-2512` | free plan: 30 req/min |
| `LLM_TIMEOUT_SECONDS` | `25` | per call (doc 04 §9); retries ×2 then failover in LLM_PROVIDER_ORDER |
| `RERANKER_MODEL` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | candidate B (default); A: BAAI/bge-reranker-v2-m3 (ADR-0014) |
| `RETRIEVAL_TAU` | `0.22` | evidence threshold on reranker B's score (calibrated in M9, docs/evaluation) |
| `RERANK_CANDIDATES` | `10` | fused candidates rescored per question (doc 05 allows up to 20) |
| `RERANK_MAX_LENGTH` | `256` | tokens per (query, passage) pair |
| `CONVERSATION_RETENTION_DAYS` | `90` | chats and checkpoints deleted automatically (Q-08/Q-14) |

## Content security and privacy [M5] (doc 08 §7–§10)

Keys: base64, 32 bytes. `make env` fills empty values with `openssl rand -base64 32` (local use only; never commit deploy/.env). Without them the API refuses supporting-document uploads and documents cannot be indexed (fail closed). Rotating a key invalidates what it protects: APP_KEK → stored supporting documents, VAULT_KEK → vault originals, VAULT_HMAC_KEY → placeholder stability.


| Variable | Example | Purpose |
|---|---|---|
| `APP_KEK` | *(generated)* |  |
| `VAULT_HMAC_KEY` | *(generated)* |  |
| `VAULT_KEK` | *(generated)* |  |
| `PII_SCORE_THRESHOLD` | `0.4` | Presidio confidence below which a finding is ignored |
| `SUPPORTING_MAX_UPLOAD_MB` | `10` | PDF / JPEG / PNG attached to leave requests |
| `DOWNLOAD_TOKEN_TTL_SECONDS` | *(dev value)* | single-use supporting-document download links |
| `RETENTION_SCAN_INTERVAL_SECONDS` | `86400` | worker loop: retention scan per tenant (make retention-scan runs it now) |
| `INJECTION_LLM_JUDGE` | `false` | platform default; the judge adapter arrives with the provider router [M6] |

## Calendar [M8] (doc 10)

CALENDAR_PROVIDER=mock needs nothing. For =google create an OAuth client (type "Web application") in a Google Cloud project in Testing mode, add yourself as a test user and register the redirect URI {OAUTH_REDIRECT_BASE_URL or PUBLIC_BASE_URL}/api/v1/calendar/connections/google/callback (installation guide).


| Variable | Example | Purpose |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | — |  |
| `GOOGLE_OAUTH_CLIENT_SECRET` | *(your key)* |  |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | the SPA as the browser sees it (OAuth return, links in e-mails) |
| `OAUTH_REDIRECT_BASE_URL` | — | default: PUBLIC_BASE_URL |
| `MOCK_CALENDAR_FAIL_MODE` | `none` | none \| transient_once \| auth_expired \| permanent (drills and tests) |

## E-mail and automation [M8] (doc 06)

| Variable | Example | Purpose |
|---|---|---|
| `MAIL_FROM_DOMAIN` | `leave.local` | notifications come from no-reply@{tenant-slug}.{domain}; Mailpit UI :8025 |
| `PLATFORM_ALERT_EMAIL` | `platform-alerts@leave.local` | W7 provider-health digest |
| `N8N_PORT` | `5678` | UI on 127.0.0.1 only (make up-automation) |
| `N8N_OWNER_EMAIL` | `automation@leave.local` | n8n owner account (managed by the environment) |
| `N8N_OWNER_PASSWORD` | *(generated)* | generated by `make env` when empty |
| `N8N_ENCRYPTION_KEY` | *(generated)* | generated by `make env`; encrypts n8n's stored credentials — keep it |

## Observability and evaluation [M9] (ADR-0022, doc 11)

`make env` fills every empty secret below. The project keys are created in Langfuse at its first start (headless init) and used by the backend's tracing when LANGFUSE_ENABLED=true.


| Variable | Example | Purpose |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | *(generated)* | pk-lf-… (generated) |
| `LANGFUSE_SECRET_KEY` | *(generated)* | sk-lf-… (generated) |
| `LANGFUSE_NEXTAUTH_SECRET` | *(generated)* |  |
| `LANGFUSE_SALT` | *(generated)* |  |
| `LANGFUSE_ENCRYPTION_KEY` | *(generated)* | 64 hex characters (generated) |
| `LANGFUSE_INIT_USER_EMAIL` | `observability@leave.local` | Langfuse UI login (http://localhost:3000) |
| `LANGFUSE_INIT_USER_PASSWORD` | *(generated)* |  |
| `LANGFUSE_S3_ACCESS_KEY` | *(generated)* | SeaweedFS identity limited to the bucket `langfuse` |
| `LANGFUSE_S3_SECRET_KEY` | *(generated)* |  |
| `CLICKHOUSE_PASSWORD` | *(generated)* |  |
| `VALKEY_PASSWORD` | *(generated)* |  |
| `LANGFUSE_PORT` | `3000` |  |
| `CLICKHOUSE_MEMORY_LIMIT` | `1G` | low-memory ClickHouse config in deploy/clickhouse (server cap 850 MB) |
| `LANGFUSE_WEB_MEMORY_LIMIT` | `1280M` |  |
| `LANGFUSE_WEB_HEAP_MB` | `1024` | Node heap of langfuse-web (the default ran out at start) |
| `LANGFUSE_WORKER_MEMORY_LIMIT` | `640M` |  |
| `CHROMA_PORT` | `8100` | eval profile: Chroma for the retrieval comparison (ADR-0011) |
| `RERANKER_BACKEND` | `onnx` | torch \| onnx \| onnx-int8 — ONNX fp32 chosen in M9 (same ranking, faster) |
| `EVAL_MAX_CALLS` | `250` | live evaluation budget (provider calls) for make eval-live |
| `EVAL_PACE_SECONDS` | `6` | pause between live turns (Gemini free tier) |
