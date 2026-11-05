"""Token budgets per LLM call (doc 04 §8) — Groq's free TPM caps make them a correctness requirement."""

from __future__ import annotations

import math
from dataclasses import dataclass

SAFETY_FACTOR = 1.15  # counts come from the e5 SentencePiece tokenizer, not the provider's


@dataclass(frozen=True, slots=True)
class TokenBudgets:
    system: int = 700
    history: int = 800
    context: int = 1800
    tools: int = 200
    total: int = 3500
    max_output: int = 700


def estimate(raw_count: int) -> int:
    return math.ceil(raw_count * SAFETY_FACTOR)
