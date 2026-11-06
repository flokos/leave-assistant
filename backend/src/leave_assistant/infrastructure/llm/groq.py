"""Groq adapter (`langchain-groq` 1.1): tool-calling structured output (works on every Groq tool-use model);
SDK retries disabled — the router owns retries and failover (ADR-0010)."""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from typing import Any, TypeVar

import groq
from langchain_groq import ChatGroq
from pydantic import BaseModel, SecretStr, ValidationError

from leave_assistant.application.llm.ports import ModelRole, Prompt, ToolSpec, ToolStep, Usage
from leave_assistant.domain.agent.types import ProviderError, ProviderErrorKind
from leave_assistant.infrastructure.llm.common import (
    classify_http,
    to_messages,
    to_openai_tools,
    unpack,
    unpack_tool_step,
)

T = TypeVar("T", bound=BaseModel)
INVALID_MARKERS = (
    "json_validate_failed",
    "tool_use_failed",
    "failed to call a function",
    "output_parse_failed",
)


class GroqChatModel:
    def __init__(self, api_key: str, *, fast_model: str, answer_model: str) -> None:
        self._key = SecretStr(api_key)
        self._models = {ModelRole.FAST: fast_model, ModelRole.ANSWER: answer_model}
        self._clients: dict[tuple[str, float, int], ChatGroq] = {}

    @property
    def name(self) -> str:
        return "groq"

    def model_for(self, role: ModelRole) -> str:
        return self._models[role]

    def _client(self, model: str, timeout: float, max_tokens: int) -> ChatGroq:
        key = (model, timeout, max_tokens)
        if key not in self._clients:
            self._clients[key] = ChatGroq(
                model=model,
                api_key=self._key,
                temperature=0.0,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=0,
            )
        return self._clients[key]

    async def _guard(self, model: str, call: Awaitable[Any]) -> Any:
        try:
            return await call
        except (
            groq.APITimeoutError,
            groq.APIConnectionError,
            groq.RateLimitError,
            groq.InternalServerError,
        ) as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, self.name, model, type(exc).__name__) from exc
        except (groq.AuthenticationError, groq.PermissionDeniedError, groq.NotFoundError) as exc:
            raise ProviderError(ProviderErrorKind.FATAL, self.name, model, type(exc).__name__) from exc
        except groq.APIStatusError as exc:
            message = str(exc)
            if exc.status_code == 400 and any(m in message for m in INVALID_MARKERS):
                raise ProviderError(
                    ProviderErrorKind.INVALID_OUTPUT, self.name, model, message[:300]
                ) from exc
            raise classify_http(exc.status_code, message, self.name, model) from exc
        except ValidationError as exc:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, self.name, model, str(exc)[:300]) from exc

    async def structured(
        self, prompt: Prompt, schema: type[T], *, role: ModelRole, deadline_seconds: float
    ) -> tuple[T, Usage]:
        model = self.model_for(role)
        runnable = self._client(model, deadline_seconds, prompt.max_output_tokens).with_structured_output(
            schema, method="function_calling", include_raw=True
        )
        result = await self._guard(model, runnable.ainvoke(to_messages(prompt)))
        return unpack(result, schema, self.name, model)

    async def tool_step(
        self, prompt: Prompt, tools: Sequence[ToolSpec], *, role: ModelRole, deadline_seconds: float
    ) -> tuple[ToolStep, Usage]:
        model = self.model_for(role)
        runnable = self._client(model, deadline_seconds, prompt.max_output_tokens).bind_tools(
            to_openai_tools(tools)
        )
        message = await self._guard(model, runnable.ainvoke(to_messages(prompt)))
        return unpack_tool_step(message, self.name, model)
