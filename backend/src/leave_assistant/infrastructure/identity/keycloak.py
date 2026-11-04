"""Keycloak endpoints. JWT validation and the Admin API client arrive in M1 (ADR-0004)."""

from __future__ import annotations

from leave_assistant.config import Settings


def discovery_url(settings: Settings, realm: str | None = None) -> str:
    realm_name = realm or settings.keycloak_realm
    return f"{settings.keycloak_url.rstrip('/')}/realms/{realm_name}/.well-known/openid-configuration"
