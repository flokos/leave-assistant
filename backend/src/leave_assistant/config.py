"""Application settings (environment-driven; see deploy/.env.example and doc 03 §7)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # PostgreSQL — runtime role is RLS-bound (ADR-0003); owner/langgraph URLs are used from M1/M6
    database_url: str = "postgresql+psycopg://app_rls:app-rls-dev@localhost:5432/leave"
    database_owner_url: str | None = None
    langgraph_database_url: str | None = None

    # Object storage (SeaweedFS S3) — ADR-0016
    s3_endpoint_url: str = "http://localhost:8333"
    s3_access_key: str = "leave"
    s3_secret_key: SecretStr = SecretStr("leave-secret-dev")
    s3_region: str = "us-east-1"
    s3_bucket_documents: str = "leave-documents"

    # Identity (Keycloak) — ADR-0004. `keycloak_url` is how the backend reaches Keycloak (JWKS, Admin API);
    # `keycloak_public_url` is what the browser uses and therefore the issuer written into tokens.
    keycloak_url: str = "http://localhost:8081"
    keycloak_public_url: str = "http://localhost:8081"
    keycloak_realm: str = "leave-assistant"
    keycloak_audience: str = "backend-api"
    keycloak_admin_client_id: str = "backend-admin"
    keycloak_admin_client_secret: SecretStr = SecretStr("backend-admin-dev")
    jwt_leeway_seconds: int = 30
    jwks_ttl_seconds: int = 600
    idempotency_ttl_hours: int = 24
    seed_user_password: SecretStr = SecretStr("leave-dev")  # seed only; never used by the API

    # Worker — ADR-0001 / ADR-0019
    worker_mode: Literal["separate", "embedded"] = "separate"
    worker_poll_interval_seconds: float = 5.0
    worker_heartbeat_path: str = "/tmp/leave-assistant-worker.heartbeat"  # noqa: S108 - container-local file
    readiness_timeout_seconds: float = (
        5.0  # per dependency check; Keycloak's first response after start can be slow
    )

    # Feature flags — doc 03 §7
    retrieval_backend: Literal["pgvector", "chroma"] = "pgvector"
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    injection_llm_judge: bool = False
    calendar_provider: Literal["google", "mock"] = "mock"
    langfuse_enabled: bool = False
    llm_provider_order: str = "gemini,groq"

    @property
    def llm_providers(self) -> list[str]:
        return [p.strip() for p in self.llm_provider_order.split(",") if p.strip()]

    @property
    def keycloak_issuer(self) -> str:
        return f"{self.keycloak_public_url.rstrip('/')}/realms/{self.keycloak_realm}"

    @property
    def keycloak_jwks_url(self) -> str:
        return f"{self.keycloak_url.rstrip('/')}/realms/{self.keycloak_realm}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
