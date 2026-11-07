# Troubleshooting

Symptoms an operator can meet when running the stack, their cause and the fix. Every row comes from a problem met
during the milestones (the worklog in brackets) or from the installation guide. Development-only issues (test
tooling, type checker quirks) stay in the worklogs.

First steps for any problem: `make ps` (container states), `make logs SERVICE=<name>`, `make smoke` (liveness and
readiness through the frontend proxy; `readyz` names the failing dependency), `make stats` / `make peaks` (memory).

## Start-up and containers

| Symptom | Cause / fix |
|---|---|
| `keycloak` stays `starting` for more than 2 minutes | The first start initialises its database (slow on small machines); `make logs SERVICE=keycloak`. If the `keycloak` database is missing, the init script ran before it was added: reset the `pgdata` volume (data loss) or create the database and role by hand as in `deploy/postgres/init/01-init.sh`. |
| A port is already in use | Change `FRONTEND_PORT`, `KEYCLOAK_PORT`, `MAILPIT_PORT`, `POSTGRES_PORT` (or `API_PORT` for `make up-dev`) in `deploy/.env`. |
| Build fails downloading wheels | Retry; the uv cache mount keeps what was already downloaded. Adding a Python dependency rebuilds a large layer (about 10 minutes). |
| `api` exits with code 132 (illegal instruction) | An AVX-only CPU hit MKL's vector `tanh` (R-27). The models use a scalar-safe `tanh` already; also set `MKL_CBWR=AVX` in `deploy/.env` and restart [M5, M6]. |
| `api` is OOM-killed (exit 137, `docker inspect … OOMKilled=true`) | In embedded mode the ingestion child shares the api's memory limit: use `make up-staged` (3.5 GB limit). Do not run heavy ingestion, the host-side integration suite, Langfuse and n8n at the same time on a 7 GB host [M8, M9]. |
| The editor or desktop apps are killed while the stack runs (systemd-oomd) | RAM and swap were exhausted by too many heavy jobs at once. Stop Langfuse before integration tests, run long jobs detached (`setsid nohup make … &`), one heavy job at a time [M9]. |
| `make models` fails with `PermissionError` under `/models` | The `models` volume was created by root before M4; `make models` chowns it once. Re-run `make models`. |
| The first answer after a start takes about a minute | The api loads the embedding, reranker and spaCy models on the first turn (a warm-up runs at start; a user who arrives during it waits). |

## Readiness, identity and login

| Symptom | Cause / fix |
|---|---|
| `readyz` reports `schema` failing | Migrations not applied or behind the code: `make migrate`. |
| `readyz` reports `s3` failing | The documents bucket is created by the api at start; check `make logs SERVICE=seaweedfs` and `S3_ACCESS_KEY`/`S3_SECRET_KEY`. If SeaweedFS was stopped (for example by the OOM killer), start it again: `docker compose -f deploy/compose.yaml --profile core up -d seaweedfs`. |
| `readyz` reports `keycloak` failing while the console works | The api reaches Keycloak on `http://keycloak:8080`; `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` must be set together with the full-URL `KC_HOSTNAME`. |
| `readyz` reports `keycloak` failing with HTTP 404 | The realm was not imported: `docker compose -f deploy/compose.yaml rm -sf keycloak && make up-staged` re-imports; an existing realm is never overwritten — delete it in the console to re-import. |
| `make seed` fails with "identity provider returned HTTP 401" | `KEYCLOAK_ADMIN_CLIENT_SECRET` differs from the value imported into the realm (placeholders resolve at import time): align them or re-import the realm. |
| `make seed` fails with Keycloak 500 "Unable to find group specified by path" | The realm was re-imported and the tenants' groups are gone; the seed recreates them on its next run (`ensure_tenant` re-verifies the group) [M1]. |
| Provisioning answers 403 reading realm roles | The `backend-admin` service account lacks `view-realm` (in the realm file since M1); re-import the realm. |
| The API answers 401 "invalid issuer" | `KC_PUBLIC_URL` (the token issuer) must equal what the browser uses, e.g. `http://localhost:8081`. |
| Login answers "Account is not fully set up" | The user has a temporary password (a bootstrapped company administrator): finish the password change in the browser. |

## Documents and ingestion

