"""`leave-eval-v1` (doc 11 §6): loading, validation and label resolution (docs/evaluation/README.md §1)."""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

KINDS = frozenset({"knowledge", "probe", "unanswerable", "boundary", "business", "security", "platform"})
DOC11_SCENARIOS = 32


def default_path() -> Path:
    configured = os.environ.get("EVAL_DATASET")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[4] / "evaluation" / "datasets" / "leave-eval-v1.yaml"


def normalise(text: str) -> str:
    """NFKC (µ → μ in the scanned PDF), case-folded, without whitespace (OCR joins words)."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


@dataclass(frozen=True, slots=True)
class Label:
    doc: str
    has: tuple[str, ...]
    grade: int


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    kind: str
    tenant: str | None
    user: str | None
    question: str | None
    language: str | None
    relevant: tuple[Label, ...] = ()
    never_retrieve: tuple[str, ...] = ()
    never_retrieve_tenant: str | None = None
    outcome: str | None = None
    ragas: bool = False
    reference: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def labelled(self) -> bool:
        return bool(self.relevant)


@dataclass(frozen=True, slots=True)
class ChunkText:
    chunk_id: UUID
    tenant: str
    title: str
    content: str


def _item(data: Mapping[str, Any]) -> Item:
    return Item(
        id=str(data["id"]),
        kind=str(data["kind"]),
        tenant=data.get("tenant"),
        user=data.get("user"),
        question=data.get("question"),
        language=data.get("language"),
        relevant=tuple(
            Label(str(r["doc"]), tuple(str(a) for a in r["has"]), int(r["grade"]))
            for r in data.get("relevant", [])
        ),
        never_retrieve=tuple(data.get("never_retrieve", [])),
        never_retrieve_tenant=data.get("never_retrieve_tenant"),
        outcome=data.get("outcome"),
        ragas=bool(data.get("ragas", False)),
        reference=data.get("reference"),
        raw=dict(data),
    )


def validate(items: list[Item]) -> None:
    ids = [i.id for i in items]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate ids: {duplicates}")
    unknown = sorted({i.kind for i in items} - KINDS)
    if unknown:
        raise ValueError(f"unknown kinds: {unknown}")
    scenarios = [i for i in items if re.fullmatch(r"S\d{2}", i.id)]
    if len(scenarios) != DOC11_SCENARIOS:
        raise ValueError(f"doc 11 lists {DOC11_SCENARIOS} scenarios, the dataset has {len(scenarios)}")
    for item in items:
        if item.kind in {"knowledge", "probe"} and not (item.relevant and item.question and item.user):
            raise ValueError(f"{item.id}: answerable items need a question, a user and labels")
        if item.kind in {"unanswerable", "boundary"} and not (item.question and item.user):
            raise ValueError(f"{item.id}: needs a question and a user")
        if item.ragas and not item.reference:
            raise ValueError(f"{item.id}: RAGAS items need a reference answer")
        for label in item.relevant:
            if label.grade not in (1, 2) or not label.has:
                raise ValueError(f"{item.id}: labels need grade 1|2 and at least one anchor")


def load(path: Path | None = None) -> list[Item]:
    data = yaml.safe_load((path or default_path()).read_text(encoding="utf-8"))
    items = [_item(d) for d in data["items"]]
    validate(items)
    return items


def grades_for(item: Item, chunks: Iterable[ChunkText]) -> dict[UUID, int]:
    """chunk id → grade for the item's labels (highest grade when several labels match)."""
    out: dict[UUID, int] = {}
    anchors = [(label, [normalise(a) for a in label.has]) for label in item.relevant]
    for chunk in chunks:
        if chunk.tenant != item.tenant:
            continue
        text = normalise(chunk.content)
        for label, needles in anchors:
            if chunk.title == label.doc and any(n in text for n in needles):
                out[chunk.chunk_id] = max(out.get(chunk.chunk_id, 0), label.grade)
    return out


def unresolved(items: Iterable[Item], chunks: list[ChunkText]) -> list[str]:
    """Labels that match no chunk of the corpus (the run refuses to start: the corpus is not the seed corpus)."""
    missing: list[str] = []
    for item in items:
        for label in item.relevant:
            single = Item(item.id, item.kind, item.tenant, item.user, item.question, item.language, (label,))
            if not grades_for(single, chunks):
                missing.append(f"{item.id}: {label.doc} {label.has}")
    return missing
