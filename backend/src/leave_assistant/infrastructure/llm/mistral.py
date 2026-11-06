"""Mistral adapter (`langchain-mistralai` 1.1): native JSON-schema structured output (strict); the router owns
retries and failover (ADR-0010). Replaces Groq as the second provider when Groq is unavailable (worklog M6)."""

from __future__ import annotations

from typing import TypeVar

import httpx
from langchain_mistralai import ChatMistralAI
from pydantic import BaseModel, SecretStr, ValidationError

from leave_assistant.application.llm.ports import ModelRole, Prompt, Usage
from leave_assistant.domain.agent.types import ProviderError, ProviderErrorKind
from leave_assistant.infrastructure.llm.common import classify_http, to_messages, unpack

T = TypeVar("T", bound=BaseModel)


class MistralChatModel:
    def __init__(self, api_key: str, *, fast_model: str, answer_model: str) -> None:
        self._key = SecretStr(api_key)
        self._models = {ModelRole.FAST: fast_model, ModelRole.ANSWER: answer_model}
        self._clients: dict[tuple[str, float, int], ChatMistralAI] = {}

    @property
    def name(self) -> str:
        return "mistral"

    def model_for(self, role: ModelRole) -> str:
        return self._models[role]

    def _client(self, model: str, timeout: float, max_tokens: int) -> ChatMistralAI:
        key = (model, timeout, max_tokens)
        if key not in self._clients:
            self._clients[key] = ChatMistralAI(
                model_name=model,
                api_key=self._key,
                temperature=0.0,
                max_tokens=max_tokens,
                timeout=int(max(1, timeout)),
                max_retries=1,  # tenacity `stop_after_attempt`: total attempts, so exactly one call
            )
        return self._clients[key]

    async def structured(
        self, prompt: Prompt, schema: type[T], *, role: ModelRole, deadline_seconds: float
    ) -> tuple[T, Usage]:
        model = self.model_for(role)
        runnable = self._client(model, deadline_seconds, prompt.max_output_tokens).with_structured_output(
            schema, method="json_schema", include_raw=True
        )
        try:
            result = await runnable.ainvoke(to_messages(prompt))
        except httpx.HTTPStatusError as exc:
            raise classify_http(exc.response.status_code, exc.response.text, self.name, model) from exc
        except (httpx.TimeoutException, httpx.RequestError, httpx.StreamError, TimeoutError) as exc:
            raise ProviderError(ProviderErrorKind.TRANSIENT, self.name, model, type(exc).__name__) from exc
        except ValidationError as exc:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, self.name, model, str(exc)[:300]) from exc
        return unpack(result, schema, self.name, model)
