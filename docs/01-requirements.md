# 01 — Refined Requirements Specification

Source: the brief (`../PROJECT_BRIEF.md`). This document restates the brief as testable requirements,
fills the brief's gaps with decided defaults (rationale in doc 14 §4 and ADR-0028/0029), and separates functional from non-functional
requirements. Each requirement carries an ID used in tests, ADRs and milestone exit criteria.

Priority: **P1** = MUST (release blocker), **P2** = SHOULD, **P3** = COULD (only if time permits).

## 1. Problem statement

Enterprise knowledge is spread across documents, databases and systems. Connecting an LLM to documents
produces answers but not *reliable operations*: it cannot be trusted with permissions, business rules or
execution. The project demonstrates a controlled pattern — the LLM understands and coordinates; deterministic
services enforce identity, tenancy, rules and execution; everything is observable and auditable — using leave
management as a concrete, bounded domain (brief §2, §3, §17).

## 2. Scope

### In scope
- Leave types: annual, sick, unpaid (brief §5.1).
- Balance retrieval, requests, modification, cancellation, history, public holidays, working-day calculation,
  validation, manager approvals, HR approvals (brief §5.2).
- Company policy documents: upload, parsing (PDF/DOCX/TXT/MD + scanned PDF via OCR), structure-aware chunking,
  local embeddings, hybrid retrieval with reranking, strict grounded answers with citations (brief §7).
- Sick-leave supporting documents: upload, restricted HR access, audit, retention (brief §5.6, §5.7).
- Multi-tenancy with dynamic company creation; two seeded fictional companies (brief §3, §4.6).
- Five roles (brief §4.5); department-based organisation (brief §4.7).
- Google Calendar synchronisation (company shared + optional personal) with mock adapter (brief §10).
- In-app notifications and captured e-mail (brief §10.3).
- n8n automation workflows (brief §8). Langfuse observability, Pytest/RAGAS evaluation (brief §12).
- Greek and English UI and conversation (brief §11).

### Out of scope (explicitly, brief §5 and §15)
- General HR platform features (payroll, performance, recruiting), expenses, procurement.
- Public/cloud deployment, paid hosting, paid SaaS dependencies.
- Claude or any Anthropic model at runtime.
- Multiple autonomous agents; n8n as agent orchestrator.
- Medical diagnosis extraction from supporting documents.
- Legal retention periods (configurable only, no legal defaults assumed).

## 3. Personas

| Persona | Role (brief §4.5) | Typical goals |
|---------|-------------------|---------------|
| Eleni, warehouse coordinator | Employee | "Πόσες μέρες άδειας έχω; Θέλω 5 μέρες από 14 Δεκεμβρίου." Upload a sick note. See history and notifications. |
| Nikos, department manager | Manager | Team availability, approve/reject pending requests, understand policy before deciding. |
| Maria, HR administrator | HR Administrator | Manage HR documents, second-level approvals, review supporting documents, handle exceptions, retention review. |
| Petros, company administrator | Company Administrator | Configure departments, employees, holidays, approval rules, document permissions, calendar authorisation, security thresholds; review quarantined general documents. |
| Platform operator | Platform Administrator | Create/manage tenants, global defaults, global security policy; **no** access to tenant content. |
| Automation | Service account (n8n) | Call internal endpoints for lifecycle, schedules and notifications. |

## 4. Core user journeys

### J1 — Policy question with citations (Employee, EL or EN)
1. Employee asks in chat (either language).
2. System pseudonymises the message, detects language, routes to the Knowledge subgraph.
3. Hybrid retrieval over documents the employee may read; reranking; evidence threshold.
4. Grounded answer in the user's language with numbered citations, or an explicit "insufficient evidence"
   answer with a suggestion (e.g., contact HR).
5. Citations are clickable and show the document, version and section.

### J2 — Leave request through chat (the brief's headline scenario)
1. "How many vacation days do I have remaining? Can I request five days off starting December 14?"
2. Business subgraph: balance tool → working-day tool (holiday calendar) → validation tool → *proposal*.
3. Assistant presents a deterministic summary (dates, working days, balance before/after, approval chain) and
   asks for confirmation (graph **interrupt**).
