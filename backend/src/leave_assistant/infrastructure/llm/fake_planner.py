"""The fake model's default business planner (doc 04 §12): deterministic tool selection for the dev stack, the e2e
suite and the scenario defaults when no script is queued. It reads only what a real model would see — the
LLM-safe plan prompt: today's date, the answer language, the user's turn and the `<tool_result>` blocks.

It is deliberately small: clear EL/EN phrasings of the doc 11 business scenarios, nothing clever. Tests that need
other behaviour script the `plan` prompt instead.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from leave_assistant.application.llm.ports import ToolCall, ToolSpec, ToolStep

TODAY = re.compile(r"Today is (\d{4}-\d{2}-\d{2})")
TARGET = re.compile(r"Answer in (Greek|English)")
USER = re.compile(
    r"<user>\n?((?:(?!<user>).)*?)\n?</user>", re.S
)  # prompts mention "<user> tags" in their rules
RESULT = re.compile(r'<tool_result n="(\d+)" name="(\w+)" status="(\w+)">\n?(.*?)\n?</tool_result>', re.S)
REQUEST_NUMBER = re.compile(r"\bLR-\d{4}-\d{6}\b", re.I)
INDEX = re.compile(r"#\s?(\d{1,2})\b")
DEPARTMENT = re.compile(r"\b(?:department|τμημα|τμήμα)\s+([A-Za-z]{2,8})\b|\b(OPS|FIN|HR|WH|ENG|PROD|PPL)\b")

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "ιανουαριου": 1, "φεβρουαριου": 2, "μαρτιου": 3, "απριλιου": 4, "μαιου": 5, "ιουνιου": 6, "ιουλιου": 7,
    "αυγουστου": 8, "σεπτεμβριου": 9, "οκτωβριου": 10, "νοεμβριου": 11, "δεκεμβριου": 12,
}  # fmt: skip
NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "μια": 1, "ενα": 1, "δυο": 2, "τρεις": 3, "τρια": 3, "τεσσερις": 4, "πεντε": 5, "εξι": 6,
    "επτα": 7, "εφτα": 7, "οκτω": 8, "εννεα": 9, "δεκα": 10,
}  # fmt: skip
NUMBERS = {k.replace("ς", "σ"): v for k, v in NUMBERS.items()}  # matched against folded text (final ς → σ)
MONTH_WORDS = "|".join(sorted(MONTHS, key=len, reverse=True))
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th|η)?\s+({MONTH_WORDS})\b")
MONTH_DAY = re.compile(rf"\b({MONTH_WORDS})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b")
DAYS = re.compile(
    rf"\b(\d{{1,2}}|{'|'.join(NUMBERS)})\s+(?:working\s+|εργασιμεσ\s+)?(?:days?|μερεσ|ημερεσ|μερα|ημερα)\b"
)

WORDS = {
    "balance": ("υπολοιπ", "απομενουν", "remaining", "balance", "days left", "days do i have"),
    "history": ("ιστορικο", "history", "my requests", "οι αιτησεις μου"),
    "availability": ("ποιος λειπει", "ποιοι λειπουν", "who is off", "who is away", "who is absent", "availability"),
    "approval": ("εκκρεμ", "pending approval", "approve", "εγκριν", "ενεκρινε", "εγκρισ", "reject", "απορριψ"),
    "cancel": ("ακυρωσ", "ακυρωσε", "cancel"),
    "modify": ("αλλαξε", "μεταθεσ", "move my", "change my", "modify"),
    "sick": ("sick", "αρρωστ", "ασθεν", "αναρρωτ"),
    "unpaid": ("unpaid", "ανευ αποδοχων"),
    "reject": ("reject", "decline", "απορριψ", "απορριψε"),
}  # fmt: skip
STATUS_EL = {
    "SUBMITTED": "υποβλήθηκε",
    "PENDING_MANAGER": "σε αναμονή προϊσταμένου",
    "PENDING_HR": "σε αναμονή HR",
    "APPROVED": "εγκρίθηκε",
    "ACTIVE": "σε εξέλιξη",
    "COMPLETED": "ολοκληρώθηκε",
    "REJECTED": "απορρίφθηκε",
    "CANCELLED": "ακυρώθηκε",
    "WITHDRAWN": "αποσύρθηκε",
    "MODIFICATION_PENDING": "αλλαγή σε αναμονή",
}


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).replace("ς", "σ")


def _has(folded: str, key: str) -> bool:
    return any(w.replace("ς", "σ") in folded for w in WORDS[key])


def _future(day: int, month: int, today: date) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today - timedelta(days=7):
            return candidate
    return None


def parse_start(folded: str, today: date) -> date | None:
    if "χθες" in folded or "yesterday" in folded:
        return today - timedelta(days=1)
    if match := DAY_MONTH.search(folded):
        return _future(int(match.group(1)), MONTHS[match.group(2)], today)
    if match := MONTH_DAY.search(folded):
        return _future(int(match.group(2)), MONTHS[match.group(1)], today)
    return None


def parse_days(folded: str) -> int | None:
    match = DAYS.search(folded)
    if match is None:
        return None
    token = match.group(1)
    return int(token) if token.isdigit() else NUMBERS.get(token)


def _results(text: str) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for _, name, status, body in RESULT.findall(text):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}
        out.append((name, status, data if isinstance(data, dict) else {}))
    return out


def _next_week(today: date) -> tuple[date, date]:
    monday = today + timedelta(days=7 - today.weekday())
    return monday, monday + timedelta(days=4)


def _short(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}" if len(day) >= 10 else day


class Planner:
    def __init__(self, prompt: str, tools: list[ToolSpec]) -> None:
        self.prompt = prompt
        self.tools = {t.name for t in tools}
        today = TODAY.search(prompt)
        self.today = date.fromisoformat(today.group(1)) if today else date.today()
        target = TARGET.search(prompt)
        self.greek = bool(target and target.group(1) == "Greek")
        users = USER.findall(prompt)
        self.user = users[-1].strip() if users else ""
        self.folded = fold(self.user)
        self.results = _results(prompt)
        self.called = {name for name, _, _ in self.results}

    def say(self, el: str, en: str) -> ToolStep:
        return ToolStep(text=el if self.greek else en)

    def call(self, name: str, **arguments: Any) -> ToolStep:
        return ToolStep(calls=(ToolCall(name, {k: v for k, v in arguments.items() if v is not None}),))

    def last(self, name: str) -> tuple[str, dict[str, Any]] | None:
        for tool, status, data in reversed(self.results):
            if tool == name:
                return status, data
        return None

    def step(self) -> ToolStep:
        for _tool, status, data in reversed(self.results[-1:]):
            if status != "ok":
                return self.say(
                    f"Δεν μπορώ να το κάνω αυτό: {data.get('message', 'δεν επιτρέπεται')}.",
                    f"I can't do that: {data.get('message', 'not permitted')}.",
                )
        f = self.folded
        if _has(f, "approval") and {"list_pending_approvals"} & self.tools:
            return self.approval()
        if _has(f, "availability") and "get_team_availability" in self.tools:
            return self.availability()
        if _has(f, "cancel"):
            return self.cancel()
        start = parse_start(f, self.today)
        if _has(f, "balance") and "get_my_balance" not in self.called and "get_my_balance" in self.tools:
            return self.call("get_my_balance")
        if start is not None and "prepare_leave_request" in self.tools:
            return self.request(start)
        if _has(f, "history") and "get_my_requests" not in self.called:
            return self.call("get_my_requests", year=self.today.year)
        return self.answer()

    def request(self, start: date) -> ToolStep:
        prepared = self.last("prepare_leave_request")
        if prepared is not None:
            return self.explain_invalid(prepared[1])
        leave_type = (
            "sick" if _has(self.folded, "sick") else "unpaid" if _has(self.folded, "unpaid") else "annual"
        )
        days = parse_days(self.folded)
        if "σημερα" in self.folded or "today" in self.folded:
            return self.call(
                "prepare_leave_request",
                leave_type=leave_type,
                start_date=start.isoformat(),
                end_date=self.today.isoformat(),
            )
        return self.call(
            "prepare_leave_request",
            leave_type=leave_type,
            start_date=start.isoformat(),
            working_days=days or 1,
        )

    def explain_invalid(self, report: dict[str, Any]) -> ToolStep:
        violations = "; ".join(str(v.get("detail") or v.get("code")) for v in report.get("violations", []))
        available = (report.get("balance") or {}).get("available_days")
        extra_el = f" Διαθέσιμες ημέρες: {available}." if available is not None else ""
        extra_en = f" Available days: {available}." if available is not None else ""
        return self.say(
            f"Δεν μπορώ να ετοιμάσω την αίτηση: {violations}.{extra_el} Μπορείτε να ζητήσετε λιγότερες ημέρες ή "
            "άδεια άνευ αποδοχών.",
            f"I can't prepare that request: {violations}.{extra_en} You could ask for fewer days or for unpaid "
            "leave.",
        )

    def approval(self) -> ToolStep:
        listed = self.last("list_pending_approvals")
        if listed is None:
            return self.call("list_pending_approvals")
        items = listed[1].get("items", [])
        number = REQUEST_NUMBER.search(self.user)
        index = INDEX.search(self.user)
        target = None
        if number:
            target = next((i for i in items if i["request_number"] == number.group(0).upper()), None)
            if target is None:
                target = {"request_number": number.group(0).upper()}
        elif index:
            n = int(index.group(1))
            target = next((i for i in items if i.get("n") == n), None)
        wants_decision = any(w in self.folded for w in ("approve", "εγκριν", "ενεκρινε", "reject", "απορριψ"))
        if target is not None and wants_decision and "prepare_approval_decision" not in self.called:
            reject = _has(self.folded, "reject")
            comment = None
            if reject:
                match = re.search(r"(?:because|γιατι|επειδη)\s+(.+)$", self.folded)
                comment = match.group(1) if match else "Not possible in that period."
            return self.call(
                "prepare_approval_decision",
                request_number=target["request_number"],
                decision="reject" if reject else "approve",
                comment=comment,
            )
        if not items:
            return self.say("Δεν εκκρεμεί καμία αίτηση για έγκριση.", "Nothing is waiting for your approval.")
        lines = [
            f"{i['n']}. {i['request_number']} · {i['requester']} · {_short(i['start_date'])}–{_short(i['end_date'])} "
            f"({i['working_days']})"
            for i in items
        ]
        return self.say(
            f"Εκκρεμούν {len(items)} αιτήσεις για έγκριση:\n" + "\n".join(lines),
            f"{len(items)} requests are waiting for your approval:\n" + "\n".join(lines),
        )

    def availability(self) -> ToolStep:
        found = self.last("get_team_availability")
        if found is None:
            start, end = _next_week(self.today)
            department = None
            if match := DEPARTMENT.search(self.user):
                department = (match.group(1) or match.group(2) or "").upper() or None
            return self.call(
                "get_team_availability",
                from_date=start.isoformat(),
                to_date=end.isoformat(),
                department=department,
            )
        absences = found[1].get("absences", [])
        if not absences:
            return self.say("Κανείς δεν λείπει σε αυτό το διάστημα.", "Nobody is away in that period.")
        lines = [f"{a['employee']}: {_short(a['start_date'])}–{_short(a['end_date'])}" for a in absences]
        return self.say(
            f"Λείπουν {len(absences)}:\n" + "\n".join(lines), f"{len(absences)} away:\n" + "\n".join(lines)
        )

    def cancel(self) -> ToolStep:
        number = REQUEST_NUMBER.search(self.user)
        if number is None:
            listed = self.last("get_my_requests")
            if listed is None:
                return self.call("get_my_requests", status="pending")
            requests = listed[1].get("requests", [])
            if not requests:
                return self.say("Δεν βρήκα αίτηση για ακύρωση.", "I found no request to cancel.")
            return self.call("prepare_cancellation", request_number=requests[0]["request_number"])
        if "prepare_cancellation" in self.called:
            return self.answer()
        return self.call("prepare_cancellation", request_number=number.group(0).upper())

    def answer(self) -> ToolStep:
        balance = self.last("get_my_balance")
        if balance is not None:
            rows = balance[1].get("balances", [])
            row = next((r for r in rows if r.get("leave_type") == "annual"), rows[0] if rows else None)
            if row is None:
                return self.say("Δεν βρήκα υπόλοιπο άδειας.", "I found no leave balance.")
            return self.say(
                f"Το διαθέσιμο υπόλοιπο κανονικής άδειας είναι {row['available_days']} ημέρες "
                f"({row['balance_days']} υπόλοιπο, {row['pending_days']} σε εκκρεμότητα).",
                f"Your available annual leave is {row['available_days']} days "
                f"({row['balance_days']} balance, {row['pending_days']} pending).",
            )
        listed = self.last("get_my_requests")
        if listed is not None:
            requests = listed[1].get("requests", [])
            if not requests:
                return self.say("Δεν έχετε αιτήσεις φέτος.", "You have no requests this year.")
            lines = [
                f"{r['request_number']}: {_short(r['start_date'])}–{_short(r['end_date'])} · "
                + (
                    STATUS_EL.get(r["status"], r["status"])
                    if self.greek
                    else r["status"].lower().replace("_", " ")
                )
                for r in requests[:5]
            ]
            return self.say(
                f"Έχετε {listed[1].get('count', len(requests))} αιτήσεις:\n" + "\n".join(lines),
                f"You have {listed[1].get('count', len(requests))} requests:\n" + "\n".join(lines),
            )
        return self.say(
            "Πείτε μου τι θέλετε να κάνετε: π.χ. υπόλοιπο, νέα αίτηση ή ακύρωση.",
            "Tell me what you need: for example your balance, a new request or a cancellation.",
        )


def default_plan(prompt: str, tools: list[ToolSpec]) -> ToolStep:
    return Planner(prompt, tools).step()
