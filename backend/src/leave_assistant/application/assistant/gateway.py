"""ToolGateway (doc 04 §5.2, ADR-0009): the only way a model's tool call reaches the business.

1. allowlist — tool name ∈ the role's catalogue (read-only for medium-risk input); otherwise denied and audited;
2. arguments — the tool's strict Pydantic model plus a date window; failures go back to the model to repair;
3. authorization — the services the handlers call apply the same `authorize()` as the REST API;
4. execution — service calls under the caller's tenant context (RLS); `prepare_*` creates a proposal only;
5. results — employees become stable vault placeholders by identity, free text (reasons, comments) and
   supporting-document contents are never returned, everything is size-capped;
6. trace — name, status, duration and reason per call (no arguments) for `agent_runs` and audit.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from leave_assistant.application.assistant.proposals import ProposalService, days
from leave_assistant.application.assistant.tools import (
    BY_NAME,
    AvailabilityArgs,
    BalanceArgs,
    DecisionArgs,
    LeaveArgs,
    ModificationArgs,
    NoArgs,
    PeriodArgs,
    Phase,
    PrepareLeaveArgs,
    RequestRefArgs,
    RequestsArgs,
    SearchArgs,
)
from leave_assistant.application.audit.entry import Outcome as AuditOutcome
from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.errors import (
    ApplicationError,
    AuthorizationError,
    InvalidInputError,
    NotFoundError,
)
from leave_assistant.application.leave.approvals import ApprovalService
from leave_assistant.application.leave.catalog import LeaveCatalogService
from leave_assistant.application.leave.commands import SubmitRequest
from leave_assistant.application.leave.common import load_tenant, tenant_user
from leave_assistant.application.leave.ledger import LedgerService
from leave_assistant.application.leave.planner import RoutingPlanner
from leave_assistant.application.leave.requests import LeaveRequestService
from leave_assistant.application.leave.team import TeamService
from leave_assistant.application.llm.ports import TokenCounter, ToolCall
from leave_assistant.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.privacy.reidentification import ReidentificationService
from leave_assistant.application.privacy.service import PseudonymisationService
from leave_assistant.application.retrieval.context import EvidencePolicy, build_context
from leave_assistant.application.retrieval.service import RetrievalService
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.application.tenancy.lookup import resolve_department
from leave_assistant.domain.agent.proposals import ProposalKind
from leave_assistant.domain.agent.streaming import PLACEHOLDER
from leave_assistant.domain.agent.types import CitationRef
from leave_assistant.domain.errors import DomainError
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.validation import ValidationReport
from leave_assistant.domain.leave.working_days import classify, end_for
from leave_assistant.domain.privacy.types import LlmSafeText, PlaceholderOccurrence, parse_placeholder
from leave_assistant.domain.tenancy import Employee, Principal

MAX_RESULT_CHARS = 2400
MAX_LIST_ITEMS = 12
PAST_WINDOW_DAYS = (
    60  # sick leave may be recorded after the fact; the rules engine decides the exact allowance
)
FUTURE_WINDOW_DAYS = 730
SEARCH_BUDGET_TOKENS = 600
STATUS_GROUPS: dict[str, frozenset[RequestStatus]] = {
    "pending": frozenset(
        {
            RequestStatus.SUBMITTED,
            RequestStatus.PENDING_MANAGER,
            RequestStatus.PENDING_HR,
            RequestStatus.MODIFICATION_PENDING,
        }
    ),
    "approved": frozenset({RequestStatus.APPROVED, RequestStatus.ACTIVE, RequestStatus.COMPLETED}),
    "rejected": frozenset({RequestStatus.REJECTED}),
    "cancelled": frozenset({RequestStatus.CANCELLED, RequestStatus.WITHDRAWN}),
}
CANCELLABLE = frozenset(
    {
        RequestStatus.SUBMITTED,
        RequestStatus.PENDING_MANAGER,
        RequestStatus.PENDING_HR,
        RequestStatus.APPROVED,
    }
)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Trusted, per call: built from the RunContext — never from model output."""

    principal: Principal
    conversation_id: UUID
    run_id: UUID
    today: date
    language: str
    read_only: bool = False