4. Employee confirms (chat button or message). Backend re-validates, creates the request **idempotently**,
   builds the approval steps, notifies the first approver, and records the audit trail.
5. Assistant reports the request ID and status.

### J3 — Approval (Manager, then HR when rules require)
1. Manager sees pending approvals (UI or asks the assistant "what is pending for me?").
2. Manager approves/rejects (UI action or chat proposal + confirmation interrupt in *their* session).
3. If the rule requires HR, HR is notified and performs the second step.
4. On final approval: ledger deduction, calendar sync jobs, notifications to employee and approvers.

### J4 — Document lifecycle (HR Administrator / Company Administrator)
1. Upload a policy document (category chosen by role).
2. Worker: store original → parse (Docling/OCR) → security scan (risk score) → PII scan → chunk → embed → index.
3. Risk tiers: low → active with warning; medium → pending review; high → quarantined (not retrievable).
4. Reviewer releases, edits metadata or deletes; new versions supersede old ones; re-indexing is traceable.

### J5 — Tenant onboarding (Platform Administrator → Company Administrator)
1. Platform admin creates a company (tenant) and its first company administrator.
2. Company admin configures departments, employees (provisioned in Keycloak), holidays, leave policies,
   approval rules, document permissions, retention, security thresholds, and authorises the shared calendar.

### J6 — Sick leave with supporting document (Employee → HR)
1. Employee reports sick leave (possibly retroactive) and uploads a medical note.
2. The note is stored encrypted, never parsed/indexed/sent to any LLM or Langfuse.
3. Rules route the request for HR review; HR opens the note (access audited).
4. Retention policy later flags the note for review and deletion.

## 5. Functional requirements

### 5.1 Identity, authentication, roles (FR-AUTH)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-AUTH-01 | Users authenticate via Keycloak using OIDC Authorization Code + PKCE from the SPA. | P1 | §4.4 |
| FR-AUTH-02 | The backend validates JWT signature (JWKS), issuer, audience, expiry and algorithm allowlist on every request. | P1 | §4.4 |
| FR-AUTH-03 | Tokens carry roles and tenant identity; the backend builds a trusted `Principal {user_id, tenant_id, roles, employee_id, department_id}` from the token plus its own employee table. | P1 | §4.4, §4.6 |
| FR-AUTH-04 | Five roles exactly as in brief §4.5; a user may hold several roles within one tenant. | P1 | §4.5 |
| FR-AUTH-05 | Platform Administrators belong to no tenant and cannot read tenant business data or document contents. | P1 | §4.5 |
| FR-AUTH-06 | Service accounts (n8n, worker) authenticate with OAuth2 client credentials and are limited to internal endpoints. | P1 | §8 |
| FR-AUTH-07 | User provisioning (create/disable employee) is performed by the backend through the Keycloak Admin API so that application and identity data stay consistent. | P2 | §4.5 |

### 5.2 Multi-tenancy (FR-TEN)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-TEN-01 | Every company has a unique tenant ID; every tenant-owned row, object key, vector entry, checkpoint thread and audit record carries it. | P1 | §4.6 |
| FR-TEN-02 | PostgreSQL RLS isolates tenant tables; the runtime DB role cannot bypass RLS; policies are forced for table owners. | P1 | §4.6 |
| FR-TEN-03 | Tenant context is set per transaction (`SET LOCAL`) and can never leak across pooled connections; a regression test proves it under concurrency. | P1 | §4.6 |
| FR-TEN-04 | The application layer enforces tenant and role authorization independently of RLS. | P1 | §4.6 |
| FR-TEN-05 | Retrieval applies tenant isolation *and* document permissions. | P1 | §4.6 |
| FR-TEN-06 | The LLM cannot choose or override its tenant; tenant/user context is injected as read-only runtime context. | P1 | §4.6 |
| FR-TEN-07 | Companies can be created dynamically by a Platform Administrator, with their own departments, employees, policies, approval rules, holiday calendar, document permissions, retention configuration, calendar integrations and security thresholds. | P1 | §4.6 |
| FR-TEN-08 | Two fictional companies are seeded (one Greek-speaking, one English-speaking) with departments, users, holidays, rules and sample documents in both languages. | P1 | §3 |

