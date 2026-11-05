"""Composition root helpers: build the real adapters and the application services (doc 03 §4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.container import (
    DocumentAdapters,
    DocumentOptions,
    Services,
    TokenVerifierPort,
)
from leave_assistant.application.ports import IdentityAdmin
from leave_assistant.config import Settings
from leave_assistant.infrastructure.db.session import create_engine, make_session_factory
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.infrastructure.identity.jwks import JwksCache, KeyResolver
from leave_assistant.infrastructure.identity.keycloak_admin import KeycloakAdminClient
from leave_assistant.infrastructure.identity.tokens import TokenVerifier
from leave_assistant.infrastructure.ml.embedder import E5Embedder
from leave_assistant.infrastructure.ml.language import LinguaLanguageDetector
from leave_assistant.infrastructure.ml.tokenizer import E5Tokenizer
from leave_assistant.infrastructure.parsing.docling_parser import DoclingParser
from leave_assistant.infrastructure.privacy.presidio_engine import PresidioEngine
from leave_assistant.infrastructure.privacy.vault_crypto import VaultCrypto, decode_key
from leave_assistant.infrastructure.security.llm_judge import UnavailableLlmJudge
from leave_assistant.infrastructure.security.similarity_classifier import SimilarityClassifier
from leave_assistant.infrastructure.storage.crypto import EnvelopeCipher
from leave_assistant.infrastructure.storage.s3 import S3DocumentStorage, make_s3_client
from leave_assistant.infrastructure.system import SystemClock, Uuid7IdGenerator

Role = Literal["api", "worker"]


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


def document_options(settings: Settings) -> DocumentOptions:
    return DocumentOptions(
        max_upload_mb=settings.max_upload_mb,
        max_pages=settings.max_document_pages,
        chunking_rules_version=settings.chunking_rules_version,
        job_max_attempts=settings.job_max_attempts,
        job_stale_after_seconds=settings.job_stale_after_seconds,
        retrieval_leg_size=settings.retrieval_leg_size,
        retrieval_k=settings.retrieval_k,
        pii_score_threshold=settings.pii_score_threshold,
        supporting_max_upload_mb=settings.supporting_max_upload_mb,
        download_token_ttl_seconds=settings.download_token_ttl_seconds,
    )


def _secret(value: Any) -> str | None:
    return value.get_secret_value() if value is not None else None


def build_vault_cipher(settings: Settings) -> VaultCrypto:
    return VaultCrypto(
        decode_key(_secret(settings.vault_hmac_key), name="VAULT_HMAC_KEY"),
        decode_key(_secret(settings.vault_kek), name="VAULT_KEK"),
    )


def build_file_cipher(settings: Settings) -> EnvelopeCipher:
    return EnvelopeCipher(decode_key(_secret(settings.app_kek), name="APP_KEK"))


def build_document_adapters(
    settings: Settings, *, role: Role, s3_client: Any | None = None
) -> DocumentAdapters:
    """Real adapters; models load lazily on first use (the API only ever embeds queries and screens input)."""
    client = s3_client or make_s3_client(settings, timeout=60.0)
    threads = settings.worker_torch_threads if role == "worker" else settings.torch_threads
    embedder = E5Embedder(
        settings.embedding_model,
        settings.embedding_model_revision,
        batch_size=settings.embedding_batch_size,
        threads=threads,
    )
    return DocumentAdapters(
        storage=S3DocumentStorage(client, settings.s3_bucket_documents),
        parser=DoclingParser(
            ocr_languages=tuple(settings.ocr_language_list),
            artifacts_path=settings.docling_artifacts_dir,
            document_timeout_seconds=settings.document_process_timeout_seconds,
            num_threads=settings.worker_torch_threads,
        ),
        tokenizer=E5Tokenizer(settings.embedding_model, settings.embedding_model_revision),
        embedder=embedder,
        language=LinguaLanguageDetector(),
        pii_engine=PresidioEngine(threshold=settings.pii_score_threshold),
        vault_cipher=build_vault_cipher(settings),
        file_cipher=build_file_cipher(settings),
        classifier=SimilarityClassifier(embedder),
        judge=UnavailableLlmJudge(),
    )


def build_runtime(
    settings: Settings,
    *,
    role: Role = "api",
    engine: AsyncEngine | None = None,
    identity: IdentityAdmin | None = None,
    key_resolver: KeyResolver | None = None,
    verifier: TokenVerifierPort | None = None,
    adapters: DocumentAdapters | None = None,
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
    s3_client = make_s3_client(settings, timeout=60.0)
    services = Services.build(
        uow_factory=uow_factory,
        identity=identity,
        verifier=verifier,
        clock=SystemClock(),
        ids=Uuid7IdGenerator(),
        adapters=adapters or build_document_adapters(settings, role=role, s3_client=s3_client),
        options=document_options(settings),
    )
    return Runtime(
        settings=settings, engine=engine, services=services, admin_client=admin_client, s3_client=s3_client
    )
