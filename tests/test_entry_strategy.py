from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction as F
from typing import Any, cast

import pytest

from alpaca_ross_bot.adapted_scanner import ScreenResult
from alpaca_ross_bot.entry_strategy import (
    EntryDecision,
    EntryState,
    InputHealth,
    Quote,
    Trade,
    consume_attempt,
    evaluate_entry,
    plan_monitors,
)
from alpaca_ross_bot.scanner_contract import ACCEPTED_SHA256, RULE_IDS
from alpaca_ross_bot.strategy_features import Bar, Session, Slot, Trend

OPEN = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
SESSION = Session(
    date(2026, 10, 16),
    OPEN,
    OPEN + timedelta(hours=6, minutes=30),
    OPEN - timedelta(days=1),
    "synthetic-calendar",
)
TREND = Trend("ABC", SESSION, F(9), F(8), F(7), F(10), F(9), ("synthetic-feature",))
HEALTH = InputHealth(
    "synthetic-raw-contract",
    "synthetic-conditions",
    "first",
    True,
    True,
    True,
    True,
    False,
    False,
    False,
    F(19, 2),
)


def trade(event_id: str, seconds: int, price: F) -> Trade:
    at = OPEN + timedelta(seconds=seconds)
    return Trade(event_id, "ABC", at, at, price, "synthetic-conditions", True)


def quote(item: Trade, ask: F = F(1001, 100)) -> Quote:
    return Quote(
        "ABC",
        item.source_at,
        item.received_at,
        F(99, 10),
        ask,
        10,
        10,
        "synthetic-conditions",
        True,
    )


def prior(minutes: int = 1, start_minutes: int = 0, high: F = F(10)) -> Bar:
    slot = Slot(
        OPEN + timedelta(minutes=start_minutes),
        OPEN + timedelta(minutes=start_minutes + minutes),
    )
    return Bar(
        "ABC",
        slot,
        F(99, 10),
        high,
        F(98, 10),
        F(99, 10),
        1,
        slot.end,
        0,
        f"synthetic-prior-{start_minutes}",
    )


def evaluate(state: EntryState, item: Trade, bar: Bar | None = None) -> EntryDecision:
    return evaluate_entry(
        state, TREND, item, quote(item), bar, HEALTH, item.received_at
    )


def armed() -> EntryState:
    state = EntryState("ABC", SESSION.day)
    first = evaluate(state, trade("first", 1, F(21, 2)))
    assert first.reasons == ("P05:GAP_PASSED",)
    result = evaluate(first.state, trade("arm", 61, F(99, 10)))
    assert result.state.armed
    return result.state


def test_new_strict_cross_emits_signal_without_consuming_attempt() -> None:
    item = trade("trigger", 62, F(2001, 200))
    result = evaluate(armed(), item, prior())
    assert result.outcome == "RESEARCH_SIGNAL"
    assert result.signal is not None and result.signal.limit_price == F(1001, 100)
    assert not result.state.attempted and not result.order_authority
    assert result.coverage == "NOT_SIP_EQUIVALENT"
    reserved = consume_attempt(
        result.state, result.signal.signal_id, "journal:intent:123"
    )
    assert reserved.attempted and reserved.pending_signal is None
    assert reserved.durable_intent_ref == "journal:intent:123"
    with pytest.raises(ValueError):
        consume_attempt(reserved, result.signal.signal_id, "journal:intent:123")
    with pytest.raises(ValueError):
        consume_attempt(result.state, result.signal.signal_id, "")
    with pytest.raises(ValueError):
        consume_attempt(result.state, "other-signal", "journal:intent:123")


@pytest.mark.parametrize("value", [F(10), F(999, 100)])
def test_touching_or_below_prior_high_is_not_cross(value: F) -> None:
    assert evaluate(armed(), trade("not-cross", 62, value), prior()).signal is None


def test_arm_trade_can_supply_cross_baseline_without_extra_observation() -> None:
    assert (
        evaluate(armed(), trade("next", 62, F(1001, 100)), prior()).signal is not None
    )


