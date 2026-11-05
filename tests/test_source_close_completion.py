"""Explicit accepted as-of completion, with frozen source facts and later vetoes."""

import copy
import json
from datetime import timedelta
from fractions import Fraction
from typing import Any

import pytest

from alpaca_ross_bot import source_close_completion
from alpaca_ross_bot._causal_risk_memo import causal_risk_verification_scope
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner, EntryRiskPlan
from alpaca_ross_bot.crossed_stop_attribution import SourceHttpCloseAttributions
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.source_close_completion import completion_identity
from test_source_account_risk_owner import account_book, report
from test_source_account_risk_owner import http_capture as http_capture_fixture
from test_source_account_risk_owner import source_http as source_http_fixture
from test_source_close_http_capture import capture

source_http = source_http_fixture
http_capture = http_capture_fixture


def fully_reported(case: Any) -> tuple[Any, Any, Any, Any, Any, list[dict[str, Any]]]:
    fixture = case[3]
    source, risk, owner, emergency, at, _, _ = fixture[6]
    packet, rows = report(case, "2", terminal=True)
    SourceHttpCloseAttributions(source.journal, owner.initial).observe(
        "complete-source-index",
        at,
        client_order_id=case[2].order["client_order_id"],
        capture_sequences=(packet["capture_ref"]["sequence"],),
    )
    selection = account_book(case, at, cash="498.98", position="0", source_fills=rows)
    risk.observe_source("complete-source-account", selection, at)
    assert risk.view()["last_snapshot"]["qualified"]
    return source, risk, owner, emergency, selection, rows


