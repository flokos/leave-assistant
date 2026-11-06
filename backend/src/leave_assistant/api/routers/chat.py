"""Chat (doc 09 §2.2/§3): conversations, the SSE answer stream, resuming a paused run with a decision (M7) and the
redacted run history (M7)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from sse_starlette.sse import EventSourceResponse

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.chat import (
    ConversationOut,
    MessageOut,
    ResumeIn,
    RunOut,
    SendMessageIn,
    resume_examples,
    sse_examples,
)
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.application.chat.service import ChatEvent

router = APIRouter(prefix="/chat/conversations", tags=["chat"], route_class=IdempotentRoute)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(principal: CurrentPrincipal, services: CurrentServices) -> ConversationOut:
    conversation = await services.chat.create(principal)
    return ConversationOut.from_view(await services.chat.get(principal, conversation.id))


@router.get("", response_model=PageOut[ConversationOut])
async def list_conversations(
    principal: CurrentPrincipal,
    services: CurrentServices,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[ConversationOut]:
    page = await services.chat.list(principal, limit=limit, cursor=cursor)
    return PageOut(items=[ConversationOut.from_view(v) for v in page.items], next_cursor=page.next_cursor)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> ConversationOut:
    return ConversationOut.from_view(await services.chat.get(principal, conversation_id))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_conversation(
    conversation_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> Response:
    await services.chat.archive(principal, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> list[MessageOut]:
    return [MessageOut.from_view(v) for v in await services.chat.messages(principal, conversation_id)]


# A stream cannot be replayed from an idempotency record; one run per conversation (409 chat/run-in-progress) is
# what prevents duplicate turns (doc 09 note, M6).
@router.post(
    "/{conversation_id}/messages",
    response_class=EventSourceResponse,
    responses={200: {"content": sse_examples(), "description": "SSE stream (doc 09 §3)"}},
    openapi_extra=NO_IDEMPOTENCY,
)
async def send_message(
    conversation_id: UUID,
    body: SendMessageIn,
    request: Request,
    principal: CurrentPrincipal,
    services: CurrentServices,
) -> EventSourceResponse:
    events = await services.chat.send(
        principal, conversation_id, body.text, request_id=getattr(request.state, "request_id", None)
    )

    async def stream() -> AsyncIterator[dict[str, str]]:
        event: ChatEvent
        async for event in events:
            yield {"event": event.name, "data": json.dumps(event.data, ensure_ascii=False)}

    return EventSourceResponse(
        stream(), ping=15, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


def _sse(events: AsyncIterator[ChatEvent]) -> EventSourceResponse:
    async def stream() -> AsyncIterator[dict[str, str]]:
        async for event in events:
            yield {"event": event.name, "data": json.dumps(event.data, ensure_ascii=False)}

    return EventSourceResponse(
        stream(), ping=15, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


# The proposal id is the idempotency key of a resume (doc 04 §6): a decided proposal streams its stored outcome.
@router.post(
    "/{conversation_id}/resume",
    response_class=EventSourceResponse,
    responses={200: {"content": resume_examples(), "description": "SSE stream (doc 09 §3)"}},
    openapi_extra=NO_IDEMPOTENCY,
)
async def resume_conversation(
    conversation_id: UUID,
    body: ResumeIn,
    request: Request,
    principal: CurrentPrincipal,
    services: CurrentServices,
) -> EventSourceResponse:
    events = await services.chat.resume(
        principal,
        conversation_id,
        proposal_id=body.proposal_id,
        decision=body.decision,
        payload_hash=body.payload_hash,
        edits=body.edits.model_dump(mode="json") if body.edits else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return _sse(events)


@router.get("/{conversation_id}/runs", response_model=list[RunOut])
async def list_runs(
    conversation_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> list[RunOut]:
    return [RunOut.from_view(v) for v in await services.chat.runs(principal, conversation_id)]