@pytest.mark.parametrize("ask", [F(10011, 1000), F(11)])
def test_no_ask_chase(ask: F) -> None:
    item = trade("trigger", 62, F(2001, 200))
    result = evaluate_entry(
        armed(), TREND, item, quote(item, ask), prior(), HEALTH, item.received_at
    )
    assert result.reasons == ("P08:NO_CHASE",)
    assert not result.state.attempted


def test_no_trade_chase_and_strict_nextcent_even_cent_aligned_high() -> None:
    assert evaluate(armed(), trade("chase", 62, F(10011, 1000)), prior()).signal is None
    result = evaluate(armed(), trade("trigger", 62, F(1001, 100)), prior())
    assert result.signal is not None and result.signal.limit_price == F(1001, 100)
    item = trade("trigger2", 62, F(9999, 1000))
    fractional = evaluate_entry(
        armed(),
        TREND,
        item,
        quote(item, F(10)),
        prior(high=F(9991, 1000)),
        HEALTH,
        item.received_at,
    )
    assert fractional.signal is not None and fractional.signal.limit_price == 10


def test_session_invalidation_sticky_and_cancels_even_after_reserved_attempt() -> None:
    result = evaluate(armed(), trade("trigger", 62, F(1001, 100)), prior())
    assert result.signal is not None
    reserved = consume_attempt(result.state, result.signal.signal_id, "intent")
    item = trade("break", 63, F(899, 100))
    result = evaluate_entry(
        reserved,
        TREND,
        item,
        quote(item),
        prior(),
        replace(HEALTH, low_since_open=F(899, 100)),
        item.received_at,
    )
    assert result.state.invalidated and result.cancel_working_entry_required
    result2 = evaluate(result.state, trade("recover", 64, F(1001, 100)), prior())
    assert result2.signal is None and result2.state.invalidated


def test_inclusive_pullback_zone_endpoints() -> None:
    initial = evaluate(EntryState("ABC", SESSION.day), trade("first", 1, F(11))).state
    for price in (F(9), F(10)):
        item = trade(f"zone-{price}", 61, price)
        result = evaluate_entry(
            initial,
            TREND,
            item,
            quote(item),
            prior(),
            replace(HEALTH, low_since_open=F(9)),
            item.received_at,
        )
        assert result.state.armed and not result.state.invalidated


def test_first_trade_gap_failure_cannot_be_repaired_by_later_gap() -> None:
    state = evaluate(EntryState("ABC", SESSION.day), trade("first", 1, F(10))).state
    result = evaluate(state, trade("late", 61, F(11)), prior())
    assert not result.state.gap_passed and result.signal is None


@pytest.mark.parametrize(
    "missing",
    [
        "first",
        "monitor",
        "continuity",
        "condition",
        "subscription",
        "source",
        "gap",
        "incident",
        "ownership",
        "low",
    ],
)
def test_missing_input_blocks_and_never_self_grants_runtime(missing: str) -> None:
    values: dict[str, dict[str, Any]] = {
        "first": {"first_trade_id": "missing-first"},
        "monitor": {"monitored": False},
        "continuity": {"continuity_from_open": False},
        "condition": {"condition_policy_ref": "unknown"},
        "subscription": {"subscription_acknowledged": False},
        "source": {"source_contract_ref": ""},
        "gap": {"unresolved_gap": True},
        "incident": {"incident_veto": True},
        "ownership": {"uncertain_ownership": True},
        "low": {"low_since_open": None},
    }
    item = trade("first", 1, F(11))
    health = replace(HEALTH, **values[missing])
    result = evaluate_entry(
        EntryState("ABC", SESSION.day),
        TREND,
        item,
        quote(item),
        None,
        health,
        item.received_at,
    )
    assert result.outcome == "UNAVAILABLE" and result.signal is None
    assert not result.order_authority and result.cancel_working_entry_required


@pytest.mark.parametrize("milliseconds", [-1, 1001])
def test_negative_or_over1000ms_input_age_blocks(milliseconds: int) -> None:
    item = trade("trigger", 62, F(1001, 100))
    decision_at = item.source_at + timedelta(milliseconds=milliseconds)
    assert (
        evaluate_entry(
            armed(), TREND, item, quote(item), prior(), HEALTH, decision_at
        ).signal
        is None
    )


