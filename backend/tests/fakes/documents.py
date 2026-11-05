"""Fakes for the M4 adapters: in-memory storage, a Markdown-ish parser, a word tokenizer, a hashing embedder."""

from __future__ import annotations

import hashlib
import math
import re
import zlib
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from leave_assistant.domain.documents.formats import MARKDOWN, TEXT
from leave_assistant.domain.documents.types import Block, BlockType, ParsedDocument

_WORD = re.compile(r"[\w']+", re.UNICODE)


class MemoryDocumentStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail_put = False

    async def put_file(self, key: str, path: Path, *, content_type: str) -> None:
        await self.put_bytes(key, path.read_bytes(), content_type=content_type)  # noqa: ASYNC240

    async def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        if self.fail_put:
            raise OSError("storage unavailable")
        self.objects[key] = (bytes(data), content_type)

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    async def download_to(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key][0])  # noqa: ASYNC240

    async def stream(self, key: str) -> AsyncIterator[bytes]:  # type: ignore[override]
        data = self.objects[key][0]
        for start in range(0, len(data), 1 << 16):
            yield data[start : start + (1 << 16)]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects


def parse_markdown(text: str) -> ParsedDocument:
    """Headings (#), list items (-/*), pipe tables and paragraphs — enough structure for pipeline tests."""
    blocks: list[Block] = []
    paragraph: list[str] = []
    table: list[tuple[str, ...]] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(Block(BlockType.PARAGRAPH, " ".join(paragraph), page=1))
            paragraph.clear()

    def flush_table() -> None:
        if table:
            blocks.append(Block(BlockType.TABLE, "", page=1, rows=tuple(table)))
            table.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_table()
            continue
        if line.startswith("#"):
            flush_paragraph()
            flush_table()
            level = len(line) - len(line.lstrip("#"))
            blocks.append(Block(BlockType.HEADING, line.lstrip("#").strip(), level=level, page=1))
        elif line.startswith("|"):
            flush_paragraph()
            cells = tuple(c.strip() for c in line.strip("|").split("|"))
            if all(set(c) <= set("-: ") for c in cells):
                continue  # separator row
            table.append(cells)
        elif line.startswith(("- ", "* ")):
            flush_paragraph()
            flush_table()
            blocks.append(Block(BlockType.LIST_ITEM, line[2:].strip(), page=1))
        else:
            flush_table()
            paragraph.append(line)
    flush_paragraph()
    flush_table()
    return ParsedDocument(blocks=tuple(blocks), pages=1)


class FakeParser:
    """Text and Markdown are parsed; binary formats answer with a canned result registered by content hash."""

    def __init__(self) -> None:
        self.canned: dict[str, ParsedDocument] = {}
        self.error: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def register(self, data: bytes, parsed: ParsedDocument) -> None:
        self.canned[hashlib.sha256(data).hexdigest()] = parsed

    def parse(self, path: Path, mime: str, *, max_pages: int | None = None) -> ParsedDocument:
        self.calls.append((path.name, mime))
        if self.error is not None:
            raise self.error
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.canned:
            return self.canned[digest]
        if mime in (TEXT, MARKDOWN):
            return parse_markdown(data.decode("utf-8", errors="replace"))
        return ParsedDocument(blocks=(), pages=None, warnings=("binary content without a canned parse",))


class WordTokenizer:
    def count(self, text: str) -> int:
        return len(text.split())


class FakeEmbedder:
    """Deterministic bag-of-words hashing into `dimension` buckets; cosine similarity reflects shared words."""

    def __init__(self, dimension: int = 32) -> None:
        self._dimension = dimension
        self.model_id = "fake-e5@test"
        self.passages: list[str] = []
        self.queries: list[str] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> list[float]:
        if text.startswith(("query: ", "passage: ")):
            raise ValueError("texts must not be prefixed by the caller")
        vector = [0.0] * self._dimension
        for token in _WORD.findall(text.casefold()):
            vector[zlib.crc32(token.encode()) % self._dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        self.passages.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self._vector(text)
