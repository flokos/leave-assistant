"""Seeds the leave catalogue into the in-memory world: types, 2026 policies, holidays, rules and year grants."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from leave_assistant.domain.leave.ledger import EntryType, LedgerEntry, grant_key
from leave_assistant.domain.leave.types import (
    ApprovalRule,
    GrantMode,
    LeavePolicy,
    LeaveType,
    PublicHoliday,
    StepRole,
)
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock, SequentialIds
from tests.fakes.world import TenantWorld, World

GREEK_2026 = [
    (date(2026, 1, 1), "Πρωτοχρονιά", "New Year's Day", True),
    (date(2026, 1, 6), "Θεοφάνεια", "Epiphany", True),
    (date(2026, 2, 23), "Καθαρά Δευτέρα", "Clean Monday", False),
    (date(2026, 3, 25), "25η Μαρτίου", "Independence Day", True),
    (date(2026, 4, 10), "Μεγάλη Παρασκευή", "Orthodox Good Friday", False),
    (date(2026, 4, 13), "Δευτέρα του Πάσχα", "Orthodox Easter Monday", False),
    (date(2026, 5, 1), "Πρωτομαγιά", "Labour Day", True),
    (date(2026, 6, 1), "Αγίου Πνεύματος", "Whit Monday", False),
    (date(2026, 8, 15), "Κοίμηση της Θεοτόκου", "Assumption", True),
    (date(2026, 10, 28), "28η Οκτωβρίου", "Ochi Day", True),
    (date(2026, 12, 25), "Χριστούγεννα", "Christmas Day", True),
    (date(2026, 12, 26), "Δεύτερη ημέρα Χριστουγέννων", "Second day of Christmas", True),
]
IRISH_2026 = [
    (date(2026, 1, 1), "Πρωτοχρονιά", "New Year's Day", True),
    (date(2026, 2, 2), "Αγίας Βριγίτας", "St Brigid's Day", False),
    (date(2026, 3, 17), "Αγίου Πατρικίου", "St Patrick's Day", True),
    (date(2026, 4, 6), "Δευτέρα του Πάσχα", "Easter Monday", False),
    (date(2026, 5, 4), "Αργία Μαΐου", "May Bank Holiday", False),
    (date(2026, 6, 1), "Αργία Ιουνίου", "June Bank Holiday", False),
    (date(2026, 8, 3), "Αργία Αυγούστου", "August Bank Holiday", False),
    (date(2026, 10, 26), "Αργία Οκτωβρίου", "October Bank Holiday", False),
    (date(2026, 12, 25), "Χριστούγεννα", "Christmas Day", True),
    (date(2026, 12, 26), "Αγίου Στεφάνου", "St Stephen's Day", True),
]


def seed_leave_catalogue(
    store: MemoryStore, world: World, clock: FixedClock, ids: SequentialIds, *, year: int = 2026
) -> dict[str, dict[str, LeaveType]]:
    """Returns {tenant key: {code: LeaveType}}."""
    now = clock.now()
    result: dict[str, dict[str, LeaveType]] = {}
    for key, tw, holidays in (("a", world.a, GREEK_2026), ("b", world.b, IRISH_2026)):
        tenant = tw.tenant
        types: dict[str, LeaveType] = {}
        for code, el, en, bearing, retro, doc_above in (
            ("annual", "Κανονική άδεια", "Annual leave", True, 0, None),
            ("sick", "Αναρρωτική άδεια", "Sick leave", True, 30, 3),
            ("unpaid", "Άδεια άνευ αποδοχών", "Unpaid leave", False, 0, None),
        ):
            lt = LeaveType(ids.new_id(), tenant.id, code, el, en, bearing, retro, doc_above, True, now, now)
            store.leave_types[lt.id] = lt
            types[code] = lt
        # sick leave allows a negative balance so that cap-exceeding requests reach HR review (Q-02) instead of being blocked
        for code, entitlement, allow_negative in (
            ("annual", Decimal("20"), False),
            ("sick", Decimal("10"), True),
        ):
            for y in (year, year + 1):
                policy = LeavePolicy(
                    ids.new_id(),
                    tenant.id,
                    types[code].id,
                    y,
                    entitlement,
                    Decimal("5"),
                    date(y, 3, 31),  # carry-over into year y expires on 31 March of year y (doc 01 §6.2)
                    allow_negative,
                    0,
                    None,
                    True,
                    GrantMode.UPFRONT,
                    now,
                    now,
                )
                store.leave_policies[policy.id] = policy
        for day, el, en, recurring in holidays:
            holiday = PublicHoliday(ids.new_id(), tenant.id, day, el, en, recurring, now, now)
            store.holidays[holiday.id] = holiday
        for rule in (
            ApprovalRule(
                ids.new_id(),
                tenant.id,
                types["sick"].id,
                Decimal("0"),
                Decimal("3"),
                False,
                (),
                True,
                10,
                True,
                now,
                now,
            ),
            ApprovalRule(
                ids.new_id(),
                tenant.id,
                types["sick"].id,
                Decimal("3.01"),
                None,
                True,
                (StepRole.HR,),
                False,
                20,
                True,
                now,
                now,
            ),
            ApprovalRule(
                ids.new_id(),
                tenant.id,
                None,
                Decimal("0"),
                Decimal("5"),
                False,
                (StepRole.MANAGER,),
                False,
                30,
                True,
                now,
                now,
            ),
            ApprovalRule(
                ids.new_id(),
                tenant.id,
                None,
                Decimal("5.01"),
                None,
                False,
                (StepRole.MANAGER, StepRole.HR),
                False,
                40,
                True,
                now,
                now,
            ),
        ):
            store.approval_rules[rule.id] = rule
        for employee in tw.employees.values():
            for code, entitlement in (("annual", Decimal("20")), ("sick", Decimal("10"))):
                entry = LedgerEntry(
                    ids.new_id(),
                    tenant.id,
                    employee.id,
                    types[code].id,
                    year,
                    EntryType.GRANT,
                    entitlement,
                    date(year, 1, 1),
                    grant_key(employee.id, types[code].id, year),
                    now,
                    reason=f"entitlement {year}",
                )
                store.ledger.append(entry)
        result[key] = types
    return result


def employee_of(tw: TenantWorld, key: str) -> UUID:
    return tw.employees[key].id
