"""Langfuse tracing (ADR-0022, doc 04 §12): optional (`LANGFUSE_ENABLED`); inputs are already pseudonymised and the
`mask` callback redacts identifier patterns as a second line of defence (reuses the log scrubber's patterns)."""

from __future__ import annotations

import hashlib
from typing import Any

from leave_assistant.application.chat.context import RunContext
from leave_assistant.infrastructure.observability.logging import scrub_value


def mask(*, data: Any, **_: Any) -> Any:
    return scrub_value(data)


def opaque_user(tenant_id: object, user_id: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{user_id}".encode()).hexdigest()[:16]


class Tracer:
    """Builds the Langfuse LangChain callback and run metadata (session = thread, user = opaque hash, tenant tag)."""

    def __init__(self, *, public_key: str, secret_key: str, host: str) -> None:
        from langfuse import Langfuse

        self._public_key = public_key
        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host, mask=mask)

    def handler(self, context: RunContext) -> Any:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(public_key=self._public_key)

    @staticmethod
    def metadata(context: RunContext) -> dict[str, Any]:
        return {
            "langfuse_session_id": str(context.thread_id),
            "langfuse_user_id": opaque_user(context.tenant_id, context.principal.user_id),
            "langfuse_tags": [f"tenant:{context.tenant_id}"],
        }

    def flush(self) -> None:
        self._client.flush()
