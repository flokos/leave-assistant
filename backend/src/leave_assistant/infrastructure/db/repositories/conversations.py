"""Conversations, messages, agent runs and provider events (M6, doc 07 §4.6)."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.chat.models import (
    AgentRun,
    Conversation,
    ConversationMessage,
    ConversationStatus,
    MessageRole,
    ProviderEventRecord,
)
from leave_assistant.infrastructure.db.models import (
    AgentRunRow,
    ConversationMessageRow,
    ConversationRow,
    ProviderEventRow,
)
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _conversation(r: ConversationRow) -> Conversation:
    return Conversation(
        id=r.id,
        tenant_id=r.tenant_id,
        employee_id=r.employee_id,
        user_id=r.user_id,
        title=r.title,
        status=ConversationStatus(r.status),
        locale=r.locale,
        created_at=r.created_at,
        last_activity_at=r.last_activity_at,
        run_lock_until=r.run_lock_until,
    )


class SqlConversationRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[ConversationRow]:
        return select(ConversationRow).where(ConversationRow.tenant_id == self._tenant)

    async def get(self, conversation_id: UUID) -> Conversation | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(ConversationRow.id == conversation_id))
        ).scalar_one_or_none()
        return _conversation(row) if row else None

    async def list_for_employee(
        self, employee_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[Conversation]:
        if self._tenant is None:
            return []
        stmt = self._base().where(
            ConversationRow.employee_id == employee_id,
            ConversationRow.status == ConversationStatus.OPEN.value,
        )
        if after is not None:  # newest first
            stmt = stmt.where(tuple_(ConversationRow.last_activity_at, ConversationRow.id) < tuple_(*after))
        stmt = stmt.order_by(ConversationRow.last_activity_at.desc(), ConversationRow.id.desc()).limit(limit)
        return [_conversation(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, conversation: Conversation) -> None:
        self._s.add(
            ConversationRow(
                id=conversation.id,
                tenant_id=conversation.tenant_id,
                employee_id=conversation.employee_id,
                user_id=conversation.user_id,
                title=conversation.title,
                status=conversation.status.value,
                locale=conversation.locale,
                created_at=conversation.created_at,
                last_activity_at=conversation.last_activity_at,
                run_lock_until=conversation.run_lock_until,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, conversation: Conversation) -> None:
        row = await self._s.get(ConversationRow, conversation.id)
        if row is None or row.tenant_id != self._tenant:
            return
        row.title = conversation.title
        row.status = conversation.status.value
        row.last_activity_at = conversation.last_activity_at
        row.run_lock_until = conversation.run_lock_until
        await self._s.flush()

    async def try_lock(self, conversation_id: UUID, *, until: datetime, now: datetime) -> bool:
        """Atomic: one run per conversation; a crashed run's lock expires at `until`."""
        stmt = (
            update(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                ConversationRow.tenant_id == self._tenant,
                or_(ConversationRow.run_lock_until.is_(None), ConversationRow.run_lock_until < now),
            )
            .values(run_lock_until=until)
            .returning(ConversationRow.id)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none() is not None

    async def unlock(self, conversation_id: UUID) -> None:
        await self._s.execute(
            update(ConversationRow)
            .where(ConversationRow.id == conversation_id, ConversationRow.tenant_id == self._tenant)
            .values(run_lock_until=None)
        )

    async def expired(self, *, inactive_before: datetime) -> builtins.list[Conversation]:
        if self._tenant is None:
            return []
        stmt = self._base().where(
            or_(
                ConversationRow.last_activity_at < inactive_before,
                ConversationRow.status == ConversationStatus.ARCHIVED.value,
            )
        )
        return [_conversation(r) for r in (await self._s.execute(stmt)).scalars()]

    async def delete(self, conversation_id: UUID) -> None:
        await self._s.execute(
            delete(ConversationRow).where(
                ConversationRow.id == conversation_id, ConversationRow.tenant_id == self._tenant
            )
        )


class SqlMessageRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, message: ConversationMessage) -> None:
        self._s.add(
            ConversationMessageRow(
                id=message.id,
                tenant_id=message.tenant_id,
                conversation_id=message.conversation_id,
                role=message.role.value,
                content_pseudonymised=message.content,
                citations=message.citations,
                provenance=message.provenance,
                run_id=message.run_id,
                outcome=message.outcome,
                created_at=message.created_at,
                proposal_id=message.proposal_id,
            )
        )
        await flush_or_conflict(self._s)

    async def list_for_conversation(self, conversation_id: UUID) -> builtins.list[ConversationMessage]:
        if self._tenant is None:
            return []
        stmt = (
            select(ConversationMessageRow)
            .where(
                ConversationMessageRow.tenant_id == self._tenant,
                ConversationMessageRow.conversation_id == conversation_id,
            )
            .order_by(ConversationMessageRow.created_at, ConversationMessageRow.id)
        )
        return [
            ConversationMessage(
                id=r.id,
                tenant_id=r.tenant_id,
                conversation_id=r.conversation_id,
                role=MessageRole(r.role),
                content=r.content_pseudonymised,
                created_at=r.created_at,
                citations=list(r.citations or []),
                provenance=dict(r.provenance or {}),
                run_id=r.run_id,
                outcome=r.outcome,
                proposal_id=r.proposal_id,
            )
            for r in (await self._s.execute(stmt)).scalars()
        ]


class SqlAgentRunRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, run: AgentRun) -> None:
        self._s.add(
            AgentRunRow(
                id=run.id,
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                turn=run.turn,
                intent=run.intent,
                language=run.language,
                route=run.route,
                provider_used=run.provider_used,
                model_used=run.model_used,
                failovers=run.failovers,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                latency_ms=run.latency_ms,
                tool_trace=run.tool_trace,
                details=run.details,
                outcome=run.outcome,
                langfuse_trace_id=run.langfuse_trace_id,
                created_at=run.created_at,
            )
        )
        await flush_or_conflict(self._s)

    async def count_for_conversation(self, conversation_id: UUID) -> int:
        if self._tenant is None:
            return 0
        stmt = select(func.count()).where(
            AgentRunRow.tenant_id == self._tenant, AgentRunRow.conversation_id == conversation_id
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def list_for_conversation(self, conversation_id: UUID) -> builtins.list[AgentRun]:
        if self._tenant is None:
            return []
        stmt = (
            select(AgentRunRow)
            .where(AgentRunRow.tenant_id == self._tenant, AgentRunRow.conversation_id == conversation_id)
            .order_by(AgentRunRow.created_at, AgentRunRow.turn)
        )
        return [
            AgentRun(
                id=r.id,
                tenant_id=r.tenant_id,
                conversation_id=r.conversation_id,
                turn=r.turn,
                intent=r.intent,
                language=r.language,
                route=r.route,
                provider_used=r.provider_used,
                model_used=r.model_used,
                failovers=r.failovers,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                latency_ms=r.latency_ms,
                outcome=r.outcome,
                created_at=r.created_at,
                tool_trace=list(r.tool_trace or []),
                details=dict(r.details or {}),
                langfuse_trace_id=r.langfuse_trace_id,
            )
            for r in (await self._s.execute(stmt)).scalars()
        ]


class SqlProviderEventRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add_many(self, events: Sequence[ProviderEventRecord]) -> None:
        for e in events:
            self._s.add(
                ProviderEventRow(
                    id=e.id,
                    tenant_id=e.tenant_id,
                    provider=e.provider,
                    model=e.model,
                    event=e.event,
                    reason=e.reason,
                    latency_ms=e.latency_ms,
                    run_id=e.run_id,
                    occurred_at=e.occurred_at,
                )
            )
        if events:
            await flush_or_conflict(self._s)