@dataclass(slots=True)
class ToolOutcome:
    call_id: str
    name: str
    status: str  # ok | denied | invalid | not_found | error
    result: dict[str, Any]
    placeholders: dict[str, dict[str, Any]] = field(default_factory=dict)
    proposal_id: UUID | None = None
    refs: tuple[CitationRef, ...] = ()
    source_version_ids: tuple[str, ...] = ()
    duration_ms: int = 0
    reason: str | None = None
    args_hash: str = ""

    def trace(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
            "args_hash": self.args_hash,
            "proposal": self.proposal_id is not None,
        }

    def text(self) -> str:
        return json.dumps(self.result, ensure_ascii=False, separators=(",", ":"), default=str)


def _args_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:4]:
        location = ".".join(str(p) for p in error.get("loc", ()) if p != "__root__")
        parts.append(f"{location}: {error.get('msg')}" if location else str(error.get("msg")))
    return "; ".join(parts)[:300]


def report_json(report: ValidationReport, *, leave_type: str, start: date, end: date) -> dict[str, Any]:
    balance = report.balance
    return {
        "valid": report.valid,
        "leave_type": leave_type,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "working_days": report.working_days,
        "excluded": [
            {"date": e.day.isoformat(), "reason": e.reason, "name": e.name} for e in report.excluded_dates
        ][:MAX_LIST_ITEMS],
        "balance": None
        if balance is None
        else {
            "balance_days": days(balance.balance_days),
            "pending_days": days(balance.pending_days),
            "available_days": days(balance.available_days),
            "after_days": days(balance.after_days),
        },
        "approval_chain": [r.value for r in report.approval_chain],
        "auto_acknowledged": report.auto_acknowledged,
        "violations": [{"code": v.code, "detail": v.detail} for v in report.violations],
        "warnings": [{"code": v.code, "detail": v.detail} for v in report.warnings],
    }


