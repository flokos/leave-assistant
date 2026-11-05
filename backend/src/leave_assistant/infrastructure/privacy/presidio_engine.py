"""PresidioEngine (ADR-0017): spaCy `el`/`en`, built-in recognisers, custom Greek identifiers with checksums and
context boosters, plus the known-entity layer. Loads lazily (≈ 20 s) in the process that first needs it."""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import cached_property
from typing import Any

from leave_assistant.application.ports import KnownEntity
from leave_assistant.domain.privacy.checksums import afm_valid, amka_valid
from leave_assistant.domain.privacy.types import EntityType, Finding
from leave_assistant.infrastructure.privacy.known_entities import known_entity_findings

LANGUAGES = ("en", "el")
MODELS = {"en": "en_core_web_lg", "el": "el_core_news_md"}
ENTITY_MAP = {
    "PERSON": EntityType.PERSON,
    "EMAIL_ADDRESS": EntityType.EMAIL,
    "PHONE_NUMBER": EntityType.PHONE,
    "GR_PHONE": EntityType.PHONE,
    "IBAN_CODE": EntityType.IBAN,
    "LOCATION": EntityType.ADDRESS,
    "GR_AMKA": EntityType.AMKA,
    "GR_AFM": EntityType.AFM,
    "GR_ID_CARD": EntityType.ID_CARD,
    "DATE_TIME": EntityType.DATE_OF_BIRTH,  # only kept with birth context (see `_keep`)
}
BIRTH_CONTEXT = re.compile(r"(γενν|birth|born|ημ/?νια γεν|dob)", re.IGNORECASE)
GREEK_CONTEXT = {
    "amka": ["αμκα", "amka", "κοινωνικής ασφάλισης", "social security"],
    "afm": ["αφμ", "afm", "φορολογικ", "tax number", "tax id", "δου"],
    "id": ["αδτ", "ταυτότητ", "ταυτοτητ", "id card", "identity card", "δελτίο"],
    "phone": ["τηλ", "τηλέφωνο", "κινητό", "phone", "mobile", "tel"],
}


def _recognizers() -> list[Any]:
    from presidio_analyzer import Pattern, PatternRecognizer

    class ChecksumRecognizer(PatternRecognizer):
        def __init__(self, *, validator: Any, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._validator = validator

        def validate_result(self, pattern_text: str) -> bool | None:
            return bool(self._validator(pattern_text))

    recognizers: list[Any] = []
    for language in LANGUAGES:
        # Presidio's predefined e-mail/IBAN recognisers are registered for `en` only; these cover both languages
        recognizers.append(
            PatternRecognizer(
                supported_entity="EMAIL_ADDRESS",
                name=f"EMAIL_{language}",
                supported_language=language,
                patterns=[Pattern("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", 1.0)],
            )
        )
        recognizers.append(
            PatternRecognizer(
                supported_entity="IBAN_CODE",
                name=f"IBAN_{language}",
                supported_language=language,
                patterns=[
                    Pattern("iban", r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b", 0.8)
                ],
                context=["iban", "τράπεζα", "λογαριασμ", "bank", "account"],
            )
        )
        recognizers.append(
            ChecksumRecognizer(
                validator=amka_valid,
                supported_entity="GR_AMKA",
                name=f"GR_AMKA_{language}",
                supported_language=language,
                patterns=[Pattern("amka", r"\b\d{11}\b", 0.5)],
                context=GREEK_CONTEXT["amka"],
            )
        )
        recognizers.append(
            ChecksumRecognizer(
                validator=afm_valid,
                supported_entity="GR_AFM",
                name=f"GR_AFM_{language}",
                supported_language=language,
                patterns=[Pattern("afm", r"\b\d{9}\b", 0.4)],
                context=GREEK_CONTEXT["afm"],
            )
        )
        recognizers.append(
            PatternRecognizer(
                supported_entity="GR_ID_CARD",
                name=f"GR_ID_CARD_{language}",
                supported_language=language,
                patterns=[Pattern("id_card", r"\b[Α-ΩA-Z]{1,2}[\s-]?\d{6}\b", 0.4)],
                context=GREEK_CONTEXT["id"],
            )
        )
        recognizers.append(
            PatternRecognizer(
                supported_entity="GR_PHONE",
                name=f"GR_PHONE_{language}",
                supported_language=language,
                patterns=[
                    Pattern("gr_mobile", r"(?<!\d)(?:\+30[\s-]?)?69\d[\s-]?\d{3}[\s-]?\d{4}(?!\d)", 0.6),
                    Pattern("gr_landline", r"(?<!\d)(?:\+30[\s-]?)?2\d{2}[\s-]?\d{3}[\s-]?\d{4}(?!\d)", 0.5),
                ],
                context=GREEK_CONTEXT["phone"],
            )
        )
    return recognizers


class PresidioEngine:
    def __init__(self, *, threshold: float = 0.4) -> None:
        self._threshold = threshold

    @cached_property
    def _analyzer(self) -> Any:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": code, "model_name": name} for code, name in MODELS.items()],
        }
        engine = AnalyzerEngine(
            nlp_engine=NlpEngineProvider(nlp_configuration=configuration).create_engine(),
            supported_languages=list(LANGUAGES),
        )
        for recognizer in _recognizers():
            engine.registry.add_recognizer(recognizer)
        return engine

    def load(self) -> None:
        _ = self._analyzer

    @staticmethod
    def _keep(entity: EntityType, text: str, start: int) -> bool:
        if entity is EntityType.DATE_OF_BIRTH:
            return BIRTH_CONTEXT.search(text[max(0, start - 60) : start]) is not None
        return True

    def detect(self, text: str, *, language: str | None, known: Sequence[KnownEntity]) -> list[Finding]:
        if not text.strip():
            return []
        lang = language if language in LANGUAGES else "en"
        findings: list[Finding] = []
        for result in self._analyzer.analyze(text=text, language=lang, score_threshold=0.0):
            entity = ENTITY_MAP.get(result.entity_type)
            if entity is None or result.score < self._threshold or not self._keep(entity, text, result.start):
                continue
            findings.append(
                Finding(
                    entity,
                    result.start,
                    result.end,
                    float(result.score),
                    "presidio",
                    text[result.start : result.end],
                )
            )
        findings.extend(known_entity_findings(text, known))
        return findings
