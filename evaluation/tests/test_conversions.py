"""Sample → RAGAS and dataset item → Langfuse conversions (no network)."""

from __future__ import annotations

from typing import Any

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


def test_paired_differences_use_common_items_only() -> None:
    from leave_eval.ragas_run import METRICS, paired

    nan = float("nan")
    base = {"items": {"a": {m: [0.5, 0.7] for m in METRICS}, "b": {m: [1.0] for m in METRICS}}}
    challenger = {"items": {"a": {m: [0.8, nan] for m in METRICS}, "c": {m: [0.0] for m in METRICS}}}
    result = paired(base, challenger)
    assert all(result[m]["n"] == 1 and result[m]["delta"] == 0.2 for m in METRICS)


def test_usage_merge_sums_nested_details() -> None:
    from leave_eval.ragas_run import judge_chat, merge_usage

    first = {"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 1}, "model": "x"}
    second = {"prompt_tokens": 5, "prompt_tokens_details": {"cached_tokens": 2}, "model": "y"}
    assert merge_usage(merge_usage({}, first), second) == {
        "prompt_tokens": 15,
        "prompt_tokens_details": {"cached_tokens": 3},
        "model": "x",
    }
    chat = judge_chat(model="mistral-large-4", api_key="test")
    combined = chat._combine_llm_outputs([{"token_usage": first}, None, {"token_usage": second}])
    assert combined["token_usage"]["prompt_tokens_details"] == {"cached_tokens": 3}


def test_checkpoint_restores_finished_repetitions_per_config(tmp_path: Any) -> None:
    import json

    from leave_eval.ragas_run import load_checkpoint

    path = tmp_path / "ragas.partial.jsonl"
    rows = [
        {"config": "B", "rep": 0, "items": {"S01": {"faithfulness": 1.0}}},
        {"config": "A", "rep": 0, "items": {"S01": {"faithfulness": 0.5}}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert load_checkpoint(path, "A") == {0: {"S01": {"faithfulness": 0.5}}}
    assert load_checkpoint(tmp_path / "missing.jsonl", "A") == {}
