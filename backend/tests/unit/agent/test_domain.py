"""Citation verification, streaming re-identification, budgets."""

from __future__ import annotations

from leave_assistant.domain.agent.citations import markers_in, renumber, verify
from leave_assistant.domain.agent.streaming import BufferedReidentifier, reidentify_all

NAMES = {"PERSON_0001": "Μαρία Οικονόμου", "EMAIL_0002": "[email]"}


def resolve(placeholder: str) -> str:
    return NAMES.get(placeholder, "[unavailable]")


def test_verify_accepts_grounded_answers_and_rejects_bad_citations() -> None:
    assert verify("Twenty days per year [1], carry-over up to five [2].", [1, 2], 3).ok
    unknown = verify("Twenty days [4].", [4], 3)
    assert not unknown.ok and "unknown sources [4]" in unknown.problems[0] and unknown.cited == ()
    assert not verify("Twenty days per year.", [], 3).ok  # no inline citation
    assert "contains a URL" in verify("See https://x.example [1]", [1], 1).problems
    assert "declared citations not used in the text" in verify("Twenty days [1].", [1, 2], 2).problems
    assert markers_in("a [2] b [1] c [2]") == [2, 1]
    assert renumber("a [3] b [1] c [3]", [3, 1]) == "a [1] b [2] c [1]"


def test_buffered_reidentifier_never_splits_a_placeholder() -> None:
    text = "Η PERSON_0001 εγκρίνει. Γράψτε στο EMAIL_0002 ή ρωτήστε τον PERSON_0099. HR Team."
    for size in (1, 2, 3, 5, 7, 11):
        stream = BufferedReidentifier(resolve)
        out = "".join(stream.feed(text[i : i + size]) for i in range(0, len(text), size)) + stream.flush()
        assert out == reidentify_all(text, resolve), size
    assert "Μαρία Οικονόμου" in reidentify_all(text, resolve) and "[unavailable]" in reidentify_all(
        text, resolve
    )
    stream = BufferedReidentifier(resolve)
    assert stream.feed("Contact PERS") == "Contact "  # held: may become a placeholder
    assert stream.feed("ON_00") == ""
    assert stream.feed("01 now") == "Μαρία Οικονόμου now"
    assert stream.feed("HRX") == "" and stream.feed(" done") == "HRX done"  # released once disambiguated


def test_declared_citations_are_attached_only_when_valid() -> None:
    from leave_assistant.domain.agent.citations import attach_declared

    assert attach_declared("Είκοσι ημέρες τον χρόνο.", [1, 2], 3) == "Είκοσι ημέρες τον χρόνο. [1][2]"
    assert attach_declared("Already cited [1].", [1], 3) == "Already cited [1]."
    assert (
        attach_declared("Invented source.", [4], 3) == "Invented source."
    )  # stays invalid → verifier rejects
    assert attach_declared("Nothing declared.", [], 3) == "Nothing declared."


def test_split_grouped_markers() -> None:
    # Gemini writes "[1, 2, 3]" although the prompt asks for one source per bracket (live smoke, worklog M6)
    from leave_assistant.domain.agent.citations import split_grouped

    assert split_grouped("Five days carry over [1, 2, 3].") == "Five days carry over [1][2][3]."
    assert split_grouped("Μέχρι 31 Μαρτίου [2;3] και [1].") == "Μέχρι 31 Μαρτίου [2][3] και [1]."
    assert split_grouped("Article [12] stays.") == "Article [12] stays."
    assert verify(split_grouped("Five days [1, 2]."), [1, 2], 2).ok


def test_truncated_structured_output_is_invalid() -> None:
    # a JSON object cut off by the token cap may still parse: it must never be accepted as an answer
    import pytest
    from langchain_core.messages import AIMessage

    from leave_assistant.agent.state import AnswerOutput
    from leave_assistant.domain.agent.types import ProviderError, ProviderErrorKind
    from leave_assistant.infrastructure.llm.common import unpack

    raw = AIMessage("{}", response_metadata={"finish_reason": "MAX_TOKENS"})
    result = {
        "raw": raw,
        "parsed": AnswerOutput(answer="Yes, up to five unused days carry over into"),
        "parsing_error": None,
    }
    with pytest.raises(ProviderError) as caught:
        unpack(result, AnswerOutput, "gemini", "gemini-3.6-flash")
    assert caught.value.kind is ProviderErrorKind.INVALID_OUTPUT