### 5.3 Organisation (FR-ORG)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-ORG-01 | Each company has departments; each employee belongs to exactly one department **[assumption A-03]**. | P1 | §4.7 |
| FR-ORG-02 | Each department has one manager; an employee may have an explicit individual manager override used for approval routing. | P1 | §4.7 |
| FR-ORG-03 | Managers see leave balances, availability and requests of their department only; confidential HR data (supporting documents, HR-restricted documents) is excluded. | P1 | §4.5, §4.7 |

### 5.4 Leave management (FR-LEAVE)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-LEAVE-01 | Leave types annual, sick, unpaid; per-tenant configuration of entitlement and behaviour per type. | P1 | §5.1 |
| FR-LEAVE-02 | Balances are derived from an append-only ledger (grants, deductions, reversals, adjustments, carry-over, expiry); no mutable balance column. | P1 | §5.4 |
| FR-LEAVE-03 | Working days exclude weekends (configurable work-week) and the tenant's public holidays; different calendars per tenant. | P1 | §5.3 |
| FR-LEAVE-04 | Validation: date order, not in the past (configurable grace for sick leave), no overlap with non-cancelled requests, working days > 0, sufficient available balance for balance-bearing types, optional notice period and max consecutive days. | P1 | §5.2 |
| FR-LEAVE-05 | Requests can be created, modified and cancelled with rules per state (doc 07 §5); modifications of approved requests re-enter approval. | P1 | §5.2 |
| FR-LEAVE-06 | Leave history per employee, filterable by period/type/status. | P1 | §5.2 |
| FR-LEAVE-07 | All ledger and request state changes are transactional, idempotent and audited. | P1 | §5.4, §6.2 |
| FR-LEAVE-08 | Available balance = ledger balance − working days of pending (not yet approved) requests. | P1 | decided |
| FR-LEAVE-09 | Half-day leave. | P3 | deferred (decided Q-01; schema-ready) |

### 5.5 Approvals (FR-APPR)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-APPR-01 | Approval rules are per-tenant data (leave type, working-day thresholds, ordered steps, document requirement), evaluated in backend code only. | P1 | §5.5 |
| FR-APPR-02 | Seeded rules: ≤ 5 working days → manager; > 5 → manager then HR; sick leave → auto-acknowledged up to 3 working days without a document, otherwise HR review; never a manager step for sick leave (§6.4). | P1 | §5.5, decided Q-02 |
| FR-APPR-03 | Each request gets materialised approval steps with approver role, decision, decider, timestamp and comment. | P1 | §5.5 |
| FR-APPR-04 | Approvers can approve/reject from the UI and through the assistant (proposal + confirmation). | P1 | §6, §11 |
| FR-APPR-05 | Routing: the department manager (or individual manager override) approves manager steps; HR Administrators approve HR steps; a department manager's own request routes its manager step to the manager override if set, otherwise to HR. | P1 | §4.7, decided Q-03 |
| FR-APPR-06 | On final approval: ledger deduction, calendar sync jobs, notifications; on rejection/cancellation: reversal where applicable. | P1 | §3 |
| FR-APPR-07 | HR Administrators may decide a pending manager step as fallback (tenant setting, default on), recorded and audited as a fallback decision; nobody may decide a step on their own request. | P1 | decided Q-03 |

