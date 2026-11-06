"""ProposalService (doc 04 §6, ADR-0009): the confirm-before-execute record and its exactly-once execution.

`prepare_*` tools create a proposal with the validated payload and a hash; only the owner's confirmation executes it,
through the same application services as the REST API (they re-validate everything inside their own transaction).

Exactly once:
1. an atomic claim `pending → confirmed` (only one caller wins; it records the run that claimed it);
2. the service call runs with `acting_via_agent(proposal_id)`, so the request event it writes names the proposal;
3. the outcome is stored (`executed` / `failed`). A second confirmation returns the stored outcome; a claim left
   behind by a crash is recovered from the request event (effect happened → executed) or re-executed (it did not).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from leave_assistant.application.assistant.models import ActionProposal, PendingInterrupt
from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.errors import ApplicationError, ConflictError, NotFoundError
from leave_assistant.application.leave.approvals import ApprovalService
from leave_assistant.application.leave.commands import CancelRequest, Decide, ModifyRequest, SubmitRequest
from leave_assistant.application.leave.common import load_tenant, tenant_user
from leave_assistant.application.leave.requests import LeaveRequestService
from leave_assistant.application.leave.views import RequestDetail
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.request_context import acting_via_agent
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.agent.proposals import (
    EDITABLE,
    TERMINAL,
    ProposalKind,
    ProposalStatus,
    payload_hash,
)
from leave_assistant.domain.errors import DomainError
from leave_assistant.domain.leave.request import DecisionChannel
from leave_assistant.domain.tenancy import Principal

STALE_CLAIM = timedelta(minutes=2)


class ProposalExpired(ApplicationError):
    problem_type = "chat/proposal-expired"


class ProposalHashMismatch(ApplicationError):
    problem_type = "chat/proposal-hash-mismatch"


class NoPendingProposal(ApplicationError):
    problem_type = "chat/no-pending-proposal"


class ExecutionInterrupted(ApplicationError):
    """A non-business failure after the claim (database, network). The claim stays with this run, so a retry of
    the same run may continue; the business service re-validates and the request event prevents a double effect."""

    problem_type = "chat/execution-interrupted"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ProposalStatus  # executed | failed
    outcome: dict[str, Any]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ProposalCard:
    """What the confirmation card shows (doc 09 §4 ProposalCard), rendered at read time from the database row."""

    proposal_id: UUID
    kind: ProposalKind
    status: ProposalStatus
    payload_hash: str
    expires_at: datetime
    summary: dict[str, Any]
    actions: tuple[str, ...]
    outcome: dict[str, Any] = field(default_factory=dict)


def days(value: Decimal | int | float | str | None) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)).normalize(), "f")
    return text


def outcome_of(detail: RequestDetail) -> dict[str, Any]:
    request = detail.request
    return {
        "request_id": str(request.id),
        "request_number": request.request_number,
        "status": request.status.value,
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "working_days": days(request.working_days),
        "pending_steps": [s.approver_role.value for s in detail.steps if s.status.value == "pending"],
    }


class ProposalService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        requests: LeaveRequestService,
        approvals: ApprovalService,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._requests = requests
        self._approvals = approvals

    # --- creation ------------------------------------------------------------------------------------------------
    async def create(
        self,
        principal: Principal,
        *,
        conversation_id: UUID,
        run_id: UUID | None,
        kind: ProposalKind,
        payload: dict[str, Any],
    ) -> ActionProposal:
        tenant_id, employee_id = tenant_user(principal)
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            conversation = await uow.conversations.get(conversation_id)
            if conversation is None or conversation.employee_id != employee_id:
                raise NotFoundError("conversation not found")
            tenant = await load_tenant(uow, tenant_id)
            proposal = ActionProposal(
                id=self._ids.new_id(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                employee_id=employee_id,
                kind=kind,
                payload=payload,
                payload_hash=payload_hash(payload),
                status=ProposalStatus.PENDING,
                expires_at=now + timedelta(minutes=tenant.settings.proposal_ttl_minutes),
                created_at=now,
                run_id=run_id,
            )
            await uow.proposals.add(proposal)
            await self._audit.record(
                uow,
                principal,
                "agent.proposal.created",
                resource_type="action_proposal",
                resource_id=proposal.id,
                details={"kind": kind.value, "conversation_id": str(conversation_id)},
            )
            await uow.commit()
        return proposal

    async def open_interrupt(self, principal: Principal, proposal_id: UUID) -> ActionProposal:
        """The graph paused on this proposal: mirror the interrupt for the UI and cleanup (doc 07 §4.6)."""
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            proposal = await self._owned(uow, principal, proposal_id)
            await uow.pending_interrupts.put(
                PendingInterrupt(
                    conversation_id=proposal.conversation_id,
                    tenant_id=proposal.tenant_id,
                    proposal_id=proposal.id,
                    expires_at=proposal.expires_at,
                    created_at=self._clock.now(),
                )
            )
            await uow.commit()
        return proposal

    # --- reads ---------------------------------------------------------------------------------------------------
    async def _owned(self, uow: UnitOfWork, principal: Principal, proposal_id: UUID) -> ActionProposal:
        proposal = await uow.proposals.get(proposal_id)
        if proposal is None or proposal.employee_id != principal.employee_id:
            raise NotFoundError(
                "proposal not found"
            )  # someone else's proposal is indistinguishable from none
        return proposal

    async def get(self, principal: Principal, proposal_id: UUID) -> ActionProposal:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await self._owned(uow, principal, proposal_id)

    async def pending(self, principal: Principal, conversation_id: UUID) -> ActionProposal | None:
        """The open, unexpired proposal of a conversation (at most one)."""
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            interrupt = await uow.pending_interrupts.get(conversation_id)
            if interrupt is None:
                return None
            proposal = await uow.proposals.get(interrupt.proposal_id)
        if (
            proposal is None
            or proposal.employee_id != principal.employee_id
            or proposal.status is not ProposalStatus.PENDING
            or proposal.expires_at <= self._clock.now()
        ):
            return None
        return proposal

    async def cards(self, principal: Principal, proposal_ids: list[UUID]) -> dict[UUID, ProposalCard]:
        out: dict[UUID, ProposalCard] = {}
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            for proposal_id in dict.fromkeys(proposal_ids):
                proposal = await uow.proposals.get(proposal_id)
                if proposal is not None and proposal.employee_id == principal.employee_id:
                    out[proposal_id] = await self._card(uow, proposal)
        return out

    async def card(self, principal: Principal, proposal_id: UUID) -> ProposalCard:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await self._card(uow, await self._owned(uow, principal, proposal_id))

    async def _card(self, uow: UnitOfWork, proposal: ActionProposal) -> ProposalCard:
        status = proposal.status
        if status is ProposalStatus.PENDING and proposal.expires_at <= self._clock.now():
            status = ProposalStatus.EXPIRED
        summary = {
            k: v for k, v in proposal.payload.items() if k not in {"request_id", "step_id", "step_version"}
        }
        requester = proposal.payload.get("requester_employee_id")
        if requester:
            employee = await uow.employees.get(UUID(str(requester)))
            summary["requester"] = employee.display_name if employee else None
            summary.pop("requester_employee_id", None)
        actions: tuple[str, ...] = ()
        if status is ProposalStatus.PENDING:
            actions = ("confirm", "reject", "edit") if proposal.kind in EDITABLE else ("confirm", "reject")
        return ProposalCard(
            proposal_id=proposal.id,
            kind=proposal.kind,
            status=status,
            payload_hash=proposal.payload_hash,
            expires_at=proposal.expires_at,
            summary=summary,
            actions=actions,
            outcome=proposal.outcome,
        )

    # --- decisions -----------------------------------------------------------------------------------------------
    async def check_resumable(
        self, principal: Principal, conversation_id: UUID, proposal_id: UUID, expected_hash: str
    ) -> ActionProposal:
        """Before a resume stream starts (problems become HTTP errors). A proposal that was already decided is
        returned as is — the caller replays its stored outcome instead of running the graph (S25)."""
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            proposal = await self._owned(uow, principal, proposal_id)
            if proposal.conversation_id != conversation_id:
                raise NotFoundError("proposal not found")
            if proposal.status is not ProposalStatus.PENDING:
                return proposal
            interrupt = await uow.pending_interrupts.get(conversation_id)
            if interrupt is None or interrupt.proposal_id != proposal_id:
                raise NoPendingProposal("this proposal is no longer waiting for a decision")
            if proposal.expires_at <= now:
                await self._expire(uow, principal, proposal, now)
                await uow.commit()
                raise ProposalExpired("the proposal expired; ask again to prepare a new one")
            if expected_hash != proposal.payload_hash:
                raise ProposalHashMismatch("the proposal changed since it was shown; reload it")
            return proposal

    async def reject(
        self, principal: Principal, proposal_id: UUID, *, reason: str = "user"
    ) -> ActionProposal:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            proposal = await self._owned(uow, principal, proposal_id)
            if proposal.status is ProposalStatus.PENDING:
                await self._close(uow, principal, proposal, ProposalStatus.REJECTED, {"reason": reason})
                await uow.commit()
            return proposal

    async def supersede_pending(self, principal: Principal, conversation_id: UUID) -> list[UUID]:
        """A new message drops the graph's open interrupt (LangGraph starts a fresh run): the proposal follows."""
        closed: list[UUID] = []
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            for proposal in await uow.proposals.list_for_conversation(conversation_id):
                if (
                    proposal.status is ProposalStatus.PENDING
                    and proposal.employee_id == principal.employee_id
                ):
                    await self._close(
                        uow, principal, proposal, ProposalStatus.REJECTED, {"reason": "superseded"}
                    )
                    closed.append(proposal.id)
            await uow.pending_interrupts.delete(conversation_id)
            await uow.commit()
        return closed

    async def _close(
        self,
        uow: UnitOfWork,
        principal: Principal | None,
        proposal: ActionProposal,
        status: ProposalStatus,
        outcome: dict[str, Any],
    ) -> None:
        proposal.status = status
        proposal.decided_at = self._clock.now()
        proposal.outcome = outcome
        await uow.proposals.update(proposal)
        await uow.pending_interrupts.delete(proposal.conversation_id)
        reason = outcome.get("reason")
        action = "agent.proposal.superseded" if reason == "superseded" else f"agent.proposal.{status.value}"
        await self._audit.record(
            uow,
            principal,
            action,
            resource_type="action_proposal",
            resource_id=proposal.id,
            details={"kind": proposal.kind.value, **outcome},
        )

    async def _expire(
        self, uow: UnitOfWork, principal: Principal | None, proposal: ActionProposal, now: datetime
    ) -> None:
        await self._close(uow, principal, proposal, ProposalStatus.EXPIRED, {"reason": "expired"})

    async def expire_due(self) -> dict[str, int]:
        """Periodic sweep (worker): pending proposals past their expiry and their interrupts."""
        now = self._clock.now()
        async with self._uow(
            TenantContext(tenant_id=None, user_id="proposal.expire", platform_admin=True)
        ) as uow:
            tenants = await uow.tenants.list(limit=500, after=None)
        expired = 0
        for tenant in tenants:
            context = TenantContext(tenant_id=tenant.id, user_id="proposal.expire", roles=("system",))
            async with self._uow(context) as uow:
                for proposal in await uow.proposals.due_for_expiry(now):
                    await self._expire(uow, None, proposal, now)
                    expired += 1
                await uow.commit()
        return {"expired": expired}

    # --- execution -----------------------------------------------------------------------------------------------
    async def execute(self, principal: Principal, proposal_id: UUID, *, run_id: UUID) -> ExecutionResult:
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            proposal = await self._owned(uow, principal, proposal_id)
            claimed = await uow.proposals.claim(proposal_id, run_id=run_id, now=now)
            if claimed is None:
                current = await self._owned(uow, principal, proposal_id)
                stored = self._stored(current, run_id=run_id, now=now)
                if stored is not None:
                    return stored
                proposal = current  # our own claim (a retry of this run) or a recovered stale one
                if current.claimed_by_run != run_id:
                    current.claimed_by_run, current.claimed_at = run_id, now
                    await uow.proposals.update(current)
            else:
                proposal = claimed
            await uow.pending_interrupts.delete(proposal.conversation_id)
            effect = await uow.request_events.find_by_proposal(proposal.id)
            await uow.commit()
        if effect is not None:  # the business change committed before a crash: record it, do not repeat it
            detail = await self._requests.get(principal, effect.request_id)
            return await self._finish(
                principal, proposal, ProposalStatus.EXECUTED, outcome_of(detail), detail
            )
        try:
            with acting_via_agent(proposal.id):
                detail = await self._dispatch(principal, proposal)
        except (ApplicationError, DomainError) as exc:  # a business rule or a permission said no
            outcome = _failure(exc)
            return await self._finish(principal, proposal, ProposalStatus.FAILED, outcome, None)
        except Exception as exc:
            raise ExecutionInterrupted(f"execution interrupted: {type(exc).__name__}") from exc
        return await self._finish(principal, proposal, ProposalStatus.EXECUTED, outcome_of(detail), detail)

    def _stored(self, proposal: ActionProposal, *, run_id: UUID, now: datetime) -> ExecutionResult | None:
        """Why a claim failed: a finished execution (replay), a decided or expired proposal, or a claim that this
        run (retry) or a crashed run (stale) left behind — the last two return None and the caller continues."""
        if proposal.status in {ProposalStatus.EXECUTED, ProposalStatus.FAILED}:
            return ExecutionResult(proposal.status, proposal.outcome, replayed=True)
        if proposal.status is ProposalStatus.EXPIRED or (
            proposal.status is ProposalStatus.PENDING and proposal.expires_at <= now
        ):
            raise ProposalExpired("the proposal expired; ask again to prepare a new one")
        if proposal.status in TERMINAL:
            raise NoPendingProposal("this proposal is no longer waiting for a decision")
        if proposal.status is ProposalStatus.CONFIRMED:
            stale = proposal.claimed_at is None or proposal.claimed_at < now - STALE_CLAIM
            if proposal.claimed_by_run == run_id or stale:
                return None
            raise ConflictError("this proposal is being executed", problem_type="chat/proposal-in-progress")
        raise NoPendingProposal("this proposal is no longer waiting for a decision")

    async def _dispatch(self, principal: Principal, proposal: ActionProposal) -> RequestDetail:
        p = proposal.payload
        if proposal.kind is ProposalKind.SUBMIT:
            return await self._requests.submit(
                principal,
                SubmitRequest(
                    leave_type_code=str(p["leave_type"]),
                    start_date=_date(p["start_date"]),
                    end_date=_date(p["end_date"]),
                    reason=p.get("reason") or None,
                    idempotency_key=f"proposal:{proposal.id}",
                ),
            )
        if proposal.kind is ProposalKind.MODIFY:
            return await self._requests.modify(
                principal,
                UUID(str(p["request_id"])),
                ModifyRequest(
                    start_date=_date(p["start_date"]),
                    end_date=_date(p["end_date"]),
                    version=int(p["version"]),
                ),
            )
        if proposal.kind is ProposalKind.CANCEL:
            return await self._requests.cancel(
                principal, UUID(str(p["request_id"])), CancelRequest(version=int(p["version"]))
            )
        decide = Decide(
            version=int(p["step_version"]), comment=p.get("comment") or None, via=DecisionChannel.AGENT
        )
        step_id = UUID(str(p["step_id"]))
        if proposal.kind is ProposalKind.APPROVE:
            return await self._approvals.approve(principal, step_id, decide)
        return await self._approvals.reject(principal, step_id, decide)

    async def _finish(
        self,
        principal: Principal,
        proposal: ActionProposal,
        status: ProposalStatus,
        outcome: dict[str, Any],
        detail: RequestDetail | None,
    ) -> ExecutionResult:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            current = await self._owned(uow, principal, proposal.id)
            if current.status in {ProposalStatus.EXECUTED, ProposalStatus.FAILED}:
                return ExecutionResult(current.status, current.outcome, replayed=True)
            current.status = status
            current.decided_at = self._clock.now()
            current.outcome = outcome
            if detail is not None:
                current.executed_request_id = detail.request.id
            await uow.proposals.update(current)
            await self._audit.record(
                uow,
                principal,
                "agent.proposal.executed",
                resource_type="action_proposal",
                resource_id=current.id,
                details={"kind": current.kind.value, "status": status.value, **_audit_outcome(outcome)},
            )
            await uow.commit()
        return ExecutionResult(status, outcome)

    async def release(self, principal: Principal, proposal_id: UUID, *, run_id: UUID) -> None:
        """After the retries of a run gave up: a claim this run still holds becomes `failed` — unless the request
        event shows the effect happened, then it is recorded as executed."""
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            proposal = await uow.proposals.get(proposal_id)
            if (
                proposal is None
                or proposal.status is not ProposalStatus.CONFIRMED
                or proposal.claimed_by_run != run_id
            ):
                return
            effect = await uow.request_events.find_by_proposal(proposal.id)
        if effect is not None:
            detail = await self._requests.get(principal, effect.request_id)
            await self._finish(principal, proposal, ProposalStatus.EXECUTED, outcome_of(detail), detail)
            return
        await self._finish(
            principal,
            proposal,
            ProposalStatus.FAILED,
            {"code": "internal-error", "detail": "execution failed"},
            None,
        )


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _failure(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "problem_type", None) or getattr(exc, "code", None) or "error"
    detail = getattr(exc, "detail", None) or str(exc)
    outcome: dict[str, Any] = {"code": str(code), "detail": str(detail)[:300]}
    errors = getattr(exc, "extra", {}).get("errors") if hasattr(exc, "extra") else None
    if errors:
        outcome["violations"] = errors
    params = getattr(exc, "params", None)
    if params:
        outcome["params"] = {k: str(v) for k, v in params.items()}
    return outcome


def _audit_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    return {k: outcome[k] for k in ("request_number", "status", "code") if k in outcome}


def card_json(card: ProposalCard) -> dict[str, Any]:
    """The SSE `interrupt` payload and `MessageOut.card` (doc 09 §4 ProposalCard)."""
    return {
        "proposal_id": str(card.proposal_id),
        "kind": card.kind.value,
        "status": card.status.value,
        "payload_hash": card.payload_hash,
        "expires_at": card.expires_at.isoformat(),
        "summary": card.summary,
        "actions": list(card.actions),
        "outcome": card.outcome,
    }
