"""Composition root helpers: build the real adapters and the application services (doc 03 §4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.container import Services, TokenVerifierPort
from leave_assistant.application.ports import IdentityAdmin
from leave_assistant.config import Settings
from leave_assistant.infrastructure.db.session import create_engine, make_session_factory
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.infrastructure.identity.jwks import JwksCache, KeyResolver
from leave_assistant.infrastructure.identity.keycloak_admin import KeycloakAdminClient
from leave_assistant.infrastructure.identity.tokens import TokenVerifier
from leave_assistant.infrastructure.system import SystemClock, Uuid7IdGenerator


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: AsyncEngine
    services: Services
    admin_client: KeycloakAdminClient | None = None
    s3_client: Any | None = None

    async def aclose(self) -> None:
        if self.admin_client is not None:
            await self.admin_client.aclose()
        await self.engine.dispose()


def build_runtime(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
    identity: IdentityAdmin | None = None,
    key_resolver: KeyResolver | None = None,
    verifier: TokenVerifierPort | None = None,
) -> Runtime:
    engine = engine or create_engine(settings)
    uow_factory = SqlAlchemyUnitOfWorkFactory(make_session_factory(engine))
    admin_client: KeycloakAdminClient | None = None
    if identity is None:
        admin_client = KeycloakAdminClient(settings)
        identity = admin_client
    if verifier is None:
        keys = key_resolver or JwksCache(
            settings.keycloak_jwks_url,
            ttl_seconds=settings.jwks_ttl_seconds,
            timeout=settings.readiness_timeout_seconds,
        )
        verifier = TokenVerifier(
            keys,
            issuer=settings.keycloak_issuer,
            audience=settings.keycloak_audience,
            leeway_seconds=settings.jwt_leeway_seconds,
        )
    services = Services.build(
        uow_factory=uow_factory,
        identity=identity,
        verifier=verifier,
        clock=SystemClock(),
        ids=Uuid7IdGenerator(),
    )
    return Runtime(settings=settings, engine=engine, services=services, admin_client=admin_client)
