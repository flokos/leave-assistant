"""Render the seed corpus sources (Markdown) into the committed PDF / DOCX / TXT / MD / scanned-PDF files.

Run once on a developer machine (`uv run --group corpus python scripts/build_corpus.py`); the output under
`src/leave_assistant/seed/corpus/files/` is committed and shipped in the backend image. fpdf2 (LGPL) is a build-time
tool only. Fonts: Noto Sans (Greek + Latin) from the host, DejaVu Sans as fallback.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leave_assistant.seed.data import COMPANIES, SeedDocument  # noqa: E402

SOURCES = ROOT / "src/leave_assistant/seed/corpus/sources"
FILES = ROOT / "src/leave_assistant/seed/corpus/files"
FONT_DIRS = (Path("/usr/share/fonts/truetype/noto"), Path("/usr/share/fonts/truetype/dejavu"))
FONTS = {
    "regular": ("NotoSans-Regular.ttf", "DejaVuSans.ttf"),
    "bold": ("NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf"),
}


def font(kind: str) -> Path:
    for directory in FONT_DIRS:
        for name in FONTS[kind]:
            candidate = directory / name
            if candidate.exists():
                return candidate
    raise SystemExit("no Unicode TTF font found (install fonts-noto-core or fonts-dejavu)")


@dataclass
class Element:
    kind: str  # heading | paragraph | list | table
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)


def parse(source: str) -> list[Element]:
    elements: list[Element] = []
    paragraph: list[str] = []
    table: list[list[str]] = []

    def flush() -> None:
        if paragraph:
            elements.append(Element("paragraph", " ".join(paragraph)))
            paragraph.clear()
        if table:
            elements.append(Element("table", rows=[list(r) for r in table]))
            table.clear()

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            elements.append(Element("heading", line.lstrip("#").strip(), level))
        elif line.startswith("|"):
            if paragraph:
                flush()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            table.append(cells)
        elif line.startswith(("- ", "* ")):
            flush()
            elements.append(Element("list", line[2:].strip()))
        else:
            if table:
                flush()
            paragraph.append(line)
    flush()
    return elements


def render_pdf(elements: list[Element], target: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.add_font("Body", "", str(font("regular")))
    pdf.add_font("Body", "B", str(font("bold")))
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for element in elements:
        if element.kind == "heading":
            size = {1: 18, 2: 14}.get(element.level, 12)
            pdf.set_font("Body", "B", size)
            pdf.ln(4 if element.level > 1 else 2)
            pdf.multi_cell(width, size * 0.6, element.text)
            pdf.ln(2)
        elif element.kind == "paragraph":
            pdf.set_font("Body", "", 10.5)
            pdf.multi_cell(width, 5.6, element.text)
            pdf.ln(2.5)
        elif element.kind == "list":
            pdf.set_font("Body", "", 10.5)
            pdf.multi_cell(width, 5.6, "•  " + element.text)
            pdf.ln(1)
        elif element.kind == "table":
            pdf.set_font("Body", "", 9.5)
            with pdf.table(text_align="LEFT", width=width, col_widths=None) as table:
                for index, row in enumerate(element.rows):
                    line = table.row()
                    for cell in row:
                        line.cell(cell)
                    if index == 0:
                        pdf.set_font("Body", "", 9.5)
            pdf.ln(3)
    target.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(target))


def render_docx(elements: list[Element], target: Path) -> None:
    from docx import Document

    document = Document()
    for element in elements:
        if element.kind == "heading":
            document.add_heading(element.text, level=min(element.level, 4))
        elif element.kind == "paragraph":
            document.add_paragraph(element.text)
        elif element.kind == "list":
            document.add_paragraph(element.text, style="List Bullet")
        elif element.kind == "table":
            columns = max(len(r) for r in element.rows)
            table = document.add_table(rows=0, cols=columns)
            table.style = "Table Grid"
            for row in element.rows:
                cells = table.add_row().cells
                for index, value in enumerate(row):
                    cells[index].text = value
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))


def render_txt(elements: list[Element], target: Path) -> None:
    lines: list[str] = []
    for element in elements:
        if element.kind == "heading":
            lines.append(element.text.upper() if element.level == 1 else element.text)
        elif element.kind == "paragraph":
            lines.append(element.text)
        elif element.kind == "list":
            lines.append("- " + element.text)
        elif element.kind == "table":
            lines.extend(" | ".join(r) for r in element.rows)
        lines.append("")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def render_scan(elements: list[Element], target: Path) -> None:
    """Typeset onto page images (200 dpi A4) and save an image-only PDF — the OCR path (FR-DOC-01)."""
    from PIL import Image, ImageDraw, ImageFont

    width, height, margin = 1654, 2339, 140
    regular = ImageFont.truetype(str(font("regular")), 34)
    bold = ImageFont.truetype(str(font("bold")), 44)
    pages: list[Image.Image] = []

    def new_page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
        image = Image.new("L", (width, height), 250)
        return image, ImageDraw.Draw(image)

    def wrap(text: str, draw: ImageDraw.ImageDraw, fnt: ImageFont.FreeTypeFont) -> list[str]:
        words, lines, current = text.split(), [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=fnt) > width - 2 * margin:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    image, draw = new_page()
    y = margin
    for element in elements:
        fnt = bold if element.kind == "heading" else regular
        lines = (
            wrap(element.text, draw, fnt)
            if element.kind != "table"
            else [" | ".join(r) for r in element.rows]
        )
        if element.kind == "list":
            lines = wrap("- " + element.text, draw, regular)
        step = 60 if element.kind == "heading" else 48
        for line in lines:
            if y + step > height - margin:
                pages.append(image)
                image, draw = new_page()
                y = margin
            draw.text((margin, y), line, fill=20, font=fnt)
            y += step
        y += 30
    pages.append(image)
    target.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(str(target), "PDF", resolution=200.0, save_all=True, append_images=pages[1:])


RENDERERS = {"pdf": render_pdf, "docx": render_docx, "txt": render_txt, "scan": render_scan}


def build(spec: SeedDocument, tenant_slug: str, stem: str) -> Path:
    source = (SOURCES / tenant_slug / f"{stem}.md").read_text(encoding="utf-8")
    target = FILES / tenant_slug / f"{stem}.{spec.extension}"
    if spec.format == "md":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    else:
        RENDERERS[spec.format](parse(source), target)
    return target


SUPPORTING = ROOT / "src/leave_assistant/seed/corpus/supporting"


def render_medical_note(target: Path) -> None:
    """A fictional Greek medical note (PNG) attached to a seeded sick request — M5 supporting document (doc 08 §9)."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1240, 880
    image = Image.new("L", (width, height), 250)
    draw = ImageDraw.Draw(image)
    bold = ImageFont.truetype(str(font("bold")), 40)
    regular = ImageFont.truetype(str(font("regular")), 30)
    lines = [
        ("ΙΑΤΡΙΚΗ ΒΕΒΑΙΩΣΗ (ΔΕΙΓΜΑ — ΠΛΑΣΜΑΤΙΚΑ ΣΤΟΙΧΕΙΑ)", bold),
        ("Ιατρείο Γενικής Ιατρικής «Υγεία» · Λεωφ. Παραδείγματος 12, Πειραιάς", regular),
        ("", regular),
        ("Βεβαιώνεται ότι η/ο εργαζόμενη/ος με αρ. μητρώου AEG-004", regular),
        ("εξετάστηκε στις 2 Νοεμβρίου 2026 και χρήζει αναρρωτικής άδειας", regular),
        ("δύο (2) ημερών, από 2/11/2026 έως 3/11/2026, λόγω ίωσης.", regular),
        ("", regular),
        ("Ο ιατρός: Δρ. Π. Παράδειγμα · ΑΜ ΙΣΑ 000000 (πλασματικό)", regular),
        ("Σφραγίδα / Υπογραφή", regular),
        ("", regular),
        ("Το έγγραφο δημιουργήθηκε για τα δεδομένα επίδειξης του Leave Assistant.", regular),
    ]
    y = 70
    for text, fnt in lines:
        draw.text((70, y), text, fill=25, font=fnt)
        y += 62 if fnt is bold else 50
    draw.rectangle([40, 40, width - 40, height - 40], outline=90, width=3)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(target), "PNG", optimize=True)


def main() -> int:
    for company in COMPANIES:
        for spec in company.documents:
            for stem in (*spec.earlier_versions, spec.slug):
                target = build(spec, company.slug, stem)
                sys.stdout.write(f"{target.relative_to(ROOT)}  {target.stat().st_size:,} bytes\n")
    note = SUPPORTING / "medical-note.png"
    render_medical_note(note)
    sys.stdout.write(f"{note.relative_to(ROOT)}  {note.stat().st_size:,} bytes\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
