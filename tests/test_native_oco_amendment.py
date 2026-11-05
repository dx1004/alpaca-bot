"""Final-target semantic preparation never sends or changes group protection."""

import copy
from datetime import timedelta
from fractions import Fraction
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_amendment import derive_final_target_preparation
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal, _causal_oco_reader
from test_native_account_risk_owner import full_book
from test_native_account_risk_owner import native_risk_book as native_risk_book_fixture
from test_observation_candidate_binding import CID
from test_oco_exit_evidence import capture

native_risk_book = native_risk_book_fixture
AGE = timedelta(seconds=30)


def qualify(book: Any) -> Any:
    source, risk, owner, fixture, reported = book
    at = fixture[2]
    owner.bind_native_exit_census(
        "amendment-native-source",
        "group-a",
        capture(fixture, reported, at),
        at,
        AGE,
        at,
    )
    selection = full_book(source, reported, at, cash="480", position=2)
    risk.observe_native("amendment-native-account", selection, at)
    assert risk.view()["last_snapshot"]["qualified"]
    return at


def test_source_final_target_semantics_retry_reopen_rollback_and_conflict(
    native_risk_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = native_risk_book
    source, risk, owner, fixture, reported = book
    at = fixture[2]
    with pytest.raises(JournalError):
        owner.prepare_final_target("no-owned-group", "group-a", at, AGE, at)
    at = qualify(book)
    before = owner.view()
    prior_state = copy.deepcopy(source.journal.read_state(owner.state_key))
    risk_before = copy.deepcopy(risk.view())
    intent_ids = source.journal.read_order_intent_ids()
    raw_orders = copy.deepcopy(reported.order)
    result = owner.prepare_final_target("final-a", "group-a", at, AGE, at)
    operation = result.final_target_preparations[0]
    receipt = operation["receipt"]
    assert receipt["classification"] == "SEMANTIC_FINAL_TARGET_PROPOSAL_NOT_PATCH_BYTES"
    assert Fraction(receipt["provisional_target"]) == Fraction("11.03")
    assert Fraction(receipt["final_target"]) == 11
    assert Fraction(receipt["average_entry"]) == 10
    assert receipt["residual_quantity"] == "2"
    assert receipt["frozen_stop"] == "19/2"
    assert receipt["continuity"] == "UNKNOWN" and not any(receipt["authority"].values())
    assert "request_body" not in receipt and "patch_body" not in receipt
    assert result.model == before.model
    assert result.native_exit_bindings == before.native_exit_bindings
    assert risk.view() == risk_before
    assert source.journal.read_order_intent_ids() == intent_ids
    assert reported.order == raw_orders
    event = source.journal.read_oco_research_events()[-1][0]
    assert operation["source_horizon"] == event.sequence - 1
    tail = source.journal.read_evidence_sequence()
    assert (
        owner.prepare_final_target(
            "final-a", "group-a", at, AGE, at + timedelta(seconds=60)
        )
        == result
    )
    assert source.journal.read_evidence_sequence() == tail
    # The existing accounting registry must replay the new semantic-only event.
    refreshed = full_book(source, reported, at, cash="480", position=2)
    risk.observe("account-after-semantic-proposal", refreshed, at)
    assert risk.view()["last_snapshot"]["qualified"]
    assert (
        risk.view()["pending_open_loss_reserve"]
        == risk_before["pending_open_loss_reserve"]
    )
    assert (
        risk.view()["additional_local_cash_hold"]
        == risk_before["additional_local_cash_hold"]
    )
    assert owner.view() == result
    tail = source.journal.read_evidence_sequence()
    original_append = source.journal._append_locked

    def interrupted(event: Any, payload: bytes, updates: Any = None) -> Any:
        value = original_append(event, payload, updates)
        if event.event_kind == "PREPARE_FINAL_TARGET":
            raise JournalError("synthetic amendment storage failure")
        return value

    before_failure = tuple(source.journal._db.iterdump())
    with monkeypatch.context() as context:
        context.setattr(source.journal, "_append_locked", interrupted)
        with pytest.raises(JournalError, match="storage failure"):
            owner.prepare_final_target("rolled-back", "group-a", at, AGE, at)
    assert tuple(source.journal._db.iterdump()) == before_failure
    assert owner.view() == result
    shrunk: dict[str, Any] = {
        "kind": "PREPARE_FINAL_TARGET",
        "operation": dict(operation, source_horizon=operation["source_horizon"] - 1),
        "dedupe_key": "unused",
    }
    with pytest.raises(JournalError, match="source proof differs"):
        owner._reduce(prior_state, shrunk, False, event.sequence)
    altered = copy.deepcopy(shrunk)
    altered["operation"] = copy.deepcopy(operation)
    altered["operation"]["receipt"]["authority"]["patch_bytes_validated"] = 0
    with pytest.raises(JournalError, match="source proof differs"):
        owner._reduce(prior_state, altered, False, event.sequence)
    reopened = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:

        def deny(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("current constructor entered amendment replay")

        with monkeypatch.context() as context:
            context.setattr(AccountRiskOwner, "__init__", deny)
            context.setattr(OcoJournal, "__init__", deny)
            context.setattr(NativeOcoRequests, "__init__", deny)
            assert (
                _causal_oco_reader(reopened, owner.initial).read_verified_at(tail, at)
                == result
            )
    finally:
        reopened.close()
    conflicted = owner.prepare_final_target("final-a", "another-group", at, AGE, at)
    assert conflicted.conflict_keys and conflicted.model == before.model
    assert conflicted.final_target_preparations == result.final_target_preparations
    tail = source.journal.read_evidence_sequence()
    assert (
        owner.prepare_final_target("final-a", "another-group", at, AGE, at)
        == conflicted
    )
    assert source.journal.read_evidence_sequence() == tail
    assert owner.verify_rebuild()


def test_latest_account_native_cancel_and_late_entry_evidence_veto(
    native_risk_book: Any,
) -> None:
    book = native_risk_book
    source, risk, owner, fixture, reported = book
    at = qualify(book)

    def derive(clock: Any = at) -> dict[str, Any]:
        return derive_final_target_preparation(
            source.journal,
            owner.initial,
            group_id="group-a",
            decision_at=clock,
            max_age=AGE,
            through_sequence=source.journal.read_evidence_sequence(),
        )

    with pytest.raises(JournalError, match="stale|future"):
        derive(at + timedelta(seconds=31))
    selection = full_book(source, reported, at, cash="480", position=2)
    with pytest.raises(JournalError, match="newest account"):
        derive()
    risk.observe("newest-amendment-account", selection, at)
    newest = capture(fixture, reported, at)
    with pytest.raises(JournalError, match="newest native"):
        derive()
    owner.bind_native_exit_census("newest-native", "group-a", newest, at, AGE, at)
    assert derive()["residual_quantity"] == "2"
    partial_at = at + timedelta(seconds=1)
    reported.order.update(
        status="partially_filled", filled_qty="1", updated_at=partial_at.isoformat()
    )
    reported.order["legs"][0].update(qty="1", updated_at=partial_at.isoformat())
    reported.fills[reported.order["id"]] = [
        {
            "id": "amendment-native-target-fill",
            "activity_type": "FILL",
            "order_id": reported.order["id"],
            "symbol": "AAPL",
            "side": "sell",
            "qty": "1",
            "price": "11.03",
            "transaction_time": partial_at.isoformat(),
        }
    ]
    owner.bind_native_exit_census(
        "partial-target",
        "group-a",
        capture(fixture, reported, partial_at),
        partial_at,
        AGE,
        partial_at,
    )
    selection = full_book(source, reported, partial_at, cash="491.03", position=1)
    risk.observe("partial-target-account", selection, partial_at)
    partial = derive(partial_at)
    assert partial["residual_quantity"] == "1"
    assert Fraction(partial["average_entry"]) == 10
    assert Fraction(partial["final_target"]) == 11
    later = partial_at + timedelta(seconds=1)
    reported.order.update(status="pending_cancel", updated_at=later.isoformat())
    reported.order["legs"][0].update(updated_at=later.isoformat())
    owner.bind_native_exit_census(
        "pending-cancel",
        "group-a",
        capture(fixture, reported, later),
        later,
        AGE,
        later,
    )
    selection = full_book(source, reported, later, cash="491.03", position=1)
    risk.observe("pending-cancel-account", selection, later)
    assert risk.view()["last_snapshot"]["qualified"]
    with pytest.raises(JournalError, match="working original"):
        derive(later)
    # A terminal entry can report another execution; old T cannot hide that census.
    raced = later + timedelta(seconds=1)
    source.order(CID, raced, "canceled", 3)
    source.fill(CID, raced, 1, "10")
    selection = full_book(source, reported, raced, cash="481.03", position=2)
    risk.observe("late-entry-account", selection, raced)
    assert risk.view()["last_snapshot"]["qualified"]
    with pytest.raises(JournalError, match="entry census"):
        derive(raced)
    reported.order["replaced_by"] = "88888888-8888-4888-8888-888888888888"
    owner.bind_native_exit_census(
        "replacement", "group-a", capture(fixture, reported, raced), raced, AGE, raced
    )
    with pytest.raises(JournalError, match="clean source-bound"):
        derive(raced)
