"""Working-day arithmetic (doc 07 §6, FR-LEAVE-03): civil dates, tenant work week, tenant public holidays."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from leave_assistant.domain.errors import InvalidValue


@dataclass(frozen=True, slots=True)
class ExcludedDate:
    day: date
    reason: Literal["weekend", "holiday"]
    name: str | None = None


@dataclass(frozen=True, slots=True)
class WorkingDays:
    count: int
    excluded: tuple[ExcludedDate, ...]


def _check_range(start: date, end: date) -> None:
    if end < start:
        raise InvalidValue(
            "end_date must not be before start_date", code="leave/invalid-dates", params={"field": "end_date"}
        )


def iter_days(start: date, end: date) -> Iterable[date]:
    _check_range(start, end)
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def expand_holidays(holidays: Iterable[tuple[date, str, bool]], years: Iterable[int]) -> dict[date, str]:
    """Map every holiday to the requested years (recurring ones repeat on the same month/day)."""
    years = tuple(years)
    expanded: dict[date, str] = {}
    for day, name, recurring in holidays:
        if recurring:
            for year in years:
                try:
                    expanded[day.replace(year=year)] = name
                except ValueError:  # 29 February in a non-leap year
                    continue
        elif day.year in years or not years:
            expanded[day] = name
    return expanded


def classify(
    start: date, end: date, *, work_week: Iterable[int], holidays: Mapping[date, str]
) -> WorkingDays:
    weekdays = frozenset(int(d) for d in work_week)
    excluded: list[ExcludedDate] = []
    count = 0
    for day in iter_days(start, end):
        if day in holidays:
            excluded.append(ExcludedDate(day, "holiday", holidays[day]))
        elif day.weekday() not in weekdays:
            excluded.append(ExcludedDate(day, "weekend"))
        else:
            count += 1
    return WorkingDays(count=count, excluded=tuple(excluded))


def count_working_days(
    start: date, end: date, *, work_week: Iterable[int], holidays: Mapping[date, str]
) -> WorkingDays:
    return classify(start, end, work_week=work_week, holidays=holidays)


def working_dates(
    start: date, end: date, *, work_week: Iterable[int], holidays: Mapping[date, str]
) -> list[date]:
    weekdays = frozenset(int(d) for d in work_week)
    return [d for d in iter_days(start, end) if d not in holidays and d.weekday() in weekdays]


def split_by_year(start: date, end: date) -> list[tuple[date, date]]:
    """Segments of [start, end] per calendar year (policy periods are calendar years — plan decision 7)."""
    _check_range(start, end)
    segments: list[tuple[date, date]] = []
    cursor = start
    while cursor.year < end.year:
        year_end = date(cursor.year, 12, 31)
        segments.append((cursor, year_end))
        cursor = year_end + timedelta(days=1)
    segments.append((cursor, end))
    return segments
