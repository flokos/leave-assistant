"""Structural anomalies (doc 08 §8.2): active content inside PDFs, macros inside DOCX — hard flags."""

from __future__ import annotations

import zipfile
from pathlib import Path

# `/OpenAction` and `/AA` are only containers: ordinary PDFs use `/OpenAction` for the initial view (every fpdf2 file
# does). What makes them dangerous is the action they name, and those action types are the markers below.
# Byte scan only: markers inside compressed object streams are not seen (doc 13 R-29).
PDF_ACTIVE_MARKERS: tuple[tuple[bytes, str], ...] = (
    (b"/JavaScript", "pdf_javascript"),
    (b"/JS ", "pdf_javascript"),
    (b"/JS<", "pdf_javascript"),
    (b"/JS(", "pdf_javascript"),
    (b"/Launch", "pdf_launch_action"),
    (b"/EmbeddedFile", "pdf_embedded_file"),
    (b"/SubmitForm", "pdf_submit_form"),
)


def pdf_structural_flags(data: bytes) -> list[str]:
    found: list[str] = []
    for marker, flag in PDF_ACTIVE_MARKERS:
        if marker in data and flag not in found:
            found.append(flag)
    return found


def docx_has_macros(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return any(name.lower().endswith("vbaproject.bin") for name in archive.namelist())
    except zipfile.BadZipFile:
        return False
