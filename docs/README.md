# Documentation Index — Enterprise Leave Management AI Assistant

This directory is the single source of truth for *why* and *how* the system is built.
It is organised so that an engineer who has never seen the project can read it top to bottom
and implement the system without reconstructing the original discussions.

**Status:** Architecture baseline v0.1 — produced before any application code exists
(brief §16: "Do not start implementing the application yet").
Every statement about versions, licences and limits was verified on **2026-11-04** and is
marked as such; anything not verified is explicitly labelled *assumption* or *to verify*.

## Reading order

| # | Document | Brief deliverable(s) covered |
|---|----------|------------------------------|
| 00 | [Executive summary](00-executive-summary.md) | 1 |
| 01 | [Requirements specification](01-requirements.md) | 2, 3 |
| 02 | [Technology stack, versions and licences](02-technology-stack.md) | 4, plus §15.6 "check versions and licensing", §15.7 "resource requirements" |
| 03 | [System, component and deployment architecture](03-system-architecture.md) | 5, 6, 7 |
| 04 | [LangGraph agent architecture and workflows](04-langgraph-workflows.md) | 8 |
| 05 | [RAG, document processing and ingestion](05-rag-and-ingestion.md) | 9 |
| 06 | [n8n automation workflows](06-n8n-workflows.md) | 10 |
| 07 | [Data model and database schema](07-data-model.md) | 11 |
| 08 | [Security architecture: tenancy, identity, PII, prompt injection](08-security-architecture.md) | 12, 13, 15 |
| 09 | [API contracts](09-api-contracts.md) | 14 |
| 10 | [Google Calendar integration](10-calendar-integration.md) | 16 |
| 11 | [Testing and evaluation strategy](11-testing-and-evaluation.md) | 17 |
| 12 | [Implementation roadmap and milestones](12-implementation-roadmap.md) | 20 |
| 13 | [Risk register](13-risk-register.md) | 21 |
| 14 | [Decision log, assumptions and decided questions](14-decision-log-and-open-questions.md) | 22 + closing section of §16 |
| adr | [Architecture Decision Records](adr/README.md) | 19 |

Deliverable 18 (documentation structure) is this file plus the
[documentation plan](14-decision-log-and-open-questions.md#documentation-plan) for the implementation phase.

## Conventions used in these documents

- **MUST / SHOULD / COULD** follow RFC 2119 meaning and map to priority P1 / P2 / P3.
- Requirement IDs look like `FR-LEAVE-03`; ADRs look like `ADR-0007`; risks look like `R-12`.
- Diagrams are Mermaid. They render on GitHub, GitLab and in most IDEs.
- "The brief" means [`../PROJECT_BRIEF.md`](../PROJECT_BRIEF.md) (the master project brief).
  Section references such as *brief §4.6* point into it.
- Nothing here claims that any code, test or measurement exists yet. Sections that must be
  filled with real results during implementation are marked **[to be filled during Mx]**.

## Documentation plan for the implementation phase

The brief (§13) requires documentation to remain a first-class deliverable. The structure below
will be created incrementally; this architecture package is its seed.

```
docs/
├── README.md                      # this index
├── 00-14 …                        # architecture baseline (this package) — kept current, not frozen
├── adr/                           # one ADR per decision; superseded ADRs are never deleted
├── motivation/                    # problem statement, business requirements (extracted from the brief)
├── development/                   # methodology, AI-assisted process log, problems encountered, changes
│   ├── process.md
│   ├── worklog/                   # one short entry per milestone: what was done, what changed and why
│   └── problems-encountered.md
├── operations/                    # install, compose, env vars, DB init, seed data, ingestion, tests, troubleshooting
├── evaluation/                    # datasets, methodology, actual results, security assessment, known weaknesses
└── api/                           # generated OpenAPI snapshot + human-written contract notes
```
