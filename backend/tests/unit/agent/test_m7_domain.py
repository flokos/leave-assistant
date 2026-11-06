"""M7 domain: end dates from working days, grounding of business answers, proposal states and hashes."""

from __future__ import annotations

from datetime import date

import pytest

from leave_assistant.domain.agent.grounding import numbers_in, ungrounded_numbers
from leave_assistant.domain.agent.proposals import (
    ProposalStatus,
    check_transition,
    payload_hash,
)
from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.leave.working_days import count_working_days, end_for

WEEK = (0, 1, 2, 3, 4)
CHRISTMAS = {date(2026, 12, 25): "Christmas Day", date(2026, 12, 26): "Second day of Christmas"}


def test_end_for_counts_working_days_and_skips_holidays_and_weekends() -> None:
    # the headline: five days from Monday 14 December 2026
    assert end_for(date(2026, 12, 14), 5, work_week=WEEK, holidays={}) == date(2026, 12, 18)
    # S13 adapted: three days from Wednesday 23 December → 23, 24, (25 holiday, weekend) 28
    assert end_for(date(2026, 12, 23), 3, work_week=WEEK, holidays=CHRISTMAS) == date(2026, 12, 28)
    # a Saturday start counts from Monday
    assert end_for(date(2026, 12, 12), 1, work_week=WEEK, holidays={}) == date(2026, 12, 14)
    end = end_for(date(2026, 12, 23), 3, work_week=WEEK, holidays=CHRISTMAS)
    assert count_working_days(date(2026, 12, 23), end, work_week=WEEK, holidays=CHRISTMAS).count == 3


@pytest.mark.parametrize("days", [0, -2, 1000])
def test_end_for_rejects_impossible_counts(days: int) -> None:
    with pytest.raises(InvalidValue):
        end_for(date(2026, 12, 14), days, work_week=WEEK, holidays={})


def test_numbers_ignore_markers_and_placeholders_and_normalise_decimals() -> None:
    assert numbers_in("PERSON_0003 έχει 12,5 ημέρες [1] από 20.0 [2]") == {"12.5", "20"}
    assert numbers_in("LR-2026-000123 · 2026-12-14") == {"2026", "123", "12", "14"}


def test_ungrounded_numbers_are_reported() -> None:
    sources = ['{"available_days": "20", "pending_days": "0"}', "<user>five days from 14 December</user>"]
    assert ungrounded_numbers("Έχετε 20 ημέρες διαθέσιμες.", sources) == set()
    assert ungrounded_numbers("You have 25 days left from 14 December.", sources) == {"25"}


def test_proposal_transitions() -> None:
    check_transition(ProposalStatus.PENDING, ProposalStatus.CONFIRMED)
    check_transition(ProposalStatus.CONFIRMED, ProposalStatus.EXECUTED)
    for current, target in (
        (ProposalStatus.EXECUTED, ProposalStatus.CONFIRMED),
        (ProposalStatus.REJECTED, ProposalStatus.CONFIRMED),
        (ProposalStatus.PENDING, ProposalStatus.EXECUTED),
    ):
        with pytest.raises(InvalidValue):
            check_transition(current, target)


def test_payload_hash_is_canonical() -> None:
    a = {"start_date": "2026-12-14", "end_date": "2026-12-18", "leave_type": "annual"}
    b = {"leave_type": "annual", "end_date": "2026-12-18", "start_date": "2026-12-14"}
    assert payload_hash(a) == payload_hash(b)
    assert payload_hash(a) != payload_hash({**a, "end_date": "2026-12-21"})
