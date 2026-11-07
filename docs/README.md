# Documentation Index — Enterprise Leave Management AI Assistant

This directory is the single source of truth for *why* and *how* the system is built.
It is organised so that an engineer who has never seen the project can read it top to bottom
and implement the system without reconstructing the original discussions.

**Status:** Architecture baseline v0.1 (2026-11-04), kept current during implementation — M0–M10 delivered (see
[development/worklog/](development/worklog/) and [changes since the baseline](development/changes-since-baseline.md)). The baseline was produced before any application code existed
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
| 15 | [Technology justification](15-technology-justification.md) — every technology and tool: why, alternatives, trade-offs, evidence | 4, brief §13 "Technology decisions" |
| adr | [Architecture Decision Records](adr/README.md) | 19 |

Deliverable 18 (documentation structure) is this file plus the
[documentation plan](14-decision-log-and-open-questions.md#documentation-plan) for the implementation phase.

## Conventions used in these documents

- **MUST / SHOULD / COULD** follow RFC 2119 meaning and map to priority P1 / P2 / P3.
- Requirement IDs look like `FR-LEAVE-03`; ADRs look like `ADR-0007`; risks look like `R-12`.
- Diagrams are Mermaid. They render on GitHub, GitLab and in most IDEs.
- "The brief" means [`../PROJECT_BRIEF.md`](../PROJECT_BRIEF.md) (the master project brief).
  Section references such as *brief §4.6* point into it.
- The baseline (written before any code) claimed no code, test or measurement. Since implementation began, every
  result in these documents comes from an executed run, cited with its milestone and date; amendments are marked
  with the milestone that made them (e.g. *M9 note*), and no **[to be filled]** marker remains.

## Live documents (implementation phase)

| Document | Purpose |
|----------|---------|
| [operations/installation.md](operations/installation.md) | Prerequisites, first start, seed data, document ingestion, profiles, running the tests |
| [operations/configuration.md](operations/configuration.md) | Every environment variable (generated from `deploy/.env.example`) |
| [operations/troubleshooting.md](operations/troubleshooting.md) | Symptoms, causes and fixes by area |
| [operations/backup-restore.md](operations/backup-restore.md) | Backup, restore and the keys that must be kept |
| [development/changes-since-baseline.md](development/changes-since-baseline.md) | Architectural changes made during implementation, with reasons |
| [evaluation/](evaluation/summary.md) | Evaluation protocol, results, security assessment, known weaknesses, future improvements |
| [development/process.md](development/process.md) | Methodology, AI-assisted process, definition of done, conventions |
| [development/worklog/](development/worklog/) | One entry per milestone: what was built, measurements, problems, changes versus the plan |
| [api/](api/README.md) | Generated OpenAPI snapshot (`openapi.json`, checked by `make contract`) and contract notes |

## Documentation plan for the implementation phase

The brief (§13) requires documentation to remain a first-class deliverable. The structure as built (M10); the
plan's separate `motivation/` folder and `problems-encountered.md` were folded into docs 00/01 (motivation,
requirements) and the worklogs plus [troubleshooting](operations/troubleshooting.md) (problems):

```
docs/
├── README.md                      # this index
├── 00-15 …                        # architecture package (kept current, not frozen) and the technology justification
├── adr/                           # one ADR per decision; amendments appended, superseded ADRs never deleted
├── development/
│   ├── process.md                 # methodology, AI-assisted process, definition of done
│   ├── changes-since-baseline.md  # architectural changes during implementation, with reasons
│   └── worklog/                   # one entry per milestone: built, measured, problems, deviations
├── operations/                    # installation, configuration (generated), troubleshooting, backup and restore
├── evaluation/                    # protocol, results, security assessment, known weaknesses, summary
└── api/                           # generated OpenAPI snapshot + contract notes
```
