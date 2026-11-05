"""FR-AUTH-02: signature, issuer, audience, expiry, algorithm allowlist; JWKS cache behaviour."""

from __future__ import annotations

import time
from uuid import uuid4

import httpx
import jwt
import pytest
import respx

from leave_assistant.application.errors import AuthenticationError, DependencyError
from leave_assistant.infrastructure.identity.jwks import JwksCache
from leave_assistant.infrastructure.identity.tokens import TokenVerifier
from tests.fakes.tokens import AUDIENCE, ISSUER, TestKeys, claims_for


@pytest.fixture
def verifier(test_keys: TestKeys) -> TokenVerifier:
    return TokenVerifier(test_keys.resolver(), issuer=ISSUER, audience=AUDIENCE, leeway_seconds=30)


async def test_valid_token_yields_claims(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    tenant = uuid4()
    token = test_keys.sign(
        claims_for(sub="u1", roles=["employee", "offline_access"], tenant_id=tenant, username="u1@x")
    )
    claims = await verifier.verify(token)
    assert claims.sub == "u1"
    assert claims.username == "u1@x"
    assert claims.roles == {"employee", "offline_access"}
    assert claims.tenant_ids == (str(tenant),)


async def test_string_tenant_claim_is_accepted(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    claims = claims_for(sub="u1", roles=["employee"])
    claims["tenant_id"] = "abc"
    assert (await verifier.verify(test_keys.sign(claims))).tenant_ids == ("abc",)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"lifetime": -120}, "token expired"),
        ({"audience": "other-api"}, "invalid audience"),
        ({"issuer": "http://evil.example/realms/leave-assistant"}, "invalid issuer"),
    ],
)
async def test_rejected_claims(
    verifier: TokenVerifier, test_keys: TestKeys, overrides: dict, message: str
) -> None:
    token = test_keys.sign(claims_for(sub="u1", roles=["employee"], **overrides))
    with pytest.raises(AuthenticationError, match=message):
        await verifier.verify(token)


async def test_expiry_within_leeway_is_accepted(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    token = test_keys.sign(claims_for(sub="u1", roles=["employee"], lifetime=-10))
    assert (await verifier.verify(token)).sub == "u1"


async def test_alg_none_is_rejected(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    token = jwt.encode(
        claims_for(sub="u1", roles=["employee"]), key=None, algorithm="none", headers={"kid": test_keys.kid}
    )
    with pytest.raises(AuthenticationError, match="unsupported token algorithm"):
        await verifier.verify(token)


async def test_hmac_token_is_rejected(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    token = jwt.encode(
        claims_for(sub="u1", roles=["employee"]),
        key="not-a-secret",
        algorithm="HS256",
        headers={"kid": test_keys.kid},
    )
    with pytest.raises(AuthenticationError, match="unsupported token algorithm"):
        await verifier.verify(token)


async def test_forged_signature_is_rejected(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    other = TestKeys(kid=test_keys.kid)  # same kid, different private key
    with pytest.raises(AuthenticationError, match="invalid signature"):
        await verifier.verify(other.sign(claims_for(sub="u1", roles=["employee"])))


async def test_unknown_kid_is_rejected(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    with pytest.raises(AuthenticationError, match="unknown signing key"):
        await verifier.verify(test_keys.sign(claims_for(sub="u1", roles=["employee"]), kid="rotated"))


async def test_missing_kid_is_rejected(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    token = jwt.encode(claims_for(sub="u1", roles=["employee"]), key=test_keys.private, algorithm="RS256")
    with pytest.raises(AuthenticationError, match="no key id"):
        await verifier.verify(token)


async def test_malformed_token_is_rejected(verifier: TokenVerifier) -> None:
    with pytest.raises(AuthenticationError, match="malformed token"):
        await verifier.verify("not.a.token")


async def test_missing_required_claims_rejected(verifier: TokenVerifier, test_keys: TestKeys) -> None:
    claims = claims_for(sub="u1", roles=["employee"])
    del claims["iat"]
    with pytest.raises(AuthenticationError, match="invalid token"):
        await verifier.verify(test_keys.sign(claims))


@pytest.mark.respx(assert_all_called=False)
async def test_jwks_cache_refreshes_on_unknown_kid_with_rate_limit(
    test_keys: TestKeys, respx_mock: respx.MockRouter
) -> None:
    url = "http://keycloak:8080/realms/leave-assistant/protocol/openid-connect/certs"
    now = [1000.0]
    route = respx_mock.get(url).mock(return_value=httpx.Response(200, json=test_keys.jwks()))
    cache = JwksCache(url, ttl_seconds=600, min_refresh_interval=10, monotonic=lambda: now[0])

    assert (await cache.get_key(test_keys.kid)).key_id == test_keys.kid
    assert route.call_count == 1
    with pytest.raises(AuthenticationError):  # unknown kid right after a fetch: no refetch (rate limit)
        await cache.get_key("rotated")
    assert route.call_count == 1

    rotated = TestKeys(kid="rotated")
    route.mock(return_value=httpx.Response(200, json={"keys": [test_keys.jwk, rotated.jwk]}))
    now[0] += 11
    assert (await cache.get_key("rotated")).key_id == "rotated"
    assert route.call_count == 2

    now[0] += 601  # TTL elapsed: next lookup refreshes even for a known kid
    await cache.get_key(test_keys.kid)
    assert route.call_count == 3


@pytest.mark.respx(assert_all_called=False)
async def test_jwks_cache_keeps_keys_when_idp_is_down(
    test_keys: TestKeys, respx_mock: respx.MockRouter
) -> None:
    url = "http://keycloak:8080/realms/leave-assistant/protocol/openid-connect/certs"
    now = [1000.0]
    route = respx_mock.get(url).mock(return_value=httpx.Response(200, json=test_keys.jwks()))
    cache = JwksCache(url, ttl_seconds=600, monotonic=lambda: now[0])
    await cache.get_key(test_keys.kid)
    route.mock(side_effect=httpx.ConnectError("down"))
    now[0] += 601
    assert (await cache.get_key(test_keys.kid)).key_id == test_keys.kid  # stale keys still served


@pytest.mark.respx(assert_all_called=False)
async def test_jwks_cache_without_keys_and_idp_down_is_a_dependency_error(
    respx_mock: respx.MockRouter,
) -> None:
    url = "http://keycloak:8080/realms/leave-assistant/protocol/openid-connect/certs"
    respx_mock.get(url).mock(side_effect=httpx.ConnectError("down"))
    cache = JwksCache(url)
    with pytest.raises(DependencyError):
        await cache.get_key("any")


def test_claims_for_uses_current_time() -> None:
    claims = claims_for(sub="u", roles=[])
    assert abs(claims["iat"] - int(time.time())) < 5
