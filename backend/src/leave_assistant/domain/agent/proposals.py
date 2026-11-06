"""Action proposals (doc 04 §6, doc 07 §4.3): what `prepare_*` tools create and only a human confirmation executes.

The proposal id is the idempotency key; the payload hash binds the confirmation to exactly the content the user saw.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from leave_assistant.domain.errors import InvalidValue


class ProposalKind(StrEnum):
    SUBMIT = "submit"
    MODIFY = "modify"
    CANCEL = "cancel"
    APPROVE = "approve"
    REJECT = "reject"


class ProposalStatus(StrEnum):
    PENDING = "pending"  # waiting for the user's confirmation (an interrupt is open)
    CONFIRMED = "confirmed"  # claimed by one execution — nobody else may execute it
    EXECUTED = "executed"
    REJECTED = "rejected"  # declined, superseded by a new message or replaced by an edit
    EXPIRED = "expired"
    FAILED = "failed"  # a business rule or a persistent error stopped the execution


class ConfirmationDecision(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    EDIT = "edit"


TERMINAL: frozenset[ProposalStatus] = frozenset(
    {ProposalStatus.EXECUTED, ProposalStatus.REJECTED, ProposalStatus.EXPIRED, ProposalStatus.FAILED}
)
TRANSITIONS: Mapping[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PENDING: frozenset(
        {ProposalStatus.CONFIRMED, ProposalStatus.REJECTED, ProposalStatus.EXPIRED}
    ),
    ProposalStatus.CONFIRMED: frozenset({ProposalStatus.EXECUTED, ProposalStatus.FAILED}),
}
EDITABLE: frozenset[ProposalKind] = frozenset({ProposalKind.SUBMIT, ProposalKind.MODIFY})


def check_transition(current: ProposalStatus, target: ProposalStatus) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidValue(
            f"a {current.value} proposal cannot become {target.value}",
            code="chat/proposal-state",
            params={"from": current.value, "to": target.value},
        )


def canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()
