"""Language detection restricted to the two supported languages (doc 05 §3 "language detection")."""

from __future__ import annotations

import unicodedata
from functools import cached_property
from typing import Any

GREEK = "el"
ENGLISH = "en"


def _script_counts(text: str) -> tuple[int, int]:
    greek = latin = 0
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if name.startswith("GREEK"):
            greek += 1
        elif name.startswith("LATIN"):
            latin += 1
    return greek, latin


class ScriptLanguageDetector:
    """Deterministic script heuristic: Greek letters → `el`, Latin letters → `en` (also used by tests)."""

    def detect(self, text: str) -> str | None:
        greek, latin = _script_counts(text[:20000])
        if greek == 0 and latin == 0:
            return None
        return GREEK if greek >= latin else ENGLISH


class LinguaLanguageDetector:
    """lingua restricted to EL/EN (doc 02 §3), with the script heuristic as the decisive tie-breaker."""

    def __init__(self) -> None:
        self._fallback = ScriptLanguageDetector()

    @cached_property
    def _detector(self) -> Any:
        from lingua import Language, LanguageDetectorBuilder

        return LanguageDetectorBuilder.from_languages(Language.GREEK, Language.ENGLISH).build()

    def detect(self, text: str) -> str | None:
        sample = text[:5000]
        greek, latin = _script_counts(sample)
        if greek == 0 and latin == 0:
            return None
        if greek and not latin:
            return GREEK
        if latin and not greek:
            return ENGLISH
        detected = self._detector.detect_language_of(sample)
        if detected is None:
            return self._fallback.detect(sample)
        return GREEK if detected.name == "GREEK" else ENGLISH
