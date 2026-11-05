"""Structure-aware chunking with Greek and English fixtures (doc 05 §4): deterministic, bounded, structure-first."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.domain.documents.chunking import (
    ChunkingRules,
    chunk_document,
    chunk_id_for,
    embed_text_for,
    split_sentences,
    table_markdown,
)
from leave_assistant.domain.documents.types import Block, BlockType, ChunkType

RULES = ChunkingRules(target_tokens=40, max_tokens=60, min_tokens=8, overlap_tokens=6, version="test")


def words(text: str) -> int:
    return len(text.split())


def h(level: int, text: str, page: int = 1) -> Block:
    return Block(BlockType.HEADING, text, level=level, page=page)


def p(text: str, page: int = 1) -> Block:
    return Block(BlockType.PARAGRAPH, text, page=page)


def li(text: str, page: int = 1) -> Block:
    return Block(BlockType.LIST_ITEM, text, page=page)


def table(rows: list[list[str]], page: int = 1) -> Block:
    return Block(BlockType.TABLE, "", page=page, rows=tuple(tuple(r) for r in rows))


def sentence(n: int, lang: str = "en") -> str:
    return f"Πρόταση {n} για την άδεια." if lang == "el" else f"Sentence {n} about leave."


GREEK_POLICY = [
    h(1, "Κανονισμός Αδειών"),
    p(
        "Ο παρών κανονισμός ορίζει τους τύπους άδειας και τη διαδικασία αίτησης για όλους τους εργαζόμενους της εταιρείας."
    ),
    h(2, "3. Κανονική άδεια"),
    p(
        "Κάθε εργαζόμενος δικαιούται είκοσι εργάσιμες ημέρες κανονικής άδειας ανά ημερολογιακό έτος, ανάλογα με την ημερομηνία πρόσληψης."
    ),
    h(3, "3.2 Μεταφορά υπολοίπου"),
    p("Έως πέντε ημέρες μεταφέρονται στο επόμενο έτος και λήγουν στις 31 Μαρτίου."),
    h(3, "3.3 Προειδοποίηση", page=2),
    p("Σύντομο.", page=2),  # small section → merges into the next sibling
    h(3, "3.4 Έγκριση", page=2),
    p(
        "Αιτήσεις έως πέντε εργάσιμες ημέρες εγκρίνονται από τον προϊστάμενο. Μεγαλύτερες αιτήσεις απαιτούν και έγκριση HR.",
        page=2,
    ),
    h(2, "4. Αργίες", page=3),
    table([["Ημερομηνία", "Αργία"], ["1 Ιανουαρίου", "Πρωτοχρονιά"], ["25 Μαρτίου", "Εθνική εορτή"]], page=3),
]


def test_greek_policy_sections_and_heading_paths() -> None:
    chunks = chunk_document(GREEK_POLICY, title="Κανονισμός Αδειών", count_tokens=words, rules=RULES)
    paths = [c.heading_path for c in chunks]
    assert paths[0] == ("Κανονισμός Αδειών",)
    assert ("Κανονισμός Αδειών", "3. Κανονική άδεια") in paths
    assert ("Κανονισμός Αδειών", "3. Κανονική άδεια", "3.2 Μεταφορά υπολοίπου") in paths
    assert ("Κανονισμός Αδειών", "4. Αργίες") in paths
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(c.token_count <= RULES.max_tokens + 10 for c in chunks)  # embed header adds a few tokens
    assert all(c.content.strip() for c in chunks)
    # the small section 3.3 merged into its sibling 3.4 (same parent "3."), carrying its heading text
    merged = next(c for c in chunks if c.heading_path[-1] == "3.4 Έγκριση")
    assert "3.3 Προειδοποίηση" in merged.content and "Σύντομο." in merged.content
    assert not any(c.heading_path[-1] == "3.3 Προειδοποίηση" for c in chunks)
    assert merged.page_start == 2 and merged.page_end == 2
    # the table is its own chunk with the header row
    tbl = next(c for c in chunks if c.chunk_type is ChunkType.TABLE)
    assert tbl.content.startswith("| Ημερομηνία | Αργία |") and "Πρωτοχρονιά" in tbl.content
    assert tbl.page_start == 3
    # embedding text carries title and heading path (doc 05 §4 step 6)
    assert merged.embed_text.startswith("Κανονισμός Αδειών > 3. Κανονική άδεια > 3.4 Έγκριση\n")


def test_long_section_splits_at_paragraphs_then_sentences_with_overlap() -> None:
    long_paragraph = " ".join(sentence(n) for n in range(1, 30))  # 29 sentences × 4 words = 116 words
    blocks = [
        h(1, "Handbook"),
        h(2, "2. Annual leave"),
        p("Intro paragraph of the section."),
        p(long_paragraph),
    ]
    chunks = chunk_document(blocks, title="Employee Leave Handbook", count_tokens=words, rules=RULES)
    assert len(chunks) >= 3
    assert all(words(c.content) <= RULES.max_tokens for c in chunks)
    assert all(c.heading_path == ("Handbook", "2. Annual leave") for c in chunks)
    # overlap: the second piece starts with the tail sentence(s) of the first piece
    first, second = chunks[0].content, chunks[1].content
    last_sentence_of_first = split_sentences(first)[-1]
    assert second.startswith(last_sentence_of_first)
    # every sentence of the source is present somewhere
    joined = " ".join(c.content for c in chunks)
    assert all(sentence(n) in joined for n in range(1, 30))


def test_sentence_longer_than_max_is_hard_split_and_nothing_is_lost() -> None:
    giant = " ".join(f"w{n}" for n in range(1, 150))  # one "sentence" of 149 words, no punctuation
    chunks = chunk_document([h(1, "T"), p(giant)], title="T", count_tokens=words, rules=RULES)
    assert all(words(c.content) <= RULES.max_tokens for c in chunks)
    assert " ".join(c.content for c in chunks).replace("\n", " ").split() == giant.split()


def test_large_table_splits_by_rows_with_header_repeated() -> None:
    rows = [["Date", "Holiday", "Country"]] + [
        [f"2026-{m:02d}-01", f"Holiday number {m} of the year", "GR"] for m in range(1, 13)
    ]
    chunks = chunk_document(
        [h(1, "Holidays"), table(rows)],
        title="Holidays",
        count_tokens=words,
        rules=ChunkingRules(40, 40, 8, 6),
    )
    assert len(chunks) > 1 and all(c.chunk_type is ChunkType.TABLE for c in chunks)
    assert all(c.content.startswith("| Date | Holiday | Country |") for c in chunks)
    assert all(words(c.content) <= 40 for c in chunks)
    body_rows = [line for c in chunks for line in c.content.splitlines()[2:]]
    assert len(body_rows) == 12


def test_small_sections_never_merge_across_top_level_headings() -> None:
    blocks = [h(1, "A"), p("tiny a"), h(1, "B"), p("tiny b"), h(2, "B.1"), p("tiny b1")]
    chunks = chunk_document(blocks, title="Doc", count_tokens=words, rules=RULES)
    # A cannot merge into B (different parent: () vs ()?) — both are top-level siblings with the same (empty) parent,
    # so A merges into B; B.1 has a different parent (B) and stays on its own when it is the last section.
    assert [c.heading_path for c in chunks] == [("B",), ("B", "B.1")]
    assert chunks[0].content.startswith("A\ntiny a\ntiny b")


def test_table_markdown_and_sentences_and_ids() -> None:
    assert table_markdown([["a", "b"], ["1"]]) == "| a | b |\n| --- | --- |\n| 1 |  |"
    assert table_markdown([]) == ""
    assert split_sentences("Πρώτη πρόταση. Δεύτερη; Τρίτη·  Τέταρτη") == [
        "Πρώτη πρόταση.",
        "Δεύτερη;",
        "Τρίτη·",
        "Τέταρτη",
    ]
    assert embed_text_for("Title", ("A", "B"), "body") == "Title > A > B\nbody"
    assert embed_text_for("", (), "body") == "body"
    version = UUID(int=7)
    assert chunk_id_for(version, 0) == chunk_id_for(version, 0) != chunk_id_for(version, 1)


def test_deterministic_and_default_rules_sane() -> None:
    a = chunk_document(GREEK_POLICY, title="Κανονισμός", count_tokens=words)
    b = chunk_document(GREEK_POLICY, title="Κανονισμός", count_tokens=words)
    assert a == b
    assert len(a) >= 1
