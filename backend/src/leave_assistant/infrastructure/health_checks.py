"""Readiness checks for the infrastructure the core profile depends on (doc 03 §6, M0)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.health import CheckResult, ReadinessCheck
from leave_assistant.config import Settings
from leave_assistant.infrastructure.db.session import ping
from leave_assistant.infrastructure.identity.keycloak import discovery_url
from leave_assistant.infrastructure.storage.s3 import bucket_exists


def _failure(name: str, exc: BaseException) -> CheckResult:
    return CheckResult(name, False, f"{type(exc).__name__}: {str(exc)[:200]}")


def postgres_check(engine: AsyncEngine, timeout: float) -> ReadinessCheck:
    async def check() -> CheckResult:
        try:
            await asyncio.wait_for(ping(engine), timeout=timeout)
            return CheckResult("postgres", True)
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            return _failure("postgres", exc)

    return check


def s3_check(client: Any, bucket: str, timeout: float) -> ReadinessCheck:
    async def check() -> CheckResult:
        try:
            exists = await asyncio.wait_for(asyncio.to_thread(bucket_exists, client, bucket), timeout=timeout)
            return CheckResult("s3", exists, "" if exists else f"bucket {bucket!r} missing")
        except Exception as exc:  # noqa: BLE001
            return _failure("s3", exc)

    return check


def keycloak_check(settings: Settings, timeout: float) -> ReadinessCheck:
    url = discovery_url(settings, realm="master")

    async def check() -> CheckResult:
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                response = await http.get(url)
            ok = response.status_code == 200 and "issuer" in response.json()
            return CheckResult("keycloak", ok, "" if ok else f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _failure("keycloak", exc)

    return check


def default_checks(settings: Settings, engine: AsyncEngine, s3_client: Any) -> list[ReadinessCheck]:
    timeout = settings.readiness_timeout_seconds
    return [
        postgres_check(engine, timeout),
        s3_check(s3_client, settings.s3_bucket_documents, timeout),
        keycloak_check(settings, timeout),
    ]
