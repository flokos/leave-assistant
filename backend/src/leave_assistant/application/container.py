"""The application services wired by the composition root (main.py) and consumed by the API layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.identity.claims import TokenClaims
from leave_assistant.application.leave.approvals import ApprovalService
from leave_assistant.application.leave.catalog import LeaveCatalogService
from leave_assistant.application.leave.ledger import LedgerService
from leave_assistant.application.leave.planner import RoutingPlanner
from leave_assistant.application.leave.requests import LeaveRequestService, RequestWorkflow
from leave_assistant.application.leave.rollover import RolloverService
from leave_assistant.application.leave.team import TeamService
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import Clock, IdentityAdmin, IdGenerator, UnitOfWorkFactory
from leave_assistant.application.tenancy.departments import DepartmentService
from leave_assistant.application.tenancy.employees import EmployeeService
from leave_assistant.application.tenancy.provisioning import TenantProvisioningService


class TokenVerifierPort(Protocol):
    async def verify(self, token: str) -> TokenClaims: ...


@dataclass(slots=True)
class Services:
    uow_factory: UnitOfWorkFactory
    identity: IdentityAdmin
    verifier: TokenVerifierPort
    clock: Clock
    ids: IdGenerator
    audit: AuditService
    provisioning: TenantProvisioningService
    employees: EmployeeService
    departments: DepartmentService
    catalog: LeaveCatalogService
    ledger: LedgerService
    leave_requests: LeaveRequestService
    approvals: ApprovalService
    team: TeamService
    notifications: NotificationService
    rollover: RolloverService

    @classmethod
    def build(
        cls,
        *,
        uow_factory: UnitOfWorkFactory,
        identity: IdentityAdmin,
        verifier: TokenVerifierPort,
        clock: Clock,
        ids: IdGenerator,
    ) -> Services:
        audit = AuditService(clock, ids)
        planner = RoutingPlanner(clock)
        notifications = NotificationService(uow_factory, ids, clock)
        ledger = LedgerService(uow_factory, ids, clock, audit)
        workflow = RequestWorkflow(ids, clock, audit, notifications, ledger, planner)
        leave_requests = LeaveRequestService(uow_factory, workflow)
        return cls(
            uow_factory=uow_factory,
            identity=identity,
            verifier=verifier,
            clock=clock,
            ids=ids,
            audit=audit,
            provisioning=TenantProvisioningService(uow_factory, identity, ids, clock, audit),
            employees=EmployeeService(uow_factory, identity, ids, clock, audit),
            departments=DepartmentService(uow_factory, ids, clock, audit),
            catalog=LeaveCatalogService(uow_factory, ids, clock, audit, planner),
            ledger=ledger,
            leave_requests=leave_requests,
            approvals=ApprovalService(uow_factory, workflow, leave_requests),
            team=TeamService(uow_factory),
            notifications=notifications,
            rollover=RolloverService(uow_factory, ids, clock, audit, ledger),
        )
