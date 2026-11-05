from __future__ import annotations

from datetime import date

import pytest

from leave_assistant.domain.retention.eligibility import (
    NEVER_DELETED,
    DocumentClass,
    RetentionAction,
    ReviewStatus,
    eligible_on,
    is_eligible,
)


def test_eligibility_dates() -> None:
    assert eligible_on(date(2026, 1, 31), 365) == date(2027, 1, 31)
    assert is_eligible(date(2025, 1, 1), 365, date(2026, 1, 1)) and not is_eligible(
        date(2025, 1, 2), 365, date(2026, 1, 1)
    )
    with pytest.raises(ValueError):
        eligible_on(date(2026, 1, 1), 4000)
    assert DocumentClass.LEAVE_RECORD in NEVER_DELETED
    assert RetentionAction("notify_only") and ReviewStatus("approved_delete")
