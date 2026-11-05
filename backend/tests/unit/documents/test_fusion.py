"""Reciprocal rank fusion: scores, tie-breaks and the per-document cap."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.domain.documents.fusion import LegHit, reciprocal_rank_fusion

DOC_A, DOC_B, DOC_C = UUID(int=1), UUID(int=2), UUID(int=3)
VER = UUID(int=9)


def hit(n: int, doc: UUID, rank: int, score: float) -> LegHit:
    return LegHit(chunk_id=UUID(int=100 + n), document_id=doc, version_id=VER, rank=rank, score=score)


def test_fusion_adds_reciprocal_ranks_and_keeps_leg_metadata() -> None:
    vector = [hit(1, DOC_A, 1, 0.9), hit(2, DOC_B, 2, 0.8), hit(3, DOC_A, 3, 0.7)]
    keyword = [hit(2, DOC_B, 1, 5.0), hit(4, DOC_C, 2, 4.0)]
    fused = reciprocal_rank_fusion(vector, keyword, limit=10)
    assert [f.chunk_id for f in fused] == [UUID(int=102), UUID(int=101), UUID(int=104), UUID(int=103)]
    top = fused[0]
    assert top.vector_rank == 2 and top.keyword_rank == 1 and top.vector_similarity == 0.8
    assert abs(top.score - (1 / 62 + 1 / 61)) < 1e-12
    assert fused[2].vector_rank is None and fused[2].keyword_rank == 2 and fused[2].vector_similarity is None


def test_ties_prefer_higher_vector_similarity() -> None:
    vector = [hit(1, DOC_A, 1, 0.5)]
    keyword = [hit(2, DOC_B, 1, 3.0)]  # same RRF score as chunk 1, but no vector similarity
    fused = reciprocal_rank_fusion(vector, keyword, limit=5)
    assert [f.chunk_id for f in fused] == [UUID(int=101), UUID(int=102)]


def test_per_document_cap_and_limit() -> None:
    vector = [hit(n, DOC_A, n, 1.0 - n / 10) for n in range(1, 6)] + [hit(9, DOC_B, 6, 0.1)]
    fused = reciprocal_rank_fusion(vector, [], limit=4, max_per_document=3)
    assert [f.document_id for f in fused] == [DOC_A, DOC_A, DOC_A, DOC_B]
    assert len(reciprocal_rank_fusion(vector, [], limit=2)) == 2
