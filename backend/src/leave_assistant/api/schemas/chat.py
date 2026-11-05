"""Chat schemas (doc 09 §2.2). Message text is re-identified for the owner at read time."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.chat.service import ConversationView, MessageView


class ConversationOut(ApiModel):
    id: UUID
    title: str
    created_at: datetime
    last_activity_at: datetime

    @classmethod
    def from_view(cls, view: ConversationView) -> ConversationOut:
        c = view.conversation
        return cls(id=c.id, title=view.title, created_at=c.created_at, last_activity_at=c.last_activity_at)


class CitationItem(ApiModel):
    n: int
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    version_no: int
    title: str
    heading_path: list[str]
    page: int | None


class MessageOut(ApiModel):
    id: UUID
    role: str
    text: str
    citations: list[CitationItem]
    outcome: str | None
    created_at: datetime

    @classmethod
    def from_view(cls, view: MessageView) -> MessageOut:
        m = view.message
        return cls(
            id=m.id,
            role=m.role.value,
            text=view.text,
            citations=[CitationItem(**c) for c in m.citations],
            outcome=m.outcome,
            created_at=m.created_at,
        )


class SendMessageIn(ApiModel):
    text: str = Field(min_length=1, max_length=2000)


def sse_examples() -> dict[str, Any]:
    return {
        "text/event-stream": {
            "schema": {"type": "string"},
            "example": 'event: token\ndata: {"text": "Κάθε εργαζόμενος δικαιούται"}\n\n',
        }
    }
