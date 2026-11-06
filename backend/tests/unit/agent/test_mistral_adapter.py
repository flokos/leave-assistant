"""Mistral adapter against a mocked HTTP transport: JSON-schema request, error classes, truncation, one attempt."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from leave_assistant.application.llm.ports import ModelRole, Prompt, PromptMessage
from leave_assistant.domain.agent.types import ProviderError, ProviderErrorKind
from leave_assistant.domain.privacy.types import LlmSafeText
from leave_assistant.infrastructure.llm.mistral import MistralChatModel


class Out(BaseModel):
    answer: str
    citations: list[int]


PROMPT = Prompt(
    "generate@3",
    (
        PromptMessage("system", LlmSafeText("Answer in Greek.")),
        PromptMessage("user", LlmSafeText("<user>Μεταφέρονται οι μέρες;</user>")),
    ),
)


def completion(content: str, finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "x",
        "object": "chat.completion",
        "model": "ministral-14b-2512",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


def adapter(handler: Any) -> tuple[MistralChatModel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)  # type: ignore[no-any-return]

    model = MistralChatModel("test-key", fast_model="ministral-8b-2512", answer_model="ministral-14b-2512")
    client = model._client("ministral-14b-2512", 25.0, PROMPT.max_output_tokens)
    client.async_client = httpx.AsyncClient(
        base_url="https://api.mistral.ai/v1", transport=httpx.MockTransport(record)
    )
    return model, seen


async def ask(model: MistralChatModel) -> tuple[Out, Any]:
    return await model.structured(PROMPT, Out, role=ModelRole.ANSWER, deadline_seconds=25.0)


async def test_parses_json_schema_output_and_usage() -> None:
    body = json.dumps({"answer": "Ναι, έως πέντε ημέρες [1].", "citations": [1]}, ensure_ascii=False)
    model, seen = adapter(lambda _: httpx.Response(200, json=completion(body)))
    out, usage = await ask(model)
    assert out.citations == [1] and "πέντε" in out.answer
    assert (usage.input_tokens, usage.output_tokens) == (120, 30)
    sent = json.loads(seen[0].content)
    assert sent["model"] == "ministral-14b-2512"
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["temperature"] == 0.0


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, ProviderErrorKind.TRANSIENT),
        (503, ProviderErrorKind.TRANSIENT),
        (401, ProviderErrorKind.FATAL),
        (404, ProviderErrorKind.FATAL),
    ],
)
async def test_http_errors_map_to_router_classes_with_a_single_attempt(
    status: int, kind: ProviderErrorKind
) -> None:
    model, seen = adapter(lambda _: httpx.Response(status, json={"message": "nope"}))
    with pytest.raises(ProviderError) as caught:
        await ask(model)
    assert caught.value.kind is kind and caught.value.provider == "mistral"
    assert len(seen) == 1  # the router owns retries


async def test_network_timeout_is_transient() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    model, seen = adapter(boom)
    with pytest.raises(ProviderError) as caught:
        await ask(model)
    assert caught.value.kind is ProviderErrorKind.TRANSIENT
    assert len(seen) == 1


async def test_truncated_output_is_invalid_even_if_it_parses() -> None:
    body = json.dumps({"answer": "Ναι, έως πέντε", "citations": []}, ensure_ascii=False)
    model, _ = adapter(lambda _: httpx.Response(200, json=completion(body, finish="length")))
    with pytest.raises(ProviderError) as caught:
        await ask(model)
    assert caught.value.kind is ProviderErrorKind.INVALID_OUTPUT
