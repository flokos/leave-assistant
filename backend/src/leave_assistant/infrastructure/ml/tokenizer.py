"""Token counts with the e5 tokenizer — the same tokenizer that embeds (doc 05 §4 step 3)."""

from __future__ import annotations

from functools import cached_property
from typing import Any


class E5Tokenizer:
    def __init__(self, model: str, revision: str | None = None) -> None:
        self._model = model
        self._revision = revision

    @cached_property
    def _tokenizer(self) -> Any:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(self._model, revision=self._revision)  # type: ignore[no-untyped-call]

    def count(self, text: str) -> int:
        return len(self._tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"])
