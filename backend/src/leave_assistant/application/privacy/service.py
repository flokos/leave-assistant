"""PseudonymisationService: findings → tenant-stable placeholders through the vault; produces `LlmSafeText`.

Detection is blocking (Presidio) and runs outside transactions (`detect_many`); `apply` maps findings to placeholders
inside the caller's unit of work so chunks and vault rows commit together. Missing keys fail closed.
"""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from leave_assistant.application.errors import SecurityNotConfigured
from leave_assistant.application.ports import (
    Clock,
    IdGenerator,
    KnownEntity,
    PiiEngine,
    UnitOfWork,
    VaultCipher,
    VaultEntry,
)
from leave_assistant.domain.privacy.normalise import normalise_value
from leave_assistant.domain.privacy.types import (
    EntityType,
    Finding,
    LlmSafeText,
    PlaceholderOccurrence,
    apply_replacements,
    format_placeholder,
    merge_findings,
)


@dataclass(frozen=True, slots=True)
class PseudonymisedText:
    safe: LlmSafeText
    occurrences: tuple[PlaceholderOccurrence, ...]
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def has_pii(self) -> bool:
        return bool(self.occurrences)


class PseudonymisationService:
    def __init__(
        self,
        engine: PiiEngine,
        cipher: VaultCipher,
        ids: IdGenerator,
        clock: Clock,
        *,
        threshold: float = 0.4,
    ) -> None:
        self._engine = engine
        self._cipher = cipher
        self._ids = ids
        self._clock = clock
        self._threshold = threshold

    @property
    def configured(self) -> bool:
        return self._cipher.configured

    def detect(
        self, text: str, *, language: str | None, known: Sequence[KnownEntity]
    ) -> builtins.list[Finding]:
        findings = [
            f for f in self._engine.detect(text, language=language, known=known) if f.score >= self._threshold
        ]
        return merge_findings(findings)

    def detect_many(
        self, texts: Sequence[str], *, language: str | None, known: Sequence[KnownEntity]
    ) -> builtins.list[builtins.list[Finding]]:
        return [self.detect(text, language=language, known=known) for text in texts]

    async def apply(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        text: str,
        findings: Sequence[Finding],
        *,
        source_kind: str,
        source_id: str | None,
    ) -> PseudonymisedText:
        if not self._cipher.configured:
            raise SecurityNotConfigured("vault keys are not configured; refusing to produce LLM-safe text")
        replacements: list[tuple[int, int, str]] = []
        occurrences: list[PlaceholderOccurrence] = []
        counts: dict[str, int] = {}
        for finding in findings:
            entry = await self._entry_for(uow, tenant_id, finding, source_kind)
            replacements.append((finding.start, finding.end, entry.placeholder))
            occurrences.append(
                PlaceholderOccurrence(
                    placeholder=entry.placeholder,
                    entity_type=entry.entity_type,
                    source_kind=source_kind,
                    source_id=source_id,
                    subject_employee_id=entry.subject_employee_id,
                )
            )
            counts[entry.entity_type.value] = counts.get(entry.entity_type.value, 0) + 1
        safe = LlmSafeText(apply_replacements(text, replacements))
        return PseudonymisedText(safe=safe, occurrences=tuple(occurrences), counts=counts)

    async def pseudonymise(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        text: str,
        *,
        language: str | None,
        known: Sequence[KnownEntity],
        source_kind: str,
        source_id: str | None = None,
    ) -> PseudonymisedText:
        """Detect + apply in one call (user messages and tool results in M6/M7)."""
        return await self.apply(
            uow,
            tenant_id,
            text,
            self.detect(text, language=language, known=known),
            source_kind=source_kind,
            source_id=source_id,
        )

    async def resolve(
        self, uow: UnitOfWork, tenant_id: UUID, placeholder: str
    ) -> tuple[str, VaultEntry] | None:
        entry = await uow.pii_vault.get_by_placeholder(placeholder)
        if entry is None:
            return None
        return self._cipher.decrypt(tenant_id, entry.value_encrypted), entry

    @staticmethod
    def lookup_key(finding: Finding) -> str:
        """Known employees map by identity (every name variant → one placeholder); others by normalised value."""
        if finding.subject_employee_id is not None and finding.entity_type is EntityType.PERSON:
            return f"employee:{finding.subject_employee_id}"
        return normalise_value(finding.entity_type.value, finding.text)

    async def _entry_for(
        self, uow: UnitOfWork, tenant_id: UUID, finding: Finding, source_kind: str
    ) -> VaultEntry:
        value_hmac = self._cipher.hmac(tenant_id, f"{finding.entity_type.value}:{self.lookup_key(finding)}")
        now: datetime = self._clock.now()
        entry = await uow.pii_vault.get_by_hmac(value_hmac)
        if entry is None:
            seq = await uow.pii_vault.next_sequence(finding.entity_type)
            entry = VaultEntry(
                id=self._ids.new_id(),
                tenant_id=tenant_id,
                entity_type=finding.entity_type,
                value_hmac=value_hmac,
                value_encrypted=self._cipher.encrypt(tenant_id, finding.text),
                placeholder=format_placeholder(finding.entity_type, seq),
                first_seen_at=now,
                last_seen_at=now,
                source_kinds=(source_kind,),
                subject_employee_id=finding.subject_employee_id,
            )
            await uow.pii_vault.add(entry)
            return entry
        changed = False
        if source_kind not in entry.source_kinds:
            entry.source_kinds = (*entry.source_kinds, source_kind)
            changed = True
        if entry.subject_employee_id is None and finding.subject_employee_id is not None:
            entry.subject_employee_id = finding.subject_employee_id
            changed = True
        entry.last_seen_at = now
        if changed or True:
            await uow.pii_vault.update(entry)
        return entry
