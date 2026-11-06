"""Gemini adapter (`langchain-google-genai` 4.4): native JSON-schema structured output; SDK retries disabled — the
router owns retries and failover (ADR-0010)."""

from __future__ import annotations

from typing import Any, TypeVar

import httpx
from google.genai import errors as genai_errors
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from pydantic import BaseModel, ValidationError

from leave_assistant.application.llm.ports import ModelRole, Prompt, Usage
from leave_assistant.domain.agent.types import ProviderError, ProviderErrorKind
from leave_assistant.infrastructure.llm.common import classify_http, to_messages, unpack

T = TypeVar("T", bound=BaseModel)


def _status(error: BaseException) -> int | None:
    for candidate in (error, error.__cause__, error.__context__):
        code = getattr(candidate, "code", None) or getattr(candidate, "status_code", None)
        if isinstance(code, int):
            return code
    return None


class GeminiChatModel:
    def __init__(
        self,
        api_key: str,
        *,
        fast_model: str,
        answer_model: str,
        timeout_seconds: float | None = None,
        thinking_level: str | None = "low",
        thinking_tokens: int = 1024,
    ) -> None:
        self._key = api_key
        self._thinking_level = thinking_level
        self._thinking_tokens = thinking_tokens
        self._timeout_override = timeout_seconds
        self._models = {ModelRole.FAST: fast_model, ModelRole.ANSWER: answer_model}
        self._clients: dict[tuple[str, float, int], ChatGoogleGenerativeAI] = {}

    @property
    def name(self) -> str:
        return "gemini"

    def model_for(self, role: ModelRole) -> str:
        return self._models[role]

    def _client(self, model: str, timeout: float, max_tokens: int) -> ChatGoogleGenerativeAI:
        key = (model, timeout, max_tokens)
        if key not in self._clients:
            options: dict[str, Any] = {}
            if self._thinking_level is not None:
                options["thinking_level"] = self._thinking_level
            self._clients[key] = ChatGoogleGenerativeAI(
                model=model,
                google_api_key=self._key,
                temperature=0.0,
                # thoughts count against the cap: the answer budget plus a thinking allowance
                max_output_tokens=max_tokens + self._thinking_tokens,
                timeout=timeout,
                max_retries=0,
                **options,
            )
        return self._clients[key]

    async def structured(
        self, prompt: Prompt, schema: type[T], *, role: ModelRole, deadline_seconds: float
    ) -> tuple[T, Usage]:
        model = self.model_for(role)
        if self._timeout_override is not None:
            deadline_seconds = min(deadline_seconds, self._timeout_override)
        runnable = self._client(model, deadline_seconds, prompt.max_output_tokens).with_structured_output(
            schema, method="json_schema", include_raw=True
        )
        try:
            result = await runnable.ainvoke(to_messages(prompt))
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError, ConnectionError) as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, self.name, model, type(exc).__name__) from exc
        except ValidationError as exc:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, self.name, model, str(exc)[:300]) from exc
        except (genai_errors.APIError, ChatGoogleGenerativeAIError) as exc:
            status = _status(exc)
            if status is None:  # wrapped network failure without an HTTP status
                raise ProviderError(
                    ProviderErrorKind.TRANSIENT, self.name, model, f"{type(exc).__name__}: {exc}"[:300]
                ) from exc
            raise classify_http(status, str(exc), self.name, model) from exc
        return unpack(result, schema, self.name, model)
