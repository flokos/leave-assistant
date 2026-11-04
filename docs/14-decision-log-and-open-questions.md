# 14 — Decision Log, Assumptions and Open Questions

This is the closing section the brief asks for (§16): which decisions were accepted unchanged, which are
recommended for modification and why, which requirements remain ambiguous, which assumptions were made, and
the questions that genuinely need your input. It also lists the review focus areas the brief flagged.

## 1. Decisions accepted unchanged

| Brief | Decision | Where realised |
|-------|----------|----------------|
| §4.1 | Local-only Docker Compose; no cloud; free API tiers | doc 03 §6, ADR-0025 |
| §4.2 | Gemini primary, Groq alternative; failover, bounded retries, timeouts, health, logging, env config; no failover on invalid output/security/authorization | doc 04 §9, ADR-0010 |
| §4.3 | Python, FastAPI, PostgreSQL, SQLAlchemy, Pydantic; backend owns authz, rules, audit, integrations; clear layering | doc 03 §4, ADR-0002 |
| §4.4 | Keycloak with OIDC/OAuth2/JWT/RBAC, tenant-aware context, logically separate DB | doc 08 §4, ADR-0004 |
| §4.5 | Five roles with the stated responsibilities; platform vs company administration separated | doc 08 §5 |
| §4.6 | Shared PostgreSQL + RLS, tenant IDs everywhere, independent app authorization, trusted graph context | doc 07 §2, doc 08 §6, ADR-0003 |
| §4.7 | Department-based organisation with individual manager overrides | doc 07 §4.2 |
| §5 | Leave types, functionality, holidays, ledger, configurable approval rules in backend code, supporting documents, retention | doc 01 §6, doc 07 §5, ADR-0005/0006 |
| §6 | LangGraph; one orchestrator + Knowledge/Business/Approval subgraphs; Postgres checkpointer; tool-execution security; idempotency | doc 04, ADR-0007/0008/0009 |
| §7 | Docling, SeaweedFS via S3 with a storage abstraction, structure-aware chunking, multilingual-e5-small locally, hybrid search + local reranker, strict grounding, both vector backends evaluated | doc 05, ADR-0011–0016 |
| §8 | n8n CE for automation only; Python keeps parsing/embedding/security/retrieval/business logic | doc 06, ADR-0019 |
| §9 | Presidio + custom Greek recognisers; pseudonymisation with a local mapping; bidirectional protection; layered injection defence; risk tiers with per-company thresholds and the stated review roles | doc 08 §7–§8, ADR-0017/0018 |
| §10 | Google Calendar with company and personal calendars, OAuth2, minimal event content, auto sync + manual reconciliation, real + mock adapters; in-app + simulated e-mail with n8n | doc 10, ADR-0020/0021 |
| §11 | React + TypeScript, role workspaces, EL/EN, no business logic in the frontend | doc 03 §8, ADR-0024 |
| §12 | Self-hosted Langfuse; Pytest + RAGAS + Langfuse; 25–30 scenarios; security tests without paid calls | doc 11, ADR-0022/0023 |
| §13 | Documentation as a first-class deliverable with ADRs and Mermaid | docs/README.md |

## 2. Decisions recommended for modification

