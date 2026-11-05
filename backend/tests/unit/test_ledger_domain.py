"""ADR-0005 / Q-01: balance = Σ entries; pro-rating on hire; carry-over capping; idempotency keys."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from leave_assistant.domain.leave.ledger import (
    EntryType,
    balance_of,
    carryover_amount,
    carryover_key,
    deduction_key,
    expiry_key,
    grant_key,
    prorated_entitlement,
    round_half_day,
)


def test_balance_is_sum_of_signed_quantities() -> None:
    assert balance_of([Decimal("20"), Decimal("-5"), Decimal("5"), Decimal("-3")]) == Decimal("17")
    assert balance_of([]) == Decimal("0")


def test_round_half_day() -> None:
    assert round_half_day(Decimal("11.6666")) == Decimal("11.5")
    assert round_half_day(Decimal("11.75")) == Decimal("12.0")
    assert round_half_day(Decimal("8.25")) == Decimal("8.5")  # half-up at the .25 boundary
    assert round_half_day(Decimal("8.24")) == Decimal("8.0")


def test_prorated_entitlement_examples() -> None:
    twenty = Decimal("20")
    assert prorated_entitlement(twenty, hire_date=date(2015, 3, 2), year=2026, prorate=True) == twenty
    assert prorated_entitlement(twenty, hire_date=date(2026, 1, 1), year=2026, prorate=True) == twenty
    assert prorated_entitlement(twenty, hire_date=date(2026, 3, 1), year=2026, prorate=True) == Decimal(
        "16.5"
    )  # 10/12
    assert prorated_entitlement(twenty, hire_date=date(2026, 7, 15), year=2026, prorate=True) == Decimal(
        "8.5"
    )  # 5/12
    assert prorated_entitlement(twenty, hire_date=date(2026, 12, 20), year=2026, prorate=True) == Decimal("0")
    assert prorated_entitlement(twenty, hire_date=date(2027, 2, 1), year=2026, prorate=True) == Decimal("0")
    assert prorated_entitlement(twenty, hire_date=date(2026, 7, 15), year=2026, prorate=False) == twenty


@given(
    entitlement=st.integers(min_value=0, max_value=120).map(lambda n: Decimal(n) / 2),
    hire=st.dates(min_value=date(2020, 1, 1), max_value=date(2027, 12, 31)),
)
def test_prorating_bounds(entitlement: Decimal, hire: date) -> None:
    value = prorated_entitlement(entitlement, hire_date=hire, year=2026, prorate=True)
    assert Decimal("0") <= value <= entitlement
    assert value % Decimal("0.5") == 0


def test_carryover_amount_is_capped_and_never_negative() -> None:
    assert carryover_amount(Decimal("8"), cap=Decimal("5")) == Decimal("5")
    assert carryover_amount(Decimal("3"), cap=Decimal("5")) == Decimal("3")
    assert carryover_amount(Decimal("-2"), cap=Decimal("5")) == Decimal("0")


def test_idempotency_keys_are_stable_and_distinct() -> None:
    e, t, r = UUID(int=1), UUID(int=2), UUID(int=3)
    assert grant_key(e, t, 2026) == grant_key(e, t, 2026)
    assert (
        len(
            {grant_key(e, t, 2026), carryover_key(e, t, 2026), expiry_key(e, t, 2026), deduction_key(r, 2026)}
        )
        == 4
    )
    assert EntryType.DEDUCTION.value == "DEDUCTION"
