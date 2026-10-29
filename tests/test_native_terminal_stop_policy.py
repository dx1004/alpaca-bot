"""Raw prior-resize proof changes observation only; legacy history stays pinned."""

from datetime import timedelta
from fractions import Fraction
from typing import Any

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_exit_evidence import (
    NATIVE_TERMINAL_STOP_POLICY,
    derive_native_exit_census,
)
from alpaca_ross_bot.oco_journal import OcoJournal
from test_native_oco_correlation_integration import ROOT, STOP
from test_native_oco_http import native_http as native_http
from test_oco_exit_evidence import bind, capture, fill, post


def partial(fixture: Any) -> tuple[Any, Any, Any, int]:
    reported = post(fixture)
    owner = fixture[0].requests.owner
    at = fixture[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.order["status"] = "partially_filled"
    reported.order["legs"][0]["qty"] = "0.5"
    sequence = capture(fixture, reported, at)
    first = bind(fixture, sequence, at, "partial-resize")
    assert first.native_exit_bindings[0]["status"] == "BOUND_NATIVE_EXIT_CENSUS"
    return reported, owner, at, sequence


def terminal(fixture: Any, reported: Any, at: Any, *, stop: str = "0") -> int:
    reported.order.update(status="filled", filled_qty="1", updated_at=at.isoformat())
    reported.order["legs"][0].update(
        status="filled" if Fraction(stop) else "canceled",
        filled_qty=stop,
        updated_at=at.isoformat(),
    )
    reported.fills[ROOT].append(
        reported.fills[ROOT][0]
        | {"id": "target-final", "transaction_time": at.isoformat()}
    )
    if Fraction(stop):
        reported.fills[STOP] = [
            reported.fills[ROOT][-1]
            | {
                "id": "stop-final",
                "order_id": STOP,
                "qty": stop,
                "price": "9.50",
            }
        ]
    return capture(fixture, reported, at)


def opted(owner: Any, seq: int, at: Any, event: str = "terminal-v1") -> Any:
    return owner.bind_native_exit_census(
        event,
        "group-a",
        seq,
        at,
        timedelta(seconds=30),
        at,
        observation_policy=NATIVE_TERMINAL_STOP_POLICY,
    )


def test_terminal_resize_policy_preserves_legacy_and_retries_reopen(
    native_http: Any,
) -> None:
    reported, owner, at, prior_seq = partial(native_http)
    # Historical proof uses its own decision/freshness; today's capture is fresh.
    later = at + timedelta(minutes=2)
    seq = terminal(native_http, reported, later)
    legacy = bind(native_http, seq, later, "legacy-terminal")
    assert legacy.native_exit_bindings[0]["schema"] == 1
    assert legacy.native_exit_bindings[0]["issues"] == [
        "NATIVE_EXIT_STOP_QUANTITY_RELATION_UNQUALIFIED"
    ]
    history = owner.journal.read_oco_research_events()
    accepted = opted(owner, seq, later)
    receipt = accepted.native_exit_bindings[0]
    assert receipt["schema"] == 2 and receipt["status"] == "BOUND_NATIVE_EXIT_CENSUS"
    assert receipt["terminal_stop_proof"]["capture_ref"]["sequence"] == prior_seq
    assert receipt["source_observation"]["orders"][1]["quantity"] == "1/2"
    assert accepted.model.exit_quantity == 1
    assert not receipt["risk_release_authorized"] and not receipt["protection_accepted"]
    assert owner.journal.read_oco_research_events()[: len(history)] == history
    before = owner.journal.read_oco_research_events()
    assert opted(owner, seq, later) == accepted
    assert owner.journal.read_oco_research_events() == before
    next_at = later + timedelta(seconds=1)
    repeat = opted(
        owner, capture(native_http, reported, next_at), next_at, "repeat-terminal"
    )
    assert repeat.native_exit_bindings[0]["status"] == "BOUND_NATIVE_EXIT_CENSUS"
    assert (
        repeat.native_exit_bindings[0]["terminal_stop_proof"]["capture_ref"]["sequence"]
        == prior_seq
    )
    assert owner.verify_rebuild()
    reopened = Journal.open_private(owner.journal._path, owner.journal._path.parent)
    try:
        assert OcoJournal(reopened, owner.initial).view() == repeat
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "case",
    [
        "working",
        "identity",
        "terms",
        "fill_conflict",
        "quantity",
        "new_fill_predates_order_or_post",
    ],
)
def test_terminal_resize_policy_refuses_other_defects(
    native_http: Any, case: str
) -> None:
    reported, owner, at, _ = partial(native_http)
    later = at + timedelta(seconds=2)
    terminal(native_http, reported, later)
    stop = reported.order["legs"][0]
    if case == "working":
        stop["status"] = "new"
    elif case == "identity":
        stop["id"] = "99999999-9999-4999-8999-999999999999"
    elif case == "terms":
        stop["client_order_id"] = "changed-stop-client"
    elif case == "fill_conflict":
        reported.fills[ROOT][0]["price"] = "11.04"
    elif case == "quantity":
        stop["qty"] = "0.25"
    else:
        reported.fills[ROOT][-1]["transaction_time"] = (
            native_http[2] - timedelta(microseconds=1)
        ).isoformat()
    result = opted(owner, capture(native_http, reported, later), later)
    assert result.native_exit_bindings[0]["status"] == "QUARANTINED_NATIVE_EXIT_CENSUS"
    assert result.model.exit_quantity == Fraction(1, 2)
    assert not result.native_exit_bindings[0]["risk_release_authorized"]


