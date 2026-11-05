# Installation and local operation

Everything runs locally with Docker Compose (brief §4.1). This page is validated at the end of every milestone by
following it from a clean clone.

## Prerequisites
- Docker Engine ≥ 29 with Compose v2 (`docker compose version`), ~15 GB free disk (images ≈ 8–10 GB, models ≈ 3–4 GB).
- RAM: the `core` profile needs ~4 GB free; 8 GB total is the practical minimum, 16 GB for the `observability`/`eval`
  profiles (doc 02 §10). Hosts with less free memory use the staged start-up below.
- Only for development outside containers: Python 3.14 with [uv](https://docs.astral.sh/uv/) 0.13.x, Node ≥ 22.12
  (24 LTS recommended) with npm.
- External accounts are needed only later: Gemini/Groq API keys from M6, a Google Cloud project from M8 — free tiers,
  fictional data only (ADR-0029).

## First start
```bash
make env            # copies deploy/.env.example to deploy/.env — review the values (dev passwords, ports)
make build          # builds the api and frontend images; the first build downloads ~2.5 GB of Python wheels
make up             # core profile; waits until every service is healthy (Keycloak imports the realm on first start)
make bootstrap      # applies the database migrations and seeds the two fictional companies (idempotent)
make smoke          # liveness + readiness through the frontend proxy (readyz reports `schema` until migrated)
```
Then open:
- http://localhost:8080 — the application: sign in with a seed user (below), the status page shows your principal
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

`make ps` shows service health; `make logs SERVICE=api` follows one service.

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
make test-integration  # DB integration + security suites against the running stack (creates database `leave_test`)
make contract       # regenerate docs/api/openapi.json + frontend/src/api/schema.d.ts and fail on drift
make up-dev         # core profile with hot reload; api also published on http://localhost:8000
```
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
| `api` OOM-killed | Raise the memory limit in `deploy/compose.yaml` or use `make up-staged`. |
| Build fails downloading wheels | Retry; the uv cache mount keeps already-downloaded packages. |
| `readyz` reports `schema` failing | Migrations not applied or behind the code: `make migrate`. |
| `readyz` reports `keycloak` failing with HTTP 404 | Realm not imported (first start happened without the realm file): `docker compose … rm -sf keycloak && make up-staged` re-imports; existing realms are never overwritten — delete the realm in the console to re-import. |
| `make seed` fails with "identity provider returned HTTP 401" | `KEYCLOAK_ADMIN_CLIENT_SECRET` in `deploy/.env` differs from the value imported into the realm (placeholders resolve at import time); align them or re-import the realm. |
| Login works but the API answers 401 "invalid issuer" | `KC_PUBLIC_URL` (issuer) must match what the browser uses, e.g. `http://localhost:8081`; the API validates `iss` against `KEYCLOAK_PUBLIC_URL`. |
| Login answers "Account is not fully set up" | The user has a temporary password: complete the password change in the browser (direct-grant tooling cannot do it). |