| # | Established direction | Recommended change | Justification | Impact / reversibility | ADR |
|---|-----------------------|--------------------|---------------|------------------------|-----|
| 1 | MailHog for e-mail capture | **Mailpit** | MailHog is unmaintained; Mailpit is the actively maintained drop-in (same SMTP/UI ports, plus a REST API used by tests). | None functionally; one image name | 0021 |
| 2 | Approval subgraph implies the graph waits for manager/HR | **Graph interrupts are single-user; cross-user approvals are a DB state machine** | A paused run carries the requester's trusted context; resuming it with another user's decision mixes security contexts and keeps threads open for days. Every required LangGraph capability is still demonstrated in each user's run. | Cleaner security; same UX; reversible | 0006, 0007 |
| 3 | ChromaDB and pgvector both selected | **pgvector default; Chroma as an `eval` profile behind the same port; default confirmed by measured results** | Only pgvector can sit under RLS and share the transaction/predicates with FTS; two production vector stores would duplicate capability (brief §15). The comparison the brief asks for is preserved. | Reversible by configuration | 0011 |
| 4 | Self-hosted Langfuse as part of the stack | **Langfuse remains self-hosted but as an optional Compose profile** | v4 requires ClickHouse + Redis/Valkey + S3 + Postgres; it does not fit the 7 GB development machine alongside the core profile. | No capability lost; used for all evaluation runs | 0022 |
| 5 | (implicit) a task queue for background work | **PostgreSQL-backed job queue, no Celery/Redis** | Fewer moving parts; transactional enqueue with business changes; sufficient for local scale. | Reversible (port) | 0019 |
| 6 | (implicit) Redis for Langfuse | **Valkey** | BSD-3 licence; supported by Langfuse. SeaweedFS reused as Langfuse's blob store. | None | 0022 |
| 7 | Presidio with Greek support | **Note the CC BY-NC-SA licence of the Greek spaCy model** | Not a change of technology; a licence constraint that must be visible. | Swap path documented | 0017 |
| 8 | (implicit) Python version | **Python 3.14, verified by resolver and install smoke test (3.13 identical fallback)** | Latest version supported by the entire cross-checked dependency set. | Base image tag | 0026 |
| 9 | (implicit) latest ML libraries | **transformers 4.57.x / sentence-transformers 5.7.x (not 5.x/6.x)** | Docling (`docling-ibm-models`) and `optimum-onnx` pin `transformers<4.58`; sentence-transformers 6 requires transformers 5. Verified by resolver probes. | Revisit when Docling supports transformers 5 | 0026 |
| 10 | Keycloak (realm design unspecified) | **Single realm with tenant groups/claims** | Dynamic tenant creation as a group/attribute operation; one SPA configuration; platform admins outside any tenant group. | Reversible but costly later | 0004 |

## 3. Ambiguous requirements and the interpretation taken

| Topic | Ambiguity | Interpretation (decided — see §4) |
|-------|-----------|--------------------------|
| "Sick leave: HR review when required" | When is it required? | ≤ 3 working days without document → auto-acknowledged; otherwise HR review (Q-02) |
| Manager's own leave | Who approves? | Manager step skipped → HR (Q-03) |
| "Manage requests" for employees | Includes modification of approved requests? | Yes, re-entering approval (doc 01 §6.5) |
| Document upload permissions | Not finalised in the brief | Proposal in doc 05 §1.1 / doc 08 §5.2 |
| "Workflow history" | Graph history or business history? | Both: `leave_request_events` (business) and redacted run history (graph) |
| "Conversation history" protection | Stored how? | Pseudonymised at rest; re-identified per reader (doc 08 §7) |
| Calendar "availability status" | What field? | `transparency` busy for personal; summary "Out of office" |
| Retention "documents eligible for deletion" | Which classes? | supporting documents, company documents (superseded/retired), conversations; leave records never (Q-08) |
| Platform admin "global security policies" | What exactly? | Default thresholds + allowed ranges + upload limits; no tenant content |
| Reranker | Not predetermined | Chosen by evaluation between two Apache-2.0 candidates (ADR-0014) |

## 4. Open questions — decided (2026-11-04)

The brief asks for autonomy on decisions that can be derived from its principles. On request, the formerly open
questions were decided by applying those principles: deterministic rules in backend data (§5.5), per-company
configurability (§4.6), need-to-know and medical confidentiality (§4.5, §5.6), fail-closed privacy (§9.3), free
tiers and local-only operation (§4.1), simplicity over optional complexity (§15), human oversight and auditability
(§2). Every decision below is configuration or policy and can be overridden without architectural change; the
rationale is recorded in ADR-0028 (business rules) and ADR-0029 (operating decisions).

