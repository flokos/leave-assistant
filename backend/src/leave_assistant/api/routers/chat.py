"""Chat (doc 09 §2.2/§3, M6 subset): conversations and an SSE answer stream. `resume` and `runs` arrive with M7."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from sse_starlette.sse import EventSourceResponse

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.chat import ConversationOut, MessageOut, SendMessageIn, sse_examples
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
