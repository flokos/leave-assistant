"""Routing planner: validation + rule selection + step materialisation — one code path for simulate and submit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from leave_assistant.application.errors import InvalidInputError
from leave_assistant.application.ports import Clock, UnitOfWork
from leave_assistant.domain.leave.rules import RoutingDecision, route, select_rule
from leave_assistant.domain.leave.types import LeavePolicy, LeaveType
from leave_assistant.domain.leave.validation import (
    PeriodBalance,
    ValidationContext,
    ValidationReport,
    validate_request,
)
from leave_assistant.domain.leave.working_days import expand_holidays
from leave_assistant.domain.tenancy import Employee, Tenant

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Plan:
    report: ValidationReport
    routing: RoutingDecision | None
    policy: LeavePolicy | None
    holidays: dict[date, str]


class RoutingPlanner:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def holidays_for(self, uow: UnitOfWork, years: tuple[int, ...]) -> dict[date, str]:
        rows = await uow.holidays.list()
        return expand_holidays(((h.holiday_date, h.name_en, h.recurring) for h in rows), years)

    async def plan(
        self,
        uow: UnitOfWork,
        *,
        tenant: Tenant,
        employee: Employee,
        leave_type: LeaveType,
        start: date,
        end: date,
        exclude_request_id: UUID | None = None,
        credit_days_by_year: dict[int, Decimal] | None = None,
        has_document: bool = False,
        today: date | None = None,
    ) -> Plan:
        today = today or self._clock.now().date()
        years = (
            tuple(range(min(start.year, end.year), max(start.year, end.year) + 1))
            if end >= start
            else (start.year,)
        )
        holidays = await self.holidays_for(uow, years)
        policy = await uow.leave_policies.get_for(leave_type.id, start.year)
        existing = [
            r for r in await uow.leave_requests.list_for_employee(employee.id) if r.id != exclude_request_id
        ]
        balances: dict[int, PeriodBalance] = {}
        if leave_type.balance_bearing:
            for year in years:
                rows = [
                    r
                    for r in await uow.ledger.balances(year=year, employee_id=employee.id)
                    if r.leave_type_id == leave_type.id
                ]
                row = rows[0] if rows else None
                credit = (credit_days_by_year or {}).get(year, ZERO)
                balances[year] = PeriodBalance(
                    (row.balance_days if row else ZERO) + credit, row.pending_days if row else ZERO
                )
        context = ValidationContext(
            today=today,
            leave_type=leave_type,
            policy=policy,
            work_week=tenant.settings.work_week,
            holidays=holidays,
            existing_requests=existing,
            balances=balances,
            has_document=has_document,
        )
        report = validate_request(start, end, context)
        if not report.valid:
            return Plan(report=report, routing=None, policy=policy, holidays=holidays)

        rules = await uow.approval_rules.list()
        rule = select_rule(rules, leave_type.id, report.working_days)
        if rule is None:
            raise InvalidInputError(
                "no approval rule matches this request; the company administrator must configure one",
                problem_type="leave/no-approval-rule",
            )
        department = await uow.departments.get(employee.department_id)
        within_cap = report.balance is None or report.balance.after_days >= 0
        routing = route(
            rule,
            leave_type=leave_type,
            requester_id=employee.id,
            department_manager_id=department.manager_employee_id if department else None,
            manager_override_id=employee.manager_override_employee_id,
            has_document=has_document,
            within_cap=within_cap,
        )
        report = report.with_routing(tuple(s.approver_role for s in routing.steps), routing.auto_acknowledged)
        return Plan(report=report, routing=routing, policy=policy, holidays=holidays)
