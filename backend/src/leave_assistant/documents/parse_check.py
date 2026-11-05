"""Parse one file with the real Docling parser and print its structure (`make test-ocr` runs it in the container)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from leave_assistant.config import get_settings
from leave_assistant.domain.documents.formats import EXTENSIONS
from leave_assistant.infrastructure.parsing.docling_parser import DoclingParser


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("usage: python -m leave_assistant.documents.parse_check <file>\n")
        return 2
    path = Path(argv[0])
    mime = EXTENSIONS[path.suffix.lstrip(".").lower()]
    settings = get_settings()
    parser = DoclingParser(
        ocr_languages=tuple(settings.ocr_language_list),
        artifacts_path=settings.docling_artifacts_dir,
        document_timeout_seconds=settings.document_process_timeout_seconds,
        num_threads=settings.worker_torch_threads,
    )
    started = time.monotonic()
    parsed = parser.parse(path, mime, max_pages=settings.max_document_pages)
    elapsed = time.monotonic() - started
    headings = [b.text for b in parsed.blocks if b.type.value == "heading"]
    tables = sum(1 for b in parsed.blocks if b.type.value == "table")
    text = parsed.text
    sys.stdout.write(
        f"file: {path.name} ({mime})\npages: {parsed.pages}  blocks: {len(parsed.blocks)}  headings: {len(headings)}  "
        f"tables: {tables}  characters: {len(text)}  seconds: {elapsed:.1f}\n"
    )
    for heading in headings[:12]:
        sys.stdout.write(f"  # {heading}\n")
    sys.stdout.write("sample: " + " ".join(text.split())[:400] + "\n")
    if parsed.warnings:
        sys.stdout.write("warnings: " + "; ".join(parsed.warnings) + "\n")
    return 0 if text.strip() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
