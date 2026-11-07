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

### What each role sees (M3–M10)
| Role | Workspace menu |
|------|----------------|
| Employee | *Assistant* (chat with citations and confirmation cards), *My leave* (balances, request wizard: check → submit, history, request detail with modify/cancel, supporting documents), *Notifications*, *Documents*, *Calendar* |
| Manager | + *Approvals* (approve/reject with comment), *Team availability*, *Team requests* |
| HR Administrator | the manager pages (HR-fallback steps flagged) + *Balances* (with ledger adjustments), *Review queue*, *Retention*, *Audit log* (with CSV export) |
| Company Administrator | *Employees* (create → one-time temporary password, edit, roles, deactivate), *Departments* (manager designation), *Leave types & policies*, *Public holidays* (incl. CSV import), *Approval rules* (incl. routing simulation), *Company settings* (language, timezone, work week, conversation retention, card lifetime, HR fallback), *Security policy* (risk tiers, chat screening, upload limits), *Balances*, *Review queue*, *Retention*, *Audit log* (with CSV export) |
| Platform Administrator | *Companies* (create, suspend/resume, first administrator → one-time password), *Platform audit* (with CSV export), *Platform settings* (defaults of new companies, global security defaults and the ranges companies may use) |

The menu is a convenience only: the API authorises every call (FR-UI-04), and opening a route of another role shows
a polite notice. EN/EL is switched in the header; the choice is remembered per browser.

### Creating a company by hand (Platform Administrator)
```bash
TOKEN=$(make -s token EMAIL=platform.admin)
curl -s -X POST localhost:8080/api/v1/platform/tenants -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -H 'Content-Type: application/json' -d '{"slug":"newco","name":"New Co","timezone":"Europe/Dublin"}'   # omitted values come from Platform settings (M10)
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

### Calendars (M8)
Open **Calendar** in the menu. Every employee can connect their **own** calendar; company administrators also connect
the **company leave calendar**. After a final approval the worker writes the absence (name, dates, availability —
never the leave type or the reason) to the connected calendars; a cancellation removes it. The page shows the
connection status, the synchronisation history (company administrators can retry failed jobs) and, for company
administrators, a reconciliation report with **Link**, **Delete event** and **Ignore** actions.

By default the calendar is the **mock** provider (`CALENDAR_PROVIDER=mock`): "Connect" completes immediately and
events are stored in the database table `app.mock_calendar_events`. `MOCK_CALENDAR_FAIL_MODE` simulates failures
(`transient_once`, `permanent`, `auth_expired` — the last one expires every connection made before the api started;
re-authorise on the page and the parked jobs resume).

**Google Calendar (optional).**
1. In a Google Cloud project, enable the *Google Calendar API* and configure the OAuth consent screen in **Testing**
   status with your own account as a test user (refresh tokens then expire after 7 days — re-authorise weekly, R-34).
2. Create an OAuth client of type *Web application* with the redirect URI
   `http://localhost:8080/api/v1/calendar/connections/google/callback` (or your `OAUTH_REDIRECT_BASE_URL`).
3. Put `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` and `CALENDAR_PROVIDER=google` in `deploy/.env` and
   restart the api (`make up-staged`). The company connection creates a calendar "Leave — {Company}" in the
   authorising account; share it with your staff in Google Calendar.

