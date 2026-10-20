"""Source-bound P03/P05-P08/P13 offline entry reducer, never order authority.

Strict crossing compares two eligible trades with the CURRENT prior-bar high.
Decision-time switch/expiry prevents a late old-timeframe signal at a boundary.
Changing a bar threshold alone is not a trade crossing. The arming trade may be
its baseline; a timeframe switch discards the previous timeframe's baseline.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from fractions import Fraction

from .adapted_scanner import ScreenResult
from .observation_admission import ObservationContract
from .scanner_contract import ACCEPTED_SHA256, RULE_IDS
from .strategy_features import (
    COVERAGE,
    VERSION,
    Bar,
    FeatureUnavailable,
    Trend,
    positive,
    research_candidate_version,
    session_slots,
    utc,
)


@dataclass(frozen=True)
class MonitorPlan:
    active: tuple[str, ...]
    desired: tuple[str, ...]
    remove_first: tuple[str, ...]
    add_after_ack: tuple[str, ...]
    unavailable: tuple[str, ...]
    reasons: tuple[str, ...]
    order_authority: bool = False
    coverage: str = COVERAGE


def plan_monitors(
    current: tuple[str, ...],
    reserved: tuple[str, ...],
    results: tuple[ScreenResult, ...],
    completed_at: datetime,
    decision_at: datetime,
    sweep_complete: bool,
) -> MonitorPlan:
    """Plan only: subscriptions remain unchanged until a verified external handoff."""
    utc(completed_at)
    utc(decision_at)
    if type(sweep_complete) is not bool:
        raise ValueError("P03: sweep completion must be a strict bool")
    if (
        len(set(current)) != len(current)
        or len(current) > 30
        or len(set(reserved)) != len(reserved)
        or any(not value.strip() for value in current + reserved)
        or len({item.symbol for item in results}) != len(results)
    ):
        raise ValueError("P03: invalid monitor identities")
    if not sweep_complete or not timedelta(
        0
    ) <= decision_at - completed_at <= timedelta(seconds=300):
        return MonitorPlan(current, (), (), (), (), ("P03:SWEEP_UNAVAILABLE",))
    if len(reserved) > 30:
        return MonitorPlan(current, (), (), (), (), ("P03:RESERVED_CAPACITY_ESCALATE",))
    matches = [item for item in results if item.status == "SCREEN_MATCH"]
    if any(
        type(item.percent_change) is not Fraction
        or item.contract_sha256 != ACCEPTED_SHA256
        or item.rule_ids != RULE_IDS
        or item.coverage_label != COVERAGE
        or item.profile_id != "GENERAL_IEX_DAILY30_SCREEN_V1"
        for item in matches
    ):
        return MonitorPlan(
            current, (), (), (), (), ("P03:SCREEN_IDENTITY_UNAVAILABLE",)
        )
    ranked = sorted(
        matches, key=lambda item: (-(item.percent_change or Fraction(0)), item.symbol)
    )
    desired = (
        tuple(sorted(reserved))
        + tuple(item.symbol for item in ranked if item.symbol not in reserved)[
            : 30 - len(reserved)
        ]
    )
    remove = tuple(symbol for symbol in current if symbol not in desired)
    add = tuple(symbol for symbol in desired if symbol not in current)
    unavailable = tuple(item.symbol for item in ranked if item.symbol not in desired)
    reason = "P03:HANDOFF_REQUIRED" if remove or add else "P03:UNCHANGED"
    return MonitorPlan(current, desired, remove, add, unavailable, (reason,))


@dataclass(frozen=True)
class Trade:
    event_id: str
    symbol: str
    source_at: datetime
    received_at: datetime
    price: Fraction
    condition_policy_ref: str
    conditions_eligible: bool
    correction: bool = False

    def __post_init__(self) -> None:
        utc(self.source_at)
        utc(self.received_at)
        positive(self.price)
        if not self.event_id.strip() or not self.symbol.strip():
            raise ValueError("P13: trade identity required")


@dataclass(frozen=True)
class Quote:
    symbol: str
    source_at: datetime
    received_at: datetime
    bid: Fraction
    ask: Fraction
    bid_size: int
    ask_size: int
    condition_policy_ref: str
    conditions_eligible: bool


@dataclass(frozen=True)
class InputHealth:
    """Offline attestations, NOT a live data qualifier or execution release."""

    source_contract_ref: str
    condition_policy_ref: str
    first_trade_id: str
    continuity_from_open: bool
    monitored: bool
    stream_connected: bool
    subscription_acknowledged: bool
    unresolved_gap: bool
    incident_veto: bool
    uncertain_ownership: bool
    low_since_open: Fraction | None


@dataclass(frozen=True)
class Signal:
    signal_id: str
    symbol: str
    session_day: date
    trade_id: str
    limit_price: Fraction
    low_since_open: Fraction
    version: str = VERSION
    order_authority: bool = False


@dataclass(frozen=True)
class EntryState:
    symbol: str
    session_day: date
    version: str = VERSION
    first_seen: bool = False
    gap_passed: bool = False
    armed: bool = False
    invalidated: bool = False
    attempted: bool = False
    veto_latched: bool = False
    last_trade_at: datetime | None = None
    last_trade_price: Fraction | None = None
    timeframe: int | None = None
    last_decision_at: datetime | None = None
    seen: tuple[Trade, ...] = ()
    pending_signal: Signal | None = None
    durable_intent_ref: str | None = None
    trend_snapshot: Trend | None = None


@dataclass(frozen=True)
class EntryDecision:
    state: EntryState
    outcome: str
    reasons: tuple[str, ...]
    signal: Signal | None = None
    cancel_working_entry_required: bool = False
    order_authority: bool = False
    coverage: str = COVERAGE


def _result(
    state: EntryState,
    outcome: str,
    reason: str,
    signal: Signal | None = None,
    cancel: bool = False,
) -> EntryDecision:
    return EntryDecision(state, outcome, (reason,), signal, cancel)


def _input_reason(
    trade: Trade, quote: Quote, health: InputHealth, decision_at: datetime
) -> str | None:
    flags = (
        trade.conditions_eligible,
        trade.correction,
        quote.conditions_eligible,
        health.continuity_from_open,
        health.monitored,
        health.stream_connected,
        health.subscription_acknowledged,
        health.unresolved_gap,
        health.incident_veto,
        health.uncertain_ownership,
    )
    if any(type(value) is not bool for value in flags):
        return "P13:MALFORMED_BOOL_FACT"
    if (
        not health.source_contract_ref.strip()
        or not health.condition_policy_ref.strip()
        or not trade.conditions_eligible
        or not quote.conditions_eligible
        or trade.condition_policy_ref != health.condition_policy_ref
        or quote.condition_policy_ref != health.condition_policy_ref
    ):
        return "P13:CONDITION_OR_SOURCE_POLICY_UNAVAILABLE"
    if (
        not health.monitored
        or not health.continuity_from_open
        or not health.first_trade_id
        or not health.stream_connected
        or not health.subscription_acknowledged
        or health.unresolved_gap
        or health.incident_veto
        or health.uncertain_ownership
    ):
        return "P03/P13:DATA_OR_INCIDENT_VETO"
    if quote.symbol != trade.symbol:
        return "P13:QUOTE_SYMBOL_MISMATCH"
    try:
        for stamp in (
            trade.source_at,
            trade.received_at,
            quote.source_at,
            quote.received_at,
        ):
            utc(stamp)
            if not timedelta(0) <= decision_at - stamp <= timedelta(milliseconds=1000):
                return "P13:STALE_OR_FUTURE_INPUT"
        positive(quote.bid)
        positive(quote.ask)
        if health.low_since_open is None:
            return "P03/P09:LOW_RECONSTRUCTION_UNAVAILABLE"
        positive(health.low_since_open)
    except FeatureUnavailable:
        return "P13:MALFORMED_INPUT"
    if (
        trade.source_at > trade.received_at
        or quote.source_at > quote.received_at
        or quote.bid > quote.ask
        or type(quote.bid_size) is not int
        or type(quote.ask_size) is not int
        or quote.bid_size <= 0
        or quote.ask_size <= 0
        or health.low_since_open > trade.price
    ):
        return "P13:INVALID_QUOTE_OR_CLOCK_OR_LOW"
    return None


def evaluate_entry(
    state: EntryState,
    trend: Trend,
    trade: Trade,
    quote: Quote,
    prior_bar: Bar | None,
    health: InputHealth,
    decision_at: datetime,
    *,
    observation_contract: ObservationContract | None = None,
) -> EntryDecision:
    """Evaluate a new eligible trade. Signals never consume the attempt allowance."""
    utc(decision_at)
    version = research_candidate_version(observation_contract)
    session = trend.session
    if state.trend_snapshot is not None and state.trend_snapshot != trend:
        return _result(
            replace(state, veto_latched=True, pending_signal=None),
            "UNAVAILABLE",
            "P04/P05:FROZEN_FEATURES_CHANGED",
            cancel=True,
        )
    if (
        state.version != version
        or trend.version != version
        or state.symbol != trend.symbol
        or trade.symbol != state.symbol
        or state.session_day != session.day
    ):
        return _result(state, "UNAVAILABLE", "P03/P05:IDENTITY_MISMATCH", cancel=True)
    if state.last_decision_at is not None and decision_at < state.last_decision_at:
        return _result(state, "UNAVAILABLE", "P13:STALE_DECISION", cancel=True)
    state_flags = (
        state.first_seen,
        state.gap_passed,
        state.armed,
        state.invalidated,
        state.attempted,
        state.veto_latched,
    )
    if any(type(value) is not bool for value in state_flags):
        return _result(state, "UNAVAILABLE", "P13:MALFORMED_STATE_BOOL", cancel=True)
    reason = _input_reason(trade, quote, health, decision_at)
    if reason:
        return _result(
            replace(state, veto_latched=True, pending_signal=None),
            "UNAVAILABLE",
            reason,
            cancel=True,
        )
    end = min(session.open_at + timedelta(minutes=60), session.close_at)
    if decision_at >= end or trade.source_at >= end:
        return _result(
            replace(state, pending_signal=None),
            "NO_SIGNAL",
            "P06/P07:ENTRY_WINDOW_EXPIRED",
            cancel=True,
        )
    if state.invalidated:
        return _result(state, "NO_SIGNAL", "P06:SESSION_INVALIDATED", cancel=True)
    old = next((item for item in state.seen if item.event_id == trade.event_id), None)
    if old is not None:
        if old == trade:
            return _result(state, "NO_SIGNAL", "P06:DUPLICATE_TRADE")
        changed = replace(state, veto_latched=True, pending_signal=None)
        return _result(changed, "UNAVAILABLE", "P13:CHANGED_EVENT_ID", cancel=True)
    state = replace(
        state,
        seen=state.seen + (trade,),
        last_decision_at=decision_at,
        pending_signal=None,
        trend_snapshot=trend,
    )
    if (
        trade.correction
        or state.veto_latched
        or (state.last_trade_at is not None and trade.source_at <= state.last_trade_at)
    ):
        state = replace(state, veto_latched=True)
        return _result(
            state, "UNAVAILABLE", "P04/P13:CORRECTION_OR_NONMONOTONE_TRADE", cancel=True
        )
    if trade.source_at < session.open_at or decision_at < session.open_at:
        return _result(state, "NO_SIGNAL", "P07:BEFORE_RTH")
    if session.available_at > decision_at:
        return _result(state, "UNAVAILABLE", "P04:CALENDAR_UNAVAILABLE", cancel=True)
    if state.invalidated:
        return _result(state, "NO_SIGNAL", "P06:SESSION_INVALIDATED", cancel=True)
    try:
        for price in (
            trend.prior_close,
            trend.daily21,
            trend.daily50,
            trend.hourly9,
            trend.hourly21,
        ):
            positive(price)
    except FeatureUnavailable:
        return _result(state, "UNAVAILABLE", "P04:FEATURE_UNAVAILABLE", cancel=True)
    if (
        not trend.prior_close > trend.daily21 > trend.daily50
        or not trend.hourly9 > trend.hourly21
    ):
        return _result(state, "NO_SIGNAL", "P05:HIGHER_TIMEFRAME_TREND_FAILED")
    tf = 1 if decision_at < session.open_at + timedelta(minutes=15) else 5
    baseline = state.last_trade_price if state.timeframe == tf else None
    next_state = replace(
        state, last_trade_at=trade.source_at, last_trade_price=trade.price, timeframe=tf
    )
    if not state.first_seen:
        if trade.event_id != health.first_trade_id:
            return _result(
                replace(next_state, veto_latched=True),
                "UNAVAILABLE",
                "P03/P05:FIRST_TRADE_RECONSTRUCTION_REQUIRED",
                cancel=True,
            )
        passed = trade.price > trend.prior_close and trade.price > trend.hourly9
        return _result(
            replace(next_state, first_seen=True, gap_passed=passed),
            "NO_SIGNAL",
            "P05:GAP_PASSED" if passed else "P05:GAP_FAILED",
        )
    if not state.gap_passed:
        return _result(next_state, "NO_SIGNAL", "P05:GAP_FAILED")
    if trade.price < trend.hourly21:
        return _result(
            replace(next_state, invalidated=True),
            "NO_SIGNAL",
            "P06:BELOW_EMA21_SESSION_INVALIDATED",
            cancel=True,
        )
    armed_now = (
        not state.armed
        and state.last_trade_price is not None
        and state.last_trade_price > trend.hourly9
        and trend.hourly21 <= trade.price <= trend.hourly9
    )
    next_state = replace(next_state, armed=state.armed or armed_now)
    if not next_state.armed:
        return _result(next_state, "NO_SIGNAL", "P05:PULLBACK_NOT_ARMED")
    if armed_now:
        return _result(next_state, "NO_SIGNAL", "P05:PULLBACK_ARMED")
    if state.attempted:
        return _result(next_state, "NO_SIGNAL", "P06:ATTEMPT_ALREADY_RESERVED")
    expected = tuple(
        slot
        for slot in session_slots(session, tf, decision_at)
        if slot.end <= trade.source_at
    )
    if (
        prior_bar is None
        or not expected
        or prior_bar.slot != expected[-1]
        or prior_bar.symbol != state.symbol
        or prior_bar.available_at > decision_at
    ):
        return _result(
            next_state,
            "UNAVAILABLE",
            "P04/P06:PRIOR_ENTRY_BAR_UNAVAILABLE",
            cancel=True,
        )
    if baseline is None or not baseline <= prior_bar.high < trade.price:
        return _result(next_state, "NO_SIGNAL", "P06/P07:NO_NEW_SAME_TIMEFRAME_CROSS")
    limit_price = Fraction(prior_bar.high * 100 // 1 + 1, 100)
    if trade.price > limit_price or quote.ask > limit_price:
        return _result(next_state, "NO_SIGNAL", "P08:NO_CHASE")
    assert health.low_since_open is not None
    signal = Signal(
        f"{version}:{session.day}:{state.symbol}:{trade.event_id}",
        state.symbol,
        session.day,
        trade.event_id,
        limit_price,
        health.low_since_open,
        version=version,
    )
    return _result(
        replace(next_state, pending_signal=signal),
        "RESEARCH_SIGNAL",
        "P05/P06/P07/P08:NEW_CROSS",
        signal,
    )


def consume_attempt(
    state: EntryState, signal_id: str, durable_intent_ref: str
) -> EntryState:
    """Mirror external reservation evidence; this is not persistence proof."""
    if (
        state.attempted
        or state.pending_signal is None
        or state.pending_signal.signal_id != signal_id
        or not durable_intent_ref.strip()
    ):
        raise ValueError("P06: current signal and durable intent evidence required")
    return replace(
        state,
        attempted=True,
        pending_signal=None,
        durable_intent_ref=durable_intent_ref,
    )
