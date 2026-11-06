"""ContextBuilder (doc 05 §6.5): evidence selection by τ, ≤ 3 chunks per document, adjacent-chunk merge, the
1,800-token context budget, `<document id="n">` blocks from the LLM-safe text only, and the citation map."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from leave_assistant.application.llm.ports import TokenCounter
from leave_assistant.application.retrieval.service import ScoredHit
from leave_assistant.domain.agent.budget import estimate
from leave_assistant.domain.agent.types import CitationRef
from leave_assistant.domain.privacy.types import LlmSafeText


@dataclass(frozen=True, slots=True)
class ContextBundle:
    text: LlmSafeText
    refs: tuple[CitationRef, ...]  # index n-1 ↔ <document id="n">
    top_score: float
    evidence: int  # chunks at or above τ
    tokens: int
    source_version_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    tau: float = 0.3
    min_evidence_chunks: int = 1
    max_per_document: int = 3
    budget_tokens: int = 1800


def build_context(hits: Sequence[ScoredHit], counter: TokenCounter, policy: EvidencePolicy) -> ContextBundle:
    evidence = [h for h in hits if h.rerank_score >= policy.tau]
    top = max((h.rerank_score for h in hits), default=0.0)
    per_document: dict[str, list[ScoredHit]] = {}
    for scored in evidence:  # already sorted by score
        bucket = per_document.setdefault(str(scored.hit.document.id), [])
        if len(bucket) < policy.max_per_document:
            bucket.append(scored)
    # documents in order of their best chunk; chunks of one version merged when adjacent
    groups: list[list[ScoredHit]] = []
    for bucket in per_document.values():
        ordered = sorted(bucket, key=lambda s: (str(s.hit.version.id), s.hit.chunk.ordinal))
        current: list[ScoredHit] = []
        for scored in ordered:
            if current and (
                scored.hit.version.id != current[-1].hit.version.id
                or scored.hit.chunk.ordinal != current[-1].hit.chunk.ordinal + 1
            ):
                groups.append(current)
                current = []
            current.append(scored)
        if current:
            groups.append(current)
    groups.sort(key=lambda g: max(s.rerank_score for s in g), reverse=True)
    blocks: list[str] = []
    refs: list[CitationRef] = []
    used = 0
    versions: set[str] = set()
    for group in groups:
        first = max(
            group, key=lambda s: s.rerank_score
        ).hit  # the citation points at the best chunk of the group
        n = len(refs) + 1
        section = " > ".join(first.chunk.heading_path[1:]) or first.document.title
        body = "\n".join(s.hit.chunk.content_llm_safe for s in group)
        block = (
            f'<document id="{n}" title="{escape(first.document.title)}" section="{escape(section)}">\n'
            f"{body}\n</document>"
        )
        cost = estimate(counter.count(block))
        if used + cost > policy.budget_tokens:
            continue
        used += cost
        blocks.append(block)
        versions.add(str(first.version.id))
        refs.append(
            CitationRef(
                n=n,
                chunk_id=first.chunk.id,
                document_id=first.document.id,
                version_id=first.version.id,
                version_no=first.version.version_no,
                title=first.document.title,
                heading_path=tuple(first.chunk.heading_path),
                page=first.chunk.page_start,
            )
        )
    return ContextBundle(
        text=LlmSafeText("\n\n".join(blocks)),
        refs=tuple(refs),
        top_score=top,
        evidence=len(evidence),
        tokens=used,
        source_version_ids=frozenset(versions),
    )


def sufficient(bundle: ContextBundle, policy: EvidencePolicy) -> bool:
    return bundle.evidence >= policy.min_evidence_chunks and bool(bundle.refs)
