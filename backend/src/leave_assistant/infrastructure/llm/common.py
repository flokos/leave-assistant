"""Shared adapter code: LangChain message conversion and structured-output result handling."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from leave_assistant.application.llm.ports import Prompt, ToolCall, ToolSpec, ToolStep, Usage
from leave_assistant.domain.agent.types import ProviderError, ProviderErrorKind

TRUNCATED_REASONS = {"MAX_TOKENS", "LENGTH"}
# Gemini reports a function call it could not form as a finish reason, not an exception
MALFORMED_CALL_REASONS = {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}
MAX_CALLS_PER_STEP = 3
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


def to_openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """The OpenAI function format every LangChain chat model's `bind_tools` accepts."""
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def _finish(metadata: dict[str, Any]) -> str:
    return str(metadata.get("finish_reason") or metadata.get("stop_reason") or "").upper()


def unpack_tool_step(message: Any, provider: str, model: str) -> tuple[ToolStep, Usage]:
    """An AIMessage from `bind_tools(...).ainvoke` → tool calls or answer text, with the doc 04 §9 error classes."""
    finish = _finish(getattr(message, "response_metadata", {}) or {})
    if finish in {r.upper() for r in BLOCKED_REASONS}:
        raise ProviderError(ProviderErrorKind.CONTENT_BLOCKED, provider, model, f"finish_reason={finish}")
    if finish in TRUNCATED_REASONS:
        raise ProviderError(
            ProviderErrorKind.INVALID_OUTPUT, provider, model, f"truncated: finish_reason={finish}"
        )
    if finish in MALFORMED_CALL_REASONS:
        raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, provider, model, f"finish_reason={finish}")
    calls = tuple(
        ToolCall(str(c["name"]), dict(c.get("args") or {}))
        for c in (getattr(message, "tool_calls", None) or [])[:MAX_CALLS_PER_STEP]
    )
    invalid = getattr(message, "invalid_tool_calls", None) or []
    if invalid and not calls:
        detail = "; ".join(str(i.get("error") or i.get("name") or "invalid call") for i in invalid)[:300]
        raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, provider, model, f"invalid tool call: {detail}")
    text = "" if calls else message_text(message).strip()
    if not calls and not text:
        raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, provider, model, "neither a tool call nor text")
    usage_meta = getattr(message, "usage_metadata", None) or {}
    usage = Usage(int(usage_meta.get("input_tokens", 0) or 0), int(usage_meta.get("output_tokens", 0) or 0))
    return ToolStep(calls=calls, text=text), usage
