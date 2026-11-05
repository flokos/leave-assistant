"""Upload validation: magic bytes decide; extensions only disambiguate text; spoofed files are rejected."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from leave_assistant.domain.documents.formats import (
    DOCX,
    MARKDOWN,
    PDF,
    TEXT,
    TooLarge,
    UnsupportedType,
    check_size,
    sanitize_filename,
    sniff,
    verify_docx,
)


def docx_bytes(with_document: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if with_document:
            archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("head", "filename", "declared", "mime", "ext"),
    [
        (b"%PDF-1.7\n%\xe2\xe3", "policy.pdf", "application/pdf", PDF, "pdf"),
        (b"%PDF-1.4", "policy", None, PDF, "pdf"),
        (docx_bytes()[:4096], "handbook.docx", None, DOCX, "docx"),
        ("# Αργίες 2026\n\n- 1 Ιανουαρίου".encode(), "holidays.md", None, MARKDOWN, "md"),
        (b"Release freeze: 15-31 December", "notice.txt", None, TEXT, "txt"),
        (b"plain words", "notes", "text/markdown; charset=utf-8", MARKDOWN, "md"),
        (b"plain words", "notes", None, TEXT, "txt"),
        ("Ελληνικό κείμενο".encode()[:-1], "cut.txt", None, TEXT, "txt"),  # multibyte cut at the boundary
    ],
)
def test_sniff_accepts_supported_types(
    head: bytes, filename: str, declared: str | None, mime: str, ext: str
) -> None:
    fmt = sniff(head, filename=filename, declared_mime=declared)
    assert (fmt.mime, fmt.extension) == (mime, ext)


@pytest.mark.parametrize(
    ("head", "filename"),
    [
        (b"%PDF-1.7", "policy.txt"),  # spoofed extension
        (b"GIF89a\x00\x00", "image.gif"),
        (b"\x89PNG\r\n\x1a\n", "scan.pdf"),
        (b"text with \x00 NUL", "notes.txt"),
        (b"PK\x03\x04", "archive.zip"),
        (b"", "empty.txt"),
    ],
)
def test_sniff_rejects_unsupported_or_spoofed(head: bytes, filename: str) -> None:
    with pytest.raises(UnsupportedType) as info:
        sniff(head, filename=filename)
    assert info.value.code == "document/unsupported-type"


def test_verify_docx_requires_word_document(tmp_path: Path) -> None:
    good = tmp_path / "good.docx"
    good.write_bytes(docx_bytes())
    verify_docx(good)
    bad = tmp_path / "bad.docx"
    bad.write_bytes(docx_bytes(with_document=False))
    with pytest.raises(UnsupportedType):
        verify_docx(bad)
    garbage = tmp_path / "garbage.docx"
    garbage.write_bytes(b"PK\x03\x04 not a zip")
    with pytest.raises(UnsupportedType):
        verify_docx(garbage)


def test_size_limits() -> None:
    check_size(10, max_mb=25)
    with pytest.raises(TooLarge) as info:
        check_size(25 * 1024 * 1024 + 1, max_mb=25)
    assert info.value.code == "document/too-large" and info.value.params == {"max_mb": 25}
    with pytest.raises(UnsupportedType):
        check_size(0, max_mb=25)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Κανονισμός Αδειών.pdf", "Κανονισμός Αδειών.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\policy.docx", "policy.docx"),
        ("weird\x00name?.md", "weird_name_.md"),
        ("", "document.pdf"),
        (None, "document.pdf"),
        ("a" * 300 + ".txt", "a" * 196 + ".txt"),
    ],
)
def test_sanitize_filename(name: str | None, expected: str) -> None:
    assert sanitize_filename(name, extension="pdf") == expected
