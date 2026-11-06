from __future__ import annotations

from leave_assistant.evaluation.retrieval import latest_per_variant


def test_a_rerun_replaces_the_failed_entry_and_keeps_the_order() -> None:
    entries = [
        {"model": "B", "backend": "torch", "load_s": 1},
        {"model": "B", "backend": "onnx-int8", "error": "exit 1"},
        {"model": "B", "backend": "onnx", "load_s": 2},
        {"model": "B", "backend": "onnx-int8", "load_s": 3},
    ]
    latest = latest_per_variant(entries)
    assert [(e["backend"], "error" in e) for e in latest] == [
        ("torch", False),
        ("onnx-int8", False),
        ("onnx", False),
    ]