### E-mail and automation (M8)
```bash
make up-automation        # core + the automation profile: n8n-init imports and publishes W0–W7, then n8n starts
```
- Mailpit (http://localhost:8025) receives every notification e-mail, in the recipient's language, from
  `no-reply@{company}.leave.local`. Mailpit's API: `GET http://localhost:8025/api/v1/messages`.
- n8n's UI is on http://localhost:5678 (localhost only): sign in with `N8N_OWNER_EMAIL` and the generated
  `N8N_OWNER_PASSWORD` from `deploy/.env`. Workflows W0–W7 and the error handler are imported from
  `automation/n8n/workflows`; after editing in the UI run `make n8n-export` and commit the JSON.
- Without the profile everything else still works: in-app notifications are created by the backend, and the worker
  keeps its daily schedules; e-mails wait in the outbox and are sent when n8n runs.
- `GET /api/v1/platform/health` (platform administrator) shows dead events, parked/failed calendar jobs and
  provider health per company.

### Observability and evaluation (M9)
**Langfuse** (traces of every assistant run, the evaluation dataset and experiments) runs in the `observability`
profile; **Chroma** (only for the retrieval comparison) in the `eval` profile. Both are optional.

```bash
make env                 # generates the Langfuse, ClickHouse, Valkey and S3 secrets into deploy/.env (once)
make up-observability    # Langfuse web + worker, ClickHouse (low-memory settings), Valkey; UI http://localhost:3000
```
Sign in with `LANGFUSE_INIT_USER_EMAIL` and the generated `LANGFUSE_INIT_USER_PASSWORD` from `deploy/.env`. The
organisation, project and API keys are created at the first start; set `LANGFUSE_ENABLED=true` and restart the api
(`make up-staged`) to trace assistant runs (inputs are pseudonymised and masked again before they leave the backend).
Langfuse needs about 1.3 GB on top of the core stack, so on the 7 GB machine run it only when needed.

The evaluation (doc 11, `docs/evaluation/`) runs on the host against the seeded dev database:

```bash
make eval-session        # stops frontend, n8n, worker, api and keycloak; starts Chroma and Langfuse
make eval-offline        # retrieval comparison, reranker runtimes, τ calibration, security figures
make eval-live           # live answers (pinned to Mistral, EVAL_ANSWER_PROVIDERS) + live agent scenarios
make eval-ragas          # RAGAS scores (judge mistral-large-4, ≈ 45 min per repetition on the free plan; resumable)
make eval-report         # performance figures
make eval-session-end    # back to `make up-staged`
make eval-trace          # with LANGFUSE_ENABLED=true make up-staged: one traced chat turn, checked for leaks
make chat-perf           # NFR-PERF-01 in a one-off api container (stop the api first on the 7 GB machine)
```
**Memory on the 7 GB machine.** Run one heavy step at a time. On 2026-11-06, the api at its 3.5 GB embedded limit,
the host-side integration suite, Langfuse, a RAGAS run and a browser together exhausted RAM and swap.
systemd-oomd then killed the editor and the SeaweedFS container. Stop Langfuse (`make eval-session-end` or `docker
compose … stop langfuse-web langfuse-worker clickhouse valkey`) before `make test-integration`. Start long runs
detached (`setsid nohup make eval-ragas > ragas.log 2>&1 &`); RAGAS keeps finished repetitions in
`results/<date>-ragas.partial.jsonl` and resumes from them.
Live steps use the free-tier keys and stop at `EVAL_MAX_CALLS` provider calls (pause `EVAL_PACE_SECONDS` between
turns). The harness refuses to run when documents other than the seed corpus are retrievable — remove test uploads
first. Results land in `docs/evaluation/results/` with the commit, the date and a corpus fingerprint.

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
it); the generated reference is [configuration.md](configuration.md). The backend reads them through `backend/src/leave_assistant/config.py`; unknown values for enumerated flags fail
fast at start-up.

## Data, reset, backup
- Named volumes: `pgdata` (PostgreSQL: `leave`, `keycloak`, `n8n`, `langfuse` databases), `seaweed` (objects), `models`
  (downloaded ML models), `mailpit`.
- Full reset: `docker compose -f deploy/compose.yaml --profile core down -v` (deletes all data).
- Backup and restore: `make backup DIR=…` / `make restore DIR=…` — see [backup-restore.md](backup-restore.md) (keep
  `deploy/.env` with every backup: it holds the encryption keys).

## Troubleshooting
See [troubleshooting.md](troubleshooting.md): symptoms by area (start-up, identity, documents, assistant,
integrations, observability, backup) with causes and fixes.
