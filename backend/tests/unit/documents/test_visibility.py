"""Category visibility matrix, ACL rules (incl. the HR-restricted hard rule), upload and review permissions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from leave_assistant.domain.documents.types import AclEntry, AclPrincipalType, Category
from leave_assistant.domain.documents.visibility import (
    AclNotAllowed,
    DocumentScope,
    Reader,
    allowed_upload_categories,
    can_manage,
    can_read,
    can_review,
    can_upload,
    validate_acl,
)
from leave_assistant.domain.tenancy import Role

OPS, FIN = UUID(int=11), UUID(int=12)
OWNER, EMP_OPS, EMP_FIN, MGR_OPS, HR, ADMIN = (UUID(int=n) for n in range(21, 27))
NOW = datetime(2026, 11, 5, tzinfo=UTC)


def reader(roles: set[Role], employee_id: UUID | None, department_id: UUID | None = None) -> Reader:
    return Reader(frozenset(roles), employee_id, department_id)


READERS = {
    "employee_ops": reader({Role.EMPLOYEE}, EMP_OPS, OPS),
    "employee_fin": reader({Role.EMPLOYEE}, EMP_FIN, FIN),
    "manager_ops": reader({Role.MANAGER, Role.EMPLOYEE}, MGR_OPS, OPS),
    "hr": reader({Role.HR_ADMIN, Role.EMPLOYEE}, HR, FIN),
    "company_admin": reader({Role.COMPANY_ADMIN, Role.EMPLOYEE}, ADMIN, FIN),
    "platform_admin": reader({Role.PLATFORM_ADMIN}, None, None),
    "automation": reader({Role.AUTOMATION}, None, None),
}


def scope(category: Category, **kw: object) -> DocumentScope:
    return DocumentScope(category=category, owner_employee_id=OWNER, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("category", "visible_to"),
    [
        (Category.POLICY_GENERAL, {"employee_ops", "employee_fin", "manager_ops", "hr", "company_admin"}),
        (Category.POLICY_HR_RESTRICTED, {"hr", "company_admin"}),
        (Category.ADMIN_INTERNAL, {"company_admin"}),
    ],
)
def test_category_matrix(category: Category, visible_to: set[str]) -> None:
    for name, r in READERS.items():
        assert can_read(r, scope(category)) == (name in visible_to), name


def test_department_notice_members_manager_hr_admin() -> None:
    notice = scope(Category.DEPARTMENT_NOTICE, department_id=OPS, department_manager_employee_id=MGR_OPS)
    assert can_read(READERS["employee_ops"], notice)
    assert can_read(READERS["manager_ops"], notice)
    assert can_read(READERS["hr"], notice) and can_read(READERS["company_admin"], notice)
    assert not can_read(READERS["employee_fin"], notice)
    # a manager from another department who was designated manager of OPS still reads it
    other_manager = reader({Role.MANAGER, Role.EMPLOYEE}, MGR_OPS, FIN)
    assert can_read(other_manager, notice)
    assert not can_read(READERS["platform_admin"], notice)


def test_owner_always_reads_own_document() -> None:
    owner = reader({Role.MANAGER, Role.EMPLOYEE}, OWNER, FIN)
    assert can_read(owner, scope(Category.ADMIN_INTERNAL))


def acl(principal_type: AclPrincipalType, principal_id: str) -> AclEntry:
    return AclEntry(UUID(int=99), UUID(int=1), UUID(int=2), principal_type, principal_id, NOW)


def test_acl_extends_within_the_tenant_but_never_hr_restricted() -> None:
    internal = scope(Category.ADMIN_INTERNAL, acl=(acl(AclPrincipalType.DEPARTMENT, str(OPS)),))
    assert can_read(READERS["employee_ops"], internal) and not can_read(READERS["employee_fin"], internal)
    by_role = scope(Category.ADMIN_INTERNAL, acl=(acl(AclPrincipalType.ROLE, "manager"),))
    assert can_read(READERS["manager_ops"], by_role) and not can_read(READERS["employee_ops"], by_role)
    by_employee = scope(Category.ADMIN_INTERNAL, acl=(acl(AclPrincipalType.EMPLOYEE, str(EMP_FIN)),))
    assert can_read(READERS["employee_fin"], by_employee) and not can_read(
        READERS["employee_ops"], by_employee
    )
    restricted = scope(
        Category.POLICY_HR_RESTRICTED,
        acl=(acl(AclPrincipalType.ROLE, "employee"), acl(AclPrincipalType.EMPLOYEE, str(EMP_OPS))),
    )
    assert not can_read(READERS["employee_ops"], restricted)
    assert not can_read(READERS["manager_ops"], restricted)
    assert can_read(READERS["hr"], restricted)
    with pytest.raises(AclNotAllowed) as info:
        validate_acl(Category.POLICY_HR_RESTRICTED, [(AclPrincipalType.ROLE, "employee")])
    assert info.value.code == "document/acl-not-allowed"
    validate_acl(Category.POLICY_HR_RESTRICTED, [])
    validate_acl(
        Category.POLICY_GENERAL, [(AclPrincipalType.DEPARTMENT, str(OPS)), (AclPrincipalType.ROLE, "manager")]
    )
    with pytest.raises(AclNotAllowed):
        validate_acl(Category.POLICY_GENERAL, [(AclPrincipalType.ROLE, "platform_admin")])
    with pytest.raises(AclNotAllowed):
        validate_acl(Category.POLICY_GENERAL, [(AclPrincipalType.EMPLOYEE, "not-a-uuid")])


def test_upload_permissions_per_role() -> None:
    assert allowed_upload_categories(READERS["hr"]) == {
        Category.POLICY_GENERAL,
        Category.POLICY_HR_RESTRICTED,
        Category.DEPARTMENT_NOTICE,
    }
    assert allowed_upload_categories(READERS["company_admin"]) == {
        Category.POLICY_GENERAL,
        Category.ADMIN_INTERNAL,
    }
    assert allowed_upload_categories(READERS["manager_ops"]) == {Category.DEPARTMENT_NOTICE}
    assert allowed_upload_categories(READERS["employee_ops"]) == frozenset()
    assert can_upload(READERS["hr"], Category.POLICY_HR_RESTRICTED, department_id=None)
    assert not can_upload(READERS["company_admin"], Category.POLICY_HR_RESTRICTED, department_id=None)
    assert can_upload(READERS["company_admin"], Category.ADMIN_INTERNAL, department_id=None)
    assert not can_upload(READERS["hr"], Category.ADMIN_INTERNAL, department_id=None)
    assert can_upload(READERS["manager_ops"], Category.DEPARTMENT_NOTICE, department_id=OPS)
    assert not can_upload(READERS["manager_ops"], Category.DEPARTMENT_NOTICE, department_id=FIN)
    assert not can_upload(READERS["manager_ops"], Category.DEPARTMENT_NOTICE, department_id=None)
    assert can_upload(READERS["hr"], Category.DEPARTMENT_NOTICE, department_id=FIN)
    assert not can_upload(READERS["employee_ops"], Category.POLICY_GENERAL, department_id=None)
    assert not can_upload(READERS["platform_admin"], Category.POLICY_GENERAL, department_id=None)


def test_manage_and_review() -> None:
    notice = scope(Category.DEPARTMENT_NOTICE, department_id=OPS)
    assert can_manage(READERS["manager_ops"], notice) and not can_manage(READERS["employee_ops"], notice)
    assert can_manage(reader({Role.EMPLOYEE}, OWNER, FIN), notice)  # the uploader keeps managing its upload
    assert can_manage(READERS["hr"], scope(Category.POLICY_HR_RESTRICTED))
    assert not can_manage(READERS["company_admin"], scope(Category.POLICY_HR_RESTRICTED))
    assert can_review(READERS["hr"], Category.POLICY_HR_RESTRICTED)
    assert not can_review(READERS["company_admin"], Category.POLICY_HR_RESTRICTED)
    assert can_review(READERS["company_admin"], Category.POLICY_GENERAL) and can_review(
        READERS["hr"], Category.POLICY_GENERAL
    )
    assert can_review(READERS["company_admin"], Category.ADMIN_INTERNAL) and not can_review(
        READERS["hr"], Category.ADMIN_INTERNAL
    )
    assert not can_review(READERS["manager_ops"], Category.DEPARTMENT_NOTICE)