def test_terminal_resize_policy_requires_raw_prior_proof(native_http: Any) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.order["legs"][0]["qty"] = "0.5"
    seq = terminal(native_http, reported, at)
    result = opted(owner, seq, at)
    assert result.native_exit_bindings[0]["terminal_stop_proof"] is None
    assert result.native_exit_bindings[0]["status"] == "QUARANTINED_NATIVE_EXIT_CENSUS"


def test_terminal_positive_fills_remain_complete_oversell_incident(
    native_http: Any,
) -> None:
    reported, owner, at, _ = partial(native_http)
    later = at + timedelta(seconds=1)
    result = opted(owner, terminal(native_http, reported, later, stop="0.5"), later)
    receipt = result.native_exit_bindings[0]
    assert receipt["status"] == "BOUND_NATIVE_EXIT_CENSUS"
    assert receipt["issues"] == ["GROUP_FILLS_EXCEED_ROOT_REPORTED_QUANTITY"]
    assert result.model.exit_quantity == Fraction(3, 2)
    assert result.model.incidents and result.issues
    assert not receipt["risk_release_authorized"]


@pytest.mark.parametrize(
    "policy", [True, 1, "", "NATIVE_TERMINAL_STOP_PRIOR_RESIZE_V2"]
)
def test_terminal_policy_requires_exact_version(native_http: Any, policy: Any) -> None:
    reported, owner, at, _ = partial(native_http)
    later = at + timedelta(seconds=1)
    seq = terminal(native_http, reported, later)
    before = owner.journal.read_oco_research_events()
    with pytest.raises(JournalError, match="pinned native exit observation policy"):
        owner.bind_native_exit_census(
            "bad-policy",
            "group-a",
            seq,
            later,
            timedelta(seconds=30),
            later,
            observation_policy=policy,
        )
    assert owner.journal.read_oco_research_events() == before


