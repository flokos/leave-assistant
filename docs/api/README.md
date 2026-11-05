# API snapshot

`openapi.json` is generated from the FastAPI application (`python -m leave_assistant.openapi`) and committed at every
milestone; `make contract` regenerates it together with the frontend types (`frontend/src/api/schema.d.ts`, via
`openapi-typescript`) and fails when either differs from the committed version. CI runs the same check.

Conventions and the endpoint catalogue are in [doc 09](../09-api-contracts.md). Endpoints present per milestone:

| Milestone | Endpoints |
|-----------|-----------|
| M0 | `GET /healthz`, `GET /readyz` |
| M1 | `GET /me`; `/platform/tenants` (list, create, get, patch, suspend, resume, bootstrap-admin); `GET /platform/audit`; `/admin/employees` (list, create, get, patch, deactivate, roles); `/admin/departments` (list, create, get, patch, delete, manager); `GET /admin/audit` |

State-changing routes under `/platform` and `/admin` require the `Idempotency-Key` header (replays return the stored
response with `Idempotent-Replayed: true`; a different body under the same key is `409 idempotency-mismatch`).