| ID | Question | Decision | Rationale (brief principle) | Applied in |
|----|----------|----------|------------------------------|------------|
| Q-01 | Entitlement, carry-over, half days, negative balances | Up-front grant on 1 January (`grant_mode = upfront`), pro-rated on hire by remaining full months with 0.5-day rounding; carry-over cap 5 days expiring 31 March (`CARRYOVER`/`EXPIRY` entries); whole days only (half days deferred, schema-ready); no negative balances — an audited HR `ADJUSTMENT` with reason is the only exception path. Monthly accrual is not implemented but needs no migration (ledger `GRANT` entries). | Ledger-native deterministic rules (§5.4); per-company policies (§4.6); "prefer simpler solutions" (§15); no scope creep. | doc 01 §6.2, doc 07 §4.3/§5.2, ADR-0028 |
| Q-02 | Sick-leave review | Never a manager approval step. ≤ 3 working days without a document and within the annual cap → auto-acknowledged (APPROVED at submission, ledger deduction, manager and HR notified). > 3 days, or any supporting document, or cap exceeded → HR review step. Retroactive reporting allowed up to 30 days. All thresholds are tenant data. | Medical matters are HR-confidential (§4.5, §5.6); need-to-know; thresholds in deterministic rule data (§5.5). | doc 01 §6.4, doc 07 §4.3, ADR-0028 |
| Q-03 | A manager's own request; fallback approver | The manager step is re-designated to the explicit `manager_override` if set, otherwise to HR. HR Administrators may decide any pending manager step as fallback (tenant setting `allow_hr_fallback`, default on), recorded with `fallback = true` on the step and in the audit log. Nobody decides a step on their own request. | Segregation of duties; workflows must not get stuck (reliability, §2); auditability. | doc 08 §5.2, doc 07 §4.3, ADR-0028 |
| Q-04 | Gemini free-tier terms; provider order; paid tier | Accept the free-tier terms **for fictional data only**; README and operations docs state that real personal or company data must not be used under free tiers. Gemini stays primary as the brief selects; `LLM_PROVIDER_ORDER` makes Groq-first a configuration change when Gemini quotas are exhausted. No paid tier. | Free tiers, no paid services (§4.1); pseudonymisation and exclusions make it acceptable (§9); established decision §4.2 kept. | doc 02 §7, doc 03 §7, doc 04 §9, doc 13 R-03/R-04, ADR-0029 |
| Q-05 | Hardware for evaluation | Design for the 7.1 GB reference machine as the only machine: an **evaluation-session** mode (`make eval-session`) stops `frontend` and `n8n`, runs the worker embedded in the API process, starts the `observability` profile with reduced ClickHouse/Valkey memory settings, and runs offline retrieval metrics without Langfuse first. A larger host is a convenience, never a requirement. | Reproducible local environment (§4.1); honest resource planning; no cloud fallback. | doc 02 §10, doc 11 §8, doc 12 M9, doc 13 R-01/R-02, ADR-0029 |
| Q-06 | Google project ownership; Testing status | The developer's own Google account owns the Cloud project and the OAuth client in Testing status; each tenant's company calendar is a secondary calendar created by the app in that account (isolated by calendar ID); 7-day re-authorisation is accepted and handled by the parked-job/re-auth flow. The mock adapter stays the default; the Google adapter is opt-in. | Intentional, free external integration (§4.1, §10); reliability by design (§10.2). | doc 10 §2, doc 13 R-05, ADR-0029 |
| Q-07 | Seed companies | Aegean Logistics S.A. (Piraeus; Greek-first; Greek public holidays 2026) and Brightwater Software Ltd. (Dublin; English-first; Irish public holidays 2026); documents in both languages per doc 05 §10; all people and facts fictional. | Two fictional companies (§3); distinct holiday calendars per tenant (§5.3); evaluation in both languages (§12.2). | doc 05 §10, doc 07 §8 |
| Q-08 | Retention classes and seed defaults | Supporting documents: 365 days after the leave end date, `notify_only` → HR decides. Superseded/retired company documents: 730 days, `notify_only` → reviewer decides. Conversations, messages, checkpoints and agent runs: 90 days, **automatic** deletion (not an HR record). Leave records, ledger and audit log: never deleted by the application. Seed values are labelled "placeholder, not legal advice". | Configurable, no legal defaults assumed (§5.7); HR review for documents; data minimisation for chat data (§9). | doc 07 §7, doc 06 W3/W6, ADR-0028 |
| Q-09 | What managers may see | Managers see leave type and reason for annual/unpaid requests routed to them. For sick leave they see the type ("sick leave") for planning but never the reason text, documents or HR review details. Team availability views, company-administrator views and calendars show absence only. | Need-to-know (§4.5, §10.1); managers cannot access confidential HR information (§4.7). | doc 08 §5.2, doc 09 §2.4, ADR-0028 |
| Q-10 | Approvals through chat | Yes — UI and assistant, both through the same propose → confirm → execute path. | Demonstrate the Approval subgraph and HITL (§6); one enforcement point (§6.2). | doc 04 §6 |
| Q-11 | Organisation simplification | One department per employee; one manager per department; per-employee manager override for routing. | Department-based organisation with individual assignments preserved where necessary (§4.7). | doc 07 §4.2 |
| Q-12 | Tenant bootstrap | The Platform Administrator creates the tenant and its first Company Administrator with a temporary password and a forced password change at first login; the platform administrator never holds the final credential; audited. | Separate security domains (§4.5); auditability. | doc 09 §2.9, doc 08 §4, doc 12 M1 |
| Q-13 | CI | `make` targets are the contract and must pass locally; a GitHub Actions workflow mirrors them (optional, free tier, no secrets, no live LLM calls). | Local-first, no mandatory cloud (§4.1); automated regression tests valued (§14). | doc 11 §9 |
| Q-14 | Chat retention; HR access to chat | 90 days per tenant. Owners see their full history; HR and Company Administrators see the redacted run history only (intents, tools, decisions, outcomes — no message text), each access audited; Platform Administrators see nothing. | Privacy, data minimisation and human oversight (§2, §9); audit. | doc 04 §7, doc 07 §7, ADR-0028 |

