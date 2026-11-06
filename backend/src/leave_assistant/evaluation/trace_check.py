"""Live trace check (M9, docs/evaluation/agent-and-security.md §3): one chat turn through the running api, then the
trace read back through the Langfuse public API (v4: observations by session) and checked for leaks.

    make up-staged LANGFUSE_ENABLED=true … ; python -m leave_assistant.evaluation.trace_check

The turn is sent as a seed employee over HTTP (Keycloak password grant, the SPA's public client) and carries fictional
personal data: the user's own e-mail address and full name and a phone number. Pass criteria: the trace exists for
the turn's session; no e-mail address, phone number or vault original (the user's names) appears anywhere in its
payload; it carries the tenant tag; its user id is the opaque hash, not the e-mail or the Keycloak id.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from typing import Any

import httpx

from leave_assistant.evaluation.harness import RESULTS, git_commit, stamp

API = os.environ.get("E2E_API_URL", f"http://localhost:{os.environ.get('FRONTEND_PORT', '8080')}/api/v1")
KEYCLOAK = os.environ.get("KC_PUBLIC_URL", f"http://localhost:{os.environ.get('KEYCLOAK_PORT', '8081')}")
LANGFUSE = os.environ.get("EVAL_LANGFUSE_HOST", "http://localhost:3000")
USER = "eleni.dimitriou@aegean-logistics.example"
NAMES = ("Ελένη", "Δημητρίου")
PHONE = "6912345678"
MESSAGE = (
    f"Είμαι η {NAMES[0]} {NAMES[1]} ({USER}, τηλ. {PHONE}). "
    "Πόσες μέρες κανονικής άδειας μπορώ να μεταφέρω στον επόμενο χρόνο;"
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_LIKE = re.compile(r"(?<!\d)69\d{8}(?!\d)")


def token(http: httpx.Client) -> str:
    response = http.post(
        f"{KEYCLOAK}/realms/leave-assistant/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "frontend-spa",
            "username": USER,
            "password": os.environ.get("SEED_USER_PASSWORD", "leave-dev"),
        },
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def send_turn(http: httpx.Client, bearer: str) -> tuple[str, list[dict[str, Any]]]:
    headers = {"Authorization": f"Bearer {bearer}"}
    created = http.post(
        f"{API}/chat/conversations", headers={**headers, "Idempotency-Key": str(uuid.uuid4())}
    )
    created.raise_for_status()
    conversation = str(created.json()["id"])
    events: list[dict[str, Any]] = []
    with http.stream(
        "POST",
        f"{API}/chat/conversations/{conversation}/messages",
        headers=headers,
        json={"text": MESSAGE},
        timeout=180.0,
    ) as stream:
        name = ""
        for line in stream.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and name in {"done", "error", "citations"}:
                events.append({"event": name, "data": json.loads(line.split(":", 1)[1])})
    return conversation, events


def observations_for(
    http: httpx.Client, session: str, auth: tuple[str, str], wait_s: float
) -> list[dict[str, Any]]:
    """Every observation of the session (Langfuse v4: `GET /api/public/v2/observations`; the trace list API is gone),
    with input/output, metadata and the trace's tags (`trace_context`)."""
    deadline = time.monotonic() + wait_s
    while True:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"sessionId": session, "fields": "core,basic,io,metadata,trace_context", "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            response = http.get(f"{LANGFUSE}/api/public/v2/observations", params=params, auth=auth)
            response.raise_for_status()
            body = response.json()
            rows += body.get("data", [])
            cursor = (body.get("meta") or {}).get("cursor")
            if not cursor or not body.get("data"):
                break
        if rows or time.monotonic() > deadline:
            return rows
        time.sleep(5)  # the worker ingests asynchronously


def check(observations: list[dict[str, Any]], tenant_tag_prefix: str = "tenant:") -> dict[str, Any]:
    payload = json.dumps(observations, ensure_ascii=False)
    return {
        "traces": len({o.get("traceId") for o in observations}),
        "observations": len(observations),
        "generations": sum(o.get("type") == "GENERATION" for o in observations),
        "emails": sorted(set(EMAIL.findall(payload))),
        "phones": sorted(set(PHONE_LIKE.findall(payload))),
        "names": [n for n in NAMES if n in payload],
        "tenant_tagged": all(
            any(t.startswith(tenant_tag_prefix) for t in tr.get("tags") or []) for tr in observations
        ),
        "user_ids": sorted({str(o.get("userId")) for o in observations}),
        "user_id_opaque": all(
            re.fullmatch(r"[0-9a-f]{16}", str(o.get("userId") or "")) is not None for o in observations
        ),
        "placeholders_seen": sorted(set(re.findall(r"\b[A-Z]+_\d{4}\b", payload)))[:10],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M9 live Langfuse trace check")
    parser.add_argument(
        "--wait", type=float, default=120.0, help="seconds to wait for the trace to be ingested"
    )
    args = parser.parse_args(argv)
    auth = (os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
    with httpx.Client(timeout=30.0) as http:
        conversation, events = send_turn(http, token(http))
        done = next((e["data"] for e in events if e["event"] == "done"), None)
        session = conversation  # the thread id is the conversation id
        observations = observations_for(http, session, auth, args.wait)
    result = {"date": stamp(), "commit": git_commit(), "conversation": conversation, "done": done}
    result |= check(observations)
    result["passed"] = bool(
        result["traces"]
        and not result["emails"]
        and not result["phones"]
        and not result["names"]
        and result["tenant_tagged"]
        and result["user_id_opaque"]
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{result['date']}-trace-check.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
