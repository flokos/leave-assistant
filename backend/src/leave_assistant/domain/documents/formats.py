"""Upload validation (doc 05 §3 "validate"): allowed types by magic bytes, size limits, safe file names."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from leave_assistant.domain.errors import DomainError

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT = "text/plain"
MARKDOWN = "text/markdown"

ALLOWED_MIMES: frozenset[str] = frozenset({PDF, DOCX, TEXT, MARKDOWN})
EXTENSIONS: dict[str, str] = {"pdf": PDF, "docx": DOCX, "txt": TEXT, "md": MARKDOWN, "markdown": MARKDOWN}
MIME_EXTENSION: dict[str, str] = {PDF: "pdf", DOCX: "docx", TEXT: "txt", MARKDOWN: "md"}
HEAD_BYTES = 4096
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")


class UnsupportedType(DomainError):
    code = "document/unsupported-type"


class TooLarge(DomainError):
    code = "document/too-large"


@dataclass(frozen=True, slots=True)
class FileFormat:
    mime: str
    extension: str


def sanitize_filename(name: str | None, *, extension: str) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _UNSAFE.sub("_", base).strip(" ._")
    if not base:
        base = f"document.{extension}"
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 200 - len(ext) - 1] + dot + ext) if dot else base[:200]
    return base


def check_size(size_bytes: int, *, max_mb: int) -> None:
    if size_bytes <= 0:
        raise UnsupportedType("the uploaded file is empty")
    if size_bytes > max_mb * 1024 * 1024:
        raise TooLarge(f"the file exceeds the {max_mb} MB limit", params={"max_mb": max_mb})


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # a multibyte sequence cut at the sampling boundary is fine; anything else is not UTF-8 text
        return exc.start >= len(head) - 3
    return True


def sniff(head: bytes, *, filename: str | None, declared_mime: str | None = None) -> FileFormat:
    """Decide the format from magic bytes; the extension (then the declared type) only disambiguates text."""
    extension = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    expected = EXTENSIONS.get(extension)
    if not head.strip():
        raise UnsupportedType("the uploaded file is empty", params={"filename": filename or ""})
    if head.startswith(b"%PDF-"):
        detected = PDF
    elif head.startswith(b"PK\x03\x04"):
        # a ZIP container is only accepted as DOCX when it is presented as one (`verify_docx` then checks inside)
        detected = DOCX if expected in (DOCX, None) and extension in ("docx", "") else ""
    elif _looks_like_text(head):
        if expected in (TEXT, MARKDOWN):
            detected = expected
        elif (declared_mime or "").split(";")[0].strip() in (TEXT, MARKDOWN):
            detected = (declared_mime or "").split(";")[0].strip()
        else:
            detected = (
                TEXT if extension in ("", "txt") else MARKDOWN if extension in ("md", "markdown") else ""
            )
    else:
        detected = ""
    if detected not in ALLOWED_MIMES:
        raise UnsupportedType(
            "unsupported file type; allowed: PDF, DOCX, TXT, Markdown",
            params={"filename": filename or ""},
        )
    if expected is not None and expected != detected and not {expected, detected} <= {TEXT, MARKDOWN}:
        raise UnsupportedType(
            f"the file content does not match its extension .{extension}",
            params={"filename": filename or "", "detected": detected},
        )
    return FileFormat(mime=detected, extension=MIME_EXTENSION[detected])


def verify_docx(path: Path) -> None:
    """A DOCX is a ZIP container with `word/document.xml` (magic bytes alone only prove ZIP)."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise UnsupportedType("the file is not a valid DOCX container") from exc
    if "word/document.xml" not in names:
        raise UnsupportedType("the ZIP container is not a Word document")
