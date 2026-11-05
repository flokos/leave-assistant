"""Reciprocal rank fusion of the vector and keyword legs (doc 05 §6.3, ADR-0012)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

RRF_K = 60


@dataclass(frozen=True, slots=True)
class LegHit:
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    rank: int  # 1-based within its leg
    score: float  # cosine similarity (vector leg) or ts_rank_cd (keyword leg)


@dataclass(frozen=True, slots=True)
class FusedHit:
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    score: float
    vector_rank: int | None
    keyword_rank: int | None
    vector_similarity: float | None


def reciprocal_rank_fusion(
    vector: Sequence[LegHit],
    keyword: Sequence[LegHit],
    *,
    limit: int,
    k: int = RRF_K,
    max_per_document: int = 3,
) -> list[FusedHit]:
    """`score = Σ 1/(k + rank)`; ties broken by vector similarity; at most `max_per_document` chunks per document."""
    merged: dict[UUID, FusedHit] = {}
    for hit in vector:
        merged[hit.chunk_id] = FusedHit(
            hit.chunk_id, hit.document_id, hit.version_id, 1.0 / (k + hit.rank), hit.rank, None, hit.score
        )
    for kw in keyword:
        previous = merged.get(kw.chunk_id)
        if previous is None:
            merged[kw.chunk_id] = FusedHit(
                kw.chunk_id, kw.document_id, kw.version_id, 1.0 / (k + kw.rank), None, kw.rank, None
            )
        else:
            merged[kw.chunk_id] = FusedHit(
                previous.chunk_id,
                previous.document_id,
                previous.version_id,
                previous.score + 1.0 / (k + kw.rank),
                previous.vector_rank,
                kw.rank,
                previous.vector_similarity,
            )
    ordered = sorted(
        merged.values(),
        key=lambda h: (
            -h.score,
            -(h.vector_similarity if h.vector_similarity is not None else -1.0),
            str(h.chunk_id),
        ),
    )
    per_document: dict[UUID, int] = {}
    selected: list[FusedHit] = []
    for fused in ordered:
        if per_document.get(fused.document_id, 0) >= max_per_document:
            continue
        per_document[fused.document_id] = per_document.get(fused.document_id, 0) + 1
        selected.append(fused)
        if len(selected) >= limit:
            break
    return selected
