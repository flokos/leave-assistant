# 02 — Technology Stack: Cross-Validated Versions, Licences and Resource Requirements

This document answers brief §15.6 ("check current software versions and licensing") and §15.7 ("infrastructure
and resource requirements"). Versions are **not** "latest of each"; they are the latest set that is
**mutually compatible**, established on 2026-11-04 by the method in §1 and re-verified at M0 when the real
`pyproject.toml`/`uv.lock` are created (ADR-0026).

## 1. How the versions were established

1. Collected declared constraints (`requires_python`, `requires_dist`) from PyPI metadata for every direct dependency
   and crossed them pairwise (e.g., langgraph ↔ langchain-core ↔ langchain-google-genai ↔ checkpoint-postgres ↔ psycopg).
2. Ran a real resolver: `uv lock` (uv 0.13.7) for a scratch project containing the complete runtime, dev, eval and
   ONNX groups, for Python 3.14 **and** 3.13 (identical resolution, 277 packages).
3. Probed every divergence between "latest" and "resolved" with explicit pins to find the responsible constraint
   (results in §9).
4. Installed the resolved set on the reference machine (Python 3.14.4) and ran an import and ML smoke test
   (verification record in §12).
5. Verified infrastructure images empirically where cheap (PostgreSQL + pgvector + Greek FTS + RLS on `pg18`) and
   against vendor compatibility pages (Keycloak, n8n, Langfuse, Vite, Ant Design).

The resulting lock is reproduced as the initial `backend/uv.lock` at M0; any drift is corrected in the lock, never
by editing this document first.

## 2. Runtime language: Python 3.14 (fallback 3.13)

| Constraint source | `requires_python` | Notes |
|-------------------|-------------------|-------|
| torch 2.15.0, transformers 4.57.6, presidio 2.2.366, sqlalchemy 2.1.8, langfuse 5.1.0 | ≥ 3.11 | floor |
| presidio-analyzer 2.2.366 | < 3.15 | ceiling |
| docling 2.147.0, spacy 3.8.18, psycopg 3.3.8, pydantic 2.14.2 | classifiers include 3.14 | |
| langgraph 1.2.19 | ≥ 3.10 (classifiers list up to 3.13) | pure Python; resolves and imports on 3.14 |
| Compiled wheels for cp314 | torch, onnxruntime 1.31.1, docling-parse 7.24.2, tokenizers (abi3), chromadb (abi3), lingua 2.2.0, psycopg-binary, pydantic-core, scipy, pyarrow | all present on PyPI |

Decision: **Python 3.14** (`python:3.14-slim`), matching the host interpreter (3.14.4). Python 3.13 produces the
identical lock and is the documented fallback (R-21).

## 3. Backend runtime dependencies (resolved, Python 3.14)

| Group | Package | Version | Licence | Role / constraint notes |
|-------|---------|---------|---------|-------------------------|
| Web | fastapi | 0.162.0 | MIT | API; requires starlette ≥ 0.46 (1.10.0), pydantic ≥ 2.9 |
| | uvicorn | 0.54.0 | BSD-3 | ASGI server |
| | sse-starlette | 3.6.0 | BSD-3 | SSE streaming |
| | pydantic / pydantic-settings | 2.14.2 / 2.16.2 | MIT | schemas, config; spaCy requires pydantic < 3 ✓ |
| Data | sqlalchemy[asyncio,postgresql-psycopg] | 2.1.8 | MIT | async ORM; psycopg ≥ 3.0.7 ✓ |
| | psycopg[binary,pool] / psycopg-pool | 3.3.8 / 3.3.3 | LGPL-3.0 | one driver for SQLAlchemy **and** the LangGraph checkpointer |
| | alembic | 1.20.1 | MIT | migrations |
| | pgvector (python) | 0.5.1 | MIT | SQLAlchemy `vector` type |
| Agent | langgraph | 1.2.19 | MIT | needs langchain-core ≥ 1.4.7 < 2, langgraph-checkpoint ≥ 4.3 < 5, langgraph-prebuilt 1.1.x |
| | langgraph-checkpoint / -prebuilt / -sdk | 4.3.1 / 1.1.1 / 0.4.x | MIT | |
| | langgraph-checkpoint-postgres | 3.2.0 | MIT | needs checkpoint ≥ 4.3 < 5 ✓, psycopg ≥ 3.2 ✓ |
| | langchain-core | 1.7.5 | MIT | shared by all langchain-* packages |
| | langchain-google-genai | 4.4.3 (pin `>=4.4`) | MIT | needs langchain-core ≥ 1.6.10 ✓, google-genai 2.34.0; without the explicit lower bound the resolver settles on 3.2.0 |
| | langchain-groq | 1.1.3 | MIT | groq SDK 0.37.1 |
| | langfuse (SDK) | 5.1.0 | MIT | OpenTelemetry ≥ 1.45; no LangChain dependency; server v4 compatible |
| ML | torch (CPU index) | 2.15.0+cpu | BSD-3 | `[[tool.uv.index]] pytorch-cpu`; **torchvision must come from the same index** (see §9) |
| | transformers | **4.57.6** | Apache-2.0 | ceiling `< 4.58` imposed by docling-ibm-models and optimum-onnx |
| | sentence-transformers | **5.7.0** | Apache-2.0 | 6.x requires transformers ≥ 5 → excluded (see §9) |
| | tokenizers / huggingface-hub | 0.22.2 / 0.36.2 | Apache-2.0 | consistent with transformers 4.57 |
| | docling (= docling-slim[standard]) | 2.147.0 | MIT | docling-core 2.106.0, docling-ibm-models 4.0.4, docling-parse 7.24.2; pulls opencv-python-headless 5.0, rapidocr (unused) |
| | spacy | 3.8.18 | MIT | models `el_core_news_md` 3.8.0 (**CC BY-NC-SA 3.0**), `en_core_web_lg` 3.8.0 (MIT) |
| | presidio-analyzer / -anonymizer | 2.2.366 | MIT | requires numpy < 2.5 → numpy 2.4.6; pydantic ≥ 2.12.5 ✓ |
| | lingua-language-detector | 2.2.0 | Apache-2.0 | EL/EN detection |
| | onnxruntime / optimum / optimum-onnx | 1.31.1 / 2.1.0 / 0.1.0 | MIT / Apache-2.0 | optional int8 reranker export; optimum-onnx requires transformers < 4.58 |
| Infra clients | boto3 | 1.43.123 | Apache-2.0 | S3 (SeaweedFS) |
| | PyJWT[crypto] / cryptography | 2.15.1 / 50.0.2 | MIT / Apache-2.0+BSD | JWT validation; envelope encryption |
| | httpx | 0.28.x | BSD-3 | Keycloak Admin API, OAuth |
| | google-api-python-client / google-auth / google-auth-oauthlib | 2.201.0 / 2.62.1 / 1.5.0 | Apache-2.0 | Calendar |
| | structlog / tenacity | 26.1.0 / 9.1.4 | MIT+Apache / Apache-2.0 | logging; retries (langchain-core pins tenacity != 8.4) |
| Eval client | chromadb (client) | 1.5.9 | Apache-2.0 | `eval` profile; server image pinned to the same version |

Dev/test (dependency group `dev`): pytest 9.2.0, pytest-asyncio, pytest-cov, hypothesis, respx, freezegun,
import-linter 2.15, ruff 0.17.3, mypy 2.4.1, pip-audit.

Evaluation (separate uv project `evaluation/`, own lock — see §9): ragas 0.4.3, **langchain-community < 0.4** (0.3.31),
datasets 5.1.0, langfuse 5.1.0, langchain-google-genai 4.4.3, langchain-groq, pandas.

## 4. Machine-learning models

| Model | Params / size | Role | CPU behaviour (4-core estimate; measured at M9) | Licence |
|-------|---------------|------|--------------------------------------------------|---------|
| intfloat/multilingual-e5-small | 118M / ~470 MB fp32 | embeddings (384-d, 512 tokens) | ~20–40 passages/s | MIT |
| BAAI/bge-reranker-v2-m3 | 568M / ~2.2 GB fp32, ~600 MB int8 | reranker candidate A | ~1–3 s per 20 pairs fp32 | Apache-2.0 |
| cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 | ~118M / ~470 MB | reranker candidate B | ~0.3–0.6 s per 20 pairs | Apache-2.0 |
| spaCy en_core_web_lg / el_core_news_md | ~560 MB / ~40 MB | NER for Presidio | fast | MIT / CC BY-NC-SA 3.0 |
| Docling layout + table models | ~500 MB download | parsing | 1–5 s per text page | MIT (Docling); model cards checked at M4 |
| Tesseract `ell` + `eng` | ~30 MB | OCR | 2–6 s per page | Apache-2.0 |

Excluded: `jinaai/jina-reranker-v2-base-multilingual` and newer Jina rerankers (CC BY-NC 4.0).

## 5. Infrastructure images (cross-checked against each consumer)

| Service | Image | Cross-check evidence |
|---------|-------|----------------------|
| PostgreSQL + pgvector | `pgvector/pgvector:0.8.7-pg18` (PG 18.6, pgvector 0.8.7) | Keycloak 26.8 supports PG 14–18 (tested on 18); n8n 2.46 supports 16–18; Langfuse v4 requires ≥ 15; `greek` FTS config and RLS behaviour verified empirically on this image; fallback `0.8.7-pg17` (also verified) |
| Keycloak | `quay.io/keycloak/keycloak:26.8.1` | Apache-2.0; PG 18 supported |
| n8n | `n8nio/n8n:2.46.6` | Sustainable Use License 1.0 (internal use); PG 16–18 |
| SeaweedFS | `chrislusf/seaweedfs:4.52` | Apache-2.0; S3 API for boto3 and for Langfuse blobs (`LANGFUSE_S3_*_FORCE_PATH_STYLE=true`) |
| Mailpit | `axllent/mailpit:v1.33.0` | MIT; replaces MailHog |
| Chroma (eval) | `chromadb/chroma:1.5.9` | equals client version |
| Langfuse (observability) | `langfuse/langfuse:4`, `langfuse/langfuse-worker:4` | MIT core; needs PostgreSQL, ClickHouse, Redis/Valkey, S3 |
| ClickHouse | `clickhouse/clickhouse-server:26.3.62.3` | tag from Langfuse's own compose file |
| Valkey | `valkey/valkey:8` | BSD-3; Langfuse documents Redis/Valkey |
| Base images | `python:3.14-slim` (+ `tesseract-ocr tesseract-ocr-ell tesseract-ocr-eng`), `node:24-alpine` (build), `nginx:alpine` | Vite 8.3 requires Node ≥ 20.19 / ≥ 22.12 |
| Host tooling (verified) | Docker 29.9.2, Compose v5.6.1, Python 3.14.4, Node 22.22.1, git 2.53 | |

## 6. Frontend set

Node 24 LTS (host Node 22.22 also satisfies Vite) · Vite 8.3.x · React 19.2.x · TypeScript 5.9+ · **Ant Design 6.7.0**
(MIT; peer react ≥ 18) · react-oidc-context 3.x + oidc-client-ts 3.x · @tanstack/react-query 5.x · react-i18next 16.x
(i18next 25.x) · openapi-typescript 7.x · Vitest · Playwright 1.5x. Exact pins in `frontend/package.json` +
`pnpm-lock.yaml` at M0.

## 7. Runtime LLM providers

### 7.1 Gemini (primary)
- Rate limits are per project and shown only inside AI Studio; RPD resets at midnight Pacific (official rate-limits
  page). Third-party reports (unverified) suggest very low daily quotas for current Flash models and higher ones for
  Flash-Lite; numbers must be read from AI Studio at M6 (R-03).
- Model families on the official page: Gemini 3.8 Flash, 3.6 Flash, 3.5 Flash-Lite, 3.1 Flash Lite, 2.5 Pro/Flash/
  Flash-Lite, 2.0 Flash, … Model IDs are configuration (`GEMINI_MODEL`, `GEMINI_MODEL_FALLBACK`); proposed default:
  current Flash-Lite for routing/extraction, Flash for answer generation.
- Data use: the pricing page marks free-tier content as "used to improve our products" (paid tier: not) → fictional data,
  pseudonymisation, sensitive categories never leave the host (R-04, Q-04).
- Capabilities used: function calling, structured output, system instructions, streaming (`langchain-google-genai` 4.4).

### 7.2 Groq (failover; co-primary during development)
- Developer-plan base limits (official page): e.g., `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` 30 RPM / 1K RPD /
  8K TPM / 200K TPD; free tier may be lower; limits are per organisation.
- Tool use (official page): `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `openai/gpt-oss-120b/20b`, `qwen/qwen3.8-27b`,
  `minimaxai/minimax-m2.7`, `meta-models/muse-spark-open`; JSON mode on all; parallel tool calls not on gpt-oss.
- Proposed defaults: `GROQ_MODEL=llama-3.3-70b-versatile`, `GROQ_MODEL_FALLBACK=openai/gpt-oss-120b`.
- Consequence of 8–12K TPM caps: hard 3,500-token input budget per call (doc 04 §8).

### 7.3 Provider policy
See ADR-0010: failover on transient errors only; repair-retry on invalid output; never on security/authorization errors.
Provider order is configuration (`LLM_PROVIDER_ORDER=gemini,groq` by default, as the brief selects); Groq-first is a
one-line change if Gemini quotas prove unusable (decided Q-04 — no paid tier).

## 8. Licence compliance summary

- All runtime components permit self-hosted internal business use. n8n: Sustainable Use License 1.0 ("your own internal
  business purposes"; `.ee.` files excluded — verified in `LICENSE.md`). Langfuse: MIT core, no EE add-ons used.
  psycopg: LGPL-3.0 (dynamic use, unmodified) — compliant.
- **Exception:** spaCy `el_core_news_md` is CC BY-NC-SA 3.0; acceptable for this non-commercial project; swap path via
  the `PiiEngine` port (ADR-0017). Recorded in NFR-LIC-01.
- Jina rerankers excluded (non-commercial). Model cards re-checked when each model is introduced (M4–M6) and recorded
  in `docs/operations/licences.md`.

## 9. Compatibility rules discovered by crossing (must be respected in `pyproject.toml`)

| Rule | Reason (evidence) | Effect |
|------|-------------------|--------|
| `transformers < 4.58` | `docling-ibm-models 4.0.4` (via `docling-slim[standard]`) and every `optimum-onnx` release pin it; a `transformers>=5` probe only "resolved" by backtracking to `sentence-transformers 0.3.4` | transformers 4.57.6; sentence-transformers 5.7.0 (6.x requires transformers ≥ 5 → unsatisfiable with the ONNX extra) |
| `torch` **and** `torchvision` as **direct** dependencies, both from the PyTorch CPU index | mixing `torch 2.15.0+cpu` (CPU index) with `torchvision 0.29.2` (PyPI, CUDA-linked) fails at import: `operator torchvision::nms does not exist`, which breaks `sentence-transformers`. uv applies `[tool.uv.sources]` to direct dependencies only — a transitive torchvision silently resolves from PyPI (observed in the lock: `source = registry pypi.org`) | declare `torchvision` next to `torch` in `dependencies` and map both in `[tool.uv.sources]` to the `pytorch-cpu` index |
| `langchain-google-genai >= 4.4` explicit | the unconstrained resolver settled on 3.2.0; the explicit bound resolves cleanly to 4.4.3 with google-genai 2.34.0 | explicit lower bound in `pyproject.toml` |
| `presidio-analyzer >= 2.2.366` explicit | unconstrained resolution preferred numpy 2.5.4 and an older presidio (2.2.362); the explicit bound yields presidio 2.2.366 with numpy 2.4.6, accepted by everything else | explicit lower bound |
| `numpy < 2.5` | presidio-analyzer 2.2.366 | numpy 2.4.x |
| One `langchain-core 1.x` line | langgraph, langchain-google-genai, langchain-groq all require `< 2` with different floors (max floor 1.6.10) | langchain-core 1.7.5 |
| `psycopg 3` shared | SQLAlchemy `postgresql-psycopg` extra and `langgraph-checkpoint-postgres` both target psycopg 3 | single driver, separate pools/roles |
| spaCy `3.8.x` | the 3.8.0 models require `spacy >= 3.8, < 3.9`; presidio excludes 3.8.14 on Python 3.14 | spacy 3.8.18 |
| Chroma client = server | 1.x majors must match | 1.5.9 both |
| `ragas` isolated in `evaluation/` with `langchain-community < 0.4` | ragas 0.4.3 imports `langchain_community.chat_models.vertexai`, removed in langchain-community 0.4.x (the version an unconstrained resolver selects) → `import ragas` fails; with `langchain-community<0.4` (0.3.31, which still accepts langchain-core 1.7.5) it imports cleanly — verified on 3.14 | separate uv project with its own lock and the explicit `<0.4` bound (also keeps ragas' heavy dependencies out of the runtime image) |
| Python `< 3.15` | presidio-analyzer | 3.14 chosen |
| Node ≥ 22.12 | Vite 8 | Node 24 LTS in Docker |

## 10. Infrastructure and resource requirements

### 10.1 Per-container budget (RAM, steady state)
| Service | Estimated RSS | Compose `mem_limit` | Notes |
|---------|---------------|---------------------|-------|
| postgres | 200–400 MB | 768 MB | `shared_buffers=128MB`; HNSW builds peak higher |
| keycloak | 600–900 MB | 1 GB | `JAVA_OPTS_KC_HEAP="-Xms256m -Xmx512m"`, optimised build |
| seaweedfs | 100–300 MB | 512 MB | single `weed server -s3 -filer` |
| mailpit | 20–40 MB | 128 MB | |
| api | 0.9–1.6 GB | 2 GB | e5-small + int8 reranker + spaCy models resident |
| worker | 1.2–2.5 GB (Docling peaks) | 3 GB | lazy-loaded, unloaded when idle |
| frontend (nginx) | 10–20 MB | 64 MB | |
| n8n | 250–500 MB | 768 MB | |
| chroma | 200–500 MB | 768 MB | eval only |
| langfuse-web + worker | 500 + 400 MB | 1 + 1 GB | |
| clickhouse | 1–2 GB practical (official guidance higher) | 2.5 GB | |
| valkey | 30–80 MB | 256 MB | |

### 10.1a Measured at M0 (core profile, idle, no ML models loaded yet — 2026-11-04, reference machine)
| Container | Measured RSS | Note |
|-----------|--------------|------|
| postgres | 19–61 MiB | 61 MiB right after init, ~28 MiB idle |
| keycloak | 210–499 MiB | 499 MiB during start-up (109 s to healthy), 210–304 MiB settled |
| seaweedfs | 11–34 MiB | |
| mailpit | 5–8 MiB | |
| api (embedded worker, no models) | 89 MiB | healthy 29 s after start; 8 s after a restart |
| worker (separate container, idle) | 32 MiB | |
| frontend (nginx) | 5 MiB | |
| **core total (idle)** | **≈ 0.4 GB** | the estimates above apply once embedding/reranker/Docling models are resident (M4–M6) |

### 10.2 Totals and host recommendations
| Profiles | Estimated RAM | Host |
|----------|---------------|------|
| `core` | 3.5–6 GB | fits the 7.1 GB machine only with nothing else running; `WORKER_MODE=embedded` saves ~0.8 GB |
| `core + automation` | 4–6.5 GB | 8 GB minimum, 16 GB comfortable |
| `+ observability` | 6.5–10 GB | **16 GB recommended** |
| everything incl. `eval` | 7–11 GB | 16 GB |
| evaluation session (`core` minus frontend/n8n, embedded worker, `observability` with reduced ClickHouse/Valkey settings) | 5–6.5 GB | the 7.1 GB reference machine (decided Q-05) |

Disk: images ≈ 8–10 GB; Python environment ≈ 3.5 GB (measured for the resolved set with CPU torch); models ≈ 3–4 GB;
data volumes small. CPU: 4 cores suffice; ingestion and reranking are the hot spots. No GPU required or assumed.

### 10.3 External accounts (free tiers)
Google AI Studio API key; Google Cloud project with the Calendar API and an OAuth client (consent screen in Testing
status: ≤ 100 test users, 7-day refresh tokens); Groq API key.

## 11. Version pinning policy (ADR-0026)
`uv` with `pyproject.toml` + `uv.lock` (universal lock, Python `>=3.14,<3.15`, CPU torch index, explicit lower bounds
from §9); model revisions pinned in configuration; monthly upgrade PRs run the import/ML smoke test; CI runs
`pip-audit`/`npm audit`. Docker images pinned to exact tags (no `latest`).

## 12. Verification record (2026-11-04, reference machine)

| Check | Result |
|-------|--------|
| `uv lock` of the full set, Python 3.14 | resolved (276 packages after explicit bounds) |
| `uv lock` of the full set, Python 3.13 | resolved identically (277 packages, same versions) |
| Probe `transformers>=5` | resolves only by downgrading sentence-transformers to 0.3.4 → rejected |
| Probe `sentence-transformers>=6` with ONNX extra | **unsatisfiable** (optimum-onnx requires transformers < 4.58) |
| Probe `langchain-google-genai>=4.4` | resolves (4.4.3, google-genai 2.34.0) |
| Probe `presidio-analyzer>=2.2.366` | resolves (numpy 2.4.6) |
| `uv sync` of the final set on Python 3.14.4 | installed (3.5 GB venv); first attempt exposed the torchvision index rule (§9) |
| Import smoke test (fastapi, sqlalchemy async, psycopg, alembic, langgraph + Postgres saver, langchain-core/google-genai/groq, langfuse, torch, transformers, docling, spacy, presidio, lingua, chromadb, boto3, PyJWT, onnxruntime, optimum) | all import on 3.14 |
| `import ragas` inside the runtime environment | **fails** (langchain-community 0.4.x) → evaluation isolated (§9) |
| Isolated evaluation project on Python 3.14 (`ragas`, `langchain-community<0.4`, `langfuse`, `datasets`, `langchain-google-genai>=4.4`) | resolves (135 packages: ragas 0.4.3, langchain-community 0.3.31, langchain-core 1.7.5, datasets 5.1.0), installs, `import ragas` OK |
| lingua 2.2.0 language detection | Greek and English sentences detected correctly |
| Presidio 2.2.366 with `el_core_news_md` + `en_core_web_lg` on 3.14 | engine builds; Greek text → PERSON, EMAIL_ADDRESS, PHONE_NUMBER (+ LOCATION/URL); English text → PERSON, EMAIL_ADDRESS |
| Docling 2.147.0 `DocumentConverter()` on 3.14 | constructs (full conversion exercised at M4) |
| `uv lock` with `torchvision` as a direct dependency | torchvision resolved as `0.29.2+cpu` from the PyTorch CPU index (`source = registry download.pytorch.org/whl/cpu`); 277 packages |
| sentence-transformers 5.7.0 import on 3.14 (transformers 4.57.6, torch 2.15.0+cpu, torchvision 0.29.2+cpu) | OK |
| `intfloat/multilingual-e5-small` encode on CPU | 384-d normalised vectors; Greek query vs relevant English passage cos = 0.791, vs irrelevant passage 0.687 (cross-lingual retrieval works) |
| Reranker candidate B `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` on CPU | loads; Greek query scores relevant English passage 1.287 vs irrelevant −3.861 |
| `pgvector/pgvector:0.8.7-pg18` | PG 18.6, pgvector 0.8.7, `greek`/`english`/`simple` FTS configs present, Greek stemming works, RLS `SET LOCAL` isolation behaves as designed (`''` after commit → `NULLIF` needed) |
| `pgvector/pgvector:pg17` | PG 17.11, pgvector 0.8.7, same FTS configs (fallback image) |

## 13. Sources (verified 2026-11-04)
- PyPI JSON metadata and release files: langgraph, langgraph-checkpoint-postgres, langchain-core, langchain-google-genai, langchain-groq, docling, torch, transformers, sentence-transformers, spacy, presidio-analyzer, chromadb, ragas, langfuse, psycopg, sqlalchemy, fastapi, pydantic, onnxruntime, docling-parse, tokenizers, lingua-language-detector (`https://pypi.org/pypi/<name>/json`, `https://pypi.org/project/<name>/#files`)
- Resolver: uv 0.13.7 (`uv lock`, `uv tree --invert`, `uv sync`) on the reference machine
- GitHub releases: [Keycloak](https://github.com/keycloak/keycloak/releases/latest), [SeaweedFS](https://github.com/seaweedfs/seaweedfs/releases/latest), [n8n](https://github.com/n8n-io/n8n/releases/latest), [Mailpit](https://github.com/axllent/mailpit/releases/latest); Docker Hub tags for `pgvector/pgvector`
- Compatibility pages: [Keycloak supported databases](https://www.keycloak.org/server/db), [n8n supported databases](https://docs.n8n.io/hosting/configuration/supported-databases-settings/), [Langfuse self-hosting](https://langfuse.com/self-hosting), [Langfuse PostgreSQL](https://langfuse.com/self-hosting/infrastructure/postgres), [Langfuse docker-compose.yml](https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml), [Vite guide](https://vite.dev/guide/), [antd on npm](https://registry.npmjs.org/antd/latest)
- Licences: [n8n LICENSE.md](https://github.com/n8n-io/n8n/blob/master/LICENSE.md), [spaCy el_core_news_md](https://huggingface.co/spacy/el_core_news_md), [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small), [Jina reranker v2 (CC BY-NC)](https://jina.ai/models/jina-reranker-v2-base-multilingual/), [bge-reranker-v2-m3](https://www.azion.com/en/documentation/products/ai/ai-inference/models/baai-bge-reranker-v2-m3/)
- Providers: [Gemini rate limits](https://aistudio.google.com/docs/rate-limits), [Gemini pricing / data use](https://ai.google.dev/pricing), [Groq rate limits](https://console.groq.com/docs/rate-limits), [Groq tool use](https://console.groq.com/docs/tool-use); [Google OAuth testing-status token expiry](https://developers.google.com/health/setup)
- Snowball Greek stemmer: [algorithms](https://snowballstem.org/algorithms/); PostgreSQL `greek` configuration verified empirically (pg17 and pg18 images)