def test_terminal_resize_cannot_clear_sticky_fill_conflict(native_http: Any) -> None:
    reported, owner, at, _ = partial(native_http)
    later = at + timedelta(seconds=1)
    terminal(native_http, reported, later)
    reported.fills[ROOT][0]["price"] = "11.04"
    conflict = opted(owner, capture(native_http, reported, later), later, "conflict")
    assert (
        "NATIVE_EXIT_FILL_REGRESSION_OR_CONFLICT"
        in conflict.native_exit_bindings[0]["issues"]
    )
    reported.fills[ROOT][0]["price"] = "11.03"
    later += timedelta(seconds=1)
    result = opted(
        owner, capture(native_http, reported, later), later, "after-conflict"
    )
    assert (
        "NATIVE_EXIT_PRIOR_CONFLICT_QUARANTINED"
        in result.native_exit_bindings[0]["issues"]
    )
    assert result.native_exit_bindings[0]["status"] == "QUARANTINED_NATIVE_EXIT_CENSUS"
    assert result.model.exit_quantity == Fraction(1, 2)
    assert owner.verify_rebuild()


def test_originally_stale_partial_cannot_become_resize_proof(native_http: Any) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.order["status"] = "partially_filled"
    reported.order["legs"][0]["qty"] = "0.5"
    seq = capture(native_http, reported, at)
    stale_at = at + timedelta(seconds=40)
    stale = bind(native_http, seq, stale_at, "stale-partial")
    assert stale.native_exit_bindings[0]["status"] == "QUARANTINED_NATIVE_EXIT_CENSUS"
    later = stale_at + timedelta(seconds=1)
    result = opted(owner, terminal(native_http, reported, later), later)
    assert result.native_exit_bindings[0]["terminal_stop_proof"] is None
    assert result.native_exit_bindings[0]["status"] == "QUARANTINED_NATIVE_EXIT_CENSUS"


def test_resize_proof_requires_retained_raw_capture(
    native_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    reported, owner, at, prior_seq = partial(native_http)
    later = at + timedelta(seconds=1)
    seq = terminal(native_http, reported, later)
    read_event = owner.journal.read_event
    monkeypatch.setattr(
        owner.journal,
        "read_event",
        lambda sequence: None if sequence == prior_seq else read_event(sequence),
    )
    with pytest.raises(JournalError, match="capture is missing"):
        opted(owner, seq, later)


def test_policy_change_reusing_event_id_preserves_original_receipt(
    native_http: Any,
) -> None:
    reported, owner, at, _ = partial(native_http)
    later = at + timedelta(seconds=1)
    seq = terminal(native_http, reported, later)
    legacy = bind(native_http, seq, later, "same-terminal-event")
    before = owner.journal.read_oco_research_events()
    conflict = opted(owner, seq, later, "same-terminal-event")
    assert conflict.conflict_keys
    assert conflict.model == legacy.model
    assert conflict.native_exit_bindings == legacy.native_exit_bindings
    assert owner.journal.read_oco_research_events()[: len(before)] == before
    recorded = owner.journal.read_oco_research_events()
    assert opted(owner, seq, later, "same-terminal-event") == conflict
    assert owner.journal.read_oco_research_events() == recorded
    assert owner.verify_rebuild()


def test_late_reported_fill_preserves_original_prefix(native_http: Any) -> None:
    reported, owner, at, prior_seq = partial(native_http)
    prior = owner.view().native_exit_bindings[0]
    history = owner.journal.read_oco_research_events()
    later = at + timedelta(seconds=1)
    terminal(native_http, reported, later)
    # Execution time is distinct from the time an activity becomes observable.
    # This fill happened after POST but appeared only after the old raw census.
    executed_at = native_http[2] + timedelta(microseconds=500)
    assert native_http[2] < executed_at < at
    reported.fills[ROOT][-1]["transaction_time"] = executed_at.isoformat()
    result = opted(owner, capture(native_http, reported, later), later)
    assert result.native_exit_bindings[0]["status"] == "BOUND_NATIVE_EXIT_CENSUS"
    assert result.model.exit_quantity == 1
    assert owner.journal.read_oco_research_events()[: len(history)] == history
    replayed_prior = derive_native_exit_census(
        owner.journal,
        owner.initial,
        group_id="group-a",
        capture_sequence=prior_seq,
        decision_at=at,
        max_age=timedelta(seconds=30),
        through_sequence=prior["source_horizon"],
    )
    assert replayed_prior == prior
    assert owner.verify_rebuild()
