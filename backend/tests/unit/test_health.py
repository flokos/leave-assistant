from __future__ import annotations

from httpx import AsyncClient

from leave_assistant import __version__


async def test_healthz_reports_version_and_env(client: AsyncClient) -> None:
    response = await client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "version": __version__, "app_env": "test"}
    assert response.headers["X-Request-Id"]


async def test_request_id_is_echoed_when_provided(client: AsyncClient) -> None:
    response = await client.get("/api/v1/healthz", headers={"X-Request-Id": "abc-123"})
    assert response.headers["X-Request-Id"] == "abc-123"


async def test_readyz_is_200_when_all_checks_pass(client: AsyncClient) -> None:
    response = await client.get("/api/v1/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert {c["name"] for c in body["checks"]} == {"postgres", "s3"}


async def test_readyz_is_503_when_a_check_fails(degraded_client: AsyncClient) -> None:
    response = await degraded_client.get("/api/v1/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    failed = [c for c in body["checks"] if not c["ok"]]
    assert failed == [{"name": "keycloak", "ok": False, "detail": "HTTP 503"}]


async def test_unknown_route_is_a_problem_document(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "urn:leave-assistant:problem:not-found"
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/does-not-exist"
    assert body["request_id"]


async def test_openapi_is_served_under_the_api_prefix(client: AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/readyz" in response.json()["paths"]
