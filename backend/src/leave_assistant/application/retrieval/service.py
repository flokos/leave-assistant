"""RetrievalService: `search` (hybrid + RRF, no reranker until M9) and `citation` (readers only)."""

from __future__ import annotations

import asyncio
from uuid import UUID

from leave_assistant.application.documents.policy import reader_for, require_tenant_user, scope_for
from leave_assistant.application.documents.views import RetrievalBundle, SearchHit
from leave_assistant.application.errors import ApplicationError, InvalidInputError, NotFoundError
from leave_assistant.application.ports import EmbeddingModel, LanguageDetector, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.fusion import FusedHit, reciprocal_rank_fusion
from leave_assistant.domain.documents.types import Chunk, Citation, Document, DocumentVersion
from leave_assistant.domain.documents.visibility import can_read
from leave_assistant.domain.tenancy import Principal

MAX_QUERY_LENGTH = 500


class NotRetrievable(ApplicationError):
    problem_type = "document/not-retrievable"


class RetrievalService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        embedder: EmbeddingModel,
        language: LanguageDetector,
        *,
        leg_size: int = 30,
        default_k: int = 10,
        max_per_document: int = 3,
    ) -> None:
        self._uow = uow_factory
        self._embedder = embedder
        self._language = language
        self._leg_size = leg_size
        self._default_k = default_k
        self._max_per_document = max_per_document

    async def search(self, principal: Principal, query: str, *, k: int | None = None) -> RetrievalBundle:
        require_tenant_user(principal, action="document.search")
        text = " ".join(query.split())
        if not text or len(text) > MAX_QUERY_LENGTH:
            raise InvalidInputError(
                f"query must be 1–{MAX_QUERY_LENGTH} characters",
                errors=[{"loc": ["query", "q"], "msg": f"1–{MAX_QUERY_LENGTH} characters"}],
            )
        limit = max(1, min(k or self._default_k, 50))
        language = self._language.detect(text) or principal.locale.value
        vector = await asyncio.to_thread(self._embedder.embed_query, text)
        reader = reader_for(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            vector_hits = await uow.chunks.vector_search(vector, reader=reader, limit=self._leg_size)
            keyword_hits = await uow.chunks.keyword_search(
                text, language=language, reader=reader, limit=self._leg_size
            )
            fused = reciprocal_rank_fusion(
                vector_hits, keyword_hits, limit=limit, max_per_document=self._max_per_document
            )
            hits: list[SearchHit] = []
            documents: dict[UUID, Document] = {}
            versions: dict[UUID, DocumentVersion] = {}
            for item in fused:
                chunk = await uow.chunks.get(item.chunk_id)
                if chunk is None:
                    continue
                if item.document_id not in documents:
                    document = await uow.documents.get(item.document_id)
                    if document is None:
                        continue
                    documents[item.document_id] = document
                if item.version_id not in versions:
                    version = await uow.document_versions.get(item.version_id)
                    if version is None:
                        continue
                    versions[item.version_id] = version
                hits.append(self._hit(item, chunk, documents[item.document_id], versions[item.version_id]))
        return RetrievalBundle(query=text, language=language, hits=hits)

    async def citation(self, principal: Principal, chunk_id: UUID) -> SearchHit:
        require_tenant_user(principal, action="document.read")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            chunk = await uow.chunks.get(chunk_id)
            if chunk is None:
                raise NotFoundError("citation not found")
            version = await uow.document_versions.get(chunk.version_id)
            document = await uow.documents.get(version.document_id) if version else None
            if version is None or document is None or document.deleted_at is not None:
                raise NotFoundError("citation not found")
            if not can_read(reader_for(principal), await scope_for(uow, document)):
                raise NotFoundError("citation not found")
            if not (version.retrievable and chunk.retrievable):
                raise NotRetrievable("the cited version is no longer active")
        fused = FusedHit(chunk.id, document.id, version.id, 0.0, None, None, None)
        return self._hit(fused, chunk, document, version)

    @staticmethod
    def _hit(item: FusedHit, chunk: Chunk, document: Document, version: DocumentVersion) -> SearchHit:
        return SearchHit(
            chunk=chunk,
            document=document,
            version=version,
            score=item.score,
            vector_rank=item.vector_rank,
            keyword_rank=item.keyword_rank,
            citation=Citation(
                chunk_id=chunk.id,
                document_id=document.id,
                version_id=version.id,
                version_no=version.version_no,
                title=document.title,
                heading_path=chunk.heading_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            ),
        )
