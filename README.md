# Enterprise Leave Management AI Assistant

A multilingual (Greek/English), multi-tenant leave-management assistant that lets employees ask policy questions
and request leave in natural language, while deterministic backend services own identity, tenancy, business rules,
approvals, execution and audit. The LLM coordinates; the backend decides.

**Project status: all milestones M0–M10 are implemented.** The architecture package in [`docs/`](docs/README.md)
was written first, as the project brief requires; code followed milestone by milestone (roadmap in
[docs/12](docs/12-implementation-roadmap.md), one worklog per milestone in
[docs/development/worklog/](docs/development/worklog/)). All data is fictional; the LLM providers run on free tiers.

| Start here | |
|------------|--|
| [Project brief](PROJECT_BRIEF.md) | Product motivation, requirements and established technology decisions |
| [Documentation index](docs/README.md) | Reading order for the whole documentation |
| [Executive summary](docs/00-executive-summary.md) | The design in ten minutes |
| [Installation](docs/operations/installation.md) | From a clean clone to a running stack, seed data and tests |
| [Evaluation summary](docs/evaluation/summary.md) | Measured results against the targets, security assessment, known weaknesses, future improvements |
| [Architecture Decision Records](docs/adr/README.md) | Why each significant decision was made |
| [Changes since the baseline](docs/development/changes-since-baseline.md) | What changed during implementation, and why |

## What it does
- **Employees** ask about leave policies in Greek or English and get answers grounded in the company's documents,
  with citations, in the language they asked in; they check balances and request, change or cancel leave through
  the chat or the forms — every change is shown on a confirmation card first.
- **Managers and HR** approve in the app or in the chat, see team availability, review supporting documents for sick
  leave and the documents held back by the content-security checks.
- **Company administrators** manage employees, departments, leave types and policies, holidays, approval rules,
  company settings, the security policy, calendars and the audit log (with CSV export).
- **Platform administrators** create companies and set the platform defaults and the global security policy; they
  never see a company's business data.

## Stack as built
Python 3.14 · FastAPI · PostgreSQL 18 with pgvector, full-text search and row-level security · SQLAlchemy 2.1 ·
LangGraph 1.2 · Gemini (primary) and Mistral (second provider; free tiers) · Keycloak 26 · SeaweedFS (S3) · Docling +
Tesseract · Sentence Transformers (multilingual-e5-small) and a cross-encoder reranker on ONNX Runtime · Presidio ·
n8n Community Edition · Mailpit · React 19 + TypeScript + Ant Design · Langfuse v4 · Pytest, Vitest, Playwright,
RAGAS. Everything runs locally with Docker Compose. Why each of these was chosen, and what else was considered:
[technology justification](docs/15-technology-justification.md).

## Quick start
```bash
make env          # creates deploy/.env and generates every key; add GEMINI_API_KEY and/or MISTRAL_API_KEY
make build
make models       # once, before the first seed: embedding, reranker and document models (2.5 GB)
make up-staged    # or `make up` on hosts with more than 4 GB free RAM
make bootstrap    # migrations + seed of the two fictional companies (idempotent)
make ingest       # processes the seeded company documents
make smoke        # liveness + readiness through the frontend proxy
```
Then open http://localhost:8080 (application; seed users and their roles are listed in the installation guide),
http://localhost:8081 (Keycloak) and http://localhost:8025 (Mailpit). Full guide, profiles and tests:
[installation](docs/operations/installation.md) · every variable: [configuration](docs/operations/configuration.md) ·
problems: [troubleshooting](docs/operations/troubleshooting.md) · [backup and restore](docs/operations/backup-restore.md).

## How this repository is organised
```
docs/        architecture package (00–15), ADRs, operations, evaluation results, development process and worklogs
backend/     FastAPI application, LangGraph agent, job worker, evaluation harness, migrations, tests
frontend/    React + TypeScript single-page application, Vitest and Playwright tests
deploy/      Docker Compose (profiles core, automation, observability, eval), Keycloak realm, init scripts
automation/  n8n workflows as JSON, credentials template, init script
evaluation/  dataset leave-eval-v1, RAGAS and Langfuse sync (its own uv project)
scripts/     key generation, backup and restore, configuration reference, memory peaks
```

Claude Code was used as the development assistant ([development process](docs/development/process.md)); it is not a
runtime component of the application.

## Further reading
- [Technology justification](docs/15-technology-justification.md) — every technology and tool in this repository:
  why it was chosen, the alternatives, advantages, disadvantages, operational implications and what the milestones
  measured.
- [Technology stack, versions and licences](docs/02-technology-stack.md) — the resolver-verified version set.
- [Risk register](docs/13-risk-register.md) and [known weaknesses](docs/evaluation/known-weaknesses.md).
