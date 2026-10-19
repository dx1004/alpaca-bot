"""Actual entry reducer declarations bind to source-shaped non-dispatch risk."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner, EntryRiskPlan
from alpaca_ross_bot.capital_stage import CapitalStageOwner
from alpaca_ross_bot.entry_strategy import EntryDecision, EntryState, evaluate_entry
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.research_risk_binding import RESEARCH_BINDING
from alpaca_ross_bot.research_spec import RESEARCH_SHA256
from test_account_risk_owner import account_sources, view
from test_entry_strategy import HEALTH, OPEN, SESSION, TREND, prior, quote, trade
from test_risk_ledger_audit import POLICY, REF

AT = OPEN + timedelta(seconds=62)
CID = "declared-entry-1"


def decision() -> EntryDecision:
    trend = replace(TREND, symbol="AAPL")
    state = EntryState("AAPL", SESSION.day)
    result: EntryDecision | None = None
    for name, seconds, price in (
        ("first", 1, Fraction(21, 2)),
        ("arm", 61, Fraction(99, 10)),
        ("trigger", 62, Fraction(2001, 200)),
    ):
        tick = replace(trade(name, seconds, price), symbol="AAPL")
        result = evaluate_entry(
            state,
            trend,
            tick,
            replace(quote(tick), symbol="AAPL"),
            replace(prior(), symbol="AAPL"),
            HEALTH,
            tick.received_at,
        )
        state = result.state
    assert result is not None and result.signal is not None
    assert result.outcome == "RESEARCH_SIGNAL"
    return result


def test_reducer_signal_derives_prices_and_is_atomically_declared_not_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        original = decision()
        assert original.signal is not None
        selected = source.selection(AT)
        result = owner.reserve_research_decision(
            "first",
            selected,
            AT,
            client_order_id=CID,
            decision=original,
        )
        state = view(owner)
        assert result["quantity"] == 4 and result["reason"] == "FEASIBLE"
        assert result["binding_classification"] == RESEARCH_BINDING
        assert result["strategy_plan_qualified"] is False
        assert result["runtime_authorized"] is False
        assert state["held"]["limit"] == "10.01" and state["held"]["stop"] == "9.50"
        declared = state["held"]["research_signal"]
        assert declared["signal_id"] == original.signal.signal_id
        assert declared["research_sha256"] == RESEARCH_SHA256
        assert declared["classification"] == RESEARCH_BINDING
        assert declared["feature_refs"] == ["synthetic-feature"]
        saved = json.loads(source.journal.read_account_risk_events()[0][1])
        assert saved["research_signal"] == declared
        assert saved["plan"] == {
            "client_order_id": CID,
            "symbol": "AAPL",
            "limit": "10.01",
            "stop": "9.50",
        }
        assert source.journal.read_order_intent_ids() == ()
        assert original == decision() and not original.state.attempted
        assert original.state.pending_signal is not None
        assert owner.verify_rebuild()
        capital = CapitalStageOwner(source.journal, REF).assess(
            "after-declared-risk",
            account_sequence=selected.capture_sequence,
            calendar_sequence=selected.calendar_sequence,
            decision_at=AT,
            max_age=selected.max_age,
        )
        assert capital["account_ref"] == state["account_ref"]
        assert capital["versions"]["strategy_version"] == state["version"]
        assert capital["versions"]["risk_profile_version"] == state["version"]
        assert (
            capital["observation"]["higher_stage"]
            == "BLOCKED_PENDING_SEPARATE_ACCEPTANCE"
        )
        assert capital["runtime_authorized"] is False
        assert view(owner) == state
        repeated = owner.reserve_research_decision(
            "first",
            selected,
            AT,
            client_order_id=CID,
            decision=original,
        )
        assert (
            repeated == result and len(source.journal.read_account_risk_events()) == 1
        )
        assert view(AccountRiskOwner(source.journal, REF, POLICY)) == state


def test_low_is_floored_exactly_without_changing_declared_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        original = decision()
        assert original.signal is not None
        signal = replace(original.signal, low_since_open=Fraction(9509, 1000))
        changed = replace(
            original,
            signal=signal,
            state=replace(original.state, pending_signal=signal),
        )
        owner.reserve_research_decision(
            "floor",
            source.selection(AT),
            AT,
            client_order_id=CID,
            decision=changed,
        )
        assert view(owner)["held"]["stop"] == "9.50"
        assert view(owner)["held"]["limit"] == "10.01"
        assert source.journal.read_order_intent_ids() == ()


@pytest.mark.parametrize(
    "fault",
    [
        "no_signal",
        "wrong_symbol",
        "wrong_day",
        "wrong_version",
        "not_pending",
        "not_first",
        "gap_failed",
        "not_armed",
        "invalidated",
        "attempted",
        "veto",
        "cancel",
        "authority",
        "coverage",
        "stale",
        "future",
        "timeframe",
        "window",
    ],
)
def test_invalid_declared_state_or_timing_never_mutates_risk_or_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        original = decision()
        assert original.signal is not None
        candidate, at = original, AT
        if fault == "no_signal":
            candidate = replace(original, outcome="NO_SIGNAL", signal=None)
        elif fault in {"wrong_symbol", "wrong_day", "wrong_version"}:
            signal_changes: dict[str, dict[str, Any]] = {
                "wrong_symbol": {"symbol": "MSFT"},
                "wrong_day": {"session_day": SESSION.day - timedelta(days=1)},
                "wrong_version": {"version": "ROSS_SMALL_ACCOUNT_CANDIDATE_V1"},
            }
            signal = replace(original.signal, **signal_changes[fault])
            candidate = replace(
                original,
                signal=signal,
                state=replace(original.state, pending_signal=signal),
            )
        elif fault in {
            "not_pending",
            "not_first",
            "gap_failed",
            "not_armed",
            "invalidated",
            "attempted",
            "veto",
        }:
            state_changes: dict[str, dict[str, Any]] = {
                "not_pending": {"pending_signal": None},
                "not_first": {"first_seen": False},
                "gap_failed": {"gap_passed": False},
                "not_armed": {"armed": False},
                "invalidated": {"invalidated": True},
                "attempted": {"attempted": True},
                "veto": {"veto_latched": True},
            }
            candidate = replace(
                original, state=replace(original.state, **state_changes[fault])
            )
        elif fault == "cancel":
            candidate = replace(original, cancel_working_entry_required=True)
        elif fault == "authority":
            candidate = replace(original, order_authority=True)
        elif fault == "coverage":
            candidate = replace(original, coverage="SIP_EQUIVALENT")
        elif fault == "stale":
            at += timedelta(seconds=1, microseconds=1)
        elif fault == "future":
            at -= timedelta(microseconds=1)
        elif fault in {"timeframe", "window"}:
            at = OPEN + timedelta(minutes=15 if fault == "timeframe" else 60)
            candidate = replace(
                original,
                state=replace(
                    original.state,
                    last_trade_at=at,
                    last_decision_at=at,
                    timeframe=1 if fault == "timeframe" else 5,
                ),
            )
        selected = source.selection(at)
        with pytest.raises(JournalError):
            owner.reserve_research_decision(
                fault,
                selected,
                at,
                client_order_id=CID,
                decision=candidate,
            )
        assert owner.view() is None
        assert source.journal.read_account_risk_events() == ()
        assert source.journal.read_order_intent_ids() == ()
        assert original == decision() and not original.state.attempted


def test_second_declared_signal_cannot_overwrite_slot_or_bypass_explicit_abandonment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected, original = source.selection(AT), decision()
        assert (
            owner.reserve_research_decision(
                "first",
                selected,
                AT,
                client_order_id=CID,
                decision=original,
            )["quantity"]
            == 4
        )
        held = view(owner)["held"]
        second = owner.reserve_research_decision(
            "second",
            selected,
            AT,
            client_order_id="other",
            decision=original,
        )
        assert second["quantity"] == 0 and second["reason"] == "SLOT_OCCUPIED"
        assert view(owner)["held"] == held
        owner.abandon_unsent("abandon", selected, AT)
        old = owner.reserve_research_decision(
            "first",
            selected,
            AT,
            client_order_id=CID,
            decision=original,
        )
        assert old["quantity"] == 0 and old["runtime_authorized"] is False
        assert view(owner)["held"] is None
        assert source.journal.read_order_intent_ids() == ()
        assert owner.verify_rebuild()


def test_risk_source_veto_cannot_be_overridden_by_valid_research_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch, freeze=False) as (source, owner):
        result = owner.reserve_research_decision(
            "no-e0",
            source.selection(AT),
            AT,
            client_order_id=CID,
            decision=decision(),
        )
        assert result["quantity"] == 0 and result["reason"] == "SOURCE_INPUT_VETO"
        assert result["strategy_plan_qualified"] is False
        assert result["binding_classification"] == RESEARCH_BINDING
        assert view(owner)["held"] is None
        assert owner.verify_rebuild()


def test_old_generic_retry_cannot_return_new_slot_positive_quantity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected = source.selection(AT)
        old_plan = EntryRiskPlan("old", "AAPL", "10.01", "9.50")
        new_plan = EntryRiskPlan("new", "AAPL", "10.01", "9.50")
        assert owner.reserve("old", selected, AT, old_plan)["quantity"] == 4
        owner.abandon_unsent("release-old", selected, AT)
        assert owner.reserve("new", selected, AT, new_plan)["quantity"] == 4
        state = view(owner)
        count = len(source.journal.read_account_risk_events())
        retried = owner.reserve("old", selected, AT, old_plan)
        assert (
            retried["quantity"] == 0 and retried["reason"] == "RESERVATION_NOT_CURRENT"
        )
        assert view(owner) == state and state["held"]["client_order_id"] == "new"
        assert len(source.journal.read_account_risk_events()) == count
        assert owner.verify_rebuild()


def test_binding_and_risk_slot_roll_back_as_one_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected = source.selection(AT)
        original_append = Journal._append_locked

        def fail(
            self: Journal,
            event: EventEnvelope,
            payload: bytes,
            updates: Mapping[str, object] | None,
        ) -> None:
            original_append(self, event, payload, updates)
            if event.source_id == "ALPACA_ACCOUNT_RISK":
                raise OSError("synthetic binding commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(Journal, "_append_locked", fail)
            with pytest.raises(OSError, match="binding commit"):
                owner.reserve_research_decision(
                    "first",
                    selected,
                    AT,
                    client_order_id=CID,
                    decision=decision(),
                )
        assert owner.view() is None and owner.verify_rebuild()
        assert source.journal.read_account_risk_events() == ()
        assert source.journal.read_order_intent_ids() == ()
        assert (
            owner.reserve_research_decision(
                "first",
                selected,
                AT,
                client_order_id=CID,
                decision=decision(),
            )["quantity"]
            == 4
        )
