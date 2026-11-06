"""Sample → RAGAS and dataset item → Langfuse conversions (no network)."""

from __future__ import annotations

from leave_eval.common import dataset_items, scorable
from leave_eval.langfuse_sync import item_payload
from leave_eval.ragas_run import bootstrap, to_sample

SAMPLE = {
    "id": "S02",
    "question": "How many days of annual leave do I get?",
    "question_llm_safe": "How many days of annual leave do I get?",
    "answer_llm_safe": "Twenty (20) working days per year [1].",
    "contexts": [
        "Employee Leave Handbook — 2. Annual leave: Every employee is entitled to twenty (20) working days"
    ],
    "reference": "You are entitled to twenty (20) working days of annual leave per calendar year.",
    "outcome": "answered",
}


def test_sample_to_ragas() -> None:
    converted = to_sample(SAMPLE)
    assert converted.user_input == SAMPLE["question"] and converted.retrieved_contexts == SAMPLE["contexts"]
    assert converted.response.startswith("Twenty") and converted.reference.startswith("You are")


def test_only_grounded_answers_are_scored() -> None:
    assert scorable(SAMPLE)
    assert not scorable({**SAMPLE, "outcome": "insufficient_evidence"})
    assert not scorable({**SAMPLE, "contexts": []})


def test_dataset_items_map_to_langfuse_payloads() -> None:
    items = {i["id"]: i for i in dataset_items()}
    s01 = item_payload(items["S01"])
    assert s01["input"]["question"].startswith("Πόσες") and s01["expected_output"]["outcome"] == "answered"
    assert (
        "Κανονισμός Αδειών" in s01["expected_output"]["relevant"] and s01["metadata"]["kind"] == "knowledge"
    )
    s12 = item_payload(items["S12"])
    assert s12["expected_output"]["expected_tools"] == ["get_my_balance", "prepare_leave_request"]
    assert "user" in s12["input"] and "password" not in str(s12)


def test_bootstrap_ignores_missing_values() -> None:
    mean, low, high = bootstrap([1.0, float("nan"), 0.5])
    assert mean == 0.75 and low <= mean <= high