def _cap(result: dict[str, Any]) -> dict[str, Any]:
    """Keep results within the per-result size budget: shorten lists first, then the documents text."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return result
    capped = dict(result)
    for key, value in result.items():
        if isinstance(value, list) and len(value) > 3:
            capped[key] = value[: max(3, len(value) // 2)]
            capped["truncated"] = True
    if isinstance(capped.get("documents"), str):
        capped["documents"] = capped["documents"][: MAX_RESULT_CHARS - 200]
        capped["truncated"] = True
    return capped


class ToolGateway:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        audit: AuditService,
        privacy: PseudonymisationService,
        reidentification: ReidentificationService,
        requests: LeaveRequestService,
        approvals: ApprovalService,
        team: TeamService,
        ledger: LedgerService,
        catalog: LeaveCatalogService,
        planner: RoutingPlanner,
        retrieval: RetrievalService,
        proposals: ProposalService,
        counter: TokenCounter,
        *,
        evidence: EvidencePolicy | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._audit = audit
        self._privacy = privacy
        self._reid = reidentification
        self._requests = requests
        self._approvals = approvals
        self._team = team
        self._ledger = ledger
        self._catalog = catalog
        self._planner = planner
        self._retrieval = retrieval
        self._proposals = proposals
        self._counter = counter
        self.evidence = evidence or EvidencePolicy()

    # --- the algorithm ---------------------------------------------------------------------------------------
    async def call(self, ctx: ToolContext, call: ToolCall, call_id: str) -> ToolOutcome:
        started = time.monotonic()
        outcome = await self._call(ctx, call, call_id)
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        outcome.args_hash = _args_hash(call.arguments)
        outcome.result = _cap(outcome.result)
        return outcome

    async def _call(self, ctx: ToolContext, call: ToolCall, call_id: str) -> ToolOutcome:
        definition = BY_NAME.get(call.name)
        if definition is None:
            return await self._deny(ctx, call, call_id, "unknown tool")
        if not (ctx.principal.roles & definition.roles):
            return await self._deny(ctx, call, call_id, "your role may not use this tool")
        if ctx.read_only and definition.phase is Phase.PREPARE:
            return await self._deny(ctx, call, call_id, "only read-only tools are available for this message")
        try:
            args = definition.args.model_validate(call.arguments)
            self._check_window(ctx, args)
        except ValidationError as exc:
            return ToolOutcome(
                call_id,
                call.name,
                "invalid",
                {"error": "invalid_arguments", "message": _validation_message(exc)},
            )
        except InvalidInputError as exc:
            return ToolOutcome(
                call_id, call.name, "invalid", {"error": "invalid_arguments", "message": exc.detail}
            )
        outcome = ToolOutcome(call_id, call.name, "ok", {})
        handler = getattr(self, f"_{definition.name.value}")
        try:
            outcome.result = await handler(ctx, args, outcome)
        except AuthorizationError as exc:
            await self._audit.record_denial(self._uow, ctx.principal, exc.action or call.name, exc.resource)
            return await self._deny(ctx, call, call_id, exc.detail or "not permitted")
        except NotFoundError:
            await self._audit_tool(ctx, call.name, "not_found")
            return ToolOutcome(
                call_id,
                call.name,
                "not_found",
                {"error": "not_found", "message": "no such request or resource visible to you"},
                reason="not_found",
            )
        except (ApplicationError, DomainError) as exc:
            code = getattr(exc, "problem_type", None) or getattr(exc, "code", "invalid")
            return ToolOutcome(
                call_id,
                call.name,
                "invalid",
                {"error": str(code), "message": str(getattr(exc, "detail", "") or exc)[:300]},
            )
        return outcome

    def _check_window(self, ctx: ToolContext, args: Any) -> None:
        earliest = ctx.today - timedelta(days=PAST_WINDOW_DAYS)
        latest = ctx.today + timedelta(days=FUTURE_WINDOW_DAYS)
        for name in ("start_date", "end_date", "from_date", "to_date"):
            value = getattr(args, name, None)
            if isinstance(value, date) and not earliest <= value <= latest:
                raise InvalidInputError(
                    f"{name} {value.isoformat()} is outside the bookable window "
                    f"{earliest.isoformat()}–{latest.isoformat()}"
                )

    async def _deny(self, ctx: ToolContext, call: ToolCall, call_id: str, reason: str) -> ToolOutcome:
        await self._audit_tool(ctx, call.name, reason)
        return ToolOutcome(
            call_id, call.name, "denied", {"error": "not_permitted", "message": reason}, reason=reason
        )

    async def _audit_tool(self, ctx: ToolContext, name: str, reason: str) -> None:
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            await self._audit.record(
                uow,
                ctx.principal,
                "agent.tool.denied",
                resource_type="tool",
                resource_id=name,
                outcome=AuditOutcome.DENIED,
                details={"reason": reason[:200], "conversation_id": str(ctx.conversation_id)},
            )
            await uow.commit()

    # --- pseudonymisation of results ------------------------------------------------------------------------
    async def _people(
        self, ctx: ToolContext, outcome: ToolOutcome, employees: Sequence[Employee]
    ) -> dict[UUID, str]:
        tenant_id, _ = tenant_user(ctx.principal)
        out: dict[UUID, str] = {}
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            for employee in employees:
                if employee.id in out:
                    continue
                placeholder = await self._privacy.placeholder_for_employee(
                    uow, tenant_id, employee.id, employee.display_name, source_kind="tool_result"
                )
                out[employee.id] = placeholder
                outcome.placeholders[placeholder] = {"kind": "tool_result", "id": outcome.call_id}
            await uow.commit()
        return out

    async def _restore_own_words(self, ctx: ToolContext, text: str | None) -> str | None:
        """Free-text arguments (reason, comment) come back pseudonymised from the model. Only placeholders the user
        typed in this conversation are restored for storage; anything else stays a neutral label (doc 08 §7.4)."""
        if not text or not PLACEHOLDER.search(text):
            return text
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            occurrences: list[PlaceholderOccurrence] = []
            for message in await uow.messages.list_for_conversation(ctx.conversation_id):
                for placeholder, source in message.provenance.items():
                    span = parse_placeholder(placeholder)
                    if span is not None and source.get("kind") == "user_message":
                        occurrences.append(
                            PlaceholderOccurrence(
                                placeholder=placeholder,
                                entity_type=span[0],
                                source_kind="user_message",
                                source_id=source.get("id"),
                            )
                        )
            return await self._reid.reidentify(
                uow,
                ctx.principal,
                LlmSafeText(text),
                occurrences,
                authored_sources=frozenset({str(ctx.conversation_id)}),
            )

    # --- lookups ----------------------------------------------------------------------------------------------
    async def _request_by_number(self, ctx: ToolContext, number: str) -> Any:
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            request = await uow.leave_requests.get_by_number(number)
        if request is None:
            raise NotFoundError("request not found")
        return request

    async def _end(self, ctx: ToolContext, start: date, end: date | None, working_days: int | None) -> date:
        if end is not None:
            return end
        assert working_days is not None
        tenant_id, _ = tenant_user(ctx.principal)
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            tenant = await load_tenant(uow, tenant_id)
            holidays = await self._planner.holidays_for(uow, (start.year, start.year + 1))
        return end_for(start, working_days, work_week=tenant.settings.work_week, holidays=holidays)

    async def _type_names(self, uow: UnitOfWork) -> dict[UUID, Any]:
        return {t.id: t for t in await uow.leave_types.list(include_inactive=True)}

    def _type_name(self, ctx: ToolContext, leave_type: Any) -> str:
        return str(leave_type.name_el if ctx.language == "el" else leave_type.name_en)

    # --- retrieve tools ----------------------------------------------------------------------------------------
    async def _leave_code(self, ctx: ToolContext, value: str | None) -> str | None:
        """Leave-type arguments must be catalogue codes: a model that passes "vacation" or "κανονική" gets the list
        back instead of an empty result it would read as "no days left" (live, M7)."""
        if value is None:
            return None
        code = value.strip().lower()
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            codes = sorted(t.code for t in await uow.leave_types.list(include_inactive=False))
        if code not in codes:
            raise InvalidInputError(f"unknown leave_type {value!r}; use one of: {', '.join(codes)}")
        return code

    async def _get_my_balance(
        self, ctx: ToolContext, args: BalanceArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        year = args.year or ctx.today.year
        note = None
        try:
            code = await self._leave_code(ctx, args.leave_type)
        except InvalidInputError as exc:  # reading balances is harmless: show them all instead of an error
            code, note = None, f"{exc.detail}; showing every type"
        views = await self._ledger.balances(ctx.principal, year=year)
        rows = [
            {
                "leave_type": v.leave_type.code,
                "name": self._type_name(ctx, v.leave_type),
                "balance_days": days(v.balance_days),  # type: ignore[arg-type]
                "pending_days": days(v.pending_days),  # type: ignore[arg-type]
                "available_days": days(v.available_days),  # type: ignore[arg-type]
            }
            for v in views
            if code is None or v.leave_type.code == code
        ]
        return {"year": year, "balances": rows, **({"note": note} if note else {})}

    async def _get_my_requests(
        self, ctx: ToolContext, args: RequestsArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        year = args.year or ctx.today.year
        statuses = None if args.status in (None, "all") else STATUS_GROUPS[str(args.status)]
        page = await self._requests.list(
            ctx.principal,
            statuses=statuses,
            date_from=date(year, 1, 1),
            date_to=date(year, 12, 31),
            limit=50,
            cursor=None,
        )
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            types = await self._type_names(uow)
        rows = sorted(page.items, key=lambda r: r.start_date)
        return {
            "year": year,
            "count": len(rows),
            "requests": [
                {
                    "n": i,
                    "request_number": r.request_number,
                    "leave_type": types[r.leave_type_id].code if r.leave_type_id in types else None,
                    "start_date": r.start_date.isoformat(),
                    "end_date": r.end_date.isoformat(),
                    "working_days": days(r.working_days),
                    "status": r.status.value,
                }
                for i, r in enumerate(rows[:MAX_LIST_ITEMS], start=1)
            ],
        }

    async def _get_leave_types_and_rules(
        self, ctx: ToolContext, args: NoArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        types = await self._catalog.list_types(ctx.principal)
        policies = await self._catalog.list_policies(ctx.principal, year=ctx.today.year)
        rules = await self._catalog.list_rules(ctx.principal)
        by_id = {t.id: t for t in types}
        return {
            "year": ctx.today.year,
            "leave_types": [
                {
                    "code": t.code,
                    "name": self._type_name(ctx, t),
                    "balance_bearing": t.balance_bearing,
                    "document_required_above_days": t.requires_document_above_days,
                }
                for t in types
            ],
            "policies": [
                {
                    "leave_type": by_id[p.leave_type_id].code if p.leave_type_id in by_id else None,
                    "entitlement_days": days(p.entitlement_days),
                    "carryover_max_days": days(p.carryover_max_days),
                    "carryover_expires_on": p.carryover_expires_on.isoformat()
                    if p.carryover_expires_on
                    else None,
                    "min_notice_days": p.min_notice_days,
                    "max_consecutive_days": p.max_consecutive_days,
                }
                for p in policies
            ],
            "approval_rules": [
                {
                    "leave_type": by_id[r.leave_type_id].code if r.leave_type_id in by_id else "any",
                    "min_working_days": days(r.min_working_days),
                    "max_working_days": days(r.max_working_days),
                    "steps": [s.value for s in r.steps],
                    "auto_acknowledge": r.auto_acknowledge,
                }
                for r in sorted(rules, key=lambda r: r.priority)
                if r.active
            ][:MAX_LIST_ITEMS],
        }

    async def _calculate_working_days(
        self, ctx: ToolContext, args: PeriodArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        end = await self._end(ctx, args.start_date, args.end_date, args.working_days)
        tenant_id, _ = tenant_user(ctx.principal)
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            tenant = await load_tenant(uow, tenant_id)
            holidays = await self._planner.holidays_for(uow, tuple(range(args.start_date.year, end.year + 1)))
        counted = classify(args.start_date, end, work_week=tenant.settings.work_week, holidays=holidays)
        return {
            "start_date": args.start_date.isoformat(),
            "end_date": end.isoformat(),
            "working_days": counted.count,
            "excluded": [
                {"date": e.day.isoformat(), "reason": e.reason, "name": e.name} for e in counted.excluded
            ][:MAX_LIST_ITEMS],
        }

    async def _validate(self, ctx: ToolContext, args: LeaveArgs) -> tuple[ValidationReport, date]:
        await self._leave_code(ctx, args.leave_type)
        end = await self._end(ctx, args.start_date, args.end_date, args.working_days)
        report = await self._requests.validate(
            ctx.principal,
            SubmitRequest(
                leave_type_code=args.leave_type.strip().lower(), start_date=args.start_date, end_date=end
            ),
        )
        return report, end

    async def _validate_leave_request(
        self, ctx: ToolContext, args: LeaveArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        report, end = await self._validate(ctx, args)
        return report_json(report, leave_type=args.leave_type.strip().lower(), start=args.start_date, end=end)

    # --- prepare tools -------------------------------------------------------------------------------------------
    async def _prepare_leave_request(
        self, ctx: ToolContext, args: PrepareLeaveArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        report, end = await self._validate(ctx, args)
        code = args.leave_type.strip().lower()
        result = report_json(report, leave_type=code, start=args.start_date, end=end)
        if not report.valid:
            return {"prepared": False, **result}
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            leave_type = await uow.leave_types.get_by_code(code)
        balance = result["balance"] or {}
        payload = {
            "leave_type": code,
            "leave_type_name": {"el": leave_type.name_el, "en": leave_type.name_en} if leave_type else None,
            "start_date": args.start_date.isoformat(),
            "end_date": end.isoformat(),
            "working_days": report.working_days,
            "reason": await self._restore_own_words(ctx, args.reason),
            "available_before": balance.get("available_days"),
            "available_after": balance.get("after_days"),
            "approval_chain": result["approval_chain"],
            "auto_acknowledged": report.auto_acknowledged,
            "excluded": result["excluded"],
            "warnings": result["warnings"],
        }
        proposal = await self._proposals.create(
            ctx.principal,
            conversation_id=ctx.conversation_id,
            run_id=ctx.run_id,
            kind=ProposalKind.SUBMIT,
            payload=payload,
        )
        outcome.proposal_id = proposal.id
        return {"prepared": True, **result}

    async def _prepare_modification(
        self, ctx: ToolContext, args: ModificationArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        request = await self._request_by_number(ctx, args.request_number)
        if request.employee_id != ctx.principal.employee_id:
            raise NotFoundError(
                "request not found"
            )  # only the owner may modify: others' requests are invisible
        end = await self._end(ctx, args.start_date, args.end_date, args.working_days)
        request, report = await self._requests.validate_modification(
            ctx.principal, request.id, args.start_date, end
        )
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            leave_type = (await self._type_names(uow)).get(request.leave_type_id)
        code = leave_type.code if leave_type else ""
        result = report_json(report, leave_type=code, start=args.start_date, end=end)
        current = {
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "working_days": days(request.working_days),
        }
        if not report.valid:
            return {"prepared": False, "request_number": request.request_number, "current": current, **result}
        payload = {
            "request_id": str(request.id),
            "request_number": request.request_number,
            "version": request.version,
            "leave_type": code,
            "from": current,
            "start_date": args.start_date.isoformat(),
            "end_date": end.isoformat(),
            "working_days": report.working_days,
            "available_after": (result["balance"] or {}).get("after_days"),
            "approval_chain": result["approval_chain"],
        }
        proposal = await self._proposals.create(
            ctx.principal,
            conversation_id=ctx.conversation_id,
            run_id=ctx.run_id,
            kind=ProposalKind.MODIFY,
            payload=payload,
        )
        outcome.proposal_id = proposal.id
        return {"prepared": True, "request_number": request.request_number, "current": current, **result}

    async def _prepare_cancellation(
        self, ctx: ToolContext, args: RequestRefArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        request = await self._request_by_number(ctx, args.request_number)
        if request.employee_id != ctx.principal.employee_id:
            raise NotFoundError("request not found")
        detail = await self._requests.get(ctx.principal, request.id)
        summary = {
            "request_number": request.request_number,
            "leave_type": detail.leave_type.code,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "working_days": days(request.working_days),
            "status": request.status.value,
        }
        if request.status not in CANCELLABLE or (
            request.status is RequestStatus.APPROVED and request.start_date <= ctx.today
        ):
            return {
                "prepared": False,
                "reason": f"a {request.status.value} request cannot be cancelled here",
                **summary,
            }
        payload = {"request_id": str(request.id), "version": request.version, **summary}
        proposal = await self._proposals.create(
            ctx.principal,
            conversation_id=ctx.conversation_id,
            run_id=ctx.run_id,
            kind=ProposalKind.CANCEL,
            payload=payload,
        )
        outcome.proposal_id = proposal.id
        return {"prepared": True, **summary}

    # --- approver tools ------------------------------------------------------------------------------------------
    async def _get_team_availability(
        self, ctx: ToolContext, args: AvailabilityArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        department_id: UUID | None = None
        department_code: str | None = None
        if args.department:
            async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
                department = resolve_department(args.department, await uow.departments.list())
            if department is None:
                raise NotFoundError("department not found")
            department_id, department_code = department.id, department.code
        rows = await self._team.availability(
            ctx.principal, start=args.from_date, end=args.to_date, department_id=department_id
        )
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            employees = await uow.employees.get_many([r.employee_id for r in rows])
        names = await self._people(ctx, outcome, [e for e in employees.values()])
        return {
            "department": department_code,
            "from_date": args.from_date.isoformat(),
            "to_date": args.to_date.isoformat(),
            "count": len(rows),
            "absences": [
                {
                    "employee": names.get(r.employee_id),
                    "start_date": r.start_date.isoformat(),
                    "end_date": r.end_date.isoformat(),
                    "status": r.status,
                    "leave_type": r.leave_type_code,
                }
                for r in rows[:MAX_LIST_ITEMS]
            ],
        }

    async def _list_pending_approvals(
        self, ctx: ToolContext, args: NoArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        items = await self._approvals.pending(ctx.principal)
        names = await self._people(ctx, outcome, [i.requester for i in items])
        return {
            "count": len(items),
            "items": [
                {
                    "n": n,
                    "request_number": i.request.request_number,
                    "requester": names[i.requester.id],
                    "leave_type": i.leave_type.code,
                    "start_date": i.request.start_date.isoformat(),
                    "end_date": i.request.end_date.isoformat(),
                    "working_days": days(i.request.working_days),
                    "step": i.step.approver_role.value,
                    "fallback": i.fallback,
                }
                for n, i in enumerate(items[:MAX_LIST_ITEMS], start=1)
            ],
        }

    async def _get_request_details(
        self, ctx: ToolContext, args: RequestRefArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        request = await self._request_by_number(ctx, args.request_number)
        detail = await self._requests.get(ctx.principal, request.id)  # visibility: owner, approver, HR
        async with self._uow(TenantContext.for_principal(ctx.principal)) as uow:
            approvers = await uow.employees.get_many(
                [s.designated_employee_id for s in detail.steps if s.designated_employee_id]
            )
        people = [detail.employee, *approvers.values()]
        names = await self._people(ctx, outcome, people)
        own = detail.request.employee_id == ctx.principal.employee_id
        return {
            "request_number": detail.request.request_number,
            "owner": "you" if own else names[detail.employee.id],
            "leave_type": detail.leave_type.code if detail.leave_type_visible else None,
            "start_date": detail.request.start_date.isoformat(),
            "end_date": detail.request.end_date.isoformat(),
            "working_days": days(detail.request.working_days),
            "status": detail.request.status.value,
            "steps": [
                {
                    "step_no": s.step_no,
                    "role": s.approver_role.value,
                    "status": s.status.value,
                    "approver": names.get(s.designated_employee_id) if s.designated_employee_id else None,
                }
                for s in detail.steps
            ],
            # metadata only (doc 08 §9): never the contents of a supporting document
            "supporting_documents": detail.supporting_count if (own or detail.supporting_count) else None,
        }

    async def _prepare_approval_decision(
        self, ctx: ToolContext, args: DecisionArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        request = await self._request_by_number(ctx, args.request_number)
        if request.employee_id == ctx.principal.employee_id:
            raise AuthorizationError(
                "nobody decides a step on their own request", action="approval.decide_manager_step"
            )
        pending = await self._approvals.pending(ctx.principal)
        item = next((i for i in pending if i.request.id == request.id), None)
        if item is None:
            # visible but not decidable by this principal (another approver, a later step) → denial, not a leak
            await self._requests.get(ctx.principal, request.id)
            raise AuthorizationError(
                "this request is not waiting for your decision", action="approval.decide"
            )
        names = await self._people(ctx, outcome, [item.requester])
        kind = ProposalKind.APPROVE if args.decision == "approve" else ProposalKind.REJECT
        payload = {
            "request_id": str(item.request.id),
            "request_number": item.request.request_number,
            "step_id": str(item.step.id),
            "step_version": item.step.version,
            "step_role": item.step.approver_role.value,
            "decision": args.decision,
            "comment": await self._restore_own_words(ctx, args.comment),
            "requester_employee_id": str(item.requester.id),
            "leave_type": item.leave_type.code,
            "start_date": item.request.start_date.isoformat(),
            "end_date": item.request.end_date.isoformat(),
            "working_days": days(item.request.working_days),
            "fallback": item.fallback,
        }
        proposal = await self._proposals.create(
            ctx.principal,
            conversation_id=ctx.conversation_id,
            run_id=ctx.run_id,
            kind=kind,
            payload=payload,
        )
        outcome.proposal_id = proposal.id
        return {
            "prepared": True,
            "request_number": item.request.request_number,
            "decision": args.decision,
            "requester": names[item.requester.id],
            "start_date": item.request.start_date.isoformat(),
            "end_date": item.request.end_date.isoformat(),
            "working_days": days(item.request.working_days),
            "step": item.step.approver_role.value,
        }

    # --- knowledge -------------------------------------------------------------------------------------------------
    async def _search_policies(
        self, ctx: ToolContext, args: SearchArgs, outcome: ToolOutcome
    ) -> dict[str, Any]:
        hits = await self._retrieval.retrieve(ctx.principal, args.query)
        policy = EvidencePolicy(
            tau=self.evidence.tau,
            min_evidence_chunks=self.evidence.min_evidence_chunks,
            max_per_document=2,
            budget_tokens=SEARCH_BUDGET_TOKENS,
        )
        bundle = build_context(hits, self._counter, policy)
        outcome.refs = tuple(bundle.refs)
        outcome.source_version_ids = tuple(sorted(bundle.source_version_ids))
        for placeholder in {m.group(0) for m in PLACEHOLDER.finditer(str(bundle.text))}:
            outcome.placeholders[placeholder] = {
                "kind": "document_version",
                "id": outcome.source_version_ids[0] if outcome.source_version_ids else None,
            }
        return {"count": len(bundle.refs), "documents": str(bundle.text)}
