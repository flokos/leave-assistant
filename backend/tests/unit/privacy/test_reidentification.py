"""The doc 08 §7.4 matrix: who may see which placeholders restored."""

from __future__ import annotations

from uuid import UUID

import pytest

from leave_assistant.domain.privacy.reidentification import (
    UNAVAILABLE,
    Decision,
    PlaceholderContext,
    Viewer,
    decide,
    neutral_label,
    reidentify,
)
from leave_assistant.domain.privacy.types import EntityType, LlmSafeText
from leave_assistant.domain.tenancy import Role

ME, COLLEAGUE, OTHER = UUID(int=1), UUID(int=2), UUID(int=3)
OPS, FIN = UUID(int=11), UUID(int=12)


def viewer(roles: set[Role], department: UUID = OPS, managed: set[UUID] | None = None) -> Viewer:
    return Viewer(ME, department, frozenset(roles), frozenset(managed or set()))


EMPLOYEE = viewer({Role.EMPLOYEE})
MANAGER = viewer({Role.MANAGER, Role.EMPLOYEE}, managed={OPS})
HR = viewer({Role.HR_ADMIN, Role.EMPLOYEE}, department=FIN)
ADMIN = viewer({Role.COMPANY_ADMIN, Role.EMPLOYEE}, department=FIN)


def person(
    subject: UUID, department: UUID, source: str = "tool_result", readable: bool = True
) -> PlaceholderContext:
    return PlaceholderContext(EntityType.PERSON, source, subject, department, readable)


@pytest.mark.parametrize(
    ("who", "ctx", "expected"),
    [
        # the principal themself: always restored
        (EMPLOYEE, person(ME, OPS), Decision.RESTORE),
        (EMPLOYEE, PlaceholderContext(EntityType.EMAIL, "tool_result", ME, OPS), Decision.RESTORE),
        # a colleague in the principal's department
        (EMPLOYEE, person(COLLEAGUE, OPS), Decision.NEUTRAL),
        (MANAGER, person(COLLEAGUE, OPS), Decision.RESTORE),
        (HR, person(COLLEAGUE, FIN), Decision.RESTORE),
        (ADMIN, person(COLLEAGUE, FIN), Decision.RESTORE),
        # any employee in the tenant (other department)
        (EMPLOYEE, person(OTHER, FIN), Decision.NEUTRAL),
        (MANAGER, person(OTHER, FIN), Decision.NEUTRAL),
        (HR, person(OTHER, OPS), Decision.RESTORE),
        (ADMIN, person(OTHER, OPS, readable=True), Decision.RESTORE),
        (ADMIN, person(OTHER, OPS, readable=False), Decision.NEUTRAL),
        # identifiers of anyone else
        (EMPLOYEE, PlaceholderContext(EntityType.PHONE, "tool_result", COLLEAGUE, OPS), Decision.NEUTRAL),
        (MANAGER, PlaceholderContext(EntityType.EMAIL, "tool_result", COLLEAGUE, OPS), Decision.NEUTRAL),
        (ADMIN, PlaceholderContext(EntityType.AMKA, "tool_result", OTHER, OPS), Decision.NEUTRAL),
        (HR, PlaceholderContext(EntityType.AMKA, "tool_result", OTHER, OPS, hr_data=True), Decision.RESTORE),
        (HR, PlaceholderContext(EntityType.AMKA, "tool_result", OTHER, OPS, hr_data=False), Decision.NEUTRAL),
        (HR, PlaceholderContext(EntityType.IBAN, "user_message", OTHER, OPS), Decision.NEUTRAL),
        # entities from documents the principal may read
        (
            EMPLOYEE,
            PlaceholderContext(EntityType.PHONE, "document_version", OTHER, FIN, source_readable=True),
            Decision.RESTORE,
        ),
        (
            EMPLOYEE,
            PlaceholderContext(EntityType.PERSON, "document_version", OTHER, FIN, source_readable=False),
            Decision.NEUTRAL,
        ),
    ],
)
def test_matrix(who: Viewer, ctx: PlaceholderContext, expected: Decision) -> None:
    assert decide(who, ctx) is expected


def test_reidentify_restores_neutralises_and_marks_unknown() -> None:
    vault = {
        "PERSON_0001": ("Ελένη Δημητρίου", person(ME, OPS)),
        "PERSON_0002": ("Κώστας Μιχαηλίδης", person(COLLEAGUE, OPS)),
        "EMAIL_0003": (
            "kostas@aegean.example",
            PlaceholderContext(EntityType.EMAIL, "tool_result", COLLEAGUE, OPS),
        ),
    }
    text = LlmSafeText("PERSON_0001 και PERSON_0002 (EMAIL_0003, PHONE_0099) είναι σε άδεια.")
    assert reidentify(text, EMPLOYEE, vault.get, locale="el") == (
        f"Ελένη Δημητρίου και ένας συνάδελφος ({neutral_label(EntityType.EMAIL, 'el')}, {UNAVAILABLE}) είναι σε άδεια."
    )
    assert (
        reidentify(text, MANAGER, vault.get)
        == f"Ελένη Δημητρίου και Κώστας Μιχαηλίδης ([e-mail withheld], {UNAVAILABLE}) είναι σε άδεια."
    )
    assert (
        reidentify(text, HR, vault.get)
        == f"Ελένη Δημητρίου και Κώστας Μιχαηλίδης ([e-mail withheld], {UNAVAILABLE}) είναι σε άδεια."
    )


def test_values_the_viewer_typed_are_restored_for_them_only() -> None:
    from uuid import UUID

    from leave_assistant.domain.privacy.reidentification import Decision, PlaceholderContext, Viewer, decide
    from leave_assistant.domain.privacy.types import EntityType
    from leave_assistant.domain.tenancy import Role

    viewer = Viewer(UUID(int=1), UUID(int=10), frozenset({Role.EMPLOYEE}), frozenset())
    typed = PlaceholderContext(
        EntityType.EMAIL, "user_message", UUID(int=2), UUID(int=20), authored_by_viewer=True
    )
    assert decide(viewer, typed) is Decision.RESTORE
    other = PlaceholderContext(EntityType.EMAIL, "user_message", UUID(int=2), UUID(int=20))
    assert decide(viewer, other) is Decision.NEUTRAL
