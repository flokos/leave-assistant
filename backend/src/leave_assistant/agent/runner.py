"""LangGraphAgent: the `AgentRunner` port implemented with the compiled graph (doc 04 §3, §7, §12)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

from leave_assistant.agent.graph import AgentDeps, AgentGraph, answer_from_state
from leave_assistant.agent.tracing import Tracer
from leave_assistant.application.chat.context import RunContext
from leave_assistant.application.chat.ports import AgentEvent, FinalEvent, StatusEvent
from leave_assistant.domain.privacy.types import LlmSafeText


class LangGraphAgent:
    def __init__(self, deps: AgentDeps, checkpointer: Any, *, tracer: Tracer | None = None) -> None:
        self._checkpointer = checkpointer
        self._graph = AgentGraph(deps).build(checkpointer)
        self._tracer = tracer

    @property
    def graph(self) -> Any:
        return self._graph

    async def run(self, context: RunContext, message: LlmSafeText) -> AsyncIterator[AgentEvent]:
        if not isinstance(message, LlmSafeText):
            raise TypeError("the agent only accepts pseudonymised text")
        config: dict[str, Any] = {"configurable": {"thread_id": str(context.thread_id)}}
        if self._tracer is not None:
            config["callbacks"] = [self._tracer.handler(context)]
            config["metadata"] = self._tracer.metadata(context)
        snapshot = await self._graph.aget_state(config)
        turn = int((snapshot.values or {}).get("turn", 0)) + 1
        payload = {"messages": [{"role": "user", "content": str(message)}], "turn": turn}
        last: dict[str, Any] = {}
        async for namespace, mode, chunk in self._graph.astream(
            payload, config, context=context, stream_mode=["custom", "values"], subgraphs=True
        ):
            if mode == "custom" and isinstance(chunk, dict) and "stage" in chunk:
                yield StatusEvent(str(chunk["stage"]))
            elif mode == "values" and not namespace:
                last = chunk
        answer = last.get("answer") or {}
        if not answer:
            raise RuntimeError("the graph finished without an answer")
        yield FinalEvent(
            answer=answer_from_state(answer),
            intent=last.get("intent"),
            language=last.get("language"),
            route=last.get("route"),
            turn=turn,
            source_version_ids=tuple(last.get("sources", [])),
        )

    async def delete_threads(self, thread_ids: Sequence[UUID]) -> None:
        for thread_id in thread_ids:
            await self._checkpointer.adelete_thread(str(thread_id))