def test_exact1000ms_inclusive_source_and_receipt_age() -> None:
    item = trade("trigger", 62, F(1001, 100))
    assert (
        evaluate_entry(
            armed(),
            TREND,
            item,
            quote(item),
            prior(),
            HEALTH,
            item.source_at + timedelta(milliseconds=1000),
        ).signal
        is not None
    )


@pytest.mark.parametrize(
    "change", ["crossed", "zero-size", "future-receipt", "unknown-condition"]
)
def test_invalid_quote_unavailable(change: str) -> None:
    item = trade("trigger", 62, F(1001, 100))
    q = quote(item)
    if change == "crossed":
        q = replace(q, bid=F(11))
    elif change == "zero-size":
        q = replace(q, bid_size=0)
    elif change == "future-receipt":
        q = replace(q, received_at=item.source_at + timedelta(milliseconds=1))
    else:
        q = replace(q, conditions_eligible=False)
    assert (
        evaluate_entry(
            armed(), TREND, item, q, prior(), HEALTH, item.received_at
        ).outcome
        == "UNAVAILABLE"
    )


def test_switch_discards_1m_baseline_and_uses_completed5m_bar() -> None:
    state = armed()
    first5 = trade("switch", 900, F(1001, 100))
    result = evaluate(state, first5, prior(5, 10))
    assert result.signal is None and result.state.timeframe == 5
    below = evaluate(result.state, trade("new-baseline", 901, F(10)), prior(5, 10))
    crossed = evaluate(below.state, trade("5m-cross", 902, F(1001, 100)), prior(5, 10))
    assert crossed.signal is not None
    # Arrival straddling15m is decided using the new clock, not replayed as1m.
    old = trade("straddle", 899, F(1001, 100))
    at = OPEN + timedelta(minutes=15)
    result = evaluate_entry(state, TREND, old, quote(old), prior(1, 13), HEALTH, at)
    assert result.signal is None and result.state.timeframe == 5


@pytest.mark.parametrize("seconds", [3600, 3601])
def test_window_end_is_exclusive_and_cancels(seconds: int) -> None:
    result = evaluate(armed(), trade("expired", seconds, F(1001, 100)), prior())
    assert result.reasons == ("P06/P07:ENTRY_WINDOW_EXPIRED",)
    assert result.cancel_working_entry_required


def test_calendar_close_clips_entry_window() -> None:
    short = replace(SESSION, close_at=OPEN + timedelta(minutes=20))
    trend = replace(TREND, session=short)
    item = trade("early-close", 1200, F(1001, 100))
    assert evaluate_entry(
        EntryState("ABC", short.day),
        trend,
        item,
        quote(item),
        None,
        HEALTH,
        item.received_at,
    ).cancel_working_entry_required


def test_missing_or_future_or_wrong_priorbar_cannot_signal() -> None:
    item = trade("trigger", 62, F(1001, 100))
    for value in (
        None,
        prior(5),
        replace(prior(), symbol="OTHER"),
        replace(prior(), available_at=item.source_at + timedelta(seconds=1)),
    ):
        assert evaluate(armed(), item, value).signal is None


def test_stale_duplicate_correction_and_changed_identity_do_not_retro_signal() -> None:
    state = armed()
    duplicate = state.seen[-1]
    assert evaluate(state, duplicate, prior()).signal is None
    changed = replace(duplicate, price=F(1001, 100))
    assert evaluate(state, changed, prior()).state.veto_latched
    stale = trade("late-prior", 60, F(1001, 100))
    result = evaluate_entry(
        state, TREND, stale, quote(stale), prior(), HEALTH, OPEN + timedelta(seconds=62)
    )
    assert result.state.veto_latched and result.signal is None
    correction = replace(trade("correction", 62, F(1001, 100)), correction=True)
    assert evaluate(state, correction, prior()).state.veto_latched


