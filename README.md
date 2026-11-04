# Enterprise Leave Management AI Assistant

A multilingual (Greek/English), multi-tenant leave-management assistant that lets employees ask policy questions
and request leave in natural language, while deterministic backend services own identity, tenancy, business rules,
approvals, execution and audit. The LLM coordinates; the backend decides.

**Project status: implementation phase, milestone M0 (foundation).** The architecture package in
[`docs/`](docs/README.md) was completed first, as the project brief requires; code is added milestone by milestone
(roadmap in [docs/12](docs/12-implementation-roadmap.md)).

| Start here | |
|------------|--|
| [Project brief](PROJECT_BRIEF.md) | Product motivation, requirements and established technology decisions |
| [Documentation index](docs/README.md) | Reading order for the architecture package |
| [Executive summary](docs/00-executive-summary.md) | The design in ten minutes, including recommended changes to the brief |
| [Decision log and open questions](docs/14-decision-log-and-open-questions.md) | What was accepted, what is recommended for change, and the decisions taken on the formerly open questions |
| [Architecture Decision Records](docs/adr/README.md) | Why each significant decision was made |

## Planned stack (verified 2026-11-04)

Python 3.14 · FastAPI · PostgreSQL 18 with pgvector and row-level security · SQLAlchemy 2.1 · LangGraph 1.2 ·
Gemini and Groq (runtime LLMs, free tiers) · Keycloak 26 · SeaweedFS (S3) · Docling + Tesseract · Sentence Transformers
(multilingual-e5-small) · Presidio · n8n Community Edition · Mailpit · React 19 + TypeScript + Ant Design · Langfuse v4 ·
Pytest + RAGAS. Everything runs locally with Docker Compose.

## Quick start

```bash
make env && make build && make up    # or `make up-staged` on hosts with < 4 GB free RAM
make smoke                            # liveness + readiness through the frontend proxy
```
Then open http://localhost:8080 (application), http://localhost:8081 (Keycloak) and http://localhost:8025 (Mailpit).
Details, profiles and troubleshooting: [docs/operations/installation.md](docs/operations/installation.md).

## How this repository is organised

```
docs/        architecture package, ADRs, (later) operations, evaluation and development logs
backend/     FastAPI application, LangGraph agent, worker          (M0: skeleton, health endpoints, worker loop)
frontend/    React + TypeScript SPA                                   (M0: shell, i18n, system status page)
deploy/      Docker Compose, Keycloak realm export, init scripts      (M0: core profile)
automation/  n8n workflows as JSON                                    (from M8)
evaluation/  datasets and evaluation harness (own Python project)     (from M9)
```

Claude Code is used as the development assistant; it is not a runtime component of the application.