### 5.6 Documents and knowledge (FR-DOC, FR-RAG)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-DOC-01 | Formats: PDF, DOCX, TXT, Markdown; scanned PDF via OCR (Greek + English). | P1 | §7.1 |
| FR-DOC-02 | Originals in SeaweedFS (S3 API) behind a storage port; metadata, versions, status and extracted content in PostgreSQL. | P1 | §7.2 |
| FR-DOC-03 | Document categories with role-based visibility and optional per-document ACL; upload permissions per role (doc 08 §6). | P1 | §9.5 |
| FR-DOC-04 | Lifecycle: uploaded → processing → (active \| pending_review \| quarantined \| failed) → superseded → retired → deleted; re-index on model change; full status history. | P1 | §7.2, §8 |
| FR-DOC-05 | Docling parsing preserving headings, sections, tables, metadata. | P1 | §7.3 |
| FR-DOC-06 | Structure-aware chunking: heading tree first, then token-bounded splits with overlap; tables kept whole where possible. | P1 | §7.4 |
| FR-RAG-01 | Local embeddings with `intfloat/multilingual-e5-small`, same model for indexing and querying, `query:`/`passage:` prefixes enforced centrally. | P1 | §7.5 |
| FR-RAG-02 | Retrieval backends pgvector and ChromaDB behind one port; same corpus, embeddings and dataset for comparison; the default is justified by measured results. | P1 | §7.6 |
| FR-RAG-03 | Hybrid retrieval: vector similarity + keyword (Postgres FTS, Greek and English) + reciprocal rank fusion + local multilingual reranker + context selection within a token budget. | P1 | §7.7 |
| FR-RAG-04 | Strict grounding: answers to company-specific questions cite retrieved chunks; citation IDs are verified; insufficient evidence yields an explicit statement; clarification questions when ambiguous. | P1 | §7.8 |
| FR-RAG-05 | Quarantined, superseded, retired or unauthorised documents are never retrievable. | P1 | §9.5 |

### 5.7 Agent (FR-AGENT)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-AGENT-01 | One LangGraph orchestrator with Knowledge, Business Operations and Approval subgraphs; typed state; conditional routing. | P1 | §6 |
| FR-AGENT-02 | Tool calling through a gateway: allowlist per role, schema validation, authorization, idempotency; the model never executes privileged operations directly. | P1 | §6.2 |
| FR-AGENT-03 | Separate phases: retrieve → prepare → confirm → execute. | P1 | §6.2 |
| FR-AGENT-04 | PostgreSQL checkpointing with unique thread IDs; interrupted workflows resume after restarts; workflow history visible. | P1 | §6.1 |
| FR-AGENT-05 | Graph state, conversation history and business records are stored separately. | P1 | §6.1 |
| FR-AGENT-06 | Error recovery: bounded node retries for transient errors, provider failover, safe fallback messages, no partial business effects. | P1 | §4.2, §6 |
| FR-AGENT-07 | Responses in the user's language (EL/EN) regardless of document language. | P1 | §11 |
| FR-AGENT-08 | Provider abstraction with Gemini primary and Groq failover (as built: Mistral, M6 — ADR-0010 amendment), timeouts, bounded retries, health tracking, selection logging, env configuration; no failover on invalid output, security or authorization errors. | P1 | §4.2 |

### 5.8 PII and content security (FR-PII, FR-SEC)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-PII-01 | Presidio-based detection with custom Greek recognisers (AMKA, AFM, Greek phone numbers, Greek names via NER) and standard entities. | P1 | §9.1 |
| FR-PII-02 | Pseudonymisation with a tenant-scoped vault (`Alice Smith → PERSON_0001`); the mapping never leaves the backend. | P1 | §9.2 |
| FR-PII-03 | Coverage: user messages, retrieved chunks, tool results, conversation history, every outbound LLM request, LLM responses. | P1 | §9.3 |
| FR-PII-04 | Re-identification only for placeholders the authenticated user is authorised to see; otherwise the placeholder is replaced by a neutral label. | P1 | §9.3 |
| FR-PII-05 | Sick-leave supporting documents are excluded from parsing, indexing, LLM and Langfuse by construction; fail-closed handling for sensitive categories. | P1 | §5.6, §9.3 |
| FR-SEC-01 | Layered prompt-injection defence: ingestion inspection, detection, classification, risk score, tool allowlist, argument validation, authorization, controlled execution, adversarial tests. | P1 | §9.4 |
| FR-SEC-02 | Risk tiers low/medium/high with per-tenant thresholds; medium requires reviewer action; high is quarantined automatically; review roles per brief §9.5. | P1 | §9.5 |
| FR-SEC-03 | Append-only audit log for authentication events, authorization denials, business operations, document access (incl. supporting documents), configuration changes, retention actions, provider failover. | P1 | §2, §5.6 |
| FR-SEC-04 | Retention: per-tenant policies per document class; eligibility scan; HR notification; authorised review; audited deletion; no legal defaults assumed. | P1 | §5.7 |

