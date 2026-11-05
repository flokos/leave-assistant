"""Every allowed transition of doc 05 §2 works; everything else is `document/invalid-state`."""

from __future__ import annotations

import pytest

from leave_assistant.domain.documents.lifecycle import (
    RETRIEVABLE_STATUSES,
    TRANSITIONS,
    InvalidTransition,
    VersionEvent,
    can,
    transition,
)
from leave_assistant.domain.documents.types import VersionStatus

S, E = VersionStatus, VersionEvent


@pytest.mark.parametrize(("status", "event"), sorted(TRANSITIONS, key=lambda k: (k[0].value, k[1].value)))
def test_table_transitions(status: VersionStatus, event: VersionEvent) -> None:
    assert transition(status, event) == TRANSITIONS[(status, event)]
    assert can(status, event)


def test_table_has_the_documented_shape() -> None:
    assert len(TRANSITIONS) == 15
    assert transition(S.UPLOADED, E.PROCESS) is S.PROCESSING
    assert transition(S.PROCESSING, E.ACTIVATE) is S.ACTIVE
    assert transition(S.ACTIVE, E.SUPERSEDE) is S.SUPERSEDED
    assert transition(S.FAILED, E.REPROCESS) is S.UPLOADED
    assert RETRIEVABLE_STATUSES == frozenset({S.ACTIVE})


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (S.UPLOADED, E.ACTIVATE),
        (S.ACTIVE, E.ACTIVATE),
        (S.ACTIVE, E.RELEASE),
        (S.SUPERSEDED, E.RETIRE),
        (S.DELETED, E.REPROCESS),
        (S.QUARANTINED, E.REJECT),
        (S.ACTIVE, E.DELETE),  # active versions are retired or superseded first
    ],
)
def test_invalid_transitions(status: VersionStatus, event: VersionEvent) -> None:
    assert not can(status, event)
    with pytest.raises(InvalidTransition) as info:
        transition(status, event)
    assert info.value.code == "document/invalid-state"
    assert info.value.params == {"status": status.value, "event": event.value}
