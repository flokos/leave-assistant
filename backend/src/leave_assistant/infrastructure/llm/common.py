"""Shared adapter code: LangChain message conversion and structured-output result handling."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from leave_assistant.application.llm.ports import Prompt, Usage
from leave_assistant.domain.agent.types import ProviderError, ProviderErrorKind

TRUNCATED_REASONS = {"MAX_TOKENS", "LENGTH"}
BLOCKED_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "content_filter"}


def to_messages(prompt: Prompt) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for message in prompt.messages:
        text = str(message.content)
        if message.role == "system":
            out.append(SystemMessage(text))
        elif message.role == "assistant":
            out.append(AIMessage(text))
        else:
            out.append(HumanMessage(text))
    return out


def unpack[M: BaseModel](result: Any, schema: type[M], provider: str, model: str) -> tuple[M, Usage]:
    """`with_structured_output(..., include_raw=True)` → `{raw, parsed, parsing_error}`."""
    raw = result.get("raw") if isinstance(result, dict) else None
    metadata = getattr(raw, "response_metadata", {}) or {}
    finish = str(metadata.get("finish_reason") or "")
    if finish.upper() in {r.upper() for r in BLOCKED_REASONS}:
        raise ProviderError(ProviderErrorKind.CONTENT_BLOCKED, provider, model, f"finish_reason={finish}")
    if finish.upper() in TRUNCATED_REASONS:
        # a cut-off JSON object can still parse (a shorter string, an empty list): never accept it
        raise ProviderError(
            ProviderErrorKind.INVALID_OUTPUT, provider, model, f"truncated: finish_reason={finish}"
        )
    parsed = result.get("parsed") if isinstance(result, dict) else result
    error = result.get("parsing_error") if isinstance(result, dict) else None
    if parsed is None or error is not None:
        raise ProviderError(
            ProviderErrorKind.INVALID_OUTPUT, provider, model, str(error or "no parsed output")[:300]
        )
    if isinstance(parsed, dict):
        parsed = schema.model_validate(parsed)
    usage_meta = getattr(raw, "usage_metadata", None) or {}
    usage = Usage(int(usage_meta.get("input_tokens", 0) or 0), int(usage_meta.get("output_tokens", 0) or 0))
    return parsed, usage


def classify_http(status: int | None, message: str, provider: str, model: str) -> ProviderError:
    """HTTP status → doc 04 §9 error class."""
    text = message[:300]
    if status in (408, 409, 413, 425, 429) or (status is not None and status >= 500):
        return ProviderError(ProviderErrorKind.TRANSIENT, provider, model, f"HTTP {status}: {text}")
    if status in (401, 403, 404):
        return ProviderError(ProviderErrorKind.FATAL, provider, model, f"HTTP {status}: {text}")
    return ProviderError(ProviderErrorKind.FATAL, provider, model, f"HTTP {status}: {text}")
