"""LangGraphAgent: the `AgentRunner` port implemented with the compiled graph (doc 04 §3, §7, §12)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

from langgraph.types import Command

from leave_assistant.agent.graph import AgentDeps, AgentGraph, answer_from_state
from leave_assistant.agent.tracing import Tracer
from leave_assistant.application.chat.context import RunContext
from leave_assistant.application.chat.ports import (
    AgentEvent,
    FinalEvent,
    InterruptEvent,
    StatusEvent,
    ToolEvent,
)
from leave_assistant.domain.privacy.types import LlmSafeText


class LangGraphAgent:
    def __init__(self, deps: AgentDeps, checkpointer: Any, *, tracer: Tracer | None = None) -> None:
        self._checkpointer = checkpointer
        self._graph = AgentGraph(deps).build(checkpointer)
        self._tracer = tracer

    @property
    def graph(self) -> Any:
        return self._graph

    def _config(self, context: RunContext) -> dict[str, Any]:
        config: dict[str, Any] = {"configurable": {"thread_id": str(context.thread_id)}}
        if self._tracer is not None:
            config["callbacks"] = [self._tracer.handler(context)]
            config["metadata"] = self._tracer.metadata(context)
        return config

    async def run(self, context: RunContext, message: LlmSafeText) -> AsyncIterator[AgentEvent]:
        if not isinstance(message, LlmSafeText):
            raise TypeError("the agent only accepts pseudonymised text")
        config = self._config(context)
        snapshot = await self._graph.aget_state(config)
        turn = int((snapshot.values or {}).get("turn", 0)) + 1
        payload = {"messages": [{"id": f"t{turn}u", "role": "user", "content": str(message)}], "turn": turn}
        async for event in self._stream(payload, config, context, turn):
            yield event

    async def resume(self, context: RunContext, decision: dict[str, Any]) -> AsyncIterator[AgentEvent]:
        """Continue a paused run with the user's decision (doc 04 §6). The RunContext is rebuilt from the caller's
        token, never taken from the checkpoint."""
        config = self._config(context)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.interrupts:
            raise RuntimeError("this conversation has no paused run")
        turn = int((snapshot.values or {}).get("turn", 0))
        async for event in self._stream(Command(resume=decision), config, context, turn):
            yield event

    async def _stream(
        self, payload: Any, config: dict[str, Any], context: RunContext, turn: int
    ) -> AsyncIterator[AgentEvent]:
        last: dict[str, Any] = {}
        paused: dict[str, Any] | None = None
        async for namespace, mode, chunk in self._graph.astream(
            payload, config, context=context, stream_mode=["custom", "values"], subgraphs=True
        ):
            if mode == "custom" and isinstance(chunk, dict):
                if "stage" in chunk:
                    yield StatusEvent(str(chunk["stage"]))
                elif "tool" in chunk:
                    yield ToolEvent(str(chunk["tool"]), str(chunk.get("status", "")))
            elif mode == "values" and not namespace:
                interrupts = chunk.get("__interrupt__") if isinstance(chunk, dict) else None
                if interrupts:
                    value = interrupts[0].value
                    paused = dict(value) if isinstance(value, dict) else {}
                last = chunk
        if paused is not None:
            yield InterruptEvent(
                proposal_id=UUID(str(paused["proposal_id"])),
                kind=str(paused.get("kind", "")),
                text=str(paused.get("text", "")),
                intent=last.get("intent"),
                language=last.get("language"),
                turn=turn,
                placeholders=dict(last.get("placeholders", {})),
            )
            return
        answer = last.get("answer") or {}
        if not answer:
            raise RuntimeError("the graph finished without an answer")
        proposal = last.get("proposal_id")
        yield FinalEvent(
            answer=answer_from_state(answer),
            intent=last.get("intent"),
            language=last.get("language"),
            route=last.get("route"),
            turn=turn,
            source_version_ids=tuple(last.get("sources", [])),
            placeholders=dict(last.get("placeholders", {})),
            proposal_id=UUID(str(proposal)) if proposal else None,
            tool_steps=len(last.get("tool_steps", [])),
        )

    async def delete_threads(self, thread_ids: Sequence[UUID]) -> None:
        for thread_id in thread_ids:
            await self._checkpointer.adelete_thread(str(thread_id))
