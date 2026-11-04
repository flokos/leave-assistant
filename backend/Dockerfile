# syntax=docker/dockerfile:1.7
# Backend image: FastAPI api + worker (same image, different command). Python 3.14 per ADR-0026.
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH

# Tesseract (Greek + English) for Docling OCR, libgomp for torch, glib for opencv-headless.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ell tesseract-ocr-eng libgomp1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Dependencies first (cached layer); the project itself is installed in the next step.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "leave_assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
