# ADR-0015 — Docling parsing with Tesseract CLI OCR and structure-aware chunking

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0013, ADR-0016

## Context
Brief §7.1–§7.4: PDF/DOCX/TXT/MD plus scanned PDFs via OCR; Docling for parsing preserving headings, sections,
tables and metadata; hybrid structure-aware chunking rather than naive fixed-size splitting.

## Problem
Extract reliable structure from heterogeneous documents on CPU, support Greek OCR, and chunk so that retrieved
passages carry their heading context and tables stay coherent.

## Decision
Docling 2.147 (`docling-slim[standard]`) in the worker with `TesseractCliOcrOptions(lang=["ell","eng"])` (system
`tesseract-ocr` + `-ell` + `-eng` packages; no `tesserocr` build), table structure enabled, per-document time and page
limits. Blocks are normalised into a heading tree; chunking targets 300 tokens (max 450, min 60, 40-token overlap
within sections, tables whole or split by rows with repeated header), using the e5 tokenizer; each chunk embeds with a
`"{title} > {heading path}"` context header.

## Alternatives considered
- EasyOCR (Docling default): good Greek support, but heavier torch models in the worker; Tesseract is smaller and
  deterministic. RapidOCR: weak Greek coverage.
- Unstructured/PyMuPDF: less structure fidelity for tables; Docling is the brief's choice.
- Fixed-size chunking: explicitly rejected by the brief.

## Advantages
Structure-preserving parsing; OCR only where needed; chunks carry provenance (heading path, pages) for citations.

## Disadvantages
Docling pulls a large dependency tree (and pins `transformers<4.58`, constraining the ML stack — ADR-0026); CPU parse
times of seconds per page.

## Security implications
Parsing runs in the worker with limits; extracted text feeds the injection and PII scans before indexing; supporting
documents never enter this pipeline.

## Operational complexity
Model downloads on first use (~500 MB); Tesseract language packs in the image.

## Consequences
Chunking rules are versioned (`chunking_rules_version`) so that rule changes trigger re-indexing generations.
