"""Re-identification policy (doc 08 §7.4): which placeholders a principal may see restored, the rest become neutral labels."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from leave_assistant.domain.privacy.types import EntityType, LlmSafeText, find_placeholders
from leave_assistant.domain.tenancy import Role

UNAVAILABLE = "[unavailable]"
NEUTRAL_LABELS: dict[str, dict[EntityType, str]] = {
    "en": {
        EntityType.PERSON: "a colleague",
        EntityType.EMAIL: "[e-mail withheld]",
        EntityType.PHONE: "[phone withheld]",
        EntityType.AMKA: "[AMKA withheld]",
        EntityType.AFM: "[AFM withheld]",
        EntityType.ID_CARD: "[ID withheld]",
        EntityType.IBAN: "[IBAN withheld]",
        EntityType.ADDRESS: "[address withheld]",
        EntityType.DATE_OF_BIRTH: "[date withheld]",
        EntityType.OTHER: "[withheld]",
    },
    "el": {
        EntityType.PERSON: "ένας συνάδελφος",
        EntityType.EMAIL: "[e-mail δεν εμφανίζεται]",
        EntityType.PHONE: "[τηλέφωνο δεν εμφανίζεται]",
        EntityType.AMKA: "[ΑΜΚΑ δεν εμφανίζεται]",
        EntityType.AFM: "[ΑΦΜ δεν εμφανίζεται]",
        EntityType.ID_CARD: "[ΑΔΤ δεν εμφανίζεται]",
        EntityType.IBAN: "[IBAN δεν εμφανίζεται]",
        EntityType.ADDRESS: "[διεύθυνση δεν εμφανίζεται]",
        EntityType.DATE_OF_BIRTH: "[ημερομηνία δεν εμφανίζεται]",
        EntityType.OTHER: "[δεν εμφανίζεται]",
    },
}


class Decision(StrEnum):
    RESTORE = "restore"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class Viewer:
    """The authenticated principal as the policy sees it."""

    employee_id: UUID | None
    department_id: UUID | None
    roles: frozenset[Role]
    managed_department_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class PlaceholderContext:
    """What the vault and the provenance say about one placeholder."""

    entity_type: EntityType
    source_kind: str  # user_message | document_version | tool_result
    subject_employee_id: UUID | None = None
    subject_department_id: UUID | None = None
    source_readable: bool = True  # the principal may read the source document / sees that tool data
    hr_data: bool = False  # tool results that expose HR data (HR-only views)
    extra: dict[str, str] = field(default_factory=dict)


def decide(viewer: Viewer, ctx: PlaceholderContext) -> Decision:
    roles = viewer.roles
    if viewer.employee_id is not None and ctx.subject_employee_id == viewer.employee_id:
        return Decision.RESTORE  # the principal themself, any entity type
    if ctx.source_kind == "document_version":
        return (
            Decision.RESTORE if ctx.source_readable else Decision.NEUTRAL
        )  # readable document → visible text
    if ctx.entity_type is EntityType.PERSON:
        if Role.HR_ADMIN in roles:
            return Decision.RESTORE
        same_department = (
            ctx.subject_department_id is not None and ctx.subject_department_id == viewer.department_id
        )
        managed = (
            ctx.subject_department_id is not None
            and ctx.subject_department_id in viewer.managed_department_ids
        )
        if Role.COMPANY_ADMIN in roles:
            return Decision.RESTORE if ctx.source_readable else Decision.NEUTRAL
        if Role.MANAGER in roles and (same_department or managed):
            return Decision.RESTORE
        return Decision.NEUTRAL
    # e-mail, phone, AMKA, AFM, ID, IBAN, address, date of birth of anyone else
    if Role.HR_ADMIN in roles and ctx.source_kind == "tool_result" and ctx.hr_data:
        return Decision.RESTORE
    return Decision.NEUTRAL


Resolver = Callable[[str], tuple[str, PlaceholderContext] | None]


def neutral_label(entity_type: EntityType, locale: str) -> str:
    return NEUTRAL_LABELS.get(locale, NEUTRAL_LABELS["en"])[entity_type]


def reidentify(text: LlmSafeText, viewer: Viewer, resolve: Resolver, *, locale: str = "en") -> str:
    """Replace placeholders with originals (allowed), neutral labels (denied) or `[unavailable]` (unknown)."""
    out = str(text)
    for span in sorted(find_placeholders(text), key=lambda s: s.start, reverse=True):
        resolved = resolve(span.placeholder)
        if resolved is None:
            replacement = UNAVAILABLE
        else:
            original, ctx = resolved
            replacement = (
                original
                if decide(viewer, ctx) is Decision.RESTORE
                else neutral_label(ctx.entity_type, locale)
            )
        out = out[: span.start] + replacement + out[span.end :]
    return out
