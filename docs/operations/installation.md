# Installation and local operation

Everything runs locally with Docker Compose (brief §4.1). This page is validated at the end of every milestone by
following it from a clean clone.

## Prerequisites
- Docker Engine ≥ 29 with Compose v2 (`docker compose version`), ~15 GB free disk (images ≈ 8–10 GB, models ≈ 3–4 GB).
- RAM: the `core` profile needs ~4 GB free; 8 GB total is the practical minimum, 16 GB for the `observability`/`eval`
  profiles (doc 02 §10). Hosts with less free memory use the staged start-up below.
- Only for development outside containers: Python 3.14 with [uv](https://docs.astral.sh/uv/) 0.13.x, Node ≥ 22.12
  (24 LTS recommended) with npm.
- External accounts are needed only later: Gemini and Mistral (or Groq) API keys from M6, a Google Cloud project from M8 — free tiers,
  fictional data only (ADR-0029).

## First start
```bash
make env            # copies deploy/.env.example to deploy/.env — review the values (dev passwords, ports)
make build          # builds the api and frontend images; the first build downloads ~2.5 GB of Python wheels
make up             # core profile; waits until every service is healthy (Keycloak imports the realm on first start)
make bootstrap      # applies the database migrations and seeds the two fictional companies (idempotent)
make smoke          # liveness + readiness through the frontend proxy (readyz reports `schema` until migrated)
make models         # once: downloads the embedding and Docling models (~1.2 GB) into the `models` volume
make ingest         # processes the seeded company documents (parsing, OCR, chunking, embeddings); shows the status table
```
Then open:
- http://localhost:8080 — the application: sign in with a seed user (below); the workspace menu follows your roles
  (`/system` shows the service status and your principal)
- http://localhost:8081 — Keycloak admin console (`KC_BOOTSTRAP_ADMIN_USERNAME` / `KC_BOOTSTRAP_ADMIN_PASSWORD`);
  realm `leave-assistant` is imported from `deploy/keycloak/leave-assistant-realm.json`
- http://localhost:8025 — Mailpit (captured e-mail)

### Seed users (all fictional; password `SEED_USER_PASSWORD`, default `leave-dev`)
| User | Role(s) | Company |
|------|---------|---------|
| `platform.admin` | Platform Administrator (no company) | — |
| `giorgos.alexiou@aegean-logistics.example` | Company Administrator | Aegean Logistics S.A. (Greek) |
| `maria.oikonomou@aegean-logistics.example` | HR Administrator, manager of HR | Aegean Logistics S.A. |
| `nikos.papadakis@aegean-logistics.example` | Manager (Operations) | Aegean Logistics S.A. |
| `eleni.dimitriou@aegean-logistics.example` | Employee (Operations) | Aegean Logistics S.A. |
| `aoife.byrne@brightwater.example` | Company Administrator | Brightwater Software Ltd. (English) |
| `ciara.walsh@brightwater.example` | HR Administrator, manager of People | Brightwater Software Ltd. |
| `liam.murphy@brightwater.example` | Manager (Engineering) | Brightwater Software Ltd. |
| `niamh.kelly@brightwater.example` | Employee (Engineering) | Brightwater Software Ltd. |

The full list is in `backend/src/leave_assistant/seed/data.py`. `make token EMAIL=<user>` prints an access token for
`curl` (dev realm: direct access grants are enabled on the SPA client for this purpose only).

### What each role sees (M3)
| Role | Workspace menu |
|------|----------------|
| Employee | *My leave* (balances, request wizard: check → submit, history, request detail with modify/cancel), *Notifications* |
| Manager | + *Approvals* (approve/reject with comment), *Team availability*, *Team requests* |
| HR Administrator | the manager pages (HR-fallback steps flagged) + *Balances* (with ledger adjustments) and *Audit log* |
| Company Administrator | *Employees* (create → one-time temporary password, edit, roles, deactivate), *Departments* (manager designation), *Leave types & policies*, *Public holidays* (incl. CSV import), *Approval rules* (incl. routing simulation), *Balances*, *Audit log* |
| Platform Administrator | *Companies* (create, suspend/resume, first administrator → one-time password), *Platform audit* |

The menu is a convenience only: the API authorises every call (FR-UI-04), and opening a route of another role shows
a polite notice. EN/EL is switched in the header; the choice is remembered per browser.

### Creating a company by hand (Platform Administrator)
```bash
TOKEN=$(make -s token EMAIL=platform.admin)
curl -s -X POST localhost:8080/api/v1/platform/tenants -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"slug":"newco","name":"New Co","default_locale":"en","timezone":"Europe/Dublin"}'
curl -s -X POST localhost:8080/api/v1/platform/tenants/<id>/bootstrap-admin -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" -H 'Content-Type: application/json' \
  -d '{"email":"admin@newco.example","first_name":"Ada","last_name":"Lovelace","locale":"en"}'
```
The response contains the temporary password **once**; Keycloak forces a password change at the first login.

### Leave data (M2)
`make bootstrap` also seeds, per company, the leave types (annual 20 d, sick 10 d, unpaid), the 2026/2027 policies
(carry-over 5 days until 31 March), the 2026 public-holiday calendar (Greek / Irish), the four approval rules of
doc 01 §6.4, the 2026 entitlement grants and sample requests in every state (completed, approved, pending manager,
pending HR, rejected, cancelled, auto-acknowledged sick leave). `make rollover` runs the status rollover and the
ledger calendar (grants / carry-over / expiry) immediately; the worker does the same every 15 minutes.

```bash
TOKEN=$(make -s token EMAIL=eleni.dimitriou@aegean-logistics.example)
curl -s "localhost:8080/api/v1/leave/balances?year=2026" -H "Authorization: Bearer $TOKEN"
curl -s -X POST localhost:8080/api/v1/leave/requests/validate -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"leave_type":"annual","start_date":"2026-12-14","end_date":"2026-12-18"}'
curl -s -X POST localhost:8080/api/v1/leave/requests -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"leave_type":"annual","start_date":"2026-12-14","end_date":"2026-12-18","reason":"trip"}'
MANAGER=$(make -s token EMAIL=nikos.papadakis@aegean-logistics.example)
curl -s localhost:8080/api/v1/approvals/pending -H "Authorization: Bearer $MANAGER"
```

`make ps` shows service health; `make logs SERVICE=api` follows one service.

### Company documents (M4)
`make seed` also uploads the fictional policy corpus (six Greek documents for Aegean incl. a scanned PDF, five English
documents for Brightwater incl. a superseded handbook version) through the same `DocumentService` the API uses. Each
upload queues a `document.process` job; the worker (the `worker` container, or the api process in embedded mode)
parses the file with Docling (Tesseract OCR for scanned pages), chunks it along its headings, embeds the chunks with
`multilingual-e5-small` and indexes them in PostgreSQL (pgvector + full-text search). `make ingest` drains the queue
now and prints the job/version table; `make jobs` prints it again later; `make reindex` rebuilds the index after a
model or chunking change. Processing runs in a child process per job (memory is returned afterwards); expect
≈ 1 minute per document on the reference machine, mostly model start-up.

Then, in the UI, every company user has a **Documents** page (library, search, versions, download); HR and company
administrators upload new documents or versions. Through the API:
```bash
TOKEN=$(make -s token EMAIL=maria.oikonomou@aegean-logistics.example)
curl -s -X POST localhost:8080/api/v1/documents -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -F title="Οδηγός" -F category=policy_general -F file=@guide.md
curl -s "localhost:8080/api/v1/documents/search?q=μεταφορά%20υπολοίπου" -H "Authorization: Bearer $TOKEN"
```
`make test-ocr` parses the scanned Greek fixture inside the api container (the host does not need Tesseract).

### Content security and privacy (M5)
`make env` now generates three keys in `deploy/.env` when they are empty — `APP_KEK` (supporting-document
envelope encryption), `VAULT_HMAC_KEY` and `VAULT_KEK` (PII vault). Keep them: rotating `APP_KEK` makes stored
supporting documents unreadable, rotating `VAULT_KEK` the vault originals, rotating `VAULT_HMAC_KEY` the
placeholder stability. Without the keys the API refuses supporting-document uploads (`503 security/not-configured`)
and documents cannot be indexed (versions end `FAILED` with `pii_not_configured`): the system fails closed.

**Upgrading from M4:** run `make rescan` once after `make migrate`. Versions indexed before M5 hold raw text in
`content_llm_safe`; the rescan pseudonymises and risk-scores them from the stored structure (no re-parse), and holds
back any active version that is not low risk. A version whose first scan fails leaves retrieval until it succeeds.

What happens at ingestion (doc 08 §7–§8): every document version is scored for prompt injection (bilingual
heuristics + a similarity classifier) and scanned for personal data (Presidio with Greek recognisers plus the
tenant's employee directory). Personal data is replaced by stable placeholders (`PERSON_0001`, `EMAIL_0002`, …)
in the text the assistant will read; the originals live encrypted in the vault. Risk tiers come from the tenant's
security policy (`GET/PUT /admin/security-policy`, defaults from `GET/PUT /platform/security-defaults`):
medium → **Review queue** (HR and company administrators, menu entry under Company administration), high →
quarantined until a reviewer releases it. The seed includes an adversarial pair per company: the English
"untrusted upload" is quarantined, the Greek "σημείωμα" with a footnote addressed to the assistant waits for review.
`make security-check` re-scores the EL/EN fixture families inside the api container.

Supporting documents: an employee attaches PDF/JPEG/PNG files (≤ 10 MB) to their own request from the request
drawer; HR sees and downloads them (a reason is required and audited) from the team requests page; managers and
company administrators never see them. The seed attaches a fictional medical note to Eleni's and Dave's sick
requests. Files are encrypted per file before they reach SeaweedFS; downloads use 60-second single-use links.

Retention: company administrators manage retention policies on the **Retention** page (seed placeholders: 365 days
for supporting documents after the leave end, 730 days for superseded/retired company documents — not legal
advice); the worker scans daily (`make retention-scan` runs it now) and HR decides each item (retain or approve
deletion); deletions run as jobs and keep a tombstone for audit.

### Assistant chat (M6)
The **Assistant** page is the first menu entry and the landing page for company users. It answers policy questions
from the company's active documents, with numbered citations that open the cited section. Business questions
(balances, new requests) get a short pointer to the matching page until the tools arrive in M7.

Language model keys go into the git-ignored `deploy/.env`. Both providers have free tiers; use fictional data only,
because free-tier prompts may be used by the provider (doc 13 R-04).

```
GEMINI_API_KEY=...            # Google AI Studio
MISTRAL_API_KEY=...           # console.mistral.ai (free Experiment plan, phone verification)
GROQ_API_KEY=...              # optional: Groq console
LLM_PROVIDER_ORDER=gemini,mistral,groq   # providers without a key are skipped
```

Model IDs are settings (`GEMINI_MODEL_FAST`, `GEMINI_MODEL`, `GROQ_MODEL_FAST`, `GROQ_MODEL`). Without any key the
assistant answers `503 chat/provider-unavailable`; in `APP_ENV=dev` you can set `LLM_PROVIDER_ORDER=fake` to try the
flow with a scripted model. Answers come in the language of the question, even when the cited document is in the
other language. The input budget is 3,500 tokens per call; older turns are summarised.

After `make migrate` (which also creates the LangGraph checkpoint tables in schema `langgraph`):

| Command | What it does |
|---------|--------------|
| `make chat-smoke` | asks S01–S05 against the configured providers inside the api container and prints outcome, language, citations, provider and latency per question |
| `make chat-smoke ARGS="--order mistral"` | the same, forced onto Mistral (`--order groq` for Groq) |
| `make chat-smoke ARGS="--gemini-timeout 0.01"` | forced failover: Gemini times out, Mistral answers, `provider_events` records the failover |
| `make chat-perf` | NFR-PERF-01: 20 turns with a zero-latency fake model, p50/p95 of everything except the LLM |

The api warms up the embedder, reranker, Presidio and the input screen in the background after start; the first
question right after `make up` can take longer until that finishes. Conversations are deleted automatically after
90 days (`CONVERSATION_RETENTION_DAYS`), together with their checkpoints.

### Assistant actions (M7)
The assistant now acts, but only after you confirm. Ask in Greek or English, for example "How many days do I have
left? Can I request five days off starting December 14?". It reads your balance with a tool and prepares the request,
then shows a **card** with the dates, working days, balance before and after, and the approval chain. Nothing is
submitted until you press **Confirm**. **Not now** discards the card; **Change** lets you pick other dates or another
leave type and prepares a new card. A card expires after the tenant's `proposal_ttl_minutes` (default 15). A new
message discards an open card.

Managers ask "What is waiting for my approval?" and "Approve LR-2026-000123" (or "#1"); HR does the same for HR
steps. The approver confirms their own card in their own conversation. Executions go through the same services and
rules as the pages, so they create the same notifications and audit entries, marked `via: agent`.

| Command | What it does |
|---------|--------------|
| `make chat-smoke ARGS="--business"` | live business flow: balance in Greek, the headline request with confirmation, the manager's approval in chat; the created request is cancelled at the end |
| `GET /api/v1/chat/conversations/{id}/runs` | redacted run history (stages, tools, provider, timings) for the owner, or HR for audit |

## Low-memory hosts (< 4 GB free)
```bash
make up-staged
```
Starts PostgreSQL, SeaweedFS and Mailpit first, then Keycloak, then the API with the worker loop **embedded** in the
API process (`WORKER_MODE=embedded`; the separate `worker` container is not started) and the frontend. Close memory-hungry applications first;
`make stats` shows per-container usage.

## Profiles
| Profile | Services | Available from |
|---------|----------|----------------|
| `core` | postgres, keycloak, seaweedfs, mailpit, api, worker, frontend | M0 |
| `automation` | n8n | M8 |
| `observability` | langfuse web/worker, clickhouse, valkey | M9 |
| `eval` | chroma | M9 |

## Developer loop (outside containers)
```bash
make setup          # uv sync (backend) + npm ci (frontend)
make lint           # ruff, ruff format, mypy, import-linter, eslint, tsc
make test-unit      # pytest with coverage + vitest (no infrastructure needed)
make test-integration  # DB integration, model (`ml`) and security suites against the running stack (creates database `leave_test`; the `ml` tests download the models into ~/.cache on first run)
make rollover       # leave status rollover + ledger calendar for all tenants now
make contract       # regenerate docs/api/openapi.json + frontend/src/api/schema.d.ts and fail on drift
make test-e2e       # Playwright smoke (role logins, submit → approve → cancel, admin holiday, document upload + search) against the running stack
make up-dev         # core profile with hot reload; api also published on http://localhost:8000
```
`make test-e2e` needs the stack up and bootstrapped (`make up` or `make up-staged`, then `make bootstrap`) and a
Playwright Chromium (`cd frontend && npx playwright install chromium`, once). Screenshots and traces land in
`frontend/e2e/artifacts/` (gitignored). The suite leaves the seed data reusable (the request it creates is cancelled,
the holiday it adds is deleted).
Frontend dev server: `cd frontend && npm run dev` (proxies `/api` to `localhost:8000`; login redirects to
`http://localhost:5173/`, which the realm allows). Migrations run with `make migrate` (inside the api container) or
`cd backend && DATABASE_OWNER_URL=postgresql+psycopg://app_owner:app-owner-dev@127.0.0.1:5432/leave uv run alembic upgrade head`
on the host (PostgreSQL is published on `127.0.0.1:${POSTGRES_PORT}`).
Install the git hooks once with `pip install pre-commit && pre-commit install`.

## Configuration
All settings are environment variables documented in `deploy/.env.example` (each with the milestone that first uses
it). The backend reads them through `backend/src/leave_assistant/config.py`; unknown values for enumerated flags fail
fast at start-up.

## Data, reset, backup
- Named volumes: `pgdata` (PostgreSQL: `leave`, `keycloak`, `n8n`, `langfuse` databases), `seaweed` (objects), `models`
  (downloaded ML models), `mailpit`.
- Full reset: `docker compose -f deploy/compose.yaml --profile core down -v` (deletes all data).
- Backup/restore scripts arrive in M10 (doc 03 §6.5).

## Troubleshooting
| Symptom | Likely cause / fix |
|---------|--------------------|
| `keycloak` stays `starting` for > 2 minutes | First start initialises its database (slow on small machines); check `make logs SERVICE=keycloak`. If the `keycloak` database is missing, the init script ran before it was added — reset the `pgdata` volume. |
| `readyz` reports `keycloak` failing while the console works | The API reaches Keycloak on `http://keycloak:8080` (backchannel); `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` must be set together with the full-URL `KC_HOSTNAME`. |
| `readyz` reports `s3` failing | The bucket is created by the API at start-up; check SeaweedFS logs and `S3_ACCESS_KEY/S3_SECRET_KEY` in `deploy/.env`. |
| Port already in use | Change `FRONTEND_PORT`, `KEYCLOAK_PORT`, `MAILPIT_PORT` (or `API_PORT` for `up-dev`) in `deploy/.env`. |
| `api` exits with code 132 (illegal instruction) | An AVX-only CPU hit MKL's vector `tanh` (doc 13 R-27). The models already avoid that kernel; also set `MKL_CBWR=AVX` in `deploy/.env` and restart. |
| A card says it expired, or Confirm answers `409 chat/proposal-expired` | Cards are valid for the tenant's `proposal_ttl_minutes` (default 15); ask again for a new card. `409 chat/no-pending-proposal` means a newer message replaced it. |
| Chat answers `503 chat/provider-unavailable`, or the stream ends with that error | HTTP 503: no provider is configured (no key). Stream error: every provider failed or its circuit breaker is open (60 s; 10 min after a bad key or model). Check `make logs SERVICE=api` for `provider` events. |
| `api` OOM-killed | Raise the memory limit in `deploy/compose.yaml` or use `make up-staged`. |
| Build fails downloading wheels | Retry; the uv cache mount keeps already-downloaded packages. |
| `readyz` reports `schema` failing | Migrations not applied or behind the code: `make migrate`. |
| `readyz` reports `keycloak` failing with HTTP 404 | Realm not imported (first start happened without the realm file): `docker compose … rm -sf keycloak && make up-staged` re-imports; existing realms are never overwritten — delete the realm in the console to re-import. |
| `make seed` fails with "identity provider returned HTTP 401" | `KEYCLOAK_ADMIN_CLIENT_SECRET` in `deploy/.env` differs from the value imported into the realm (placeholders resolve at import time); align them or re-import the realm. |
| Login works but the API answers 401 "invalid issuer" | `KC_PUBLIC_URL` (issuer) must match what the browser uses, e.g. `http://localhost:8081`; the API validates `iss` against `KEYCLOAK_PUBLIC_URL`. |
| Login answers "Account is not fully set up" | The user has a temporary password: complete the password change in the browser (direct-grant tooling cannot do it). |