| Symptom | Cause / fix |
|---|---|
| A document stays `PENDING` / `PROCESSING` | The worker is not running or is busy: `make jobs` shows the queue; `make ingest` drains it inside the api container. A job without a heartbeat for 30 minutes (`job_stale_after_seconds`) is reclaimed. |
| Seeded documents are `FAILED` with `FileNotFoundError: /models/docling/…` or "Not able to find a model file" | They were processed before `make models` had finished (the embedded worker starts at once). `make retry-failed` re-queues every failed version and drains the queue [M10 clean-clone test]. |
| `make models` is killed (exit 137) or the api is OOM-killed during it | Older Makefiles ran the download inside the running api; it now runs in a one-off container with its own limit. Update and re-run `make models` [M10]. |
| A version is `FAILED` with `pii_not_configured` | `APP_KEK`, `VAULT_HMAC_KEY` or `VAULT_KEK` is empty: run `make env` (fills empty keys) and restart. Documents are never published without pseudonymisation (fail closed). |
| A version is `FAILED` with `pii_scan_failed` or `no_text` | Presidio errored, or the file has no text layer and OCR found nothing. Re-upload or check the file; scans need Tesseract (present in the image only). |
| A version is `PENDING_REVIEW` or `QUARANTINED` | The injection scan scored it medium or high (doc 08 §8.2): an HR administrator reviews it in *Review queue*. Thresholds: *Security policy*. |
| The api is OOM-killed during `make ingest` with `make up-staged` | Before M10 the drain started a second worker inside the api next to the embedded one (two ingestion children under one limit). `make ingest` now waits for the embedded worker instead [M10 clean-clone test]. |
| A long PDF takes minutes | Docling's layout and table models on this CPU: about 6–7 s per page, about 60 s per document overall (R-28). |
| Answers contain placeholders such as `PERSON_0002` where policy wording should be | Documents processed before M10 were over-pseudonymised (R-30). Re-process them: `docker compose -f deploy/compose.yaml --profile core exec -T api python -m leave_assistant.documents.reindex --force`, then `make ingest`. |

## Assistant (chat)

| Symptom | Cause / fix |
|---|---|
| Chat answers `503 chat/provider-unavailable` | No provider is configured: set `GEMINI_API_KEY` and/or `MISTRAL_API_KEY` in `deploy/.env` and restart the api. |
| The stream ends with `provider-unavailable` | Every provider failed or its circuit breaker is open (60 s; 10 minutes after a bad key or model). `make logs SERVICE=api` shows `provider` events. Gemini's free tier allows 20 answer requests per day; keep Mistral configured as the second provider [M6, M9]. |
| Every answer fails with `internal-error` | An empty model variable (e.g. `GEMINI_MODEL=`) used to override the default; fixed in M9 (`env_ignore_empty`). Remove the empty line or update. |
| A card says it expired, or *Confirm* answers `409 chat/proposal-expired` | Cards live for the company's card lifetime (default 15 minutes, *Company settings*); ask again. `409 chat/no-pending-proposal`: a newer message replaced the card. |
| The answer is tagged *Fallback answer* | The grounding verifier rejected both drafts, so the assistant showed a deterministic summary of what it found instead (R-36). Rephrase the question; repeated fallbacks on one topic point to a gap in the documents. |
| Answers are in the wrong language | The assistant answers in the question's language; very short or mixed messages fall back to the UI language. Write a full sentence. |

## Integrations and automation

| Symptom | Cause / fix |
|---|---|
| n8n exits at start with "JavaScript heap out of memory" | The default heap does not fit 512 MB; the compose file sets 1 GB and `--max-old-space-size=768` [M8]. Raise `N8N_MEMORY_LIMIT` if needed. |
| E-mails are not sent | n8n runs only in the `automation` profile (`make up-automation`); undelivered events wait in the outbox and are sent when it is back. Check Mailpit at http://localhost:8025. |
| A Google calendar connection shows `expired` every week | OAuth clients in *Testing* status issue 7-day refresh tokens (R-34): re-authorise on the *Calendar* page; parked jobs resume. |
| Calendar items stay `parked` or `dead` | `parked`: the connection is expired (re-authorise). `dead`: a permanent error; a company administrator retries it on the *Calendar* page after fixing the cause. Reconciliation repairs events deleted by hand. |

## Observability and evaluation

| Symptom | Cause / fix |
|---|---|
| Langfuse web answers 500 | SeaweedFS (its blob store) is down: start it again [M9]. |
| Langfuse web is unreachable on :3000 although healthy | Next.js bound to one network; the compose file sets `HOSTNAME=0.0.0.0`. |
| ClickHouse refuses to start with the low-memory configuration | Merge-pool thresholds above the reduced pool: use `deploy/clickhouse/` as committed. |
| Traces do not appear | `LANGFUSE_ENABLED=true` and the profile `observability` must both be on; the worker ingests asynchronously (seconds). Langfuse v4 has no trace list API: read `/api/public/v2/observations` [M9]. |
| RAGAS scores are NaN | The judge was rate-limited: Mistral Medium and Small have no free quota; the judge is `mistral-large-4`, paced by `EVAL_JUDGE_RPS` [M9]. |
| `make chat-perf` reports 10 s or more | It must run in a one-off container (the target does); inside the running api two copies of the models swap [M9]. |

## Backup and restore

| Symptom | Cause / fix |
|---|---|
| `make backup` says postgres is not running | Start the stack first (`make up-staged`). |
| After a restore the app shows errors decrypting documents or calendar tokens | `deploy/.env` holds different keys than when the backup was made. Restore the `APP_KEK`, `VAULT_HMAC_KEY`, `VAULT_KEK` and `N8N_ENCRYPTION_KEY` that were kept with the backup ([backup-restore.md](backup-restore.md)). |
| `make restore-check` reports differences | The stack wrote new rows after the restore (for example a periodic job), or the restore was partial; the script's log names each table. |
