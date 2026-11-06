"""ChatService (doc 09 §2.2/§3): conversations under RLS, one run per conversation, pseudonymised persistence with
provenance, SSE event mapping with streaming re-identification, run and provider-event records, retention."""

from __future__ import annotations

import asyncio
import builtins
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from leave_assistant.application.assistant.models import ActionProposal
from leave_assistant.application.assistant.proposals import ProposalCard, ProposalService, card_json
from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, ResourceRef, authorize, require
from leave_assistant.application.chat.context import RunContext
from leave_assistant.application.chat.models import (
    AgentRun,
    Conversation,
    ConversationMessage,
    ConversationStatus,
    MessageRole,
    ProviderEventRecord,
)
from leave_assistant.application.chat.ports import (
    AgentEvent,
    AgentRunner,
    FinalEvent,
    InterruptEvent,
    StatusEvent,
    ToolEvent,
)
from leave_assistant.application.documents.policy import reader_for, scope_for
from leave_assistant.application.errors import (
    ApplicationError,
    InvalidInputError,
    NotFoundError,
)
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import (
    Clock,
    IdGenerator,
    KnownEntity,
    LanguageDetector,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.privacy.reidentification import ReidentificationService
from leave_assistant.application.privacy.service import PseudonymisationService
from leave_assistant.application.security.policy import SecurityPolicyService
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.agent.budget import TokenBudgets
from leave_assistant.domain.agent.proposals import ProposalStatus
from leave_assistant.domain.agent.streaming import PLACEHOLDER, BufferedReidentifier
from leave_assistant.domain.agent.types import AllProvidersFailed, Outcome, ProviderError
from leave_assistant.domain.documents.visibility import can_read
from leave_assistant.domain.privacy.types import LlmSafeText, PlaceholderOccurrence, parse_placeholder
from leave_assistant.domain.tenancy import Principal

MAX_MESSAGE_CHARS = 2000
RUN_LOCK = timedelta(minutes=3)
TITLE_CHARS = 60
TOKEN_CHUNK = 24  # characters per SSE token event after verification (decision 4)


class RunInProgress(ApplicationError):
    problem_type = "chat/run-in-progress"


@dataclass(frozen=True, slots=True)
class ChatEvent:
    """One SSE event: `name` is the `event:` field, `data` the JSON payload (doc 09 §3)."""

    name: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MessageView:
    message: ConversationMessage
    text: str  # re-identified for the principal
    card: ProposalCard | None = (
        None  # M7: the confirmation card this message carries, with its current status
    )


@dataclass(frozen=True, slots=True)
class ConversationView:
    conversation: Conversation
    title: str  # re-identified
    pending: ProposalCard | None = None  # M7: the open confirmation card, if any


@dataclass(frozen=True, slots=True)
class RunView:
    """Redacted run history (doc 04 §7): what happened, never prompt bodies or arguments."""

    run: AgentRun
    proposals: tuple[ActionProposal, ...] = ()


REPLAYED = {
    "el": "Αυτή η πρόταση έχει ήδη αποφασιστεί· δεν έγινε νέα ενέργεια.",
    "en": "This proposal was already decided; nothing new was done.",
}


class ChatService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        privacy: PseudonymisationService,
        reidentification: ReidentificationService,
        policies: SecurityPolicyService,
        language: LanguageDetector,
        agent: AgentRunner | None,
        *,
        proposals: ProposalService | None = None,
        budgets: TokenBudgets | None = None,
        retention_days: int = 90,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._privacy = privacy
        self._reid = reidentification
        self._policies = policies
        self._language = language
        self.agent = agent
        self.proposals = proposals
        self.budgets = budgets or TokenBudgets()
        self._retention_days = retention_days

    # --- conversations ---------------------------------------------------------------------------------------
    @staticmethod
    def _user(principal: Principal) -> tuple[UUID, UUID]:
        require(principal, Action.CHAT)
        if principal.tenant_id is None or principal.employee_id is None:
            raise NotFoundError("conversation not found")
        return principal.tenant_id, principal.employee_id

    async def _owned(self, uow: UnitOfWork, principal: Principal, conversation_id: UUID) -> Conversation:
        conversation = await uow.conversations.get(conversation_id)
        if conversation is None or conversation.employee_id != principal.employee_id:
            raise NotFoundError(
                "conversation not found"
            )  # another user's thread is indistinguishable from none
        if conversation.status is ConversationStatus.ARCHIVED:
            raise NotFoundError("conversation not found")
        return conversation

    async def create(self, principal: Principal) -> Conversation:
        tenant_id, employee_id = self._user(principal)
        now = self._clock.now()
        conversation = Conversation(
            id=self._ids.new_id(),
            tenant_id=tenant_id,
            employee_id=employee_id,
            user_id=principal.user_id,
            title="",
            status=ConversationStatus.OPEN,
            locale=principal.locale.value,
            created_at=now,
            last_activity_at=now,
        )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await uow.conversations.add(conversation)
            await uow.commit()
        return conversation

    async def list(
        self, principal: Principal, *, limit: int | None, cursor: str | None
    ) -> Page[ConversationView]:
        _, employee_id = self._user(principal)
        size = clamp_limit(limit)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            rows = await uow.conversations.list_for_employee(
                employee_id, limit=size + 1, after=decode_cursor(cursor)
            )
            views = [ConversationView(c, await self._reid_own(uow, principal, c.title)) for c in rows]
        return build_page(views, size, lambda v: (v.conversation.last_activity_at, v.conversation.id))

    async def get(self, principal: Principal, conversation_id: UUID) -> ConversationView:
        self._user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            conversation = await self._owned(uow, principal, conversation_id)
            title = await self._reid_own(uow, principal, conversation.title)
        pending = None
        if self.proposals is not None:
            proposal = await self.proposals.pending(principal, conversation_id)
            if proposal is not None:
                pending = await self.proposals.card(principal, proposal.id)
        return ConversationView(conversation, title, pending)

    async def archive(self, principal: Principal, conversation_id: UUID) -> None:
        self._user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            conversation = await self._owned(uow, principal, conversation_id)
            conversation.status = ConversationStatus.ARCHIVED
            await uow.conversations.update(conversation)
            await self._audit.record(
                uow,
                principal,
                "chat.conversation.archived",
                resource_type="conversation",
                resource_id=conversation.id,
            )
            await uow.commit()

    async def messages(self, principal: Principal, conversation_id: UUID) -> builtins.list[MessageView]:
        self._user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await self._owned(uow, principal, conversation_id)
            stored = await uow.messages.list_for_conversation(conversation_id)
            readable = await self._readable_versions(uow, principal, stored)
            views: list[MessageView] = []
            for message in stored:
                occurrences = _occurrences(message.provenance)
                text = await self._reid.reidentify(
                    uow,
                    principal,
                    LlmSafeText(message.content),
                    occurrences,
                    readable_sources=readable,
                    authored_sources=frozenset({str(conversation_id)}),
                )
                views.append(MessageView(message, text))
        if self.proposals is not None:
            ids = [v.message.proposal_id for v in views if v.message.proposal_id is not None]
            if ids:
                cards = await self.proposals.cards(principal, [i for i in ids if i is not None])
                views = [
                    MessageView(v.message, v.text, cards.get(v.message.proposal_id))
                    if v.message.proposal_id is not None and v.message.outcome == Outcome.INTERRUPTED.value
                    else v
                    for v in views
                ]
        return views

    async def runs(self, principal: Principal, conversation_id: UUID) -> builtins.list[RunView]:
        """The owner sees their runs; HR and company administrators may read any conversation's runs for audit
        (doc 09 §2.2), which is itself audited. Others get 404 — the conversation is invisible."""
        require(principal, Action.CHAT)
        if principal.tenant_id is None:
            raise NotFoundError("conversation not found")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            conversation = await uow.conversations.get(conversation_id)
            if conversation is None:
                raise NotFoundError("conversation not found")
            if conversation.employee_id != principal.employee_id:
                resource = ResourceRef(
                    "conversation",
                    conversation.id,
                    tenant_id=conversation.tenant_id,
                    owner_employee_id=conversation.employee_id,
                )
                if not authorize(principal, Action.READ_RUN_HISTORY, resource).allowed:
                    raise NotFoundError("conversation not found")
                await self._audit.record(
                    uow,
                    principal,
                    "chat.runs.viewed",
                    resource_type="conversation",
                    resource_id=conversation.id,
                )
            runs = await uow.agent_runs.list_for_conversation(conversation_id)
            proposals = await uow.proposals.list_for_conversation(conversation_id)
            await uow.commit()
        return [RunView(r, tuple(p for p in proposals if r.id in (p.run_id, p.claimed_by_run))) for r in runs]

    # --- a turn ----------------------------------------------------------------------------------------------
    @staticmethod
    def _today(now: datetime, timezone: str | None) -> date:
        try:
            return now.astimezone(ZoneInfo(timezone or "UTC")).date()
        except ZoneInfoNotFoundError:
            return now.date()

    async def send(
        self, principal: Principal, conversation_id: UUID, text: str, *, request_id: str | None
    ) -> AsyncIterator[ChatEvent]:
        """Validates and locks before the first event (errors become HTTP problems); then streams."""
        tenant_id, employee_id = self._user(principal)
        cleaned = text.strip()
        if not cleaned or len(cleaned) > MAX_MESSAGE_CHARS:
            raise InvalidInputError(
                f"message must be 1–{MAX_MESSAGE_CHARS} characters",
                errors=[{"loc": ["body", "text"], "msg": f"1–{MAX_MESSAGE_CHARS} characters"}],
            )
        if self.agent is None:
            raise ApplicationError(
                "the assistant is not configured", problem_type="chat/provider-unavailable"
            )
        now = self._clock.now()
        context_t = TenantContext.for_principal(principal)
        async with self._uow(context_t) as uow:
            conversation = await self._owned(uow, principal, conversation_id)
            if not await uow.conversations.try_lock(conversation.id, until=now + RUN_LOCK, now=now):
                raise RunInProgress("another message is still being answered in this conversation")
            policy = await self._policies.policy_for(uow, tenant_id)
            tenant = await uow.tenants.get(tenant_id)
            known = await _known_entities(uow)
            turn = await uow.agent_runs.count_for_conversation(conversation.id) + 1
            await uow.commit()
        today = self._today(now, tenant.timezone if tenant else None)
        language = self._language.detect(cleaned)
        try:
            if self.proposals is not None:
                # a new message starts a fresh graph run, which drops an open interrupt: its proposal follows
                await self.proposals.supersede_pending(principal, conversation.id)
            findings = await asyncio.to_thread(self._privacy.detect, cleaned, language=language, known=known)
        except Exception:
            await self._unlock(context_t, conversation.id)
            raise
        return self._run(
            principal,
            conversation,
            turn=turn,
            block_threshold=policy.user_input_block_threshold,
            request_id=request_id,
            today=today,
            user=(cleaned, findings),
        )

    async def resume(
        self,
        principal: Principal,
        conversation_id: UUID,
        *,
        proposal_id: UUID,
        decision: str,
        payload_hash: str,
        edits: dict[str, Any] | None,
        request_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        """Continue a paused run with the user's decision (doc 09 §2.2). The proposal id is the idempotency key: a
        decided proposal replays its stored outcome without running anything (S25)."""
        tenant_id, _ = self._user(principal)
        if self.agent is None or self.proposals is None:
            raise ApplicationError(
                "the assistant is not configured", problem_type="chat/provider-unavailable"
            )
        now = self._clock.now()
        context_t = TenantContext.for_principal(principal)
        async with self._uow(context_t) as uow:
            conversation = await self._owned(uow, principal, conversation_id)
        proposal = await self.proposals.check_resumable(principal, conversation_id, proposal_id, payload_hash)
        if proposal.status is not ProposalStatus.PENDING:
            return self._replay(principal, conversation, proposal)
        async with self._uow(context_t) as uow:
            if not await uow.conversations.try_lock(conversation.id, until=now + RUN_LOCK, now=now):
                raise RunInProgress("another message is still being answered in this conversation")
            policy = await self._policies.policy_for(uow, tenant_id)
            tenant = await uow.tenants.get(tenant_id)
            turn = await uow.agent_runs.count_for_conversation(conversation.id) + 1
            await uow.commit()
        payload = {
            "proposal_id": str(proposal_id),
            "decision": decision,
            "payload_hash": payload_hash,
            "edits": {k: v for k, v in (edits or {}).items() if v not in (None, "")},
        }
        return self._run(
            principal,
            conversation,
            turn=turn,
            block_threshold=policy.user_input_block_threshold,
            request_id=request_id,
            today=self._today(now, tenant.timezone if tenant else None),
            decision=payload,
            proposal_id=proposal_id,
        )

    async def _replay(
        self, principal: Principal, conversation: Conversation, proposal: ActionProposal
    ) -> AsyncIterator[ChatEvent]:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            stored = [
                m
                for m in await uow.messages.list_for_conversation(conversation.id)
                if m.proposal_id == proposal.id and m.outcome != Outcome.INTERRUPTED.value
            ]
            text = REPLAYED.get(principal.locale.value, REPLAYED["en"])
            if stored:
                last = stored[-1]
                text = await self._reid.reidentify(
                    uow, principal, LlmSafeText(last.content), _occurrences(last.provenance)
                )
        outcome = {
            ProposalStatus.EXECUTED: Outcome.EXECUTED,
            ProposalStatus.FAILED: Outcome.FAILED,
        }.get(proposal.status, Outcome.REJECTED)
        yield ChatEvent("token", {"text": text})
        yield ChatEvent("done", {"run_id": None, "outcome": outcome.value, "replayed": True, "usage": {}})

    async def _run(  # noqa: PLR0912, PLR0915 - one linear protocol
        self,
        principal: Principal,
        conversation: Conversation,
        *,
        turn: int,
        block_threshold: float,
        request_id: str | None,
        today: date,
        user: tuple[str, Sequence[Any]] | None = None,
        decision: dict[str, Any] | None = None,
        proposal_id: UUID | None = None,
    ) -> AsyncIterator[ChatEvent]:
        assert (
            principal.tenant_id is not None and principal.employee_id is not None and self.agent is not None
        )
        context_t = TenantContext.for_principal(principal)
        run_id = self._ids.new_id()
        started = time.monotonic()
        run = RunContext(
            principal=principal,
            tenant_id=principal.tenant_id,
            employee_id=principal.employee_id,
            thread_id=conversation.id,
            locale=principal.locale.value,
            request_id=request_id,
            block_threshold=block_threshold,
            budgets=self.budgets,
            run_id=run_id,
            today=today,
        )
        final: FinalEvent | None = None
        paused: InterruptEvent | None = None
        outcome = Outcome.ERROR.value
        user_occurrences: tuple[PlaceholderOccurrence, ...] = ()
        try:
            events: AsyncIterator[AgentEvent]
            if user is not None:
                raw, findings = user
                async with self._uow(context_t) as uow:
                    pseudo = await self._privacy.apply(
                        uow,
                        principal.tenant_id,
                        raw,
                        findings,
                        source_kind="user_message",
                        source_id=str(conversation.id),
                    )
                    user_occurrences = pseudo.occurrences
                    now = self._clock.now()
                    await uow.messages.add(
                        ConversationMessage(
                            id=self._ids.new_id(),
                            tenant_id=principal.tenant_id,
                            conversation_id=conversation.id,
                            role=MessageRole.USER,
                            content=str(pseudo.safe),
                            created_at=now,
                            provenance=_provenance(pseudo.occurrences),
                            run_id=run_id,
                        )
                    )
                    current = await self._owned(uow, principal, conversation.id)
                    if not current.title:
                        current.title = _title(str(pseudo.safe))
                    current.last_activity_at = now
                    await uow.conversations.update(current)
                    await uow.commit()
                events = self.agent.run(run, pseudo.safe)
            else:
                assert decision is not None
                events = self.agent.resume(run, decision)
            yield ChatEvent("run_started", {"run_id": str(run_id), "turn": turn})
            async for event in events:
                if isinstance(event, StatusEvent):
                    yield ChatEvent("status", {"stage": event.stage})
                elif isinstance(event, ToolEvent):
                    yield ChatEvent("tool_call", {"name": event.name, "status": event.status})
                elif isinstance(event, InterruptEvent):
                    paused = event
                else:
                    final = event
            if paused is not None:
                outcome = Outcome.INTERRUPTED.value
                async for chat_event in self._emit_interrupt(principal, conversation, paused, run_id):
                    yield chat_event
                return  # doc 09 §3: the stream ends after the interrupt event
            if final is None:
                raise RuntimeError("the agent produced no answer")
            outcome = final.answer.outcome.value
            if final.answer.outcome is Outcome.BLOCKED:
                await self._audit_blocked(principal, conversation, run)
            async for chat_event in self._emit_answer(
                principal, conversation, final, user_occurrences, run_id, proposal_id=proposal_id
            ):
                yield chat_event
            yield ChatEvent(
                "done",
                {
                    "run_id": str(run_id),
                    "outcome": outcome,
                    "usage": {
                        "input_tokens": run.metrics.input_tokens,
                        "output_tokens": run.metrics.output_tokens,
                        "provider": run.metrics.provider,
                        "model": run.metrics.model,
                    },
                },
            )
        except AllProvidersFailed as exc:
            run.metrics.details["error"] = str(exc)[:500]
            yield ChatEvent(
                "error",
                {
                    "type": "chat/provider-unavailable",
                    "title": "The assistant is temporarily unavailable",
                    "request_id": request_id,
                },
            )
        except ProviderError as exc:
            run.metrics.details["error"] = str(exc)[:500]
            yield ChatEvent(
                "error",
                {
                    "type": f"chat/{exc.kind.value.replace('_', '-')}",
                    "title": "The assistant could not answer",
                    "request_id": request_id,
                },
            )
        except Exception as exc:  # noqa: BLE001 - any failure ends the stream with a problem-style event
            run.metrics.details["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            yield ChatEvent(
                "error", {"type": "internal-error", "title": "Something went wrong", "request_id": request_id}
            )
        finally:
            if proposal_id is not None and self.proposals is not None:
                try:  # a claim this run still holds after its retries gave up → failed (or executed, if it was)
                    await self.proposals.release(principal, proposal_id, run_id=run_id)
                except Exception as exc:  # noqa: BLE001 - never mask the run's own outcome
                    run.metrics.details["release_error"] = type(exc).__name__
            await self._finish(principal, conversation, run, run_id, turn, final, outcome, started, paused)

    async def _audit_blocked(self, principal: Principal, conversation: Conversation, run: RunContext) -> None:
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await self._audit.record(
                uow,
                principal,
                "agent.input.blocked",
                resource_type="conversation",
                resource_id=conversation.id,
                details={
                    "score": run.metrics.details.get("input_risk_score"),
                    "families": run.metrics.details.get("blocked_families", []),
                },
            )
            await uow.commit()

    async def _emit_interrupt(
        self, principal: Principal, conversation: Conversation, paused: InterruptEvent, run_id: UUID
    ) -> AsyncIterator[ChatEvent]:
        assert principal.tenant_id is not None and self.proposals is not None
        proposal = await self.proposals.open_interrupt(principal, paused.proposal_id)
        occurrences = [o for o in _occurrences(paused.placeholders) if o.placeholder in paused.text]
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await uow.messages.add(
                ConversationMessage(
                    id=self._ids.new_id(),
                    tenant_id=principal.tenant_id,
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    content=paused.text,
                    created_at=self._clock.now(),
                    provenance=_provenance(occurrences),
                    run_id=run_id,
                    outcome=Outcome.INTERRUPTED.value,
                    proposal_id=proposal.id,
                )
            )
            text = await self._reid.reidentify(uow, principal, LlmSafeText(paused.text), occurrences)
            await uow.commit()
        for start in range(0, len(text), TOKEN_CHUNK):
            yield ChatEvent("token", {"text": text[start : start + TOKEN_CHUNK]})
        card = await self.proposals.card(principal, proposal.id)
        yield ChatEvent(
            "interrupt",
            {
                "proposal_id": str(proposal.id),
                "kind": proposal.kind.value,
                "card": card_json(card),
                "expires_at": proposal.expires_at.isoformat(),
            },
        )

    async def _emit_answer(
        self,
        principal: Principal,
        conversation: Conversation,
        final: FinalEvent,
        user_occurrences: Sequence[PlaceholderOccurrence],
        run_id: UUID,
        *,
        proposal_id: UUID | None = None,
    ) -> AsyncIterator[ChatEvent]:
        assert principal.tenant_id is not None
        answer = final.answer
        versions = set(final.source_version_ids)
        occurrences = _answer_occurrences(answer.text, user_occurrences, versions, final.placeholders)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            resolved: dict[str, str] = {}
            for placeholder in dict.fromkeys(m.group(0) for m in PLACEHOLDER.finditer(answer.text)):
                resolved[placeholder] = await self._reid.reidentify(
                    uow,
                    principal,
                    LlmSafeText(placeholder),
                    occurrences,
                    readable_sources=frozenset(versions),
                )
            citations = [_citation_json(c) for c in answer.citations]
            await uow.messages.add(
                ConversationMessage(
                    id=self._ids.new_id(),
                    tenant_id=principal.tenant_id,
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    content=answer.text,
                    created_at=self._clock.now(),
                    citations=citations,
                    provenance=_provenance(occurrences),
                    run_id=run_id,
                    outcome=answer.outcome.value,
                    proposal_id=proposal_id or final.proposal_id,
                )
            )
            await uow.commit()
        stream = BufferedReidentifier(lambda p: resolved.get(p, "[unavailable]"))
        for start in range(0, len(answer.text), TOKEN_CHUNK):
            piece = stream.feed(answer.text[start : start + TOKEN_CHUNK])
            if piece:
                yield ChatEvent("token", {"text": piece})
        rest = stream.flush()
        if rest:
            yield ChatEvent("token", {"text": rest})
        if citations:
            yield ChatEvent("citations", {"items": citations})

    async def _finish(
        self,
        principal: Principal,
        conversation: Conversation,
        run: RunContext,
        run_id: UUID,
        turn: int,
        final: FinalEvent | None,
        outcome: str,
        started: float,
        paused: InterruptEvent | None = None,
    ) -> None:
        assert principal.tenant_id is not None
        tenant_id = principal.tenant_id
        now = self._clock.now()
        metrics = run.metrics
        tool_trace = list(metrics.details.pop("tool_trace", []))
        head = final or paused
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await uow.agent_runs.add(
                AgentRun(
                    id=run_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    turn=turn,
                    intent=head.intent if head else None,
                    language=head.language if head else None,
                    route=final.route if final else ("approval" if paused else None),
                    provider_used=metrics.provider,
                    model_used=metrics.model,
                    failovers=metrics.failovers,
                    input_tokens=metrics.input_tokens,
                    output_tokens=metrics.output_tokens,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    outcome=outcome,
                    created_at=now,
                    tool_trace=tool_trace,
                    details={
                        **metrics.details,
                        "llm_latency_ms": metrics.llm_latency_ms,
                        "stages": metrics.stages,
                    },
                )
            )
            await uow.provider_events.add_many(
                [
                    ProviderEventRecord(
                        id=self._ids.new_id(),
                        tenant_id=tenant_id,
                        provider=e.provider,
                        model=e.model,
                        event=e.event,
                        reason=e.reason,
                        occurred_at=now,
                        latency_ms=e.latency_ms,
                        run_id=run_id,
                    )
                    for call in metrics.calls
                    for e in call.events
                    if e.event != "selected" or call.failovers
                ]
            )
            await uow.conversations.unlock(conversation.id)
            await uow.commit()

    async def _unlock(self, context: TenantContext, conversation_id: UUID) -> None:
        async with self._uow(context) as uow:
            await uow.conversations.unlock(conversation_id)
            await uow.commit()

    # --- retention (Q-08/Q-14: 90 days, automatic) --------------------------------------------------------------
    async def cleanup_expired(self) -> dict[str, int]:
        cutoff = self._clock.now() - timedelta(days=self._retention_days)
        async with self._uow(
            TenantContext(tenant_id=None, user_id="conversation.cleanup", platform_admin=True)
        ) as uow:
            tenants = await uow.tenants.list(limit=500, after=None)
        deleted: list[UUID] = []
        for tenant in tenants:
            context = TenantContext(tenant_id=tenant.id, user_id="conversation.cleanup", roles=("system",))
            async with self._uow(context) as uow:
                for conversation in await uow.conversations.expired(inactive_before=cutoff):
                    await uow.conversations.delete(conversation.id)
                    deleted.append(conversation.id)
                if deleted:
                    await self._audit.record(
                        uow,
                        None,
                        "chat.retention.deleted",
                        resource_type="tenant",
                        resource_id=tenant.id,
                        details={"conversations": len(deleted)},
                    )
                await uow.commit()
        if deleted and self.agent is not None:
            await self.agent.delete_threads(deleted)
        return {"conversations": len(deleted)}

    # --- helpers --------------------------------------------------------------------------------------------
    async def _reid_own(self, uow: UnitOfWork, principal: Principal, text: str) -> str:
        """Titles come from the user's own messages: their placeholders are user-message occurrences."""
        if not PLACEHOLDER.search(text):
            return text
        occurrences = [
            _occurrence(p, "user_message", None) for p in {m.group(0) for m in PLACEHOLDER.finditer(text)}
        ]
        return await self._reid.reidentify(uow, principal, LlmSafeText(text), occurrences)

    @staticmethod
    async def _readable_versions(
        uow: UnitOfWork, principal: Principal, messages: Sequence[ConversationMessage]
    ) -> frozenset[str]:
        """Documents may have been restricted since the answer: readability is re-checked at read time."""
        ids = {
            str(p["id"])
            for m in messages
            for p in m.provenance.values()
            if p.get("kind") == "document_version" and p.get("id")
        }
        readable: set[str] = set()
        reader = reader_for(principal)
        for version_id in ids:
            version = await uow.document_versions.get(UUID(version_id))
            document = await uow.documents.get(version.document_id) if version else None
            if (
                document is not None
                and document.deleted_at is None
                and can_read(reader, await scope_for(uow, document))
            ):
                readable.add(version_id)
        return frozenset(readable)


async def _known_entities(uow: UnitOfWork) -> list[KnownEntity]:
    return [
        KnownEntity(e.id, e.first_name, e.last_name, e.email, e.department_id)
        for e in await uow.employees.list_active()
    ]


def _title(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= TITLE_CHARS else flat[: TITLE_CHARS - 1].rstrip() + "…"


def _occurrence(placeholder: str, kind: str, source_id: str | None) -> PlaceholderOccurrence:
    span = parse_placeholder(placeholder)
    if span is None:
        raise ValueError(f"not a placeholder: {placeholder}")
    return PlaceholderOccurrence(
        placeholder=placeholder, entity_type=span[0], source_kind=kind, source_id=source_id
    )


def _provenance(occurrences: Sequence[PlaceholderOccurrence]) -> dict[str, dict[str, Any]]:
    return {o.placeholder: {"kind": o.source_kind, "id": o.source_id} for o in occurrences}


def _occurrences(provenance: dict[str, dict[str, Any]]) -> list[PlaceholderOccurrence]:
    out: list[PlaceholderOccurrence] = []
    for placeholder, source in provenance.items():
        try:
            out.append(_occurrence(placeholder, str(source.get("kind") or "tool_result"), source.get("id")))
        except ValueError:
            continue
    return out


def _answer_occurrences(
    text: str,
    user: Sequence[PlaceholderOccurrence],
    versions: set[str],
    tools: dict[str, dict[str, Any]] | None = None,
) -> list[PlaceholderOccurrence]:
    """Placeholders the user typed keep their provenance; tool results carry their own (M7: the doc 08 §7.4 matrix
    decides, e.g. a manager sees their own department's names); the rest came from the retrieved documents
    (readable by construction); anything else is model-invented and resolves to `[unavailable]`."""
    own = {o.placeholder: o for o in user}
    tool_sources = tools or {}
    source = sorted(versions)[0] if versions else None
    out: list[PlaceholderOccurrence] = []
    for placeholder in dict.fromkeys(m.group(0) for m in PLACEHOLDER.finditer(text)):
        if placeholder in own:
            out.append(own[placeholder])
        elif placeholder in tool_sources:
            origin = tool_sources[placeholder]
            try:
                out.append(_occurrence(placeholder, str(origin.get("kind", "tool_result")), origin.get("id")))
            except ValueError:
                continue
        elif source is not None:
            try:
                out.append(_occurrence(placeholder, "document_version", source))
            except ValueError:
                continue
    return out


def _citation_json(c: Any) -> dict[str, Any]:
    return {
        "n": c.n,
        "chunk_id": str(c.chunk_id),
        "document_id": str(c.document_id),
        "version_id": str(c.version_id),
        "version_no": c.version_no,
        "title": c.title,
        "heading_path": list(c.heading_path),
        "page": c.page,
    }