def test_explicit_completion_once_closed_history_next_trade_and_late_conflict(
    http_capture: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    case = http_capture
    source, risk, owner, emergency, selection, rows = fully_reported(case)
    at = case[3][7][0]
    held = risk.view()
    original_reserve = held["pending_open_loss_reserve"]
    assert Fraction(original_reserve) > 0
    before = tuple(source.journal._db.iterdump())
    # A premature request is atomic and does not poison the canonical consumer.
    with pytest.raises(JournalError, match="consumption"):
        risk.complete_source_close("premature", selection, at)
    assert tuple(source.journal._db.iterdump()) == before
    receipt = emergency.consume_source_fills("whole-consumption", "residual", at)
    emergency_before = emergency.view()
    before_completion = source.journal.read_evidence_sequence()
    calls = list(case[2].calls)

    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("completion entered a current risk constructor")

    with monkeypatch.context() as context:
        context.setattr(AccountRiskOwner, "__init__", deny)
        risk.complete_source_close("settle-source", selection, at)
    completed = risk.view()
    scope = owner.initial.entry_scope
    row = completed["completed"][scope]
    event, payload = source.journal.read_event(row["completion_ref"][0])
    item = json.loads(payload)
    assert event.event_kind == "COMPLETE_SOURCE"
    assert event.payload_ref == row["completion_ref"][1]
    assert item["source_horizon"] == event.sequence - 1
    assert item["completion_contract"] == completion_identity()
    assert row["source_context"]["closed"]
    assert scope in row["source_completion"]["ordinary_basis"]["orders"]
    assert row["source_completion"]["ordinary_basis"]["activities"]
    assert row["source_context"]["completion_ref"] == row["completion_ref"]
    assert (
        row["source_completion"]["claims"][receipt["client_order_id"]][
            "attribution_ref"
        ]
        == receipt["attribution_ref"]
    )
    assert completed["held"] is None
    assert (
        completed["pending_open_loss_reserve"]
        == completed["additional_local_cash_hold"]
        == "0"
    )
    assert completed["losing_streak"] == 1 and row["net"] == "-26/25"
    assert completed["capacity"] != "0" and not completed["issues"]
    assert not any(item["completion_contract"]["authority"].values())
    assert emergency.view() == emergency_before and case[2].calls == calls
    assert emergency_before["latched"]
    # Late exact retries retain the original event and never update the streak twice.
    tail = source.journal.read_evidence_sequence()
    risk.complete_source_close("settle-source", selection, at + timedelta(seconds=1))
    assert source.journal.read_evidence_sequence() == tail and risk.view() == completed
    past, _ = AccountRiskOwner._read_native_causal_evidence_at(
        source.journal,
        risk.account_ref,
        at,
        risk.fee_policy,
        through_sequence=before_completion,
    )
    assert past is not None
    assert (
        past["held"] is not None
        and past["pending_open_loss_reserve"] == original_reserve
    )
    assert risk.verify_rebuild()
    # Contract mutation is checked even after the exact causal prefix is memoized.
    bad_manifest = tmp_path / "altered-completion-contract.json"
    bad_manifest.write_bytes(source_close_completion._MANIFEST.read_bytes() + b" ")
    source.journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            AccountRiskOwner._read_native_causal_evidence_at(
                source.journal,
                risk.account_ref,
                at,
                risk.fee_policy,
                through_sequence=tail,
            )
            with monkeypatch.context() as context:
                context.setattr(source_close_completion, "_MANIFEST", bad_manifest)
                with pytest.raises(JournalError, match="pinned accepted"):
                    risk.verify_rebuild()
                with pytest.raises(JournalError, match="pinned accepted"):
                    AccountRiskOwner._read_native_causal_evidence_at(
                        source.journal,
                        risk.account_ref,
                        at,
                        risk.fee_policy,
                        through_sequence=tail,
                    )
    finally:
        source.journal._db.rollback()
    # The original GET may age. A new full account book must still corroborate it.
    later = at + timedelta(seconds=31)
    case[3][7][0] = later
    fresh = account_book(case, later, cash="498.98", position="0", source_fills=rows)
    risk.observe("closed-source-fresh-account", fresh, later)
    assert risk.view()["last_snapshot"]["qualified"] and not risk.view()["issues"]
    plan = EntryRiskPlan("next-ordinary-source-account", "AAPL", "10", "9.50")
    decision = risk.reserve("next-ordinary-plan", fresh, later, plan)
    quantity = decision["quantity"]
    assert type(quantity) is int and quantity > 0
    with pytest.raises(JournalError, match="source emergency reservation"):
        source.intent(plan.client_order_id, later, qty=quantity)
    # Accounting completion grants no new order authority. The provisional
    # ordinary plan still follows ordinary risk synchronization on a flat book.
    next_selection = account_book(
        case,
        later,
        cash="498.98",
        position="0",
        source_fills=rows,
        native_open=[],
    )
    risk.observe("ordinary-open-account", next_selection, later)
    current = risk.view()
    assert current["last_snapshot"]["qualified"]
    assert current["held"]["client_order_id"] == plan.client_order_id
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" not in current["issues"]
    assert "SOURCE_ENTRY_OWNERSHIP_UNAVAILABLE" not in current["issues"]
    # Restoring matching cash cannot silently clear a postcompletion incident.
    wrong_cash = account_book(
        case,
        later,
        cash="497.98",
        position="0",
        source_fills=rows,
        native_open=[],
    )
    risk.observe("postcompletion-cash-disagreement", wrong_cash, later)
    disagreed = risk.view()
    assert not disagreed["last_snapshot"]["qualified"]
    incident = copy.deepcopy(disagreed["source_completion_incident"])
    restored = account_book(
        case,
        later,
        cash="498.98",
        position="0",
        source_fills=rows,
        native_open=[],
    )
    risk.observe("postcompletion-cash-restored", restored, later)
    restored_state = risk.view()
    assert restored_state["last_snapshot"]["qualified"]
    assert restored_state["source_completion_incident"] == incident
    assert restored_state["capacity"] == "0"
    assert "SOURCE_COMPLETION_RECOVERY_REQUIRED" in restored_state["issues"]
    blocked = risk.reserve(
        "no-unverified-resumption",
        restored,
        later,
        EntryRiskPlan("another-ordinary-plan", "AAPL", "10", "9.50"),
    )
    assert blocked["quantity"] == 0
    assert risk.view()["completed"][scope] == row
    # Matching corrected cash cannot legitimize an original entry price change.
    source.fills[0]["price"] = "9.50"
    corrected_entry = account_book(
        case,
        later,
        cash="499.98",
        position="0",
        source_fills=rows,
        native_open=[],
    )
    risk.observe("original-entry-price-correction", corrected_entry, later)
    assert not risk.view()["last_snapshot"]["qualified"]
    assert risk.view()["completed"][scope] == row
    source.fills[0]["price"] = "10"
    # An execution correction freezes the next slot and preserves settlement.
    case[2].pages = [json.dumps([rows[0] | {"price": "9.48"}]).encode(), b"[]"]
    capture(case)
    bad = account_book(
        case,
        later,
        cash="498.98",
        position="0",
        source_fills=rows,
        native_open=[],
    )
    risk.observe("late-source-correction", bad, later)
    frozen = risk.view()
    assert not frozen["last_snapshot"]["qualified"] and frozen["capacity"] == "0"
    assert frozen["held"]["client_order_id"] == plan.client_order_id
    assert frozen["completed"][scope] == row and frozen["losing_streak"] == 1


def test_completion_rejects_fraction_incident_and_changed_identity_latches(
    http_capture: Any,
) -> None:
    case = http_capture
    fixture = case[3]
    source, risk, owner, emergency, at, _, _ = fixture[6]
    packet, rows = report(case, "0.5")
    index = SourceHttpCloseAttributions(source.journal, owner.initial)
    index.observe(
        "fractional",
        at,
        client_order_id=case[2].order["client_order_id"],
        capture_sequences=(packet["capture_ref"]["sequence"],),
    )
    partial = account_book(case, at, cash="484.745", position="1.5", source_fills=rows)
    risk.observe_source("fractional-source-account", partial, at)
    emergency.consume_source_fills("fractional-consumption", "residual", at)
    original = risk.view()
    assert "quantity_incident" in original["held"]
    before = tuple(source.journal._db.iterdump())
    with pytest.raises(JournalError, match="whole source slot"):
        risk.complete_source_close("fractional-not-complete", partial, at)
    assert tuple(source.journal._db.iterdump()) == before
    # Even full later fills cannot clear the earlier unsupported sizing incident.
    later = at + timedelta(seconds=1)
    fixture[7][0] = later
    case[2].order.update(status="filled", filled_qty="2", updated_at=later.isoformat())
    terminal_rows = rows + [
        rows[0]
        | {
            "id": "completion-final-fill",
            "qty": "1.5",
            "cum_qty": "2",
            "leaves_qty": "0",
            "transaction_time": later.isoformat(),
        }
    ]
    case[2].pages = [json.dumps(terminal_rows).encode(), b"[]"]
    terminal = capture(case)
    index.observe(
        "terminal-after-fraction",
        later,
        client_order_id=case[2].order["client_order_id"],
        capture_sequences=(
            packet["capture_ref"]["sequence"],
            terminal["capture_ref"]["sequence"],
        ),
    )
    selection = account_book(
        case, later, cash="498.98", position="0", source_fills=terminal_rows
    )
    risk.observe("terminal-after-fraction-account", selection, later)
    emergency.consume_source_fills(
        "terminal-after-fraction-consumption", "residual", later
    )
    before = tuple(source.journal._db.iterdump())
    with pytest.raises(JournalError, match="whole source slot"):
        risk.complete_source_close("still-fraction-incident", selection, later)
    assert tuple(source.journal._db.iterdump()) == before
    assert (
        risk.view()["held"]["quantity_incident"]
        == original["held"]["quantity_incident"]
    )


def test_completion_retry_conflict_and_forged_horizon_are_vetoed(
    http_capture: Any,
) -> None:
    case = http_capture
    source, risk, owner, emergency, selection, _ = fully_reported(case)
    at = case[3][7][0]
    emergency.consume_source_fills("consume-before-conflict", "residual", at)
    prior = copy.deepcopy(risk.view())
    risk.complete_source_close("one-completion-key", selection, at)
    original = copy.deepcopy(risk.view())
    event, payload = source.journal.read_event(
        original["completed"][owner.initial.entry_scope]["completion_ref"][0]
    )
    item = json.loads(payload)
    item["source_horizon"] -= 1
    with pytest.raises(JournalError, match="immediately preceding"):
        risk._reduce(prior, item, False, event.sequence)
    changed = copy.copy(selection)
    object.__setattr__(changed, "max_age", selection.max_age + timedelta(seconds=1))
    risk.complete_source_close("one-completion-key", changed, at)
    conflicted = risk.view()
    assert conflicted["veto"] and conflicted["issues"] == ["EVENT_ID_CONFLICT"]
    assert conflicted["completed"] == original["completed"]
    assert conflicted["losing_streak"] == original["losing_streak"] == 1
    tail = source.journal.read_evidence_sequence()
    risk.complete_source_close("one-completion-key", changed, at + timedelta(seconds=1))
    assert source.journal.read_evidence_sequence() == tail
    assert risk.verify_rebuild()


def test_provider_position_shape_errors_are_normalized_at_registry_seam() -> None:
    from alpaca_ross_bot.source_risk_registry import _observed_positions

    malformed: tuple[list[Any], ...] = (
        [{}],
        [None],
        [{"symbol": "AAPL", "qty": "unknown"}],
    )
    for rows in malformed:
        with pytest.raises(JournalError, match="raw position shape"):
            _observed_positions(rows)
    assert _observed_positions([{"symbol": "AAPL", "qty": "0.5"}]) == {
        "AAPL": Fraction(1, 2)
    }
