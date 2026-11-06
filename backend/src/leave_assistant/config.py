"""Application settings (environment-driven; see deploy/.env.example and doc 03 §7)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
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
    rollover_interval_seconds: float = 900.0  # leave status rollover + ledger calendar (M2 plan decision 4)
    readiness_timeout_seconds: float = (
        5.0  # per dependency check; Keycloak's first response after start can be slow
    )

    # Documents, ingestion and retrieval — M4 (doc 05, ADR-0013/0015/0016/0019)
    max_upload_mb: int = 25
    max_document_pages: int = 500
    document_process_timeout_seconds: float = 900.0  # wall clock per job in the worker's child process
    job_max_attempts: int = 3
    job_heartbeat_seconds: float = 30.0
    job_stale_after_seconds: float = 1800.0  # a running job without heartbeat for this long is reclaimed
    worker_max_jobs_per_iteration: int = 10
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_model_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"  # pinned (ADR-0013)
    embedding_batch_size: int = 32
    docling_artifacts_path: str | None = None  # default: $HF_HOME/docling when HF_HOME is set
    ocr_languages: str = "ell,eng"
    torch_threads: int = 2  # the API embeds queries only
    worker_torch_threads: int = 4
    chunking_rules_version: str = "v1"
    retrieval_k: int = 10
    retrieval_leg_size: int = 30

    # Content security and privacy — M5 (doc 08 §7–§10, ADR-0017/0018). Keys: base64, 32 bytes, from the environment
    # only (`make env` generates them); without them vault writes and supporting-document uploads fail closed.
    app_kek: SecretStr | None = None  # wraps per-file DEKs of supporting documents (AES key wrap)
    vault_hmac_key: SecretStr | None = None  # HMAC-SHA256 lookups in the PII vault
    vault_kek: SecretStr | None = None  # AES-256-GCM of vault originals
    pii_score_threshold: float = 0.4
    supporting_max_upload_mb: int = 10
    download_token_ttl_seconds: int = 60
    retention_scan_interval_seconds: float = 86400.0
    injection_exemplars_version: str = "v1"

    # Agent core — M6 (doc 04, ADR-0007/0008/0010/0014). Model IDs are configuration; keys never leave deploy/.env.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    mistral_api_key: SecretStr | None = None
    gemini_model_fast: str = "gemini-3.5-flash-lite"
    gemini_model: str = "gemini-3.6-flash"
    groq_model_fast: str = "llama-3.1-8b-instant"
    groq_model: str = "llama-3.3-70b-versatile"
    # free "Experiment" plan: mistral-small/-medium are at 0 requests/min, mistral-large-4 always reasons (worklog M6)
    mistral_model_fast: str = "ministral-8b-2512"
    mistral_model: str = "ministral-14b-2512"
    llm_timeout_seconds: float = 25.0
    gemini_timeout_seconds: float | None = (
        None  # smoke/failover drills only: a tiny value forces a transient error
    )
    # Gemini 3 models think before answering and the thoughts count against the output cap: bounded level, and an
    # allowance on top of the answer budget so thinking cannot truncate the JSON (worklog M6)
    gemini_thinking_level: Literal["minimal", "low", "medium", "high"] | None = "low"
    gemini_thinking_tokens: int = 1024
    llm_max_retries: int = 2
    llm_circuit_open_seconds: float = 60.0
    llm_budget_total: int = 3500
    llm_budget_history: int = 800
    llm_budget_context: int = 1800
    rerank_candidates: int = (
        10  # doc 05 §6.3 allows ≤ 20; 10 keeps NFR-PERF-01 reachable on a 2-core CPU (worklog M6)
    )
    rerank_max_length: int = 256  # tokens per (query, passage) pair; chunks put headings and key facts first
    retrieval_tau: float = 0.5  # candidate B, calibrated on S01–S09 + off-topic probes (worklog M6)
    min_evidence_chunks: int = 1
    conversation_retention_days: int = 90
    conversation_cleanup_interval_seconds: float = 86400.0
    proposal_expiry_interval_seconds: float = (
        300.0  # M7: pending proposals past their TTL and their interrupts
    )
    # Integrations — M8 (doc 06, doc 10, ADR-0019/0020/0021)
    public_base_url: str = "http://localhost:8080"  # the SPA and the proxied API as the browser sees them
    oauth_redirect_base_url: str | None = (
        None  # default: public_base_url (Google needs it registered verbatim)
    )
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    mock_calendar_fail_mode: Literal["none", "transient_once", "auth_expired", "permanent"] = "none"
    mail_from_domain: str = "leave.local"  # notification e-mails: no-reply@{tenant-slug}.{domain}
    platform_alert_email: str = (
        "platform-alerts@leave.local"  # W7 digest (platform admins have no inbox rows)
    )
    outbox_lease_seconds: float = 300.0
    outbox_max_attempts: int = 10
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "http://langfuse-web:3000"

    # Feature flags — doc 03 §7
    retrieval_backend: Literal["pgvector", "chroma"] = "pgvector"
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    injection_llm_judge: bool = False
    calendar_provider: Literal["google", "mock"] = "mock"
    langfuse_enabled: bool = False
    llm_provider_order: str = "gemini,mistral,groq"

    @property
    def security_keys_configured(self) -> bool:
        return all(
            k is not None and k.get_secret_value().strip()
            for k in (self.app_kek, self.vault_hmac_key, self.vault_kek)
        )

    @property
    def oauth_redirect_uri(self) -> str:
        base = (self.oauth_redirect_base_url or self.public_base_url).rstrip("/")
        return f"{base}{self.api_prefix}/calendar/connections/google/callback"

    @property
    def google_configured(self) -> bool:
        secret = self.google_oauth_client_secret
        return bool(self.google_oauth_client_id and secret and secret.get_secret_value().strip())

    @property
    def llm_providers(self) -> list[str]:
        return [p.strip() for p in self.llm_provider_order.split(",") if p.strip()]

    @property
    def ocr_language_list(self) -> list[str]:
        return [lang.strip() for lang in self.ocr_languages.split(",") if lang.strip()]

    @property
    def docling_artifacts_dir(self) -> str | None:
        if self.docling_artifacts_path:
            return self.docling_artifacts_path
        hf_home = os.environ.get("HF_HOME")
        return str(Path(hf_home) / "docling") if hf_home else None

    @property
    def keycloak_issuer(self) -> str:
        return f"{self.keycloak_public_url.rstrip('/')}/realms/{self.keycloak_realm}"

    @property
    def keycloak_jwks_url(self) -> str:
        return f"{self.keycloak_url.rstrip('/')}/realms/{self.keycloak_realm}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
