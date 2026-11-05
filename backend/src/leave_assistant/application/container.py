"""The application services wired by the composition root (main.py) and consumed by the API layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.identity.claims import TokenClaims
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
        )
