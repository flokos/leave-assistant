"""The pgvector vector leg (ADR-0011): the default `VectorIndex`, delegating to the chunk repository so the search
runs under RLS and the visibility predicate in the caller's transaction."""

from __future__ import annotations

import builtins
from collections.abc import Sequence

from leave_assistant.application.ports import UnitOfWork
from leave_assistant.domain.documents.fusion import LegHit
from leave_assistant.domain.documents.visibility import Reader


class PgVectorIndex:
    name = "pgvector"

    async def search(
        self, uow: UnitOfWork, embedding: Sequence[float], *, reader: Reader, limit: int
    ) -> builtins.list[LegHit]:
        return await uow.chunks.vector_search(embedding, reader=reader, limit=limit)