### 5.9 Integrations and notifications (FR-CAL, FR-NOTIF)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-CAL-01 | Company shared calendar authorised by a Company Administrator via OAuth2; optional personal calendars by employees. | P1 | §10.1 |
| FR-CAL-02 | Events expose only employee name, absence dates and availability status. | P1 | §10.1 |
| FR-CAL-03 | Automatic synchronisation after final approval; modification and cancellation propagate; retries, idempotency, sync history, failure tracking, manual reconciliation. | P1 | §10.2 |
| FR-CAL-04 | Real Google adapter and local mock adapter behind one port; mock usable in automated tests without credentials. | P1 | §10.2 |
| FR-NOTIF-01 | In-app notifications stored in PostgreSQL; e-mail rendered per recipient locale and delivered through n8n to Mailpit. | P1 | §10.3 |
| FR-NOTIF-02 | Notification events: request submitted/approved/rejected/cancelled, approval pending, document review pending, retention review pending, calendar sync failed, calendar connection expired. | P1 | decided |

### 5.10 Automation (FR-AUTO)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-AUTO-01 | n8n workflows for ingestion lifecycle orchestration, update/re-index triggers, scheduled checks (retention, calendar retries, housekeeping) and notification dispatch. | P1 | §8 |
| FR-AUTO-02 | Parsing, chunking, embedding, security processing, permission-aware retrieval and business logic stay in Python; n8n only calls internal APIs. | P1 | §8 |
| FR-AUTO-03 | Workflows are versioned as JSON in the repository and imported automatically on start-up. | P2 | §13 |

### 5.11 Frontend (FR-UI)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-UI-01 | Workspaces per role exactly as listed in brief §11 (employee, manager, HR, company admin, platform admin). | P1 | §11 |
| FR-UI-02 | Greek and English UI; conversation language follows the user's message. | P1 | §11 |
| FR-UI-03 | Chat with streaming responses, citation panel, confirmation cards for interrupts, conversation list and history. | P1 | §11 |
| FR-UI-04 | No business logic in the frontend: it renders backend decisions and calls backend endpoints. | P1 | §11 |

### 5.12 Observability and evaluation (FR-OBS, FR-EVAL)
| ID | Requirement | Pri | Source |
|----|-------------|-----|--------|
| FR-OBS-01 | Langfuse traces for LLM calls, graph execution, tool calls, prompts, latency, tokens, experiments; sensitive content masked; no supporting document content. | P1 | §12.1 |
| FR-OBS-02 | Structured JSON logs with request/tenant/thread correlation and no raw PII; health endpoints including provider health. | P1 | decided |
| FR-EVAL-01 | Pytest suites: unit, integration, authorization, tenant isolation, idempotency, workflow, security regression. | P1 | §12.2 |
| FR-EVAL-02 | RAGAS metrics (context precision/recall, faithfulness, answer relevance) and retrieval metrics (hit@k, MRR, nDCG) per backend/reranker configuration. | P1 | §12.2 |
| FR-EVAL-03 | Evaluation dataset of ~30 scenarios (32 in v1) covering the categories in brief §12.2, in Greek and English. | P1 | §12.2 |
| FR-EVAL-04 | Security-critical tests run with scripted fake LLM responses and no paid API calls. | P1 | §12.2 |

## 6. Leave domain rules — decided semantics

The brief defines *what* exists but not every rule. The rules below were decided on 2026-11-04 by applying the
brief's principles (doc 14 §4, ADR-0028). They are seed configuration per tenant, not code constants.

