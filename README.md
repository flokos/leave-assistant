# Enterprise Leave Management AI Assistant

A multilingual (Greek/English), multi-tenant leave-management assistant that lets employees ask policy questions
and request leave in natural language, while deterministic backend services own identity, tenancy, business rules,
approvals, execution and audit. The LLM coordinates; the backend decides.

**Project status: architecture and planning phase.** No application code exists yet. The first deliverable —
requested by the project brief before any implementation — is the architecture package in [`docs/`](docs/README.md).

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

## How this repository will be organised

```
docs/        architecture package, ADRs, (later) operations, evaluation and development logs
backend/     FastAPI application, LangGraph agent, worker          (from milestone M0)
frontend/    React + TypeScript SPA                                   (from M0)
deploy/      Docker Compose, Keycloak realm export, init scripts      (from M0)
automation/  n8n workflows as JSON                                    (from M8)
evaluation/  datasets and evaluation harness (own Python project)     (from M9)
```

Claude Code is used as the development assistant; it is not a runtime component of the application.
