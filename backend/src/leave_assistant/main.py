"""FastAPI application factory and composition root (doc 03 §4)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from leave_assistant import __version__
from leave_assistant.api.errors import register_error_handlers
from leave_assistant.api.middleware import RequestIdMiddleware
from leave_assistant.api.routers import (
    approvals,
    documents,
    health,
    leave,
    me,
    notifications,
    supporting,
    team,
)
from leave_assistant.api.routers.admin import audit as admin_audit
from leave_assistant.api.routers.admin import departments as admin_departments
from leave_assistant.api.routers.admin import employees as admin_employees
from leave_assistant.api.routers.admin import jobs as admin_jobs
from leave_assistant.api.routers.admin import leave_catalog as admin_leave_catalog
from leave_assistant.api.routers.admin import ledger as admin_ledger
from leave_assistant.api.routers.admin import retention as admin_retention
from leave_assistant.api.routers.admin import security as admin_security
from leave_assistant.api.routers.platform import audit as platform_audit
from leave_assistant.api.routers.platform import security as platform_security
from leave_assistant.api.routers.platform import tenants as platform_tenants
from leave_assistant.application.container import Services
from leave_assistant.application.health import ReadinessCheck
from leave_assistant.config import Settings, get_settings
from leave_assistant.infrastructure.db.migrations import expected_head
from leave_assistant.infrastructure.health_checks import default_checks
from leave_assistant.infrastructure.observability.logging import configure_logging, get_logger
from leave_assistant.infrastructure.runtime import Runtime, build_runtime
from leave_assistant.infrastructure.storage.s3 import ensure_bucket, make_s3_client
from leave_assistant.worker.handlers import build_registry
from leave_assistant.worker.loop import run_worker_loop
from leave_assistant.worker.periodic import build_periodic
from leave_assistant.worker.runner import JobRunner

log = get_logger("api")

ROUTERS = (
    health.router,
    me.router,
    platform_tenants.router,
    platform_audit.router,
    admin_employees.router,
    admin_departments.router,
    admin_audit.router,
    leave.router,
    approvals.router,
    team.router,
    notifications.router,
    admin_leave_catalog.router,
    admin_ledger.router,
    documents.router,
    admin_jobs.router,
    supporting.router,
    admin_retention.router,
    admin_security.router,
    platform_security.router,
)


def create_app(
    settings: Settings | None = None,
    *,
    services: Services | None = None,
    readiness_checks: Sequence[ReadinessCheck] | None = None,
    bootstrap_infrastructure: bool = True,
) -> FastAPI:
    """`bootstrap_infrastructure=False` + `services=` lets tests run the HTTP layer with in-memory adapters."""
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        stop = asyncio.Event()
        worker_task: asyncio.Task[int] | None = None
        runtime: Runtime | None = None
        if bootstrap_infrastructure:
            runtime = build_runtime(settings, role="api")
            s3 = make_s3_client(settings, timeout=settings.readiness_timeout_seconds)
            try:
                created = await asyncio.to_thread(ensure_bucket, s3, settings.s3_bucket_documents)
                log.info("storage.bucket", bucket=settings.s3_bucket_documents, created=created)
            except Exception as exc:  # noqa: BLE001 - readiness reports it; startup must not crash-loop
                log.warning("storage.bucket.unavailable", error=f"{type(exc).__name__}: {exc}")
            app.state.services = runtime.services
            app.state.readiness_checks = list(
                readiness_checks or default_checks(settings, runtime.engine, s3, expected_head())
            )
            if settings.worker_mode == "embedded":
                runner = JobRunner(
                    runtime.services.jobs,
                    build_registry(runtime.services),
                    worker_id="api-embedded",
                    timeout_seconds=settings.document_process_timeout_seconds,
                    heartbeat_seconds=settings.job_heartbeat_seconds,
                )
                periodic = build_periodic(runtime.services, settings)
                worker_task = asyncio.create_task(
                    run_worker_loop(
                        settings,
                        stop,
                        periodic=periodic,
                        periodic_interval_seconds=periodic.interval,
                        runner=runner,
                    )
                )
        else:
            app.state.services = services
            app.state.readiness_checks = list(readiness_checks or [])
        log.info("api.start", version=__version__, app_env=settings.app_env, worker_mode=settings.worker_mode)
        try:
            yield
        finally:
            stop.set()
            if worker_task is not None:
                await worker_task
            if runtime is not None:
                await runtime.aclose()

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
    for router in ROUTERS:
        app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
