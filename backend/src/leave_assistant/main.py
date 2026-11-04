"""FastAPI application factory and composition root (doc 03 §4)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from leave_assistant import __version__
from leave_assistant.api.errors import register_error_handlers
from leave_assistant.api.middleware import RequestIdMiddleware
from leave_assistant.api.routers import health
from leave_assistant.application.health import ReadinessCheck
from leave_assistant.config import Settings, get_settings
from leave_assistant.infrastructure.db.session import create_engine
from leave_assistant.infrastructure.health_checks import default_checks
from leave_assistant.infrastructure.observability.logging import configure_logging, get_logger
from leave_assistant.infrastructure.storage.s3 import ensure_bucket, make_s3_client
from leave_assistant.worker.loop import run_worker_loop

log = get_logger("api")


def create_app(
    settings: Settings | None = None,
    *,
    readiness_checks: Sequence[ReadinessCheck] | None = None,
    bootstrap_infrastructure: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        stop = asyncio.Event()
        worker_task: asyncio.Task[int] | None = None
        engine = None
        if bootstrap_infrastructure:
            engine = create_engine(settings)
            s3 = make_s3_client(settings, timeout=settings.readiness_timeout_seconds)
            try:
                created = await asyncio.to_thread(ensure_bucket, s3, settings.s3_bucket_documents)
                log.info("storage.bucket", bucket=settings.s3_bucket_documents, created=created)
            except Exception as exc:  # noqa: BLE001 - readiness reports it; startup must not crash-loop
                log.warning("storage.bucket.unavailable", error=f"{type(exc).__name__}: {exc}")
            app.state.readiness_checks = list(readiness_checks or default_checks(settings, engine, s3))
            if settings.worker_mode == "embedded":
                worker_task = asyncio.create_task(run_worker_loop(settings, stop))
        else:
            app.state.readiness_checks = list(readiness_checks or [])
        log.info("api.start", version=__version__, app_env=settings.app_env, worker_mode=settings.worker_mode)
        try:
            yield
        finally:
            stop.set()
            if worker_task is not None:
                await worker_task
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="Leave Management AI Assistant API",
        version=__version__,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router, prefix=settings.api_prefix)
    return app


app = create_app()
