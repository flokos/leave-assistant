"""Structure-aware, token-bounded chunking (doc 05 §4, ADR-0015).

Deterministic and pure: the token counter is injected (the e5 tokenizer in production, a word counter in tests).
Sections follow the heading tree; large sections split at paragraph/list-item boundaries, then at sentences, with a
bounded overlap taken from the previous chunk's last sentences (only within a section); small sections merge with
the following sibling under the same parent heading; tables stay whole or split by rows with the header repeated.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from leave_assistant.domain.documents.types import Block, BlockType, ChunkType

TokenCounter = Callable[[str], int]
_SENTENCE_END = re.compile(r"(?<=[.!?;·…])\s+")


@dataclass(frozen=True, slots=True)
class ChunkingRules:
    target_tokens: int = 300
    max_tokens: int = 450
    min_tokens: int = 60
    overlap_tokens: int = 40
    version: str = "v1"


DEFAULT_RULES = ChunkingRules()


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    chunk_type: ChunkType
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    content: str
    embed_text: str  # content prefixed with the document title and heading path (doc 05 §4 step 6)
    token_count: int


@dataclass(slots=True)
class _Unit:
    text: str
    page: int | None
    is_table: bool = False
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(slots=True)
class _Section:
    heading_path: tuple[str, ...]
    units: list[_Unit]


@dataclass(slots=True)
class _Piece:
    text: str
    pages: tuple[int | None, int | None]
    is_table: bool


def chunk_id_for(version_id: UUID, ordinal: int) -> UUID:
    """Stable chunk identity: `uuid5(version_id, ordinal)` (doc 05 §4 step 7)."""
    return uuid5(version_id, str(ordinal))


def table_markdown(rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    cells = [list(r) + [""] * (width - len(r)) for r in rows]
    header, body = cells[0], cells[1:]
    lines = ["| " + " | ".join(c.replace("|", "\\|").strip() for c in header) + " |", "|" + " --- |" * width]
    lines.extend("| " + " | ".join(c.replace("|", "\\|").strip() for c in row) + " |" for row in body)
    return "\n".join(lines)


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text.strip())]
    return [p for p in parts if p]


def _sections(blocks: Sequence[Block]) -> list[_Section]:
    stack: list[tuple[int, str]] = []
    sections: list[_Section] = []
    current = _Section(heading_path=(), units=[])
    for block in blocks:
        if block.type is BlockType.HEADING:
            level = block.level or 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, " ".join(block.text.split())))
            if current.units:
                sections.append(current)
            current = _Section(heading_path=tuple(text for _, text in stack), units=[])
            continue
        if block.type is BlockType.TABLE:
            text = table_markdown(block.rows) if block.rows else block.text.strip()
            if text:
                current.units.append(_Unit(text, block.page, True, block.rows))
            continue
        text = block.text.strip()
        if text:
            current.units.append(_Unit(text, block.page))
    if current.units:
        sections.append(current)
    return sections


def _pages(units: Sequence[_Unit]) -> tuple[int | None, int | None]:
    pages = [u.page for u in units if u.page is not None]
    return (min(pages), max(pages)) if pages else (None, None)


def _hard_split(text: str, count: TokenCounter, limit: int) -> list[str]:
    """Last resort for a single sentence longer than the limit: split on whitespace."""
    words = text.split()
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and count(candidate) > limit:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _split_text(text: str, count: TokenCounter, rules: ChunkingRules) -> list[str]:
    """Split one long paragraph at sentence boundaries, greedy up to the target size."""
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in split_sentences(text):
        tokens = count(sentence)
        if tokens > rules.max_tokens:
            if current:
                pieces.append(" ".join(current))
                current, current_tokens = [], 0
            pieces.extend(_hard_split(sentence, count, rules.target_tokens))
            continue
        if current and current_tokens + tokens > rules.target_tokens:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _table_pieces(unit: _Unit, count: TokenCounter, rules: ChunkingRules) -> list[_Piece]:
    if count(unit.text) <= rules.max_tokens or len(unit.rows) < 3:
        return [_Piece(unit.text, (unit.page, unit.page), True)]
    header, body = unit.rows[0], list(unit.rows[1:])
    pieces: list[_Piece] = []
    current: list[tuple[str, ...]] = []
    for row in body:
        candidate = table_markdown([header, *current, row])
        if current and count(candidate) > rules.max_tokens:
            pieces.append(_Piece(table_markdown([header, *current]), (unit.page, unit.page), True))
            current = [row]
        else:
            current.append(row)
    if current:
        pieces.append(_Piece(table_markdown([header, *current]), (unit.page, unit.page), True))
    return pieces


def _overlap_tail(text: str, count: TokenCounter, budget: int) -> str:
    """The last sentences of `text` that fit in `budget` tokens."""
    tail: list[str] = []
    used = 0
    for sentence in reversed(split_sentences(text)):
        tokens = count(sentence)
        if used + tokens > budget:
            break
        tail.insert(0, sentence)
        used += tokens
    return " ".join(tail)


def _section_pieces(section: _Section, count: TokenCounter, rules: ChunkingRules) -> list[_Piece]:
    pieces: list[_Piece] = []
    buffer: list[_Unit] = []
    buffer_tokens = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        if buffer:
            pieces.append(_Piece("\n".join(u.text for u in buffer), _pages(buffer), False))
            buffer, buffer_tokens = [], 0

    for unit in section.units:
        if unit.is_table:
            flush()
            pieces.extend(_table_pieces(unit, count, rules))
            continue
        tokens = count(unit.text)
        if tokens > rules.max_tokens:
            flush()
            pieces.extend(
                _Piece(p, (unit.page, unit.page), False) for p in _split_text(unit.text, count, rules)
            )
            continue
        if buffer and buffer_tokens + tokens > rules.target_tokens:
            flush()
        buffer.append(unit)
        buffer_tokens += tokens
    flush()

    # overlap between consecutive text pieces of the same section (never across sections or tables)
    if rules.overlap_tokens > 0:
        for index in range(1, len(pieces)):
            previous, current = pieces[index - 1], pieces[index]
            if previous.is_table or current.is_table:
                continue
            tail = _overlap_tail(previous.text, count, rules.overlap_tokens)
            if tail and count(tail + "\n" + current.text) <= rules.max_tokens:
                pieces[index] = replace(current, text=tail + "\n" + current.text)
    return pieces


def _section_tokens(section: _Section, count: TokenCounter) -> int:
    return sum(count(u.text) for u in section.units)


def embed_text_for(title: str, heading_path: Sequence[str], content: str) -> str:
    """`"{title} > {heading path}\n{content}"`; a top heading that repeats the title is not duplicated."""
    parts = list(heading_path)
    if parts and " ".join(parts[0].split()).casefold() == " ".join(title.split()).casefold():
        parts = parts[1:]
    header = " > ".join(part for part in (title.strip(), *parts) if part)
    return f"{header}\n{content}" if header else content


def chunk_document(
    blocks: Sequence[Block],
    *,
    title: str,
    count_tokens: TokenCounter,
    rules: ChunkingRules = DEFAULT_RULES,
) -> list[ChunkDraft]:
    sections = _sections(blocks)
    emitted: list[tuple[tuple[str, ...], _Piece]] = []
    pending: _Section | None = None  # a small section waiting for a sibling to merge into

    def emit(section: _Section) -> None:
        for piece in _section_pieces(section, count_tokens, rules):
            emitted.append((section.heading_path, piece))

    for section in sections:
        if pending is not None:
            same_parent = section.heading_path[:-1] == pending.heading_path[:-1]
            first = section.units[0] if section.units else None
            if same_parent and first is not None and not first.is_table:
                label = pending.heading_path[-1] if pending.heading_path else ""
                merged = "\n".join(part for part in (label, *(u.text for u in pending.units)) if part)
                pages = _pages([*pending.units, first])
                section.units[0] = _Unit(
                    f"{merged}\n{first.text}", pages[0] if pages[0] is not None else first.page
                )
            else:
                emit(pending)
            pending = None
        if _section_tokens(section, count_tokens) < rules.min_tokens:
            pending = section
            continue
        emit(section)
    if pending is not None:
        emit(pending)

    drafts: list[ChunkDraft] = []
    for ordinal, (heading_path, piece) in enumerate(emitted):
        embed = embed_text_for(title, heading_path, piece.text)
        drafts.append(
            ChunkDraft(
                ordinal=ordinal,
                chunk_type=ChunkType.TABLE if piece.is_table else ChunkType.TEXT,
                heading_path=heading_path,
                page_start=piece.pages[0],
                page_end=piece.pages[1],
                content=piece.text,
                embed_text=embed,
                token_count=count_tokens(embed),
            )
        )
    return drafts