### 6.1 Leave types (per tenant configuration; seed values)
| Type | Balance-bearing | Seed entitlement | Retroactive | Supporting document | Approval routing |
|------|-----------------|------------------|-------------|---------------------|------------------|
| Annual | yes | 20 working days per policy year | no | no | manager (≤ 5 working days); manager → HR (> 5) |
| Sick | yes (annual cap) | 10 working days per year | yes, up to 30 days back | optional; required above 3 working days | auto-acknowledged (≤ 3 days, no document, within cap); otherwise HR review; **never a manager step** |
| Unpaid | no | — | no | no | manager (≤ 5); manager → HR (> 5) |

*M2 note:* the seed sick-leave policy sets `allow_negative = true` so that a sick request beyond the remaining
cap is routed to HR review (Q-02) instead of being rejected by the balance check; annual leave keeps the hard check.
Supporting-document requirements are advisory (warning + HR review) until uploads arrive in M5 (doc 12).

### 6.2 Entitlements and ledger (Q-01, decided)
- Entitlement is granted **up-front on 1 January** (`grant_mode = upfront`); hires during the year receive a pro-rated
  grant (remaining full months / 12 × entitlement, rounded to 0.5 day).
- Carry-over: cap 5 days (configurable), expiring 31 March (configurable) via `CARRYOVER` and `EXPIRY` ledger entries.
- Negative balances are not allowed; an audited HR `ADJUSTMENT` with reason is the only exception path.
- Whole days only; half days are deferred (P3) — quantities are `numeric(6,2)` so no migration is needed later.
- A request spanning two policy years is split into one deduction per period.
- Monthly accrual is not implemented; the ledger model supports it as periodic `GRANT` entries if ever required.

### 6.3 Request state machine
`DRAFT → SUBMITTED → PENDING_MANAGER → PENDING_HR → APPROVED → (ACTIVE → COMPLETED)`; terminal `REJECTED`,
`CANCELLED`, `WITHDRAWN`; `MODIFICATION_PENDING` re-enters the approval chain. Full definition in doc 07 §5.

### 6.4 Approval routing (Q-02, Q-03, decided)
| Condition | Steps |
|-----------|-------|
| Annual or unpaid, ≤ 5 working days | Manager |
| Annual or unpaid, > 5 working days | Manager → HR |
| Sick, ≤ 3 working days, no document, within the annual cap | Auto-acknowledged: APPROVED at submission; manager and HR notified |
| Sick, > 3 working days, or any supporting document, or cap exceeded | HR review |
| Requester is the manager of their own department | Manager step → `manager_override` employee if set, otherwise HR |
| Manager unavailable | HR Administrators may decide the manager step as fallback (tenant setting `allow_hr_fallback`, default on); recorded as a fallback decision and audited |
| Any request | Nobody decides a step on their own request; a user holding both manager and HR roles cannot decide both steps of one request |

### 6.5 Modification and cancellation
| Current state | Modify | Cancel |
|---------------|--------|--------|
| PENDING_* | allowed; restarts approval chain | allowed → CANCELLED, no ledger effect |
| APPROVED, not started | allowed → MODIFICATION_PENDING; on approval: reversal + new deduction in one transaction | allowed → CANCELLED + `REVERSAL` entry; calendar event deleted |
| ACTIVE (started) | HR only (exceptional case) | HR only; partial reversal for unused days |
| COMPLETED / REJECTED / CANCELLED | no | no |

## 7. Non-functional requirements

