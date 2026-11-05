"""DoclingParser (ADR-0015): PDF (layout + tables + Tesseract CLI OCR for bitmap pages), DOCX and Markdown.

One converter per process, built lazily (models load on first use). TXT never goes through Docling.
"""

from __future__ import annotations

import shutil
from functools import cached_property
from pathlib import Path
from typing import Any

from leave_assistant.domain.documents.formats import DOCX, MARKDOWN, PDF, TEXT
from leave_assistant.domain.documents.types import ParsedDocument
from leave_assistant.infrastructure.observability.logging import get_logger
from leave_assistant.infrastructure.parsing.normalise import normalise, plain_text_blocks

log = get_logger("parsing")


class DoclingParser:
    def __init__(
        self,
        *,
        ocr_languages: tuple[str, ...] = ("ell", "eng"),
        artifacts_path: str | None = None,
        document_timeout_seconds: float | None = None,
        num_threads: int = 4,
        tesseract_cmd: str = "tesseract",
    ) -> None:
        self._ocr_languages = list(ocr_languages)
        self._artifacts_path = artifacts_path
        self._timeout = document_timeout_seconds
        self._threads = num_threads
        self._tesseract_cmd = tesseract_cmd

    @property
    def ocr_available(self) -> bool:
        return shutil.which(self._tesseract_cmd) is not None

    @cached_property
    def _converter(self) -> Any:
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        ocr = self.ocr_available
        if not ocr:  # the image has tesseract; a developer host may not — text layers still parse
            log.warning("parsing.ocr.unavailable", tesseract_cmd=self._tesseract_cmd)
        options = PdfPipelineOptions(
            do_ocr=ocr,
            do_table_structure=True,
            ocr_options=TesseractCliOcrOptions(lang=self._ocr_languages, tesseract_cmd=self._tesseract_cmd),
            artifacts_path=self._artifacts_path,
            document_timeout=self._timeout,
            accelerator_options=AcceleratorOptions(num_threads=self._threads, device="cpu"),
        )
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )

    def warm_up(self) -> None:
        _ = self._converter

    def parse(self, path: Path, mime: str, *, max_pages: int | None = None) -> ParsedDocument:
        if mime == TEXT:
            return plain_text_blocks(path.read_text(encoding="utf-8", errors="replace"))
        if mime not in (PDF, DOCX, MARKDOWN):
            raise ValueError(f"unsupported mime for parsing: {mime}")
        from docling.datamodel.base_models import ConversionStatus

        kwargs: dict[str, Any] = {"raises_on_error": False}
        if max_pages is not None:
            kwargs["max_num_pages"] = max_pages
        result = self._converter.convert(path, **kwargs)
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            errors = "; ".join(str(getattr(e, "error_message", e)) for e in (result.errors or [])[:3])
            raise RuntimeError(f"docling conversion {result.status.value}: {errors or 'no details'}")
        parsed = normalise(result.document)
        warnings = list(parsed.warnings)
        if result.status is ConversionStatus.PARTIAL_SUCCESS:
            warnings.append("partial conversion: some pages could not be processed")
        return ParsedDocument(blocks=parsed.blocks, pages=parsed.pages, warnings=tuple(warnings))
