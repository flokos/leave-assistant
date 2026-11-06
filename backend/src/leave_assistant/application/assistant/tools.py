"""The tool catalogue (doc 04 §5.1, ADR-0009): what the model may ask for, per role and phase.

Arguments are untrusted: every tool has a strict Pydantic model (date windows, enums, the `LR-YYYY-NNNNNN` request
number pattern, length caps). The JSON schema sent to providers is deliberately minimal — type, description, enum,
required — because providers accept different JSON-schema subsets; the model here does the real validation.
Execution tools (submit, approve, cancel, modify) do not exist: `prepare_*` only creates a proposal.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from leave_assistant.application.llm.ports import ToolSpec
from leave_assistant.domain.tenancy import Role

REQUEST_NUMBER = re.compile(r"^LR-\d{4}-\d{6}$")
DATE_HELP = "date as YYYY-MM-DD"


class Phase(StrEnum):
    RETRIEVE = "retrieve"
    PREPARE = "prepare"


class ToolName(StrEnum):
    GET_MY_BALANCE = "get_my_balance"
    GET_MY_REQUESTS = "get_my_requests"
    GET_LEAVE_TYPES_AND_RULES = "get_leave_types_and_rules"
    CALCULATE_WORKING_DAYS = "calculate_working_days"
    VALIDATE_LEAVE_REQUEST = "validate_leave_request"
    PREPARE_LEAVE_REQUEST = "prepare_leave_request"
    PREPARE_MODIFICATION = "prepare_modification"
    PREPARE_CANCELLATION = "prepare_cancellation"
    GET_TEAM_AVAILABILITY = "get_team_availability"
    LIST_PENDING_APPROVALS = "list_pending_approvals"
    GET_REQUEST_DETAILS = "get_request_details"
    PREPARE_APPROVAL_DECISION = "prepare_approval_decision"
    SEARCH_POLICIES = "search_policies"


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _request_number(value: str) -> str:
    number = value.strip().upper()
    if not REQUEST_NUMBER.match(number):
        raise ValueError("request_number must look like LR-2026-000123")
    return number


class NoArgs(Args):
    pass


class BalanceArgs(Args):
    leave_type: str | None = Field(None, max_length=32, description="leave type code, e.g. annual")
    year: int | None = Field(None, ge=2000, le=2100, description="calendar year; default the current year")


class RequestsArgs(Args):
    status: Literal["pending", "approved", "rejected", "cancelled", "all"] | None = Field(
        None, description="filter by status group"
    )
    year: int | None = Field(None, ge=2000, le=2100, description="calendar year of the leave")


class PeriodArgs(Args):
    start_date: date = Field(description=f"first day, {DATE_HELP}")
    end_date: date | None = Field(None, description=f"last day, {DATE_HELP}; omit when giving working_days")
    working_days: int | None = Field(
        None, ge=1, le=60, description="number of working days instead of end_date"
    )

    @model_validator(mode="after")
    def _one_end(self) -> PeriodArgs:
        if (self.end_date is None) == (self.working_days is None):
            raise ValueError("give exactly one of end_date or working_days")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class LeaveArgs(PeriodArgs):
    leave_type: str = Field(max_length=32, description="leave type code, e.g. annual, sick, unpaid")


class PrepareLeaveArgs(LeaveArgs):
    reason: str | None = Field(None, max_length=500, description="optional reason in the user's words")


class ModificationArgs(PeriodArgs):
    request_number: str = Field(description="request number, e.g. LR-2026-000123")

    _number = field_validator("request_number")(_request_number)


class RequestRefArgs(Args):
    request_number: str = Field(description="request number, e.g. LR-2026-000123")

    _number = field_validator("request_number")(_request_number)


class AvailabilityArgs(Args):
    from_date: date = Field(description=f"first day, {DATE_HELP}")
    to_date: date = Field(description=f"last day, {DATE_HELP}")
    department: str | None = Field(
        None, max_length=16, description="department code; default your own department"
    )

    @model_validator(mode="after")
    def _order(self) -> AvailabilityArgs:
        if self.to_date < self.from_date:
            raise ValueError("to_date must not be before from_date")
        if (self.to_date - self.from_date).days > 92:
            raise ValueError("the window may span at most 92 days")
        return self


class DecisionArgs(RequestRefArgs):
    decision: Literal["approve", "reject"] = Field(description="approve or reject")
    comment: str | None = Field(None, max_length=500, description="required when rejecting")

    @model_validator(mode="after")
    def _comment(self) -> DecisionArgs:
        if self.decision == "reject" and not (self.comment or "").strip():
            raise ValueError("a comment is required when rejecting")
        return self


class SearchArgs(Args):
    query: str = Field(min_length=3, max_length=300, description="what to look up in the company policies")


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: ToolName
    phase: Phase
    roles: frozenset[Role]
    args: type[Args]
    description: str


SELF_SERVICE = frozenset({Role.EMPLOYEE, Role.MANAGER, Role.HR_ADMIN})
APPROVERS = frozenset({Role.MANAGER, Role.HR_ADMIN})
ALL_TENANT_ROLES = frozenset({Role.EMPLOYEE, Role.MANAGER, Role.HR_ADMIN, Role.COMPANY_ADMIN})

CATALOGUE: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name=ToolName.GET_MY_BALANCE,
        phase=Phase.RETRIEVE,
        roles=SELF_SERVICE,
        args=BalanceArgs,
        description="Your leave balances per type: balance, pending, available days.",
    ),
    ToolDefinition(
        name=ToolName.GET_MY_REQUESTS,
        phase=Phase.RETRIEVE,
        roles=SELF_SERVICE,
        args=RequestsArgs,
        description="Your leave requests: number, type, dates, working days, status.",
    ),
    ToolDefinition(
        name=ToolName.GET_LEAVE_TYPES_AND_RULES,
        phase=Phase.RETRIEVE,
        roles=ALL_TENANT_ROLES,
        args=NoArgs,
        description="Leave types and this year's rules: entitlement, notice, approval chain.",
    ),
    ToolDefinition(
        name=ToolName.CALCULATE_WORKING_DAYS,
        phase=Phase.RETRIEVE,
        roles=ALL_TENANT_ROLES,
        args=PeriodArgs,
        description="Working days in a period without weekends and holidays, or the end date for N working days.",
    ),
    ToolDefinition(
        name=ToolName.VALIDATE_LEAVE_REQUEST,
        phase=Phase.RETRIEVE,
        roles=SELF_SERVICE,
        args=LeaveArgs,
        description="Check a possible request (balance, overlaps, rules) without creating anything.",
    ),
    ToolDefinition(
        name=ToolName.PREPARE_LEAVE_REQUEST,
        phase=Phase.PREPARE,
        roles=SELF_SERVICE,
        args=PrepareLeaveArgs,
        description="Prepare a new leave request; the user confirms it on a card.",
    ),
    ToolDefinition(
        name=ToolName.PREPARE_MODIFICATION,
        phase=Phase.PREPARE,
        roles=SELF_SERVICE,
        args=ModificationArgs,
        description="Prepare new dates for one of your requests; the user confirms.",
    ),
    ToolDefinition(
        name=ToolName.PREPARE_CANCELLATION,
        phase=Phase.PREPARE,
        roles=SELF_SERVICE,
        args=RequestRefArgs,
        description="Prepare the cancellation of one of your requests; the user confirms.",
    ),
    ToolDefinition(
        name=ToolName.GET_TEAM_AVAILABILITY,
        phase=Phase.RETRIEVE,
        roles=APPROVERS,
        args=AvailabilityArgs,
        description="Who in your department is absent in a period.",
    ),
    ToolDefinition(
        name=ToolName.LIST_PENDING_APPROVALS,
        phase=Phase.RETRIEVE,
        roles=APPROVERS,
        args=NoArgs,
        description="Requests waiting for your approval, numbered.",
    ),
    ToolDefinition(
        name=ToolName.GET_REQUEST_DETAILS,
        phase=Phase.RETRIEVE,
        roles=SELF_SERVICE,
        args=RequestRefArgs,
        description="Details and approval steps of a request you own or may decide.",
    ),
    ToolDefinition(
        name=ToolName.PREPARE_APPROVAL_DECISION,
        phase=Phase.PREPARE,
        roles=APPROVERS,
        args=DecisionArgs,
        description="Prepare your approve or reject decision; you confirm it. Rejections need a comment.",
    ),
    ToolDefinition(
        name=ToolName.SEARCH_POLICIES,
        phase=Phase.RETRIEVE,
        roles=ALL_TENANT_ROLES,
        args=SearchArgs,
        description="Search the company leave policies; cite results as [n].",
    ),
)
BY_NAME: dict[str, ToolDefinition] = {t.name.value: t for t in CATALOGUE}


# Which tools a plan step binds, by the classified intent. Only a narrowing — for prompt size (schemas are the
# biggest part of a plan prompt) and for small models; the gateway still enforces the full role allowlist.
APPROVAL_GROUP = frozenset(
    {
        ToolName.LIST_PENDING_APPROVALS,
        ToolName.PREPARE_APPROVAL_DECISION,
        ToolName.GET_TEAM_AVAILABILITY,
        ToolName.GET_REQUEST_DETAILS,
        ToolName.GET_MY_BALANCE,
    }
)
SELF_SERVICE_GROUP = frozenset(t.name for t in CATALOGUE) - {
    ToolName.LIST_PENDING_APPROVALS,
    ToolName.PREPARE_APPROVAL_DECISION,
    ToolName.GET_TEAM_AVAILABILITY,
    # not bound (still in the catalogue and the gateway): prepare_leave_request validates by itself, and models
    # asked "can I request five days?" answered from a validation instead of preparing the card (live, M7)
    ToolName.VALIDATE_LEAVE_REQUEST,
}
APPROVAL_INTENTS = frozenset({"approval_action", "availability"})


def allowed_tools(
    roles: frozenset[Role], *, read_only: bool = False, intent: str | None = None
) -> tuple[ToolDefinition, ...]:
    """The role allowlist (doc 04 §5.2 step 1); `read_only` for medium-risk input (doc 08 §8.3); `intent` narrows
    the bound set to the approval or the self-service group."""
    group = None if intent is None else (APPROVAL_GROUP if intent in APPROVAL_INTENTS else SELF_SERVICE_GROUP)
    return tuple(
        t
        for t in CATALOGUE
        if roles & t.roles
        and (not read_only or t.phase is Phase.RETRIEVE)
        and (group is None or t.name in group)
    )


def _simplify(node: Any) -> Any:
    """Pydantic JSON schema → the subset every provider accepts (no titles, nulls, formats, patterns, defaults)."""
    if isinstance(node, dict):
        if "anyOf" in node:
            branches = [b for b in node["anyOf"] if b.get("type") != "null"]
            merged = {**branches[0]} if len(branches) == 1 else {"type": "string"}
            if "description" in node:
                merged["description"] = node["description"]
            node = merged
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in {
                "title",
                "default",
                "format",
                "pattern",
                "additionalProperties",
                "maxLength",
                "minLength",
            }:
                continue
            if key in {"minimum", "maximum"}:
                continue
            if key == "const":
                out["enum"] = [value]
                continue
            out[key] = _simplify(value)
        return out
    if isinstance(node, list):
        return [_simplify(v) for v in node]
    return node


def parameters_for(args: type[Args]) -> dict[str, Any]:
    """Property descriptions are dropped: JSON schema is token-heavy (45 e5 tokens for one parameter), and the shared
    formats — dates, request numbers, end_date or working_days — are stated once in the plan prompt (budget, M7)."""
    schema = _simplify(args.model_json_schema())
    properties = {
        name: {k: v for k, v in prop.items() if k != "description"}
        for name, prop in schema.get("properties", {}).items()
    }
    result: dict[str, Any] = {"type": "object", "properties": properties}
    required = schema.get("required")
    if required:
        result["required"] = required
    return result


def spec_for(tool: ToolDefinition) -> ToolSpec:
    return ToolSpec(name=tool.name.value, description=tool.description, parameters=parameters_for(tool.args))
