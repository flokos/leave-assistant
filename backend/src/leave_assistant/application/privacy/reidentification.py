"""ReidentificationService: resolve placeholders for a principal per the doc 08 §7.4 matrix (used by chat in M6/M7)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from leave_assistant.application.ports import UnitOfWork
from leave_assistant.application.privacy.service import PseudonymisationService
from leave_assistant.domain.privacy.reidentification import PlaceholderContext, Viewer, reidentify
from leave_assistant.domain.privacy.types import LlmSafeText, PlaceholderOccurrence, find_placeholders
from leave_assistant.domain.tenancy import Principal


class ReidentificationService:
    def __init__(self, pseudonymisation: PseudonymisationService) -> None:
        self._pseudo = pseudonymisation

    async def viewer_for(self, uow: UnitOfWork, principal: Principal) -> Viewer:
        managed: set[UUID] = set()
        if principal.employee_id is not None:
            for department in await uow.departments.list():
                if department.manager_employee_id == principal.employee_id:
                    managed.add(department.id)
        return Viewer(principal.employee_id, principal.department_id, principal.roles, frozenset(managed))

    async def reidentify(
        self,
        uow: UnitOfWork,
        principal: Principal,
        text: LlmSafeText,
        occurrences: Sequence[PlaceholderOccurrence],
        *,
        readable_sources: frozenset[str] = frozenset(),
        hr_data_sources: frozenset[str] = frozenset(),
    ) -> str:
        """`readable_sources` / `hr_data_sources` carry the source ids the caller has already authorised."""
        if principal.tenant_id is None:
            return str(text)
        viewer = await self.viewer_for(uow, principal)
        by_placeholder = {o.placeholder: o for o in occurrences}
        resolved: dict[str, tuple[str, PlaceholderContext]] = {}
        for span in find_placeholders(text):
            if span.placeholder in resolved:
                continue
            found = await self._pseudo.resolve(uow, principal.tenant_id, span.placeholder)
            if found is None:
                continue
            original, entry = found
            occurrence = by_placeholder.get(span.placeholder)
            source_kind = occurrence.source_kind if occurrence else "tool_result"
            source_id = occurrence.source_id if occurrence else None
            subject = entry.subject_employee_id
            department = None
            if subject is not None:
                employee = await uow.employees.get(subject)
                department = employee.department_id if employee else None
            resolved[span.placeholder] = (
                original,
                PlaceholderContext(
                    entity_type=entry.entity_type,
                    source_kind=source_kind,
                    subject_employee_id=subject,
                    subject_department_id=department,
                    source_readable=source_id in readable_sources
                    if source_kind == "document_version"
                    else True,
                    hr_data=source_id in hr_data_sources,
                ),
            )
        return reidentify(text, viewer, resolved.get, locale=principal.locale.value)
