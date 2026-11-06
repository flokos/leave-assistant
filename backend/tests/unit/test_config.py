from __future__ import annotations

import pytest
from pydantic import ValidationError

from leave_assistant.config import Settings


def test_defaults_match_the_documented_flags() -> None:
    s = Settings(_env_file=None)
    assert s.retrieval_backend == "pgvector"
    assert s.calendar_provider == "mock"
    assert s.worker_mode == "separate"
    assert s.langfuse_enabled is False
    assert s.llm_providers == ["gemini", "mistral", "groq"]
    assert s.readiness_timeout_seconds == 5.0


def test_environment_overrides_and_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_ORDER", " groq , gemini ")
    monkeypatch.setenv("WORKER_MODE", "embedded")
    monkeypatch.setenv("S3_SECRET_KEY", "top-secret")
    s = Settings(_env_file=None)
    assert s.llm_providers == ["groq", "gemini"]
    assert s.worker_mode == "embedded"
    assert "top-secret" not in repr(s)
    assert s.s3_secret_key.get_secret_value() == "top-secret"


def test_invalid_enum_values_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_BACKEND", "elasticsearch")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
