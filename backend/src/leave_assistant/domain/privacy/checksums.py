"""Greek identifier checks (doc 08 §7.1): AMKA (11 digits, DDMMYY + Luhn) and AFM (9 digits, mod-11)."""

from __future__ import annotations

import re
from datetime import date

_DIGITS = re.compile(r"\D")


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in number]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def amka_valid(value: str) -> bool:
    number = _DIGITS.sub("", value)
    if len(number) != 11:
        return False
    day, month, year = int(number[0:2]), int(number[2:4]), int(number[4:6])
    try:
        date(1900 + year, month, day)
    except ValueError:
        try:
            date(2000 + year, month, day)
        except ValueError:
            return False
    return luhn_valid(number)


def afm_valid(value: str) -> bool:
    number = _DIGITS.sub("", value)
    if len(number) != 9 or number == "000000000":
        return False
    total = sum(int(number[i]) * (2 ** (8 - i)) for i in range(8))
    return bool((total % 11) % 10 == int(number[8]))


def afm_check_digit(first_eight: str) -> str:
    total = sum(int(first_eight[i]) * (2 ** (8 - i)) for i in range(8))
    return str((total % 11) % 10)


def luhn_check_digit(partial: str) -> str:
    for candidate in "0123456789":
        if luhn_valid(partial + candidate):
            return candidate
    raise ValueError("unreachable")