def test_frozen_feature_change_is_veto_not_a_new_signal() -> None:
    item = trade("trigger", 62, F(1001, 100))
    result = evaluate_entry(
        armed(),
        replace(TREND, hourly9=F(11)),
        item,
        quote(item),
        prior(),
        HEALTH,
        item.received_at,
    )
    assert result.reasons == ("P04/P05:FROZEN_FEATURES_CHANGED",)


def screen(symbol: str, gain: int) -> ScreenResult:
    return ScreenResult(
        symbol,
        "SCREEN_MATCH",
        (),
        5,
        1,
        F(gain),
        "UNAVAILABLE",
        "UNAVAILABLE",
        contract_sha256=ACCEPTED_SHA256,
        rule_ids=RULE_IDS,
        input_sequence_refs=(1,),
    )


def test_monitor_reserves_owned_symbols_then_exact_rank_and_tiebreak() -> None:
    rows = tuple(screen(f"S{i:02}", i) for i in range(35)) + (screen("AAA", 34),)
    plan = plan_monitors(("OLD", "HELD"), ("HELD",), rows, OPEN, OPEN, True)
    assert plan.active == ("OLD", "HELD") and len(plan.desired) == 30
    assert plan.desired[:3] == ("HELD", "AAA", "S34")
    assert "OLD" in plan.remove_first and "HELD" not in plan.remove_first
    assert "S00" in plan.unavailable and not plan.order_authority


@pytest.mark.parametrize("age", [-1, 301])
def test_monitor_sweep_missing_stale_and_over_capacity_blocks(age: int) -> None:
    assert (
        plan_monitors((), (), (), OPEN, OPEN + timedelta(seconds=age), True).desired
        == ()
    )
    assert plan_monitors((), (), (), OPEN, OPEN, False).desired == ()
    result = plan_monitors(
        (), tuple(f"HELD{i}" for i in range(31)), (), OPEN, OPEN, True
    )
    assert result.reasons == ("P03:RESERVED_CAPACITY_ESCALATE",)


@pytest.mark.parametrize("value", ["false", 1, 0, None])
@pytest.mark.parametrize(
    "field",
    [
        "continuity_from_open",
        "monitored",
        "stream_connected",
        "subscription_acknowledged",
        "unresolved_gap",
        "incident_veto",
        "uncertain_ownership",
    ],
)
def test_nonboolean_health_cannot_qualify(field: str, value: object) -> None:
    item = trade("trigger", 62, F(1001, 100))
    malformed = replace(HEALTH, **{field: cast(Any, value)})
    result = evaluate_entry(
        armed(), TREND, item, quote(item), prior(), malformed, item.received_at
    )
    assert result.outcome == "UNAVAILABLE" and result.signal is None
    assert result.cancel_working_entry_required


@pytest.mark.parametrize("value", ["false", 1, None])
def test_nonboolean_trade_quote_and_state_cannot_qualify(value: object) -> None:
    item = trade("trigger", 62, F(1001, 100))
    for t, q, state in (
        (replace(item, conditions_eligible=cast(Any, value)), quote(item), armed()),
        (replace(item, correction=cast(Any, value)), quote(item), armed()),
        (item, replace(quote(item), conditions_eligible=cast(Any, value)), armed()),
        (item, quote(item), replace(armed(), attempted=cast(Any, value))),
    ):
        assert (
            evaluate_entry(state, TREND, t, q, prior(), HEALTH, item.received_at).signal
            is None
        )
    with pytest.raises(ValueError, match="strict bool"):
        plan_monitors((), (), (), OPEN, OPEN, value)  # type: ignore[arg-type]


def test_duplicate_still_applies_new_incident_or_expiry_cancel_obligation() -> None:
    item = trade("trigger", 62, F(1001, 100))
    result = evaluate(armed(), item, prior())
    assert result.signal is not None
    incident = evaluate_entry(
        result.state,
        TREND,
        item,
        quote(item),
        prior(),
        replace(HEALTH, incident_veto=True),
        item.received_at,
    )
    assert incident.signal is None and incident.cancel_working_entry_required
    expired = evaluate_entry(
        result.state,
        TREND,
        item,
        quote(item),
        prior(),
        HEALTH,
        OPEN + timedelta(hours=1),
    )
    assert expired.signal is None and expired.cancel_working_entry_required
