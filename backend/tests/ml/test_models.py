"""Real models (marker `ml`): e5 tokenizer and embedder, lingua detection, Docling parsing of the corpus fixtures.

Downloads the models into the local cache on first run (`make test-integration`)."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest

from leave_assistant.domain.documents.formats import DOCX, MARKDOWN, PDF, TEXT
from leave_assistant.domain.documents.types import BlockType
from leave_assistant.infrastructure.ml.embedder import E5Embedder
from leave_assistant.infrastructure.ml.language import LinguaLanguageDetector, ScriptLanguageDetector
from leave_assistant.infrastructure.ml.tokenizer import E5Tokenizer
from leave_assistant.infrastructure.parsing.docling_parser import DoclingParser

pytestmark = pytest.mark.ml

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
CORPUS = Path(__file__).resolve().parents[2] / "src/leave_assistant/seed/corpus/files"


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_e5_tokenizer_counts_tokens() -> None:
    tokenizer = E5Tokenizer(MODEL, REVISION)
    assert tokenizer.count("") == 0
    assert 3 <= tokenizer.count("Κανονισμός Αδειών της εταιρείας") <= 20
    assert tokenizer.count("annual leave") < tokenizer.count("annual leave entitlement and carry-over rules")


def test_e5_embedder_is_normalised_cross_lingual_and_prefix_safe() -> None:
    embedder = E5Embedder(MODEL, REVISION, batch_size=8, threads=2)
    assert embedder.model_id == f"{MODEL}@{REVISION}" and embedder.dimension == 384
    vectors = embedder.embed_passages(
        [
            "Up to five unused days of annual leave carry over into the next year and expire on 31 March.",
            "The office printer on the second floor needs a new toner cartridge.",
            "Έως πέντε ημέρες κανονικής άδειας μεταφέρονται στο επόμενο έτος και λήγουν στις 31 Μαρτίου.",
        ]
    )
    assert len(vectors) == 3 and all(len(v) == 384 for v in vectors)
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-3 for v in vectors)
    greek_query = embedder.embed_query("Μεταφέρονται οι μέρες που δεν πήρα;")
    english_query = embedder.embed_query("Can I carry over unused days?")
    # cross-lingual (doc 02 §12): the relevant passage in the *other* language beats an irrelevant one
    assert cosine(greek_query, vectors[0]) > cosine(greek_query, vectors[1]) + 0.05
    assert cosine(english_query, vectors[2]) > cosine(english_query, vectors[1])
    assert cosine(greek_query, vectors[2]) > cosine(greek_query, vectors[1])
    with pytest.raises(ValueError):
        embedder.embed_passages(["passage: already prefixed"])
    with pytest.raises(ValueError):
        embedder.embed_query("query: already prefixed")


def test_language_detectors() -> None:
    lingua = LinguaLanguageDetector()
    script = ScriptLanguageDetector()
    for detector in (lingua, script):
        assert detector.detect("Κάθε εργαζόμενος δικαιούται είκοσι ημέρες άδειας.") == "el"
        assert detector.detect("Every employee is entitled to twenty days of leave.") == "en"
        assert detector.detect("12345 ---") is None
    assert (
        lingua.detect(
            "Η αίτηση (request) υποβάλλεται μέσω του Leave Assistant το αργότερο τρεις ημέρες πριν."
        )
        == "el"
    )


@pytest.fixture(scope="module")
def parser() -> DoclingParser:
    return DoclingParser(ocr_languages=("ell", "eng"), num_threads=2)


def test_docling_parses_the_text_pdf_with_headings_and_tables(parser: DoclingParser) -> None:
    parsed = parser.parse(CORPUS / "aegean/kanonismos-adeion.pdf", PDF)
    headings = [b.text for b in parsed.blocks if b.type is BlockType.HEADING]
    assert parsed.pages and parsed.pages >= 2
    assert any("Μεταφορά υπολοίπου" in h for h in headings), headings
    assert any("Αναρρωτική άδεια" in h for h in headings)
    tables = [b for b in parsed.blocks if b.type is BlockType.TABLE]
    assert tables and any("Κανονική άδεια" in " ".join(" ".join(r) for r in t.rows) for t in tables)
    assert "τριάντα (30) ημέρες" in parsed.text


def test_docling_parses_docx_markdown_and_plain_text(parser: DoclingParser) -> None:
    docx = parser.parse(CORPUS / "aegean/leave-policy-en.docx", DOCX)
    assert any(b.type is BlockType.HEADING and "Carry-over" in b.text for b in docx.blocks)
    assert any(b.type is BlockType.TABLE for b in docx.blocks)
    md = parser.parse(CORPUS / "brightwater/public-holidays-2026.md", MARKDOWN)
    assert any(b.type is BlockType.HEADING for b in md.blocks) and any(
        b.type is BlockType.TABLE for b in md.blocks
    )
    assert any(
        "St Patrick" in " ".join(" ".join(r) for r in b.rows) for b in md.blocks if b.type is BlockType.TABLE
    )
    txt = parser.parse(CORPUS / "brightwater/release-freeze-dates.txt", TEXT)
    assert (
        txt.pages == 1
        and all(b.type is BlockType.PARAGRAPH for b in txt.blocks)
        and "Spring release freeze" in txt.text
    )


@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract CLI not installed on this host (runs in the container: make test-ocr)",
)
def test_docling_ocr_reads_the_scanned_greek_pdf(parser: DoclingParser) -> None:
    parsed = parser.parse(CORPUS / "aegean/diadikasia-anarrotikis.pdf", PDF)
    assert "Αναρρωτικ" in parsed.text and "Ανθρώπινου Δυναμικού" in parsed.text


def test_static_prompts_fit_their_budgets_with_the_real_tokenizer() -> None:
    from leave_assistant.agent import prompts

    tokenizer = E5Tokenizer(MODEL, REVISION)
    for name in ("system", "classify", "condense", "generate", "summarise"):
        file = prompts.load(name)
        body = prompts.system_prompt("el", hardened=True) if name == "system" else file.body
        assert tokenizer.count(body) * 1.15 <= file.budget, (name, tokenizer.count(body))


def test_both_reranker_candidates_score_relevance() -> None:
    from leave_assistant.infrastructure.ml.reranker import CrossEncoderReranker

    passages = [
        "Κάθε εργαζόμενος δικαιούται είκοσι εργάσιμες ημέρες κανονικής άδειας ανά έτος.",
        "Ο εκτυπωτής του δεύτερου ορόφου χρειάζεται νέο toner.",
    ]
    for model in (
        "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    ):  # candidate A is measured in the container (memory)
        scores = CrossEncoderReranker(model).score("Πόσες μέρες άδειας δικαιούμαι;", passages)
        assert 0.0 <= min(scores) and max(scores) <= 1.0 and scores[0] > scores[1] + 0.2, scores
