"""Fakes for the M5 adapters: a regex PII engine (plus the real known-entity layer), a keyword classifier, test keys."""

from __future__ import annotations

import re
from collections.abc import Sequence

from leave_assistant.application.ports import KnownEntity
from leave_assistant.domain.privacy.checksums import afm_valid, amka_valid
from leave_assistant.domain.privacy.types import EntityType, Finding
from leave_assistant.infrastructure.privacy.known_entities import known_entity_findings
from leave_assistant.infrastructure.privacy.vault_crypto import VaultCrypto
from leave_assistant.infrastructure.storage.crypto import EnvelopeCipher

TEST_HMAC_KEY = bytes(range(32))
TEST_VAULT_KEK = bytes(range(32, 64))
TEST_APP_KEK = bytes(range(64, 96))

_PATTERNS: tuple[tuple[EntityType, re.Pattern[str], float], ...] = (
    (EntityType.EMAIL, re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), 1.0),
    (EntityType.IBAN, re.compile(r"\bGR\d{2}(?:[ ]?[A-Z0-9]{4}){5}[ ]?[A-Z0-9]{3}\b"), 0.9),
    (EntityType.PHONE, re.compile(r"(?<!\d)(?:\+30[ -]?)?69\d[ -]?\d{3}[ -]?\d{4}(?!\d)"), 0.8),
    (EntityType.AMKA, re.compile(r"(?<!\d)\d{11}(?!\d)"), 0.7),
    (EntityType.AFM, re.compile(r"(?<!\d)\d{9}(?!\d)"), 0.6),
)


class FakePiiEngine:
    """Deterministic stand-in for Presidio: regex identifiers with checksums + the known-entity recogniser."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.error: Exception | None = None

    def detect(self, text: str, *, language: str | None, known: Sequence[KnownEntity]) -> list[Finding]:
        self.calls.append((text, language))
        if self.error is not None:
            raise self.error
        findings: list[Finding] = []
        for entity, pattern, score in _PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if entity is EntityType.AMKA and not amka_valid(value):
                    continue
                if entity is EntityType.AFM and not afm_valid(value):
                    continue
                findings.append(Finding(entity, match.start(), match.end(), score, "presidio", value))
        findings.extend(known_entity_findings(text, known))
        return findings


class FakeClassifier:
    """Keyword similarity: 0.9 for texts containing an exemplar phrase, 0.0 otherwise."""

    version = "fake-exemplars"
    PHRASES = (
        "ignore all previous instructions",
        "ignore previous instructions",
        "disregard the rules",
        "αγνόησε όλες τις προηγούμενες οδηγίες",
        "αγνόησε τις προηγούμενες οδηγίες",
    )

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def score_blocks(self, texts: Sequence[str]) -> list[float]:
        self.calls.append(list(texts))
        return [0.9 if any(p in t.casefold() for p in self.PHRASES) else 0.0 for t in texts]


def fake_vault_cipher(*, configured: bool = True) -> VaultCrypto:
    return VaultCrypto(TEST_HMAC_KEY, TEST_VAULT_KEK) if configured else VaultCrypto(None, None)


def fake_file_cipher(*, configured: bool = True) -> EnvelopeCipher:
    return EnvelopeCipher(TEST_APP_KEK if configured else None)
