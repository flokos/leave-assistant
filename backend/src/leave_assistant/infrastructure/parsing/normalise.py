"""Docling document → ordered `Block`s with heading levels, pages and table cells (doc 05 §3 "structure")."""

from __future__ import annotations

from typing import Any

from leave_assistant.domain.documents.types import Block, BlockType, ParsedDocument

_HEADING_LABELS = {"section_header", "title"}
_SKIP_LABELS = {
    "page_header",
    "page_footer",
    "footnote",
    "picture",
    "form",
    "key_value_region",
    "document_index",
}


def _page_of(item: Any) -> int | None:
    prov = getattr(item, "prov", None) or []
    for entry in prov:
        page = getattr(entry, "page_no", None)
        if page is not None:
            return int(page)
    return None


def _table_rows(item: Any) -> tuple[tuple[str, ...], ...]:
    data = getattr(item, "data", None)
    grid = getattr(data, "grid", None)
    rows: list[tuple[str, ...]] = []
    if grid:
        for row in grid:
            cells = tuple(" ".join(str(getattr(cell, "text", "") or "").split()) for cell in row)
            if any(cells):
                rows.append(cells)
        return tuple(rows)
    try:
        frame = item.export_to_dataframe()
    except Exception:  # noqa: BLE001 - tables without structure fall back to their text
        return ()
    header = tuple(str(c) for c in frame.columns)
    rows.append(header)
    for values in frame.itertuples(index=False):
        rows.append(tuple(" ".join(str(v).split()) for v in values))
    return tuple(rows)


def normalise(document: Any) -> ParsedDocument:
    """`document` is a `docling_core` DoclingDocument. Headings keep their level; lists become list items."""
    blocks: list[Block] = []
    warnings: list[str] = []
    for item, _level in document.iterate_items(with_groups=False):
        label = str(getattr(getattr(item, "label", None), "value", getattr(item, "label", "")) or "")
        page = _page_of(item)
        if label in _SKIP_LABELS:
            continue
        if label == "table":
            rows = _table_rows(item)
            text = ""
            if not rows:
                try:
                    text = str(item.export_to_markdown(doc=document))
                except Exception:  # noqa: BLE001
                    text = ""
            if rows or text.strip():
                blocks.append(Block(BlockType.TABLE, text, page=page, rows=rows))
            continue
        text = " ".join(str(getattr(item, "text", "") or "").split())
        if not text:
            continue
        if label in _HEADING_LABELS:
            heading_level = int(getattr(item, "level", 1) or 1) if label == "section_header" else 1
            blocks.append(Block(BlockType.HEADING, text, level=max(1, heading_level), page=page))
        elif label == "list_item":
            blocks.append(Block(BlockType.LIST_ITEM, text, page=page))
        elif label == "caption":
            blocks.append(Block(BlockType.CAPTION, text, page=page))
        else:  # paragraph, text, code, formula, checkbox …
            blocks.append(Block(BlockType.PARAGRAPH, text, page=page))
    pages: int | None
    try:
        pages = int(document.num_pages()) or None
    except Exception:  # noqa: BLE001
        pages = None
    return ParsedDocument(blocks=tuple(blocks), pages=pages, warnings=tuple(warnings))


def plain_text_blocks(text: str) -> ParsedDocument:
    """TXT files: paragraphs separated by blank lines, no headings (doc 05 §3: wrapped as a single section)."""
    blocks: list[Block] = []
    for paragraph in text.replace("\r\n", "\n").split("\n\n"):
        cleaned = " ".join(paragraph.split())
        if cleaned:
            blocks.append(Block(BlockType.PARAGRAPH, cleaned, page=1))
    return ParsedDocument(blocks=tuple(blocks), pages=1)
