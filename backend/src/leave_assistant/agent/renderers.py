"""Deterministic business texts (M7): what the user reads when no model text is trustworthy — the line that goes with a
proposal card, execution outcomes, and the fallback answer when a model reply fails the grounding check twice.
Everything is built from tool results (pseudonymised: people appear as placeholders) and fixed templates."""

from __future__ import annotations

from datetime import date
from typing import Any

from leave_assistant.agent.templates import FAILURE_NAMES, STATUS_NAMES, localised, template

MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(value: str | None, language: str | None) -> str:
    if not value:
        return "?"
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return value
    return (
        f"{day.day}/{day.month}/{day.year}"
        if language == "el"
        else f"{day.day} {MONTHS_EN[day.month - 1]} {day.year}"
    )


def period(start: str | None, end: str | None, language: str | None) -> str:
    return (
        fmt_date(start, language)
        if start == end
        else f"{fmt_date(start, language)}–{fmt_date(end, language)}"
    )


def last_ok(steps: list[dict[str, Any]], *names: str) -> dict[str, Any] | None:
    for step in reversed(steps):
        if step.get("status") == "ok" and (not names or step.get("name") in names):
            found: dict[str, Any] = step
            return found
    return None


def balance_line(
    steps: list[dict[str, Any]], language: str | None, leave_type: str | None = None
) -> str | None:
    step = last_ok(steps, "get_my_balance")
    if step is None:
        return None
    rows = step["result"].get("balances", [])
    row = next(
        (r for r in rows if r.get("leave_type") == (leave_type or "annual")), rows[0] if rows else None
    )
    if row is None:
        return None
    return template(
        "balance_line",
        language,
        name=row.get("name") or row.get("leave_type"),
        available=row["available_days"],
    )


def proposal_text(
    kind: str, result: dict[str, Any], steps: list[dict[str, Any]], language: str | None
) -> str:
    facts = {"request_number": result.get("request_number", "")}
    text = (
        template(f"proposal_{kind}", language, **facts)
        if kind != "submit"
        else template("proposal_submit", language)
    )
    line = balance_line(steps, language, result.get("leave_type")) if kind in {"submit", "modify"} else None
    return f"{line} {text}" if line else text


def outcome_text(kind: str, execution: dict[str, Any], language: str | None) -> str:
    outcome = execution.get("outcome", {})
    if execution.get("status") != "executed":
        code = str(outcome.get("code", ""))
        detail = (
            localised(FAILURE_NAMES, code, language)
            if code in FAILURE_NAMES
            else str(outcome.get("detail", code))
        )
        return template("failed", language, detail=detail)
    status = localised(STATUS_NAMES, str(outcome.get("status", "")), language)
    text = template(
        f"executed_{kind}", language, request_number=outcome.get("request_number", ""), status=status
    )
    return text  # the status already names who decides next ("waiting for the manager")


def _violations(result: dict[str, Any]) -> str:
    return "; ".join(str(v.get("detail") or v.get("code")) for v in result.get("violations", []))


def fallback(steps: list[dict[str, Any]], language: str | None) -> str:
    """A plain rendering of the most recent useful tool result (the grounding check failed twice)."""
    el = language == "el"
    step = last_ok(steps)
    if step is None:
        return template("iteration_limit", language)
    name, result = step["name"], step["result"]
    if name == "get_my_balance":
        lines = [
            f"{r.get('name')}: {r['available_days']} "
            + ("διαθέσιμες" if el else "available")
            + f" ({r['balance_days']} / {r['pending_days']})"
            for r in result.get("balances", [])
        ]
        return ("Υπόλοιπα άδειας:\n" if el else "Leave balances:\n") + "\n".join(lines)
    if name == "get_my_requests":
        lines = [
            f"{r['request_number']}: {period(r['start_date'], r['end_date'], language)} · "
            f"{localised(STATUS_NAMES, r['status'], language)}"
            for r in result.get("requests", [])
        ]
        return ("Οι αιτήσεις σας:\n" if el else "Your requests:\n") + ("\n".join(lines) or "—")
    if name == "list_pending_approvals":
        lines = [
            f"{i['n']}. {i['request_number']} · {i['requester']} · {period(i['start_date'], i['end_date'], language)}"
            for i in result.get("items", [])
        ]
        return ("Εκκρεμούν για έγκριση:\n" if el else "Waiting for your approval:\n") + (
            "\n".join(lines) or "—"
        )
    if name == "get_team_availability":
        lines = [
            f"{a['employee']}: {period(a['start_date'], a['end_date'], language)}"
            for a in result.get("absences", [])
        ]
        return ("Απουσίες:\n" if el else "Absences:\n") + ("\n".join(lines) or "—")
    if name == "calculate_working_days":
        return (
            f"{result['working_days']} εργάσιμες ημέρες ({period(result['start_date'], result['end_date'], language)})."
            if el
            else f"{result['working_days']} working days ({period(result['start_date'], result['end_date'], language)})."
        )
    if name in {"validate_leave_request", "prepare_leave_request", "prepare_modification"}:
        if result.get("valid") is False or result.get("prepared") is False:
            reasons = _violations(result) or str(result.get("reason", ""))
            return (
                f"Η αίτηση δεν είναι δυνατή: {reasons}."
                if el
                else f"That request is not possible: {reasons}."
            )
        return (
            f"Η αίτηση είναι εντάξει: {result.get('working_days')} εργάσιμες ημέρες."
            if el
            else f"That request is fine: {result.get('working_days')} working days."
        )
    if name == "get_request_details":
        status = localised(STATUS_NAMES, str(result.get("status", "")), language)
        return f"{result.get('request_number')}: {period(result.get('start_date'), result.get('end_date'), language)} · {status}"
    if name == "search_policies":
        return template("insufficient_evidence", language)
    return template("iteration_limit", language)
