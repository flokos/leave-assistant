"""Chroma as the vector leg — evaluation only (ADR-0011, `eval` compose profile, M9).

One collection per tenant (cosine HNSW) holding the exact pgvector embeddings (`ChromaSync`). Isolation is
application-enforced: the allowed version ids are computed in PostgreSQL under RLS with the same predicate as the
pgvector leg, and passed to Chroma as a metadata filter. That is why Chroma stays out of production.
"""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from leave_assistant.application.ports import UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.fusion import LegHit
from leave_assistant.domain.documents.visibility import Reader

BATCH = 256


def collection_name(tenant_id: UUID) -> str:
    return f"tenant_{tenant_id.hex}"


def connect(url: str) -> Any:
    """`chromadb.HttpClient` for `http://host:port` (the client is the `chroma` dependency group)."""
    try:
        import chromadb
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("RETRIEVAL_BACKEND=chroma needs the chromadb client (uv group `chroma`)") from exc
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return chromadb.HttpClient(host=parsed.hostname or "localhost", port=parsed.port or 8000)


class ChromaVectorIndex:
    name = "chroma"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def search(
        self, uow: UnitOfWork, embedding: Sequence[float], *, reader: Reader, limit: int
    ) -> builtins.list[LegHit]:
        tenant_id = uow.context.tenant_id if uow.context else None
        if tenant_id is None:
            return []
        allowed = await uow.chunks.visible_version_ids(reader=reader)
        if not allowed:
            return []
        vector = [float(x) for x in embedding]

        def query() -> Any:
            try:
                collection = self._client.get_collection(collection_name(tenant_id))
            except Exception:  # noqa: BLE001 - chroma raises a generic error for unknown collections
                return None
            return collection.query(
                query_embeddings=[vector],
                n_results=limit,
                where={"version_id": {"$in": [str(v) for v in allowed]}},
                include=["metadatas", "distances"],
            )

        result = await asyncio.to_thread(query)
        if not result or not result.get("ids"):
            return []
        hits: list[LegHit] = []
        for rank, (chunk_id, metadata, distance) in enumerate(
            zip(result["ids"][0], result["metadatas"][0], result["distances"][0], strict=True), 1
        ):
            hits.append(
                LegHit(
                    chunk_id=UUID(chunk_id),
                    document_id=UUID(str(metadata["document_id"])),
                    version_id=UUID(str(metadata["version_id"])),
                    rank=rank,
                    score=1.0 - float(distance),
                )
            )
        return hits


class ChromaSync:
    """Copies the retrievable chunks' pgvector embeddings into the tenant's collection (rebuilt from scratch)."""

    def __init__(self, client: Any, uow_factory: UnitOfWorkFactory) -> None:
        self._client = client
        self._uow = uow_factory

    async def sync_tenant(self, tenant_id: UUID) -> int:
        context = TenantContext(tenant_id=tenant_id, user_id="chroma.sync", roles=("system",))
        async with self._uow(context) as uow:
            rows = await uow.chunks.index_rows()

        def write() -> None:
            name = collection_name(tenant_id)
            try:
                self._client.delete_collection(name)
            except Exception:  # noqa: BLE001 - absent on the first sync
                pass
            collection = self._client.create_collection(name, metadata={"hnsw:space": "cosine"})
            for start in range(0, len(rows), BATCH):
                batch = rows[start : start + BATCH]
                collection.add(
                    ids=[str(r.chunk_id) for r in batch],
                    embeddings=[r.embedding for r in batch],
                    metadatas=[
                        {"version_id": str(r.version_id), "document_id": str(r.document_id)} for r in batch
                    ],
                )

        await asyncio.to_thread(write)
        return len(rows)
