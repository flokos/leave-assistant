"""Chunks with vectors (pgvector HNSW) and FTS (greek/english/simple) — both retrieval legs under the predicate."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Text, cast, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.ports import IndexRow
from leave_assistant.domain.documents.fusion import LegHit
from leave_assistant.domain.documents.types import Chunk, ChunkType
from leave_assistant.domain.documents.visibility import Reader
from leave_assistant.infrastructure.db.models import DocumentChunkRow, DocumentRow, DocumentVersionRow
from leave_assistant.infrastructure.db.repositories.documents import visibility_predicate
from leave_assistant.infrastructure.db.session import flush_or_conflict

HNSW_EF_SEARCH = 80
FTS_CONFIG = {"el": "greek", "en": "english"}


def _to_domain(r: DocumentChunkRow) -> Chunk:
    return Chunk(
        id=r.id,
        tenant_id=r.tenant_id,
        version_id=r.version_id,
        ordinal=r.ordinal,
        chunk_type=ChunkType(r.chunk_type),
        heading_path=tuple(r.heading_path or ()),
        page_start=r.page_start,
        page_end=r.page_end,
        language=r.language,
        content=r.content,
        content_llm_safe=r.content_llm_safe,
        token_count=r.token_count,
        embedding=[float(x) for x in r.embedding] if r.embedding is not None else None,
        embedding_model_id=r.embedding_model_id,
        risk_flags=tuple(r.risk_flags or ()),
        has_pii=bool(r.has_pii),
        retrievable=bool(r.retrievable),
    )


class SqlChunkRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def replace_for_version(self, version_id: UUID, chunks: Sequence[Chunk]) -> None:
        await self._s.execute(
            delete(DocumentChunkRow).where(
                DocumentChunkRow.tenant_id == self._tenant, DocumentChunkRow.version_id == version_id
            )
        )
        now = func.now()
        self._s.add_all(
            DocumentChunkRow(
                id=c.id,
                tenant_id=c.tenant_id,
                version_id=c.version_id,
                ordinal=c.ordinal,
                chunk_type=c.chunk_type.value,
                heading_path=list(c.heading_path),
                page_start=c.page_start,
                page_end=c.page_end,
                language=c.language,
                content=c.content,
                content_llm_safe=c.content_llm_safe,
                token_count=c.token_count,
                embedding=c.embedding,
                embedding_model_id=c.embedding_model_id,
                risk_flags=list(c.risk_flags),
                has_pii=c.has_pii,
                retrievable=c.retrievable,
                created_at=now,
            )
            for c in chunks
        )
        await flush_or_conflict(self._s)

    async def get(self, chunk_id: UUID) -> Chunk | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(
                select(DocumentChunkRow).where(
                    DocumentChunkRow.tenant_id == self._tenant, DocumentChunkRow.id == chunk_id
                )
            )
        ).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list_for_version(self, version_id: UUID) -> builtins.list[Chunk]:
        if self._tenant is None:
            return []
        stmt = (
            select(DocumentChunkRow)
            .where(DocumentChunkRow.tenant_id == self._tenant, DocumentChunkRow.version_id == version_id)
            .order_by(DocumentChunkRow.ordinal)
        )
        return [_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def count_for_version(self, version_id: UUID) -> int:
        stmt = select(func.count()).where(
            DocumentChunkRow.tenant_id == self._tenant, DocumentChunkRow.version_id == version_id
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def set_retrievable(self, version_id: UUID, retrievable: bool) -> int:
        result = await self._s.execute(
            update(DocumentChunkRow)
            .where(
                DocumentChunkRow.tenant_id == self._tenant,
                DocumentChunkRow.version_id == version_id,
                DocumentChunkRow.retrievable.is_(not retrievable),
            )
            .values(retrievable=retrievable)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def _searchable(self, reader: Reader):  # type: ignore[no-untyped-def]
        return (
            select(DocumentChunkRow.id, DocumentChunkRow.version_id, DocumentVersionRow.document_id)
            .join(DocumentVersionRow, DocumentVersionRow.id == DocumentChunkRow.version_id)
            .join(DocumentRow, DocumentRow.id == DocumentVersionRow.document_id)
            .where(
                DocumentChunkRow.tenant_id == self._tenant,
                DocumentChunkRow.retrievable.is_(True),
                DocumentVersionRow.retrievable.is_(True),
                DocumentRow.deleted_at.is_(None),
                visibility_predicate(reader),
            )
        )

    async def vector_search(
        self, embedding: Sequence[float], *, reader: Reader, limit: int
    ) -> builtins.list[LegHit]:
        if self._tenant is None:
            return []
        await self._s.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
        distance = DocumentChunkRow.embedding.cosine_distance(list(embedding))
        stmt = (
            self._searchable(reader)
            .add_columns((1 - distance).label("similarity"))
            .where(DocumentChunkRow.embedding.is_not(None))
            .order_by(distance, DocumentChunkRow.id)
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            LegHit(chunk_id=r[0], document_id=r[2], version_id=r[1], rank=rank, score=float(r[3]))
            for rank, r in enumerate(rows, 1)
        ]

    async def visible_version_ids(self, *, reader: Reader) -> builtins.list[UUID]:
        if self._tenant is None:
            return []
        visible = self._searchable(reader).subquery()
        stmt = select(visible.c.version_id).distinct()
        return list((await self._s.execute(stmt)).scalars())

    async def index_rows(self) -> builtins.list[IndexRow]:
        if self._tenant is None:
            return []
        stmt = (
            select(
                DocumentChunkRow.id,
                DocumentVersionRow.document_id,
                DocumentChunkRow.version_id,
                DocumentChunkRow.embedding,
            )
            .join(DocumentVersionRow, DocumentVersionRow.id == DocumentChunkRow.version_id)
            .join(DocumentRow, DocumentRow.id == DocumentVersionRow.document_id)
            .where(
                DocumentChunkRow.tenant_id == self._tenant,
                DocumentChunkRow.retrievable.is_(True),
                DocumentVersionRow.retrievable.is_(True),
                DocumentRow.deleted_at.is_(None),
                DocumentChunkRow.embedding.is_not(None),
            )
            .order_by(DocumentChunkRow.id)
        )
        return [
            IndexRow(chunk_id=r[0], document_id=r[1], version_id=r[2], embedding=[float(x) for x in r[3]])
            for r in (await self._s.execute(stmt)).all()
        ]

    async def keyword_search(
        self, query: str, *, language: str | None, reader: Reader, limit: int
    ) -> builtins.list[LegHit]:
        if self._tenant is None or not query.strip():
            return []
        config = FTS_CONFIG.get(language or "", "simple")
        tsv_lang = DocumentChunkRow.tsv_el if config == "greek" else DocumentChunkRow.tsv_en
        # any stemmed, non-stop word (OR): an AND of every word of a natural-language question matched nothing, so the
        # keyword leg never contributed to chat retrieval (found by the M9 evaluation); ts_rank_cd still prefers
        # chunks that match more of the words. The `simple` leg stays strict (no stop words to drop there).
        q_lang = cast(func.replace(cast(func.plainto_tsquery(config, query), Text), " & ", " | "), TSQUERY)
        q_simple = func.websearch_to_tsquery("simple", func.app.f_unaccent(query))
        rank = func.ts_rank_cd(tsv_lang, q_lang) + func.ts_rank_cd(DocumentChunkRow.tsv_simple, q_simple)
        stmt = (
            self._searchable(reader)
            .add_columns(rank.label("score"))
            .where(or_(tsv_lang.op("@@")(q_lang), DocumentChunkRow.tsv_simple.op("@@")(q_simple)))
            .order_by(rank.desc(), DocumentChunkRow.id)
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            LegHit(chunk_id=r[0], document_id=r[2], version_id=r[1], rank=rank_no, score=float(r[3]))
            for rank_no, r in enumerate(rows, 1)
        ]
