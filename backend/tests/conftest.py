from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from leave_assistant.application.health import CheckResult
from leave_assistant.config import Settings
from leave_assistant.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test", worker_poll_interval_seconds=0.01, worker_heartbeat_path="/tmp/la-test.heartbeat"
    )  # noqa: S108


def ok_check(name: str):
    async def check() -> CheckResult:
        return CheckResult(name, True)

    return check


def failing_check(name: str, detail: str = "boom"):
    async def check() -> CheckResult:
        return CheckResult(name, False, detail)

    return check


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings, readiness_checks=[ok_check("postgres"), ok_check("s3")], bootstrap_infrastructure=False
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http


@pytest.fixture
async def degraded_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings,
        readiness_checks=[ok_check("postgres"), failing_check("keycloak", "HTTP 503")],
        bootstrap_infrastructure=False,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
