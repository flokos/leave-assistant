"""Null-object scanners (M4 decision 1): risk 0 / tier low, LLM-safe text = text. Replaced in M5."""

from __future__ import annotations

from collections.abc import Sequence

from leave_assistant.application.ports import PiiResult, RiskAssessment
from leave_assistant.domain.documents.chunking import ChunkDraft
from leave_assistant.domain.documents.types import Block


class NoRiskScanner:
    def scan(self, blocks: Sequence[Block], drafts: Sequence[ChunkDraft]) -> RiskAssessment:
        return RiskAssessment()


class NoPiiScanner:
    def scan(self, drafts: Sequence[ChunkDraft]) -> PiiResult:
        return PiiResult(
            llm_safe_texts=tuple(d.content for d in drafts), has_pii=tuple(False for _ in drafts), summary={}
        )
