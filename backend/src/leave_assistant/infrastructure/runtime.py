"""Composition root helpers: build the real adapters and the application services (doc 03 §4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.container import (
    DocumentAdapters,
    DocumentOptions,
    Services,
    TokenVerifierPort,
)
from leave_assistant.application.llm.health import HealthTracker
from leave_assistant.application.llm.ports import ChatModel
from leave_assistant.application.llm.router import ProviderEvent, ProviderRouter
from leave_assistant.application.ports import IdentityAdmin
from leave_assistant.application.retrieval.context import EvidencePolicy
from leave_assistant.config import Settings
from leave_assistant.domain.agent.budget import TokenBudgets
from leave_assistant.infrastructure.db.session import create_engine, make_session_factory
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.infrastructure.identity.jwks import JwksCache, KeyResolver
from leave_assistant.infrastructure.identity.keycloak_admin import KeycloakAdminClient
from leave_assistant.infrastructure.identity.tokens import TokenVerifier
from leave_assistant.infrastructure.llm.fake import FakeChatModel
from leave_assistant.infrastructure.llm.gemini import GeminiChatModel
from leave_assistant.infrastructure.llm.groq import GroqChatModel
from leave_assistant.infrastructure.ml.embedder import E5Embedder
from leave_assistant.infrastructure.ml.language import LinguaLanguageDetector
from leave_assistant.infrastructure.ml.reranker import CrossEncoderReranker
from leave_assistant.infrastructure.ml.tokenizer import E5Tokenizer
from leave_assistant.infrastructure.observability.logging import get_logger
from leave_assistant.infrastructure.parsing.docling_parser import DoclingParser
from leave_assistant.infrastructure.privacy.presidio_engine import PresidioEngine
from leave_assistant.infrastructure.privacy.vault_crypto import VaultCrypto, decode_key
from leave_assistant.infrastructure.security.llm_judge import UnavailableLlmJudge
from leave_assistant.infrastructure.security.similarity_classifier import SimilarityClassifier
from leave_assistant.infrastructure.storage.crypto import EnvelopeCipher
from leave_assistant.infrastructure.storage.s3 import S3DocumentStorage, make_s3_client
from leave_assistant.infrastructure.system import SystemClock, Uuid7IdGenerator

Role = Literal["api", "worker"]
log = get_logger("runtime")


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: AsyncEngine
    services: Services
    admin_client: KeycloakAdminClient | None = None
    s3_client: Any | None = None

    checkpoint_pool: Any | None = None

    async def start_agent(self) -> bool:
        """Attach the LangGraph agent (M6): providers from `LLM_PROVIDER_ORDER` with configured keys, the Postgres
        checkpointer pool, the router and the knowledge dependencies. Without providers chat answers 503."""
        settings = self.settings
        providers = build_providers(settings)
        if not providers:
            log.warning(
                "agent.disabled", reason="no LLM provider configured", order=settings.llm_provider_order
            )
            return False
        from leave_assistant.agent.checkpoint import open_saver
        from leave_assistant.agent.graph import AgentDeps
        from leave_assistant.agent.runner import LangGraphAgent
        from leave_assistant.agent.tracing import Tracer

        saver, self.checkpoint_pool = await open_saver(settings)
        services = self.services
        budgets = TokenBudgets(
            total=settings.llm_budget_total,
            history=settings.llm_budget_history,
            context=settings.llm_budget_context,
        )
        services.chat.budgets = budgets

        async def log_event(event: ProviderEvent) -> None:
            if event.event != "selected":
                log.info(
                    "llm.provider_event",
                    provider=event.provider,
                    model=event.model,
                    event_type=event.event,
                    reason=event.reason,
                )

        router = ProviderRouter(
            providers,
            services.adapters.tokenizer,
            health=HealthTracker(open_seconds=settings.llm_circuit_open_seconds),
            budgets=budgets,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            sink=log_event,
        )
        deps = AgentDeps(
            router=router,
            retrieval=services.retrieval,
            security=services.security,
            language=services.adapters.language,
            counter=services.adapters.tokenizer,
            evidence=EvidencePolicy(
                tau=settings.retrieval_tau,
                min_evidence_chunks=settings.min_evidence_chunks,
                budget_tokens=settings.llm_budget_context,
            ),
        )
        tracer = None
        if settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key:
            tracer = Tracer(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                host=settings.langfuse_host,
            )
        services.chat.agent = LangGraphAgent(deps, saver, tracer=tracer)
        log.info("agent.ready", providers=[p.name for p in providers])
        return True

    def warm_up(self) -> dict[str, float]:
        """Load the chat-path models once (e5, reranker, Presidio, classifier exemplars) so the first user turn after a
        restart is not a minute long (worklog M6). Blocking — run it in a thread after startup."""
        import time

        services = self.services
        timings: dict[str, float] = {}
        reranker = services.adapters.reranker

        def embed() -> object:
            return services.adapters.embedder.embed_query("warm-up")

        def rerank() -> object:
            return reranker.score("warm-up", ["warm-up"]) if reranker is not None else None

        def presidio() -> object:
            return services.privacy.detect("warm-up", language="el", known=[])

        def screen() -> object:
            return services.security.screen_input("warm-up", block_threshold=1.0)

        steps: dict[str, Callable[[], object]] = {
            "embedder": embed,
            "reranker": rerank,
            "presidio": presidio,
            "screening": screen,
        }
        for name, step in steps.items():
            started = time.monotonic()
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - warm-up is best effort; the first request loads lazily instead
                log.warning("warm_up.failed", step=name, error=f"{type(exc).__name__}: {exc}"[:200])
            timings[name] = round(time.monotonic() - started, 1)
        log.info("warm_up.done", **timings)
        return timings

    async def aclose(self) -> None:
        if self.admin_client is not None:
            await self.admin_client.aclose()
        if self.checkpoint_pool is not None:
            await self.checkpoint_pool.close()
        await self.engine.dispose()


def build_providers(settings: Settings) -> list[ChatModel]:
    """Providers in `LLM_PROVIDER_ORDER`; a provider without a key is skipped (logged)."""
    providers: list[ChatModel] = []
    for name in settings.llm_providers:
        if name == "gemini" and settings.gemini_api_key and settings.gemini_api_key.get_secret_value():
            providers.append(
                GeminiChatModel(
                    settings.gemini_api_key.get_secret_value(),
                    fast_model=settings.gemini_model_fast,
                    answer_model=settings.gemini_model,
                    timeout_seconds=settings.gemini_timeout_seconds,
                    thinking_level=settings.gemini_thinking_level,
                    thinking_tokens=settings.gemini_thinking_tokens,
                )
            )
        elif name == "groq" and settings.groq_api_key and settings.groq_api_key.get_secret_value():
            providers.append(
                GroqChatModel(
                    settings.groq_api_key.get_secret_value(),
                    fast_model=settings.groq_model_fast,
                    answer_model=settings.groq_model,
                )
            )
        elif name == "fake":
            if settings.app_env == "prod":
                log.warning(
                    "llm.provider_skipped", provider=name, reason="the fake provider is never used in prod"
                )
                continue
            providers.append(FakeChatModel("fake"))
        else:
            log.warning("llm.provider_skipped", provider=name, reason="no API key")
    return providers


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
        rerank_candidates=settings.rerank_candidates,
        conversation_retention_days=settings.conversation_retention_days,
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
        reranker=CrossEncoderReranker(
            settings.reranker_model, threads=threads, max_length=settings.rerank_max_length
        ),
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
