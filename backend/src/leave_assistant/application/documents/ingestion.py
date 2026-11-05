"""IngestionPipeline (doc 05 §3): parse → normalise → language → scan → chunk → embed → index → activate.

Runs in the worker (child process). Indexing is transactional per version: chunks and vectors are written and the
status flips in one transaction; a failure leaves no partial index. Retries are the job service's business; this
class only reports the final failure on the version.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.documents.activation import activate_version
from leave_assistant.application.documents.keys import extracted_key
from leave_assistant.application.errors import ApplicationError, NotFoundError
from leave_assistant.application.notifications.models import IntegrationEvent
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import (
    Clock,
    DocumentParser,
    DocumentStorage,
    EmbeddingModel,
    IdGenerator,
    LanguageDetector,
    PiiScanner,
    RiskScanner,
    Tokenizer,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.chunking import ChunkDraft, ChunkingRules, chunk_document, chunk_id_for
from leave_assistant.domain.documents.formats import MIME_EXTENSION
from leave_assistant.domain.documents.lifecycle import VersionEvent, can, transition
from leave_assistant.domain.documents.types import (
    Block,
    BlockType,
    Chunk,
    Document,
    DocumentVersion,
    IndexGeneration,
    ParsedDocument,
    RiskTier,
    VersionStatus,
)

WORKER_CONTEXT_USER = "worker"


class IngestionError(ApplicationError):
    """A processing failure with a stable reason (`no_text`, `too_many_pages`, `parse_error`, …)."""

    problem_type = "document/processing-failed"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason, reason=reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    version_id: UUID
    status: VersionStatus
    chunks: int
    pages: int | None
    language: str | None
    skipped: bool = False


def worker_context(tenant_id: UUID) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id=WORKER_CONTEXT_USER, roles=("system",))


def blocks_to_json(parsed: ParsedDocument) -> dict[str, Any]:
    return {
        "pages": parsed.pages,
        "warnings": list(parsed.warnings),
        "blocks": [
            {
                "type": b.type.value,
                "text": b.text,
                "level": b.level,
                "page": b.page,
                "rows": [list(r) for r in b.rows],
            }
            for b in parsed.blocks
        ],
    }


def blocks_from_json(data: dict[str, Any]) -> ParsedDocument:
    return ParsedDocument(
        blocks=tuple(
            Block(
                type=BlockType(b["type"]),
                text=b["text"],
                level=int(b.get("level") or 0),
                page=b.get("page"),
                rows=tuple(tuple(str(c) for c in row) for row in b.get("rows") or ()),
            )
            for b in data.get("blocks", [])
        ),
        pages=data.get("pages"),
        warnings=tuple(data.get("warnings") or ()),
    )


class IngestionPipeline:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: DocumentStorage,
        parser: DocumentParser,
        tokenizer: Tokenizer,
        embedder: EmbeddingModel,
        language: LanguageDetector,
        risk_scanner: RiskScanner,
        pii_scanner: PiiScanner,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        notifications: NotificationService,
        *,
        rules: ChunkingRules | None = None,
        max_pages: int = 500,
    ) -> None:
        self._uow = uow_factory
        self._storage = storage
        self._parser = parser
        self._tokenizer = tokenizer
        self._embedder = embedder
        self._language = language
        self._risk = risk_scanner
        self._pii = pii_scanner
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._notifications = notifications
        self._rules = rules or ChunkingRules()
        self._max_pages = max_pages

    @property
    def generation(self) -> str:
        return f"{self._embedder.model_id}|{self._rules.version}"

    # --- document.process ------------------------------------------------------------------------------------
    async def process(self, tenant_id: UUID, version_id: UUID) -> ProcessOutcome:
        context = worker_context(tenant_id)
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            if version.status in (
                VersionStatus.ACTIVE,
                VersionStatus.PENDING_REVIEW,
                VersionStatus.QUARANTINED,
            ):
                return ProcessOutcome(
                    version.id, version.status, 0, version.pages, version.language, skipped=True
                )
            if can(version.status, VersionEvent.PROCESS):
                version.status = transition(version.status, VersionEvent.PROCESS)
            elif version.status is not VersionStatus.PROCESSING:
                raise IngestionError("invalid_state", f"version is {version.status.value}")
            version.attempts += 1
            version.updated_at = self._clock.now()
            await uow.document_versions.update(version)
            await uow.commit()

        parsed = await self._parse(version)
        text = parsed.text
        if not text.strip():
            raise IngestionError("no_text", "no text could be extracted (image-only document?)")
        if parsed.pages is not None and parsed.pages > self._max_pages:
            raise IngestionError(
                "too_many_pages", f"{parsed.pages} pages exceed the limit of {self._max_pages}"
            )
        language = self._language.detect(text[:20000]) or document.language_hint
        drafts = chunk_document(
            parsed.blocks, title=document.title, count_tokens=self._tokenizer.count, rules=self._rules
        )
        if not drafts:
            raise IngestionError("no_text", "the document produced no chunks")
        risk = self._risk.scan(parsed.blocks, drafts)
        pii = self._pii.scan(drafts)
        vectors = await asyncio.to_thread(self._embedder.embed_passages, [d.embed_text for d in drafts])
        chunks = self._chunks(
            version, drafts, vectors, pii.llm_safe_texts, pii.has_pii, risk.chunk_flags, language
        )
        key = extracted_key(tenant_id, document.id, version.version_no)
        await self._storage.put_bytes(
            key,
            json.dumps(blocks_to_json(parsed), ensure_ascii=False).encode(),
            content_type="application/json",
        )

        now = self._clock.now()
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            await uow.chunks.replace_for_version(version.id, chunks)
            version.pages = parsed.pages
            version.language = language
            version.extracted_object_key = key
            version.risk_score = risk.score
            version.risk_tier = risk.tier
            version.risk_findings = [dict(f) for f in risk.findings]
            version.pii_summary = dict(pii.summary)
            version.processing_error = None
            version.processed_at = now
            version.index_generation = self.generation
            version.updated_at = now
            event = {
                RiskTier.LOW: VersionEvent.ACTIVATE,
                RiskTier.MEDIUM: VersionEvent.SEND_TO_REVIEW,
                RiskTier.HIGH: VersionEvent.QUARANTINE,
            }[risk.tier]
            if event is VersionEvent.ACTIVATE:
                await activate_version(
                    uow, document, version, now=now, audit=self._audit, principal=None, event=event
                )
            else:
                version.status = transition(version.status, event)
                version.retrievable = False
                await uow.document_versions.update(version)
            await self._ensure_generation(uow, tenant_id, now)
            await self._audit.record(
                uow,
                None,
                "document.processed",
                resource_type="document_version",
                resource_id=version.id,
                details={
                    "document_id": str(document.id),
                    "version_no": version.version_no,
                    "status": version.status.value,
                    "pages": parsed.pages,
                    "chunks": len(chunks),
                    "language": language,
                    "risk_tier": risk.tier.value,
                    "generation": self.generation,
                    "warnings": list(parsed.warnings)[:10],
                },
            )
            await self._emit(uow, document, version, "document.processed", {"status": version.status.value})
            await self._notifications.notify(
                uow,
                [version.uploaded_by_employee_id],
                "document.processed",
                {
                    "document_id": str(document.id),
                    "title": document.title,
                    "version_no": version.version_no,
                    "status": version.status.value,
                },
                dedupe_key=f"document.processed:{version.id}",
                channels=("in_app",),
            )
            await uow.commit()
        return ProcessOutcome(version.id, version.status, len(chunks), parsed.pages, language)

    # --- document.reindex ------------------------------------------------------------------------------------
    async def reindex(self, tenant_id: UUID, version_id: UUID) -> ProcessOutcome:
        context = worker_context(tenant_id)
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
        if version.extracted_object_key is None:
            raise IngestionError(
                "no_extracted_structure", "the version has no stored structure; reprocess it"
            )
        parsed = blocks_from_json(json.loads(await self._storage.get_bytes(version.extracted_object_key)))
        drafts = chunk_document(
            parsed.blocks, title=document.title, count_tokens=self._tokenizer.count, rules=self._rules
        )
        pii = self._pii.scan(drafts)
        vectors = await asyncio.to_thread(self._embedder.embed_passages, [d.embed_text for d in drafts])
        chunks = self._chunks(
            version,
            drafts,
            vectors,
            pii.llm_safe_texts,
            pii.has_pii,
            {},
            version.language,
            retrievable=version.retrievable,
        )
        now = self._clock.now()
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            await uow.chunks.replace_for_version(version.id, chunks)
            version.index_generation = self.generation
            version.updated_at = now
            await uow.document_versions.update(version)
            await self._ensure_generation(uow, tenant_id, now)
            await self._audit.record(
                uow,
                None,
                "document.reindexed",
                resource_type="document_version",
                resource_id=version.id,
                details={
                    "document_id": str(document.id),
                    "chunks": len(chunks),
                    "generation": self.generation,
                },
            )
            await uow.commit()
        return ProcessOutcome(version.id, version.status, len(chunks), version.pages, version.language)

    async def record_failure(self, tenant_id: UUID, version_id: UUID, error: str, *, final: bool) -> None:
        """Keeps the last error on the version; after the final attempt the version becomes FAILED (doc 05 §11)."""
        context = worker_context(tenant_id)
        now = self._clock.now()
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            version.processing_error = error[:500]
            version.updated_at = now
            if final and can(version.status, VersionEvent.FAIL):
                version.status = transition(version.status, VersionEvent.FAIL)
                version.retrievable = False
            await uow.document_versions.update(version)
            if final:
                await self._audit.record(
                    uow,
                    None,
                    "document.failed",
                    resource_type="document_version",
                    resource_id=version.id,
                    details={
                        "document_id": str(document.id),
                        "version_no": version.version_no,
                        "error": error[:500],
                    },
                )
                await self._emit(uow, document, version, "document.failed", {"error": error[:500]})
                await self._notifications.notify(
                    uow,
                    [version.uploaded_by_employee_id],
                    "document.failed",
                    {
                        "document_id": str(document.id),
                        "title": document.title,
                        "version_no": version.version_no,
                        "error": error[:200],
                    },
                    dedupe_key=f"document.failed:{version.id}",
                    channels=("in_app",),
                )
            await uow.commit()

    # --- helpers ---------------------------------------------------------------------------------------------
    async def _parse(self, version: DocumentVersion) -> ParsedDocument:
        extension = MIME_EXTENSION.get(version.mime, "bin")
        with tempfile.TemporaryDirectory(prefix="ingest-") as directory:
            path = Path(directory) / f"original.{extension}"
            await self._storage.download_to(version.object_key, path)
            try:
                return await asyncio.to_thread(
                    self._parser.parse, path, version.mime, max_pages=self._max_pages
                )
            except IngestionError:
                raise
            except Exception as exc:  # noqa: BLE001 - every parser failure becomes a stable reason
                raise IngestionError("parse_error", f"{type(exc).__name__}: {str(exc)[:300]}") from exc

    def _chunks(
        self,
        version: DocumentVersion,
        drafts: list[ChunkDraft],
        vectors: list[list[float]],
        llm_safe: tuple[str, ...],
        has_pii: tuple[bool, ...],
        flags: dict[int, tuple[str, ...]],
        language: str | None,
        *,
        retrievable: bool = False,
    ) -> list[Chunk]:
        if len(vectors) != len(drafts):
            raise IngestionError("embedding_mismatch", f"{len(vectors)} vectors for {len(drafts)} chunks")
        chunks: list[Chunk] = []
        for index, draft in enumerate(drafts):
            chunks.append(
                Chunk(
                    id=chunk_id_for(version.id, draft.ordinal),
                    tenant_id=version.tenant_id,
                    version_id=version.id,
                    ordinal=draft.ordinal,
                    chunk_type=draft.chunk_type,
                    heading_path=draft.heading_path,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    language=self._language.detect(draft.content) or language,
                    content=draft.content,
                    content_llm_safe=llm_safe[index] if index < len(llm_safe) else draft.content,
                    token_count=draft.token_count,
                    embedding=list(vectors[index]),
                    embedding_model_id=self._embedder.model_id,
                    risk_flags=tuple(flags.get(draft.ordinal, ())),
                    has_pii=bool(has_pii[index]) if index < len(has_pii) else False,
                    retrievable=retrievable,
                )
            )
        return chunks

    async def _ensure_generation(self, uow: UnitOfWork, tenant_id: UUID, now: Any) -> None:
        if await uow.index_generations.get(self.generation) is None:
            await uow.index_generations.upsert(
                IndexGeneration(
                    tenant_id=tenant_id,
                    generation=self.generation,
                    embedding_model_id=self._embedder.model_id,
                    chunking_rules_version=self._rules.version,
                    status="active",
                    started_at=now,
                    completed_at=now,
                )
            )

    async def _emit(
        self, uow: UnitOfWork, document: Document, version: DocumentVersion, type: str, extra: dict[str, Any]
    ) -> None:
        await uow.integration_events.add(
            IntegrationEvent(
                id=self._ids.new_id(),
                tenant_id=document.tenant_id,
                type=type,
                payload={
                    "tenant_id": str(document.tenant_id),
                    "document_id": str(document.id),
                    "version_id": str(version.id),
                    "version_no": version.version_no,
                    **extra,
                },
                created_at=self._clock.now(),
            )
        )

    @staticmethod
    async def _load(uow: UnitOfWork, version_id: UUID) -> tuple[DocumentVersion, Document]:
        version = await uow.document_versions.get(version_id)
        if version is None:
            raise NotFoundError("version not found")
        document = await uow.documents.get(version.document_id)
        if document is None:
            raise NotFoundError("document not found")
        return version, document