| ID | Requirement | Target / verification |
|----|-------------|-----------------------|
| NFR-SEC-01 | Tenant isolation holds even if the application layer has a bug (RLS) and even if RLS is misconfigured (application checks). | Security suite: cross-tenant attempts via API, tools, retrieval, storage keys, checkpoints — all denied. |
| NFR-SEC-02 | No secret, token or mapping in logs, traces, checkpoints or the frontend. | Log/trace scrubbing tests; code review checklist. |
| NFR-SEC-03 | All state-changing endpoints and tools are idempotent via idempotency keys. | Duplicate-submission tests. |
| NFR-PRIV-01 | No PII leaves the host un-pseudonymised; supporting documents never leave the host. | Outbound request interception tests with a fake provider. |
| NFR-REL-01 | Application restarts do not lose pending interrupts, jobs or sync state. | Restart tests in CI (compose restart during an interrupted run). |
| NFR-REL-02 | Provider failover completes within the request timeout budget (≤ 25 s per LLM call, ≤ 2 failovers). | Fake provider tests. |
| NFR-PERF-01 | Policy question end-to-end (excluding LLM latency) ≤ 3 s on the reference machine (4 cores, no GPU) with reranking of ≤ 20 candidates. | Measured in M9; recorded in evaluation docs. |
| NFR-PERF-02 | Ingestion of a 30-page text PDF ≤ 3 min; scanned PDF ≤ 10 min on the reference machine. | Measured in M9. |
| NFR-RES-01 | `core` profile fits in ≤ 6 GB RAM; full stack documented at ≥ 16 GB. | Container memory limits in Compose; measured in M10. **M10: met** — staged `core` (embedded worker), sum of per-container peaks during the clean install with ingestion ≈ 4.6 GB (api 3.4 GB of 3.5 GB, Keycloak 0.85 GB, PostgreSQL 0.2 GB, SeaweedFS 0.1 GB); about 1.3 GB at rest (doc 02 §10.1b). |
| NFR-MAINT-01 | Layered architecture with dependency rules enforced by import-linter; type checking (mypy strict for domain/application); ruff; ≥ 80 % line coverage for domain and application layers. | CI gates. |
| NFR-TEST-01 | Full deterministic test suite runs without network access to LLM providers. | CI without API keys. |
| NFR-OBS-01 | Every chat turn is traceable from HTTP request → graph run → tool calls → DB writes via one correlation ID. | Trace assertions in integration tests. |
| NFR-I18N-01 | All user-facing strings (UI, notifications, assistant templates) exist in EL and EN. | i18n key completeness test. |
| NFR-PORT-01 | Single `docker compose --profile core up` from a clean clone with only `.env` edited. | Documented and tested in M10. **M10: met after four fixes** found by the test (models downloaded inside the api, documents seeded before the models, a second worker during `make ingest`, inline comments read as values in `.env`); then clone → `make env` + two API keys → install → 16/16 end-to-end (worklog M10). |
| NFR-LIC-01 | All runtime components under licences permitting self-hosted internal use; exceptions documented (spaCy Greek model). | doc 02 licence table. |

## 8. Constraints

- C-01 Local-only Docker Compose; no mandatory cloud; external APIs limited to Gemini, Groq, Google Calendar (free tiers).
  Amended M6: Mistral (free plan) is used in place of Groq, whose signup was blocked (user decision, ADR-0010).
- C-02 Reference machine today: 4 cores / 7.1 GB RAM / no GPU (measured 2026-11-04).
- C-03 Runtime LLMs: Gemini and Groq only (as built: Gemini and Mistral, see C-01). Claude is a development tool only.
- C-04 Selected technologies (brief §4–§12) are the default; deviations go through ADRs and doc 14.
- C-05 Documentation must never reference an employer, recruitment process or interview.

## 9. Traceability to milestones

| Requirement group | Milestones |
|-------------------|------------|
| FR-AUTH, FR-TEN | M1 (+ M10 platform admin UI) |
| FR-ORG, FR-LEAVE, FR-APPR | M2, M3 (UI), M7 (via agent) |
| FR-DOC, FR-RAG | M4, M5, M6, M9 |
| FR-AGENT | M6, M7 |
| FR-PII, FR-SEC | M5 (content), M1/M7 (authorization, audit), M10 (policy UI) |
| FR-CAL, FR-NOTIF, FR-AUTO | M8 |
| FR-UI | M3, M6, M7, M10 |
| FR-OBS, FR-EVAL | M9 (+ continuous from M1 for Pytest) |
