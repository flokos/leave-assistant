"""LLM ports (ADR-0010). Prompts are built from `LlmSafeText` only — passing a plain `str` is a type error for mypy
and fails the runtime check, so nothing un-pseudonymised can reach a provider (doc 08 §7.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from leave_assistant.domain.privacy.types import LlmSafeText

T = TypeVar("T", bound=BaseModel)


class ModelRole(StrEnum):
    FAST = "fast"  # classify, condense, summarise
    ANSWER = "answer"  # grounded generation


@dataclass(frozen=True, slots=True)
class PromptMessage:
    role: Literal["system", "user", "assistant"]
    content: LlmSafeText

    def __post_init__(self) -> None:
        if not isinstance(self.content, LlmSafeText):
            raise TypeError("prompt content must be LlmSafeText (pseudonymised)")


@dataclass(frozen=True, slots=True)
class Prompt:
    id: str  # prompt file id@version — referenced in traces and by the fake provider
    messages: tuple[PromptMessage, ...]
    max_output_tokens: int = 700


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


class ChatModel(Protocol):
    """One provider. Adapters translate their SDK errors into `ProviderError` with the doc 04 §9 taxonomy."""

    @property
    def name(self) -> str: ...
    def model_for(self, role: ModelRole) -> str: ...
    async def structured(
        self, prompt: Prompt, schema: type[T], *, role: ModelRole, deadline_seconds: float
    ) -> tuple[T, Usage]: ...


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


def prompt_text(messages: Sequence[PromptMessage]) -> str:
    return "\n".join(m.content for m in messages)
