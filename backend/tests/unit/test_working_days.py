"""FR-LEAVE-03: working days exclude the tenant's weekend set and public holidays; recurring holidays expand per year."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.leave.working_days import (
    count_working_days,
    expand_holidays,
    split_by_year,
    working_dates,
)

GREEK_2026 = {
    date(2026, 1, 1): "Πρωτοχρονιά",
    date(2026, 1, 6): "Θεοφάνεια",
    date(2026, 2, 23): "Καθαρά Δευτέρα",
    date(2026, 3, 25): "25η Μαρτίου",
    date(2026, 4, 10): "Μεγάλη Παρασκευή",
    date(2026, 4, 13): "Δευτέρα του Πάσχα",
    date(2026, 5, 1): "Πρωτομαγιά",
    date(2026, 6, 1): "Αγίου Πνεύματος",
    date(2026, 8, 15): "Κοίμηση της Θεοτόκου",
    date(2026, 10, 28): "28η Οκτωβρίου",
    date(2026, 12, 25): "Χριστούγεννα",
    date(2026, 12, 26): "Δεύτερη ημέρα Χριστουγέννων",
}
MON_FRI = (0, 1, 2, 3, 4)


def test_brief_headline_example_december() -> None:
    """Brief: five days from 14 December with Christmas Day excluded → 14–18 Dec are 5 working days, 21–25 → 4."""
    week = count_working_days(date(2026, 12, 14), date(2026, 12, 18), work_week=MON_FRI, holidays=GREEK_2026)
    assert week.count == 5 and week.excluded == ()
    christmas = count_working_days(
        date(2026, 12, 21), date(2026, 12, 27), work_week=MON_FRI, holidays=GREEK_2026
    )
    assert christmas.count == 4
    assert [(e.day, e.reason) for e in christmas.excluded] == [
        (date(2026, 12, 25), "holiday"),
        (date(2026, 12, 26), "holiday"),  # a holiday on a Saturday is reported as a holiday
        (date(2026, 12, 27), "weekend"),
    ]
    assert christmas.excluded[0].name == "Χριστούγεννα"


def test_new_year_scenario_s13() -> None:
    """S13: 3 days from 2 January 2026 (Fri) → 2, 5, 7 Jan are working days; 6 Jan is Epiphany."""
    result = count_working_days(date(2026, 1, 2), date(2026, 1, 7), work_week=MON_FRI, holidays=GREEK_2026)
    assert result.count == 3
    assert working_dates(date(2026, 1, 2), date(2026, 1, 7), work_week=MON_FRI, holidays=GREEK_2026) == [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 7),
    ]


def test_custom_work_week() -> None:
    sun_thu = (6, 0, 1, 2, 3)
    assert count_working_days(date(2026, 3, 1), date(2026, 3, 7), work_week=sun_thu, holidays={}).count == 5


def test_invalid_range_rejected() -> None:
    with pytest.raises(InvalidValue):
        count_working_days(date(2026, 3, 2), date(2026, 3, 1), work_week=MON_FRI, holidays={})


def test_split_by_year() -> None:
    assert split_by_year(date(2026, 12, 29), date(2027, 1, 4)) == [
        (date(2026, 12, 29), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 1, 4)),
    ]
    assert split_by_year(date(2026, 5, 1), date(2026, 5, 3)) == [(date(2026, 5, 1), date(2026, 5, 3))]


def test_expand_recurring_holidays() -> None:
    fixed = [(date(2026, 12, 25), "Christmas", True), (date(2026, 4, 13), "Easter Monday", False)]
    expanded = expand_holidays(fixed, years=(2026, 2027))
    assert expanded[date(2026, 12, 25)] == "Christmas" and expanded[date(2027, 12, 25)] == "Christmas"
    assert date(2026, 4, 13) in expanded and date(2027, 4, 13) not in expanded


@settings(max_examples=200)
@given(
    start=st.dates(min_value=date(2024, 1, 1), max_value=date(2028, 12, 31)),
    length=st.integers(min_value=0, max_value=60),
    holidays=st.sets(st.dates(min_value=date(2024, 1, 1), max_value=date(2029, 3, 1)), max_size=10),
    work_week=st.sets(st.integers(min_value=0, max_value=6), min_size=1, max_size=7),
)
def test_count_equals_days_minus_weekends_minus_holidays(
    start: date, length: int, holidays: set[date], work_week: set[int]
) -> None:
    end = start + timedelta(days=length)
    result = count_working_days(start, end, work_week=tuple(work_week), holidays={d: "h" for d in holidays})
    expected = sum(
        1
        for i in range(length + 1)
        if (start + timedelta(days=i)).weekday() in work_week and (start + timedelta(days=i)) not in holidays
    )
    assert result.count == expected
    assert result.count + len(result.excluded) == length + 1
    assert all(e.reason in {"weekend", "holiday"} for e in result.excluded)