Residual *facts* (not decisions) supplied at implementation time: Gemini and Groq API keys, the Google
account/project identifiers, and the actual free-tier quota numbers read from AI Studio at M6.

## 5. Assumptions

| ID | Assumption |
|----|------------|
| A-01 | The development machine measured today (4 cores, 7.1 GB, no GPU, Docker 29.9, Compose 5.6, Python 3.14.4, Node 22.22) is the reference environment; a larger host may be used for evaluation. |
| A-02 | All seed data and documents are fictional and authored for this project; no real personal data enters the system. |
| A-03 | One department per employee; one manager per department; overrides per employee for routing. |
| A-04 | Leave requests are whole working days; half days deferred (P3). |
| A-05 | Tenants are single-timezone organisations (`tenants.timezone`). |
| A-06 | Keycloak is the only identity provider; no social or enterprise federation is required. |
| A-07 | The browser reaches the application through the frontend container on localhost; TLS is optional locally. |
| A-08 | Free-tier provider quotas are sufficient for development with mocking; live evaluation runs are small and budgeted. |
| A-09 | Google Calendar is used with a developer-owned Google account; the company calendar lives in the authorising admin's account. |
| A-10 | Greek and English are the only supported languages; other languages fall back to English with a notice. |
| A-11 | The n8n instance is used by one organisation (the project) for internal purposes (licence compliance). |
| A-12 | Licence and version facts are correct as of 2026-11-04 and are re-verified when each component is introduced. |

## 6. Review focus areas flagged in the brief (where to look)

| Area | Design answer | Verification already done |
|------|---------------|---------------------------|
| PostgreSQL RLS | `FORCE` RLS, `NOBYPASSRLS` runtime role, `SET LOCAL` per transaction from a contextvar, `NULLIF('')` policy pattern, meta-test, concurrency leak test, separate role for the checkpointer (doc 07 §2, doc 08 §6) | Empirical test on `pgvector:0.8.7-pg18`: no rows without context; rows only inside the `SET LOCAL` transaction; `''` after commit handled by `NULLIF` |
| LangGraph persistence | Checkpoints in a separate schema/role; thread ownership verified in app tables before any resume; pseudonymised state only; single-user interrupts; cross-user approvals in the DB state machine; retention job (doc 04 §6–§7, ADR-0008) | — (design) |
| PII protection | Presidio EL/EN + known-entity dictionary + Greek recognisers; tenant-stable vault with HMAC lookup and encrypted originals; six coverage points; authorised re-identification matrix; fail-closed exclusions; Langfuse masking (doc 08 §7, ADR-0017) | spaCy Greek model availability and licence verified |
| Document security | Category/ACL visibility in SQL + policy; risk tiers with per-tenant thresholds; quarantine never retrievable; supporting documents outside every text pipeline, encrypted, audited (doc 05 §1–§2, doc 08 §8–§9) | — (design) |
| ChromaDB / pgvector coexistence | One `VectorIndex` port; pgvector default under RLS; Chroma eval-only with application-enforced isolation; identical corpus/embeddings/dataset comparison reported in M9 (doc 05 §7, ADR-0011) | pgvector 0.8.7 and Greek FTS verified on PG 18 |

## 7. Documentation plan
See [docs/README.md](README.md#documentation-plan-for-the-implementation-phase). Ownership: each milestone updates the
affected architecture documents, adds ADR amendments where decisions changed, and writes a worklog entry; M9 fills
`docs/evaluation/`; M10 completes `docs/operations/` and the final review against brief §13.
