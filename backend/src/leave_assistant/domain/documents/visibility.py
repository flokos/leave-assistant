"""Category visibility, upload/review permissions and ACL rules (doc 05 §1.1, doc 08 §6, ADR-0027).

The same rules are rendered as a SQL predicate in the chunk/document repositories (defence in depth); a parity test
runs both over one matrix. Platform administrators and service accounts never read documents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from leave_assistant.domain.documents.types import AclEntry, AclPrincipalType, Category
from leave_assistant.domain.errors import DomainError
from leave_assistant.domain.tenancy import Role

TENANT_ROLES: frozenset[Role] = frozenset({Role.EMPLOYEE, Role.MANAGER, Role.HR_ADMIN, Role.COMPANY_ADMIN})
CATEGORY_ROLE_READERS: dict[Category, frozenset[Role]] = {
    Category.POLICY_GENERAL: TENANT_ROLES,
    Category.POLICY_HR_RESTRICTED: frozenset({Role.HR_ADMIN, Role.COMPANY_ADMIN}),
    Category.DEPARTMENT_NOTICE: frozenset({Role.HR_ADMIN, Role.COMPANY_ADMIN}),  # + members and their manager
    Category.ADMIN_INTERNAL: frozenset({Role.COMPANY_ADMIN}),
}
UPLOAD_CATEGORIES: dict[Role, frozenset[Category]] = {
    Role.HR_ADMIN: frozenset(
        {Category.POLICY_GENERAL, Category.POLICY_HR_RESTRICTED, Category.DEPARTMENT_NOTICE}
    ),
    Role.COMPANY_ADMIN: frozenset({Category.POLICY_GENERAL, Category.ADMIN_INTERNAL}),
    Role.MANAGER: frozenset({Category.DEPARTMENT_NOTICE}),  # own department only (P3)
}
REVIEWERS: dict[Category, frozenset[Role]] = {
    Category.POLICY_GENERAL: frozenset({Role.HR_ADMIN, Role.COMPANY_ADMIN}),
    Category.POLICY_HR_RESTRICTED: frozenset({Role.HR_ADMIN}),
    Category.DEPARTMENT_NOTICE: frozenset({Role.HR_ADMIN, Role.COMPANY_ADMIN}),
    Category.ADMIN_INTERNAL: frozenset({Role.COMPANY_ADMIN}),
}


class AclNotAllowed(DomainError):
    code = "document/acl-not-allowed"


@dataclass(frozen=True, slots=True)
class Reader:
    roles: frozenset[Role]
    employee_id: UUID | None
    department_id: UUID | None


@dataclass(frozen=True, slots=True)
class DocumentScope:
    category: Category
    owner_employee_id: UUID
    department_id: UUID | None = None
    department_manager_employee_id: UUID | None = None
    acl: tuple[AclEntry, ...] = ()


def is_tenant_reader(reader: Reader) -> bool:
    return reader.employee_id is not None and bool(reader.roles & TENANT_ROLES)


def acl_grants(reader: Reader, scope: DocumentScope) -> bool:
    if scope.category is Category.POLICY_HR_RESTRICTED:
        return False  # hard rule: HR-restricted content is never opened by ACL
    role_names = {r.value for r in reader.roles}
    for entry in scope.acl:
        if entry.permission != "read":
            continue
        if entry.principal_type is AclPrincipalType.ROLE and entry.principal_id in role_names:
            return True
        if (
            entry.principal_type is AclPrincipalType.DEPARTMENT
            and reader.department_id is not None
            and entry.principal_id == str(reader.department_id)
        ):
            return True
        if entry.principal_type is AclPrincipalType.EMPLOYEE and entry.principal_id == str(
            reader.employee_id
        ):
            return True
    return False


def can_read(reader: Reader, scope: DocumentScope) -> bool:
    if not is_tenant_reader(reader):
        return False
    if reader.employee_id == scope.owner_employee_id:
        return True
    if reader.roles & CATEGORY_ROLE_READERS[scope.category]:
        return True
    if scope.category is Category.DEPARTMENT_NOTICE and scope.department_id is not None:
        if reader.department_id == scope.department_id:
            return True
        if scope.department_manager_employee_id is not None and (
            reader.employee_id == scope.department_manager_employee_id
        ):
            return True
    return acl_grants(reader, scope)


def allowed_upload_categories(reader: Reader) -> frozenset[Category]:
    allowed: set[Category] = set()
    for role in reader.roles:
        allowed |= UPLOAD_CATEGORIES.get(role, frozenset())
    return frozenset(allowed)


def can_upload(reader: Reader, category: Category, *, department_id: UUID | None) -> bool:
    if not is_tenant_reader(reader) or category not in allowed_upload_categories(reader):
        return False
    if category is Category.DEPARTMENT_NOTICE:
        if department_id is None:
            return False
        if Role.HR_ADMIN in reader.roles:
            return True
        return reader.department_id == department_id  # managers: own department only
    return True


def can_manage(reader: Reader, scope: DocumentScope) -> bool:
    """Edit, retire, delete or add a version: the roles that may upload to the category (or the owner)."""
    if not is_tenant_reader(reader):
        return False
    if reader.employee_id == scope.owner_employee_id:
        return True
    return can_upload(reader, scope.category, department_id=scope.department_id)


def can_review(reader: Reader, category: Category) -> bool:
    return is_tenant_reader(reader) and bool(reader.roles & REVIEWERS[category])


def validate_acl(category: Category, entries: Sequence[tuple[AclPrincipalType, str]]) -> None:
    if not entries:
        return
    if category is Category.POLICY_HR_RESTRICTED:
        raise AclNotAllowed("HR-restricted documents cannot be opened to other readers by ACL")
    role_names = {r.value for r in TENANT_ROLES}
    for principal_type, principal_id in entries:
        if principal_type is AclPrincipalType.ROLE:
            if principal_id not in role_names:
                raise AclNotAllowed(
                    f"unknown role {principal_id!r} in ACL", params={"principal_id": principal_id}
                )
        else:
            try:
                UUID(principal_id)
            except ValueError:
                raise AclNotAllowed(
                    "ACL principal must be a department or employee id", params={"principal_id": principal_id}
                ) from None
