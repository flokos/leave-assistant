"""Authorization policy: `authorize(principal, action, resource) -> Decision` (doc 08 §5).

The permission matrix below is a literal transcription of doc 08 §5.2 (actions × roles → scope). The same function
backs REST endpoints and, from M7, the tool gateway. Deny by default; every scope needs the resource attributes it
checks (missing attributes deny — fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from leave_assistant.application.errors import AuthorizationError
from leave_assistant.domain.tenancy import Principal, Role


class Action(StrEnum):
    READ_OWN_LEAVE = "leave.read_own"
    SUBMIT_OWN_REQUEST = "leave.submit_own"
    MODIFY_OWN_REQUEST = "leave.modify_own"
    CANCEL_OWN_REQUEST = "leave.cancel_own"
    READ_TEAM_REQUESTS = "team.read_requests"
    READ_TEAM_AVAILABILITY = "team.read_availability"
    READ_TEAM_LEAVE_TYPE = "team.read_leave_type"
    READ_SICK_DETAILS = "leave.read_sick_details"
    DECIDE_MANAGER_STEP = "approval.decide_manager_step"
    DECIDE_HR_STEP = "approval.decide_hr_step"
    READ_DOCUMENT_GENERAL = "document.read_general"
    READ_DOCUMENT_HR_RESTRICTED = "document.read_hr_restricted"
    MANAGE_DOCUMENTS_GENERAL = "document.manage_general"
    MANAGE_DOCUMENTS_HR = "document.manage_hr"
    MANAGE_DOCUMENTS_ADMIN_INTERNAL = "document.manage_admin_internal"
    REVIEW_DOCUMENTS_HR = "document.review_hr"
    REVIEW_DOCUMENTS_GENERAL = "document.review_general"
    VIEW_SUPPORTING_DOCUMENT = "supporting.view"
    RETENTION_REVIEW = "retention.review"
    MANAGE_ORGANISATION = "admin.manage_organisation"
    MANAGE_CALENDAR_COMPANY = "calendar.manage_company"
    CONNECT_PERSONAL_CALENDAR = "calendar.connect_personal"
    READ_CALENDAR_SYNC = (
        "calendar.read_sync"  # M8: sync history (doc 09 §2.7: C, H; owners for their requests)
    )
    MANAGE_SECURITY_POLICY = "admin.manage_security_policy"
    READ_AUDIT_TENANT = "audit.read_tenant"
    READ_RUN_HISTORY = "chat.read_run_history"
    CHAT = "chat.use"
    MANAGE_TENANTS = "platform.manage_tenants"
    MANAGE_SECURITY_DEFAULTS = "platform.manage_security_defaults"
    READ_AUDIT_PLATFORM = "platform.read_audit"
    READ_TENANT_DATA = "platform.read_tenant_data"  # never granted (doc 08 §5.2 last rows)
    INTERNAL_CALL = "internal.call"
    # M2 additions (doc 08 §5.2 amendment): catalogue reads, ledger adjustments, tenant balances, own notifications
    READ_LEAVE_CATALOGUE = "leave.read_catalogue"
    ADJUST_LEDGER = "ledger.adjust"
    READ_TENANT_BALANCES = "ledger.read_tenant_balances"
    MANAGE_OWN_NOTIFICATIONS = "notifications.manage_own"


class Scope(StrEnum):
    SELF = "self"  # resource.owner_employee_id == principal.employee_id
    DEPARTMENT = "department"  # resource in the principal's department and the principal manages it
    TENANT = "tenant"  # resource (if any) belongs to the principal's tenant
    PLATFORM = "platform"  # platform administrators only
    AUTOMATION = "automation"  # service accounts only


@dataclass(frozen=True, slots=True)
class ResourceRef:
    kind: str
    id: UUID | str | None = None
    tenant_id: UUID | None = None
    owner_employee_id: UUID | None = None
    department_id: UUID | None = None
    department_manager_employee_id: UUID | None = None

    def describe(self) -> str:
        return f"{self.kind}:{self.id}" if self.id is not None else self.kind


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str = ""


E, M, H, C, P, A = (
    Role.EMPLOYEE,
    Role.MANAGER,
    Role.HR_ADMIN,
    Role.COMPANY_ADMIN,
    Role.PLATFORM_ADMIN,
    Role.AUTOMATION,
)
_ALL_SELF: Final = {E: Scope.SELF, M: Scope.SELF, H: Scope.SELF, C: Scope.SELF}
_ALL_TENANT: Final = {E: Scope.TENANT, M: Scope.TENANT, H: Scope.TENANT, C: Scope.TENANT}

PERMISSIONS: Final[dict[Action, dict[Role, Scope]]] = {
    Action.READ_OWN_LEAVE: _ALL_SELF,
    Action.SUBMIT_OWN_REQUEST: _ALL_SELF,
    Action.MODIFY_OWN_REQUEST: _ALL_SELF,
    Action.CANCEL_OWN_REQUEST: _ALL_SELF,
    Action.READ_TEAM_REQUESTS: {M: Scope.DEPARTMENT, H: Scope.TENANT, C: Scope.TENANT},
    Action.READ_TEAM_AVAILABILITY: {M: Scope.DEPARTMENT, H: Scope.TENANT, C: Scope.TENANT},
    Action.READ_TEAM_LEAVE_TYPE: {M: Scope.DEPARTMENT, H: Scope.TENANT},
    Action.READ_SICK_DETAILS: {E: Scope.SELF, H: Scope.TENANT},
    Action.DECIDE_MANAGER_STEP: {M: Scope.DEPARTMENT, H: Scope.TENANT},
    Action.DECIDE_HR_STEP: {H: Scope.TENANT},
    Action.READ_DOCUMENT_GENERAL: _ALL_TENANT,
    Action.READ_DOCUMENT_HR_RESTRICTED: {H: Scope.TENANT, C: Scope.TENANT},
    Action.MANAGE_DOCUMENTS_GENERAL: {H: Scope.TENANT, C: Scope.TENANT},
    Action.MANAGE_DOCUMENTS_HR: {H: Scope.TENANT},
    Action.MANAGE_DOCUMENTS_ADMIN_INTERNAL: {C: Scope.TENANT},
    Action.REVIEW_DOCUMENTS_HR: {H: Scope.TENANT},
    Action.REVIEW_DOCUMENTS_GENERAL: {C: Scope.TENANT},
    Action.VIEW_SUPPORTING_DOCUMENT: {E: Scope.SELF, H: Scope.TENANT},
    Action.RETENTION_REVIEW: {H: Scope.TENANT},
    Action.MANAGE_ORGANISATION: {C: Scope.TENANT},
    Action.MANAGE_CALENDAR_COMPANY: {C: Scope.TENANT},
    Action.CONNECT_PERSONAL_CALENDAR: _ALL_SELF,
    Action.READ_CALENDAR_SYNC: {E: Scope.SELF, M: Scope.SELF, H: Scope.TENANT, C: Scope.TENANT},
    Action.MANAGE_SECURITY_POLICY: {C: Scope.TENANT},
    Action.READ_AUDIT_TENANT: {H: Scope.TENANT, C: Scope.TENANT},
    Action.READ_RUN_HISTORY: {E: Scope.SELF, M: Scope.SELF, H: Scope.TENANT, C: Scope.TENANT},
    Action.CHAT: _ALL_TENANT,
    Action.MANAGE_TENANTS: {P: Scope.PLATFORM},
    Action.MANAGE_SECURITY_DEFAULTS: {P: Scope.PLATFORM},
    Action.READ_AUDIT_PLATFORM: {P: Scope.PLATFORM},
    Action.READ_TENANT_DATA: {},
    Action.INTERNAL_CALL: {A: Scope.AUTOMATION},
    Action.READ_LEAVE_CATALOGUE: _ALL_TENANT,
    Action.ADJUST_LEDGER: {H: Scope.TENANT},
    Action.READ_TENANT_BALANCES: {H: Scope.TENANT, C: Scope.TENANT},
    Action.MANAGE_OWN_NOTIFICATIONS: _ALL_SELF,
}

_DECISION_ACTIONS: Final = frozenset({Action.DECIDE_MANAGER_STEP, Action.DECIDE_HR_STEP})


def _scope_matches(scope: Scope, principal: Principal, resource: ResourceRef | None) -> tuple[bool, str]:
    if scope is Scope.PLATFORM:
        return principal.is_platform_admin, "platform administrators only"
    if scope is Scope.AUTOMATION:
        return principal.is_automation, "service accounts only"
    if not principal.is_tenant_user:
        return False, "tenant context required"
    if scope is Scope.TENANT:
        if (
            resource is not None
            and resource.tenant_id is not None
            and resource.tenant_id != principal.tenant_id
        ):
            return False, "resource belongs to another tenant"
        return True, ""
    if resource is None:
        return False, "resource required for scoped action"
    if resource.tenant_id is not None and resource.tenant_id != principal.tenant_id:
        return False, "resource belongs to another tenant"
    if scope is Scope.SELF:
        return resource.owner_employee_id == principal.employee_id, "not the owner"
    # Scope.DEPARTMENT: same department and the principal is that department's manager (fail closed if unknown)
    if resource.department_id is None or resource.department_id != principal.department_id:
        return False, "resource outside the principal's department"
    if resource.department_manager_employee_id != principal.employee_id:
        return False, "principal is not the manager of this department"
    return True, ""


def authorize(principal: Principal, action: Action, resource: ResourceRef | None = None) -> Decision:
    cells = PERMISSIONS.get(action, {})
    if (
        action in _DECISION_ACTIONS
        and resource is not None
        and resource.owner_employee_id == principal.employee_id
    ):
        return Decision(False, "nobody decides a step on their own request")
    last_reason = "no role grants this action"
    for role in principal.roles:
        scope = cells.get(role)
        if scope is None:
            continue
        ok, reason = _scope_matches(scope, principal, resource)
        if ok:
            return Decision(True)
        last_reason = reason
    return Decision(False, last_reason)


def require(principal: Principal, action: Action, resource: ResourceRef | None = None) -> None:
    decision = authorize(principal, action, resource)
    if not decision.allowed:
        raise AuthorizationError(
            decision.reason, action=action.value, resource=resource.describe() if resource else None
        )
