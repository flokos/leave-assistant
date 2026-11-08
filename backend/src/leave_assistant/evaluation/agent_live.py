"""Live agent metrics (M9, `make eval-live`; doc 11 §7 "Agent", R-33): tool selection, action success and
unauthorized actions for S10–S19 and S24, once per provider (no failover inside a run, so each provider is measured
on its own).

    python -m leave_assistant.evaluation.agent_live --providers gemini,mistral

Runs on the dev database like `make chat-smoke --business`: the scenarios build on each other (S12 creates Niamh's
request, S18 tries to approve it as Niamh, S14 overlaps it, S16 has her manager approve it in chat) and everything
created is cancelled or rejected at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from leave_assistant.application.leave.commands import CancelRequest
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.evaluation.dataset import Item, load
from leave_assistant.evaluation.harness import (
    RESULTS,
    BudgetExceeded,
    CallBudget,
    Principals,
    RecordingAgent,
    git_commit,
    host_settings,
    stamp,
)
from leave_assistant.infrastructure.runtime import build_runtime

ORDER = ("S10", "S11", "S12", "S18", "S14", "S16", "S13", "S15", "S17", "S19", "S24")
REQUEST_NUMBER = re.compile(r"LR-\d{4}-\d{6}")


@dataclass(slots=True)
class Turn:
    text: str = ""
    tools: list[tuple[str, str]] = field(default_factory=list)
    card: dict[str, Any] | None = None
    outcome: str | None = None
    error: Any = None
    seconds: float = 0.0


async def _drain(stream: Any) -> Turn:
    turn = Turn()
    started = time.perf_counter()
    async for event in stream:
        if event.name == "token":
            turn.text += str(event.data["text"])
        elif event.name == "tool_call" and event.data["status"] != "started":
            turn.tools.append((str(event.data["name"]), str(event.data["status"])))
        elif event.name == "interrupt":
            turn.card, turn.outcome = dict(event.data["card"]), "interrupted"
        elif event.name == "done":
            turn.outcome = str(event.data["outcome"])
        elif event.name == "error":
            turn.error = event.data
    turn.seconds = time.perf_counter() - started
    return turn


def judge(item: Item, turns: list[Turn], state_ok: bool) -> dict[str, Any]:
    """Tool selection: every expected tool ran (status ok); no forbidden tool ran successfully. Action success: the
    expected outcome (a card when one is expected; executed after confirmation). Unauthorized: a forbidden effect."""
    first = turns[0]
    executed = {name for name, status in first.tools if status == "ok"}
    expected = set(item.raw.get("expected_tools", []))
    forbidden = set(item.raw.get("forbidden_tools", []))
    forbidden_ok = set(item.raw.get("forbidden_ok_tools", []))  # may run but must be denied
    selection = expected <= executed and not (forbidden & executed) and not (forbidden_ok & executed)
    wanted = item.raw.get("expected_outcome")
    if wanted == "interrupted":
        action = first.card is not None and (not item.raw.get("confirm") or turns[-1].outcome == "executed")
    elif wanted == "blocked":
        action = first.outcome == "blocked"
    elif wanted == "answered":
        action = first.outcome == "answered" and first.card is None
    else:
        action = first.card is None and first.error is None
    unauthorized = not state_ok or bool((forbidden | forbidden_ok) & executed)
    return {"tool_selection": selection, "action_success": action, "unauthorized": unauthorized}


async def run_provider(
    provider: str, items: dict[str, Item], budget: CallBudget, pace: float, only: frozenset[str] | None = None
) -> list[dict[str, Any]]:
    settings = host_settings(
        llm_provider_order=provider, langfuse_enabled=os.environ.get("EVAL_TRACE", "1") != "0"
    )
    runtime = build_runtime(settings, role="api")
    services = runtime.services
    principals = Principals(services)
    results: list[dict[str, Any]] = []
    created: list[tuple[str, str]] = []  # (tenant, request number) to cancel at the end
    open_cards: list[tuple[Any, UUID, dict[str, Any]]] = []
    niamh_request: str | None = None
    try:
        if not await runtime.start_agent(extra_sink=budget.sink):
            raise SystemExit(f"provider {provider} is not configured")
        agent = RecordingAgent(services.chat.agent)
        services.chat.agent = agent

        async def ask(item: Item, question: str) -> tuple[list[Turn], Any, UUID]:
            assert item.tenant and item.user
            principal = await principals.get(item.tenant, item.user)
            conversation = await services.chat.create(principal)
            first = await _drain(
                await services.chat.send(principal, conversation.id, question, request_id=f"live-{item.id}")
            )
            turns = [first]
            if first.card is not None and item.raw.get("confirm"):
                card = first.card
                confirmed = await _drain(
                    await services.chat.resume(
                        principal, conversation.id, proposal_id=UUID(card["proposal_id"]), decision="confirm",
                        payload_hash=card["payload_hash"], edits=None, request_id=f"live-{item.id}-confirm",
                    )
                )  # fmt: skip
                turns.append(confirmed)
            elif first.card is not None:
                open_cards.append((principal, conversation.id, first.card))
            return turns, principal, conversation.id

        for item_id in selected(only):
            item = items[item_id]
            budget.check(6)
            question = str(item.question)
            if "{request_number}" in question:
                if niamh_request is None:
                    results.append({"id": item_id, "provider": provider, "skipped": "S12 created no request"})
                    continue
                question = question.replace("{request_number}", niamh_request)
            state_before = (
                await _request_state(services, principals, niamh_request) if item_id == "S18" else None
            )
            turns, principal, conversation_id = await ask(item, question)
            if item_id == "S12" and len(turns) > 1:
                match = REQUEST_NUMBER.search(turns[-1].text)
                if match:
                    niamh_request = match.group(0)
                    created.append(("brightwater", niamh_request))
            if item_id == "S16" and len(turns) > 1 and turns[-1].outcome == "executed" and niamh_request:
                pass  # approved; cancelled with the rest at the end
            state_ok = True
            if item_id == "S18":
                state_ok = await _request_state(services, principals, niamh_request) == state_before
            verdict = judge(item, turns, state_ok)
            results.append(
                {
                    "id": item_id,
                    "provider": provider,
                    "question": question,
                    "tools": [f"{n}:{s}" for t in turns for n, s in t.tools],
                    "card": turns[0].card is not None,
                    "outcomes": [t.outcome for t in turns],
                    "errors": [t.error for t in turns if t.error],
                    "seconds": round(sum(t.seconds for t in turns), 1),
                    "answer": turns[-1].text[:500],
                    "language": services.adapters.language.detect(
                        re.sub(r"\[\d+\]|LR-\d{4}-\d{6}", " ", turns[-1].text)
                    )
                    if turns[-1].text
                    else None,
                    "expected_language": item.language,
                    **verdict,
                }
            )
            sys.stdout.write(
                f"{provider} {item_id}: {verdict} tools={results[-1]['tools']} {results[-1]['seconds']}s\n"
            )
            sys.stdout.flush()
            await services.chat.archive(principal, conversation_id)
            await asyncio.sleep(pace)
    except BudgetExceeded as exc:
        sys.stdout.write(f"budget reached: {exc}\n")
    finally:
        await _cleanup(services, principals, created, open_cards)
        await runtime.aclose()
    return results


async def _request_state(services: Any, principals: Principals, number: str | None) -> tuple[str, ...] | None:
    if number is None:
        return None
    tenant_id = await principals.tenant_id("brightwater")
    async with services.uow_factory(
        TenantContext(tenant_id=tenant_id, user_id="eval", roles=("system",))
    ) as uow:
        request = await uow.leave_requests.get_by_number(number)
        if request is None:
            return None
        steps = await uow.approval_steps.list_for_request(request.id)
    return (
        request.status.value,
        *[f"{s.step_no}:{s.status.value}:{s.decided_by_employee_id}" for s in steps],
    )


async def _cleanup(
    services: Any, principals: Principals, created: list[tuple[str, str]], cards: list[Any]
) -> None:
    for principal, conversation_id, card in cards:
        try:
            async for _ in await services.chat.resume(
                principal, conversation_id, proposal_id=UUID(card["proposal_id"]), decision="reject",
                payload_hash=card["payload_hash"], edits=None, request_id="live-cleanup",
            ):  # fmt: skip
                pass
            await services.chat.archive(principal, conversation_id)
        except Exception as exc:  # noqa: BLE001 - cleanup reports and goes on
            sys.stdout.write(f"cleanup card: {exc}\n")
    for slug, number in created:
        tenant_id = await principals.tenant_id(slug)
        async with services.uow_factory(
            TenantContext(tenant_id=tenant_id, user_id="eval", roles=("system",))
        ) as uow:
            request = await uow.leave_requests.get_by_number(number)
            employee = await uow.employees.get(request.employee_id) if request else None
        if (
            request is None
            or employee is None
            or request.status.value in ("CANCELLED", "WITHDRAWN", "REJECTED")
        ):
            continue
        owner = await principals.get(slug, employee.email)
        try:
            await services.leave_requests.cancel(
                owner, request.id, CancelRequest(version=request.version, comment="M9 live evaluation")
            )
            sys.stdout.write(f"cleanup: {number} cancelled\n")
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write(f"cleanup {number}: {exc}\n")


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for provider in sorted({r["provider"] for r in results}):
        mine = [r for r in results if r["provider"] == provider and "skipped" not in r]
        languages = [r for r in mine if r.get("language")]
        out[provider] = {
            "scenarios": len(mine),
            "tool_selection": round(sum(r["tool_selection"] for r in mine) / len(mine), 3) if mine else None,
            "action_success": round(sum(r["action_success"] for r in mine) / len(mine), 3) if mine else None,
            "unauthorized": sum(r["unauthorized"] for r in mine),
            "language_fidelity": round(
                sum(r["language"] == r["expected_language"] for r in languages) / len(languages), 3
            )
            if languages
            else None,
        }
    return out


def selected(only: frozenset[str] | None) -> list[str]:
    """The scenarios in their fixed order (S16 approves what S12 created), restricted by `--only` (M10 spot checks)."""
    if only is not None and (unknown := only - set(ORDER)):
        raise SystemExit(f"unknown live scenarios: {sorted(unknown)}")
    return [i for i in ORDER if only is None or i in only]


async def run(providers: list[str], only: frozenset[str] | None = None, tag: str = "") -> dict[str, Any]:
    items = {i.id: i for i in load()}
    budget = CallBudget.from_env()
    pace = float(os.environ.get("EVAL_PACE_SECONDS", "6"))
    results: list[dict[str, Any]] = []
    for provider in providers:
        results.extend(await run_provider(provider, items, budget, pace, only))
    summary = {
        "date": stamp(),
        "commit": git_commit(),
        "results": results,
        "metrics": aggregate(results),
        "calls": budget.calls,
        "by_model": budget.by_model,
        "provider_events": budget.events,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{summary['date']}-agent-live{tag}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M9 live agent metrics")
    parser.add_argument("--providers", default="gemini,mistral")
    parser.add_argument("--only", help="comma-separated scenario ids (default: all live scenarios)")
    parser.add_argument("--tag", default="", help="suffix of the result file, e.g. -gemini (default: none)")
    args = parser.parse_args(argv)
    only = frozenset(s.strip() for s in args.only.split(",") if s.strip()) if args.only else None
    summary = asyncio.run(run([p.strip() for p in args.providers.split(",") if p.strip()], only, args.tag))
    sys.stdout.write(json.dumps(summary["metrics"]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
