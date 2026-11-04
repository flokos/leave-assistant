"""Liveness and readiness endpoints (doc 09 §2.1)."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from leave_assistant import __version__
from leave_assistant.application.health import ReadinessCheck, run_readiness

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {"status": "ok", "version": __version__, "app_env": settings.app_env}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks: list[ReadinessCheck] = request.app.state.readiness_checks
    readiness = await run_readiness(checks)
    body = {
        "status": "ready" if readiness.ok else "not_ready",
        "version": __version__,
        "checks": [asdict(result) for result in readiness.checks],
    }
    return JSONResponse(body, status_code=200 if readiness.ok else 503)
