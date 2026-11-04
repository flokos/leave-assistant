# Architecture Decision Records

Format per record: Status · Context · Problem · Decision · Alternatives · Advantages · Disadvantages · Security
implications · Operational complexity · Consequences (brief §15). Records are never deleted; a superseding ADR
links back to the one it replaces.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](ADR-0001-modular-monolith.md) | Modular monolith with a separate worker process | Accepted |
| [0002](ADR-0002-layered-architecture.md) | Layered backend with ports and adapters | Accepted |
| [0003](ADR-0003-multitenancy-rls.md) | Shared PostgreSQL with RLS and transaction-scoped tenant context | Accepted |
| [0004](ADR-0004-keycloak-single-realm.md) | Keycloak single realm with tenant groups/claims | Accepted |
| [0005](ADR-0005-leave-ledger.md) | Ledger-based leave balances | Accepted |
| [0006](ADR-0006-approval-engine-and-state-machine.md) | Deterministic approval engine; cross-user approvals in the database | Accepted |
| [0007](ADR-0007-langgraph-topology.md) | LangGraph topology and trusted runtime context | Accepted |
| [0008](ADR-0008-langgraph-checkpointer.md) | PostgreSQL checkpointer in its own schema/role; pseudonymised state | Accepted |
| [0009](ADR-0009-tool-gateway.md) | Tool gateway: propose → validate → confirm → execute | Accepted |
| [0010](ADR-0010-llm-provider-router.md) | LLM provider router and failover policy | Accepted |
| [0011](ADR-0011-retrieval-backend.md) | pgvector default, ChromaDB evaluation profile | Accepted (default confirmed at M9) |
| [0012](ADR-0012-hybrid-retrieval.md) | Hybrid retrieval with PostgreSQL FTS, RRF and reranking | Accepted |
| [0013](ADR-0013-embeddings-e5-small.md) | Local embeddings with multilingual-e5-small | Accepted |
| [0014](ADR-0014-reranker-selection.md) | Reranker chosen by evaluation; Jina excluded | Proposed (M9) |
| [0015](ADR-0015-docling-and-chunking.md) | Docling + Tesseract OCR + structure-aware chunking | Accepted |
| [0016](ADR-0016-object-storage-and-encryption.md) | Storage port over SeaweedFS; envelope encryption | Accepted |
| [0017](ADR-0017-pii-pseudonymisation-vault.md) | PII detection, pseudonym vault, authorised re-identification | Accepted |
| [0018](ADR-0018-prompt-injection-defence.md) | Prompt-injection defence in depth; risk tiers | Accepted |
| [0019](ADR-0019-n8n-scope-and-job-queue.md) | n8n scope; outbox; PostgreSQL job queue | Accepted |
| [0020](ADR-0020-calendar-sync.md) | Google Calendar outbox sync and reconciliation | Accepted |
| [0021](ADR-0021-notifications-mailpit.md) | Notifications with n8n and Mailpit (replaces MailHog) | Accepted |
| [0022](ADR-0022-langfuse-optional-profile.md) | Langfuse v4 as optional profile; Valkey; SeaweedFS blobs | Accepted |
| [0023](ADR-0023-testing-and-evaluation.md) | Testing with fake providers; RAGAS + Langfuse evaluation | Accepted |
| [0024](ADR-0024-frontend-stack.md) | Frontend stack | Accepted |
| [0025](ADR-0025-compose-profiles-and-resources.md) | Compose profiles and resource budget | Accepted |
| [0026](ADR-0026-python-version-and-dependency-pinning.md) | Python 3.14 and resolver-verified pinning | Accepted |
| [0027](ADR-0027-document-permission-model.md) | Document categories and permissions | Accepted |
| [0028](ADR-0028-leave-rule-defaults.md) | Leave, sick-leave, routing, visibility and retention defaults | Accepted |
| [0029](ADR-0029-operating-decisions.md) | Operating decisions: free tiers, provider order, evaluation hardware, Google project, CI | Accepted |
