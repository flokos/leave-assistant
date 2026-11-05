"""Periodic leave housekeeping (plan decision 4): request status rollover and the ledger calendar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.leave.common import EventWriter
from leave_assistant.application.leave.ledger import LedgerService
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.state_machine import RequestEvent, RequestStatus, transition
from leave_assistant.domain.tenancy import Tenant


@dataclass(slots=True)
class RolloverReport:
    tenant_slug: str
    today: date
    started: int = 0
    completed: int = 0
    grants: int = 0
    carryovers: int = 0
    expiries: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def changes(self) -> int:
        return self.started + self.completed + self.grants + self.carryovers + self.expiries


class RolloverService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        ledger: LedgerService,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._ledger = ledger
        self._events = EventWriter(ids, clock)

    @staticmethod
    def system_context(tenant: Tenant) -> TenantContext:
        return TenantContext(tenant_id=tenant.id, user_id="system", roles=("system",), platform_admin=False)

    async def run_for_tenant(self, tenant: Tenant, today: date) -> RolloverReport:
        report = RolloverReport(tenant.slug, today)
        async with self._uow(self.system_context(tenant)) as uow:
            await self._roll_statuses(uow, today, report)
            await self._ledger_calendar(uow, today, report)
            if report.changes:
                await self._audit.record(
                    uow,
                    None,
                    "system.rollover",
                    resource_type="tenant",
                    resource_id=tenant.id,
                    details={
                        "today": today,
                        "started": report.started,
                        "completed": report.completed,
                        "grants": report.grants,
                        "carryovers": report.carryovers,
                        "expiries": report.expiries,
                    },
                )
            await uow.commit()
        return report

    async def _roll_statuses(self, uow: UnitOfWork, today: date, report: RolloverReport) -> None:
        now = self._clock.now()
        for request in await uow.leave_requests.list_by_status(
            frozenset({RequestStatus.APPROVED, RequestStatus.ACTIVE})
        ):
            previous = request.status
            if request.status is RequestStatus.APPROVED and request.start_date <= today:
                request.status = transition(request.status, RequestEvent.START)
                report.started += 1
                await self._events.record(
                    uow,
                    request,
                    "started",
                    from_status=previous,
                    to_status=request.status,
                    principal=None,
                    at=now,
                )
                previous = request.status
            if request.status is RequestStatus.ACTIVE and request.end_date < today:
                request.status = transition(request.status, RequestEvent.COMPLETE)
                report.completed += 1
                await self._events.record(
                    uow,
                    request,
                    "completed",
                    from_status=previous,
                    to_status=request.status,
                    principal=None,
                    at=now,
                )
            if request.status is not previous or report.started:
                if request.status is not previous or request.status is not RequestStatus.APPROVED:
                    request.updated_at = now
                    await uow.leave_requests.update(request, expected_version=request.version)

    async def _ledger_calendar(self, uow: UnitOfWork, today: date, report: RolloverReport) -> None:
        employees = await uow.employees.list_active()
        types = {t.id: t for t in await uow.leave_types.list() if t.balance_bearing}
        policies = {
            p.leave_type_id: p
            for p in await uow.leave_policies.list(year=today.year)
            if p.leave_type_id in types
        }
        if not policies:
            return
        report.grants += await self._ledger.grant_year(
            uow, year=today.year, employees=employees, policies=policies
        )
        report.carryovers += await self._ledger.carry_over(
            uow, year=today.year, employees=employees, policies=policies
        )
        report.expiries += await self._ledger.expire(
            uow, year=today.year, today=today, employees=employees, policies=policies
        )

    async def run_due(self, now: datetime | None = None) -> list[RolloverReport]:
        """All active tenants, each on its own civil date (tenant timezone). Errors are reported, not raised."""
        now = now or self._clock.now()
        async with self._uow(TenantContext(tenant_id=None, user_id="system", platform_admin=True)) as uow:
            tenants = await uow.tenants.list(limit=200, after=None)
        reports: list[RolloverReport] = []
        for tenant in tenants:
            if not tenant.is_active:
                continue
            today = now.astimezone(ZoneInfo(tenant.timezone)).date()
            try:
                reports.append(await self.run_for_tenant(tenant, today))
            except Exception as exc:  # noqa: BLE001 - one tenant must not stop the others
                reports.append(RolloverReport(tenant.slug, today, errors=[f"{type(exc).__name__}: {exc}"]))
        return reports
