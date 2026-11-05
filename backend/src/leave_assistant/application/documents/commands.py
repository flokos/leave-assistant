from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.domain.documents.types import AclPrincipalType, Category, ReviewAction


@dataclass(frozen=True, slots=True)
class Upload:
    """A received file, spooled to disk by the API layer (never held in memory)."""

    path: Path
    filename: str | None
    declared_mime: str | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CreateDocument:
    title: str
    category: Category
    upload: Upload
    department_id: UUID | None = None
    language_hint: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateDocument:
    version: int
    title: str = UNSET
    category: Category = UNSET
    department_id: UUID | None = UNSET
    acl: Sequence[tuple[AclPrincipalType, str]] = UNSET


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    action: ReviewAction
    comment: str | None = None
