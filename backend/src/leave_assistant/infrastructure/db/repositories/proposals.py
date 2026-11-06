"""Action proposals and pending interrupts (M7, doc 07 §4.3/§4.6). `claim` is the exactly-once gate: one atomic
`UPDATE … WHERE status = 'pending' AND expires_at > now RETURNING` — a second confirmation, from any process,
finds nothing to claim."""

from __future__ import annotations

import builtins
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.assistant.models import ActionProposal, PendingInterrupt
from leave_assistant.domain.agent.proposals import ProposalKind, ProposalStatus
from leave_assistant.infrastructure.db.models import ActionProposalRow, PendingInterruptRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _proposal(r: ActionProposalRow) -> ActionProposal:
    return ActionProposal(
        id=r.id,
        tenant_id=r.tenant_id,
        conversation_id=r.conversation_id,
        employee_id=r.employee_id,
        kind=ProposalKind(r.kind),
        payload=dict(r.payload or {}),
        payload_hash=r.payload_hash,
        status=ProposalStatus(r.status),
        expires_at=r.expires_at,
        created_at=r.created_at,
        run_id=r.run_id,
        claimed_by_run=r.claimed_by_run,
        claimed_at=r.claimed_at,
        decided_at=r.decided_at,
        executed_request_id=r.executed_request_id,
        outcome=dict(r.outcome or {}),
    )


class SqlProposalRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[ActionProposalRow]:
        return select(ActionProposalRow).where(ActionProposalRow.tenant_id == self._tenant)

    async def add(self, proposal: ActionProposal) -> None:
        self._s.add(
            ActionProposalRow(
                id=proposal.id,
                tenant_id=proposal.tenant_id,
                conversation_id=proposal.conversation_id,
                employee_id=proposal.employee_id,
                kind=proposal.kind.value,
                payload=proposal.payload,
                payload_hash=proposal.payload_hash,
                status=proposal.status.value,
                expires_at=proposal.expires_at,
                created_at=proposal.created_at,
                run_id=proposal.run_id,
                claimed_by_run=proposal.claimed_by_run,
                claimed_at=proposal.claimed_at,
                decided_at=proposal.decided_at,
                executed_request_id=proposal.executed_request_id,
                outcome=proposal.outcome,
            )
        )
        await flush_or_conflict(self._s)

    async def get(self, proposal_id: UUID) -> ActionProposal | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(ActionProposalRow.id == proposal_id))
        ).scalar_one_or_none()
        return _proposal(row) if row else None

    async def update(self, proposal: ActionProposal) -> None:
        await self._s.execute(
            update(ActionProposalRow)
            .where(ActionProposalRow.id == proposal.id, ActionProposalRow.tenant_id == self._tenant)
            .values(
                status=proposal.status.value,
                claimed_by_run=proposal.claimed_by_run,
                claimed_at=proposal.claimed_at,
                decided_at=proposal.decided_at,
                executed_request_id=proposal.executed_request_id,
                outcome=proposal.outcome,
            )
        )

    async def claim(self, proposal_id: UUID, *, run_id: UUID, now: datetime) -> ActionProposal | None:
        if self._tenant is None:
            return None
        stmt = (
            update(ActionProposalRow)
            .where(
                ActionProposalRow.id == proposal_id,
                ActionProposalRow.tenant_id == self._tenant,
                ActionProposalRow.status == ProposalStatus.PENDING.value,
                ActionProposalRow.expires_at > now,
            )
            .values(status=ProposalStatus.CONFIRMED.value, claimed_by_run=run_id, claimed_at=now)
            .returning(ActionProposalRow)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _proposal(row) if row else None

    async def list_for_conversation(self, conversation_id: UUID) -> builtins.list[ActionProposal]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(ActionProposalRow.conversation_id == conversation_id)
            .order_by(ActionProposalRow.created_at, ActionProposalRow.id)
        )
        return [_proposal(r) for r in (await self._s.execute(stmt)).scalars()]

    async def due_for_expiry(self, now: datetime) -> builtins.list[ActionProposal]:
        if self._tenant is None:
            return []
        stmt = self._base().where(
            ActionProposalRow.status == ProposalStatus.PENDING.value, ActionProposalRow.expires_at <= now
        )
        return [_proposal(r) for r in (await self._s.execute(stmt)).scalars()]


class SqlPendingInterruptRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def put(self, interrupt: PendingInterrupt) -> None:
        values = {
            "conversation_id": interrupt.conversation_id,
            "tenant_id": interrupt.tenant_id,
            "proposal_id": interrupt.proposal_id,
            "expires_at": interrupt.expires_at,
            "created_at": interrupt.created_at,
        }
        stmt = insert(PendingInterruptRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PendingInterruptRow.conversation_id],
            set_={
                "proposal_id": stmt.excluded.proposal_id,
                "expires_at": stmt.excluded.expires_at,
                "created_at": stmt.excluded.created_at,
            },
        )
        await self._s.execute(stmt)

    async def get(self, conversation_id: UUID) -> PendingInterrupt | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(
                select(PendingInterruptRow).where(
                    PendingInterruptRow.conversation_id == conversation_id,
                    PendingInterruptRow.tenant_id == self._tenant,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return PendingInterrupt(
            row.conversation_id, row.tenant_id, row.proposal_id, row.expires_at, row.created_at
        )

    async def delete(self, conversation_id: UUID) -> None:
        await self._s.execute(
            delete(PendingInterruptRow).where(
                PendingInterruptRow.conversation_id == conversation_id,
                PendingInterruptRow.tenant_id == self._tenant,
            )
        )
