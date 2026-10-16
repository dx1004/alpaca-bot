"""Pinned hybrid risk arithmetic; pure research calculations, never order authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

VERSION = "HYBRID_500_IEX_INTRADAY_RESEARCH_V1"
COVERAGE = "NOT_SIP_EQUIVALENT"


def exact(value: Decimal, name: str, nonnegative: bool = False) -> Fraction:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    result = Fraction(value)
    if nonnegative and result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def flag(value: bool) -> bool:
    if type(value) is not bool:
        raise ValueError("verification flag must be bool")
    return value


def whole(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("quantity must be nonnegative integer")
    return value


@dataclass(frozen=True)
class RiskAccount:
    equity: Decimal
    spendable: Decimal
    session: date
    account_ref: str
    version: str = VERSION
    equity_verified: bool = False
    funds_verified: bool = False
    account_eligible: bool = False
    symbol_tradable: bool = False
    one_x_verified: bool = False
    baseline_verified: bool = False


@dataclass(frozen=True)
class FeeBound:
    quantity: int
    entry: Decimal
    stop_exit: Decimal


@dataclass(frozen=True)
class FeeBounds:
    rows: tuple[FeeBound, ...]
    evidence_ref: str
    verified: bool = False


@dataclass(frozen=True)
class Reservation:
    identity: str
    total_quantity: int
    pending_quantity: int
    open_quantity: int
    limit: Decimal
    stop: Decimal
    bid: Decimal
    pending_entry_fees: Decimal
    pending_stop_fees: Decimal
    open_exit_fees: Decimal
    verified: bool = False


@dataclass(frozen=True)
class RiskLedger:
    account_ref: str
    session: date
    realized: Decimal
    unrealized_bid: Decimal
    accrued_charges: Decimal
    reservations: tuple[Reservation, ...] = ()
    verified: bool = False
    marks_verified: bool = False
    cashflows_verified: bool = False
    daily_stop_latched: bool = False
    uncertain_ownership: bool = False


def capacity(account: RiskAccount, ledger: RiskLedger) -> Fraction:
    """P12; accrued fees and existing bid-mark losses are already in P."""
    equity = exact(account.equity, "equity", True)
    if (
        type(account.session) is not date
        or type(ledger.session) is not date
        or ledger.session != account.session
        or type(account.account_ref) is not str
        or not account.account_ref
        or ledger.account_ref != account.account_ref
        or account.version != VERSION
    ):
        raise ValueError("account/session/version mismatch")
    if not all(
        [
            flag(v)
            for v in (
                account.equity_verified,
                ledger.verified,
                ledger.marks_verified,
                ledger.cashflows_verified,
            )
        ]
    ):
        raise ValueError("unverified equity or ledger")
    pnl = (
        exact(ledger.realized, "realized")
        + exact(ledger.unrealized_bid, "unrealized")
        - exact(ledger.accrued_charges, "accrued charges", True)
    )
    if type(ledger.reservations) is not tuple:
        raise ValueError("reservations must be immutable tuple")
    reserve = Fraction(0)
    identities: set[str] = set()
    for item in ledger.reservations:
        if (
            type(item.identity) is not str
            or not item.identity
            or item.identity in identities
            or not flag(item.verified)
        ):
            raise ValueError("unverified or duplicate reservation")
        identities.add(item.identity)
        total = whole(item.total_quantity)
        pending, opened = whole(item.pending_quantity), whole(item.open_quantity)
        if pending + opened > total:
            raise ValueError("pending and filled quantities overlap")
        limit, stop = exact(item.limit, "limit"), exact(item.stop, "stop")
        bid = exact(item.bid, "bid")
        if stop <= 0 or limit <= stop or bid <= 0:
            raise ValueError("invalid reservation prices")
        entry_fee = exact(item.pending_entry_fees, "pending entry fees", True)
        stop_fee = exact(item.pending_stop_fees, "pending stop fees", True)
        exit_fee = exact(item.open_exit_fees, "open exit fees", True)
        if (not pending and (entry_fee or stop_fee)) or (not opened and exit_fee):
            raise ValueError("charges without matching inventory")
        reserve += pending * (limit - stop) + entry_fee + stop_fee
        reserve += opened * max(Fraction(0), bid - stop) + exit_fee
    allowance = equity / 100
    return max(Fraction(0), min(allowance, allowance + pnl) - reserve)


@dataclass(frozen=True)
class SizeDecision:
    account_ref: str
    session: date
    frozen_equity: Fraction
    quantity: int
    limit: Fraction
    stop: Fraction
    capacity: Fraction
    reason: str
    version: str = VERSION
    coverage: str = COVERAGE
    rule_ids: tuple[str, ...] = ("P01", "P08", "P09", "P10", "P12")
    runtime_authorized: bool = False


def size_entry(
    account: RiskAccount,
    ledger: RiskLedger,
    fees: FeeBounds,
    trigger_high: Decimal | Fraction,
    known_low: Decimal | Fraction,
    trigger_trade: Decimal | Fraction,
    ask: Decimal | Fraction,
    *,
    daily_state: DailyRiskState,
) -> SizeDecision:
    def price(value: Decimal | Fraction, name: str) -> Fraction:
        if type(value) is Fraction:
            return value
        if isinstance(value, Decimal):
            return exact(value, name)
        raise ValueError(f"{name} must be an exact Decimal or Fraction")

    high = price(trigger_high, "trigger high")
    low = price(known_low, "known low")
    trade, offer = price(trigger_trade, "trigger trade"), price(ask, "ask")
    if min(high, low, trade, offer) <= 0:
        raise ValueError("prices must be positive")
    limit = Fraction(high.numerator * 100 // high.denominator + 1, 100)
    stop = Fraction(low.numerator * 100 // low.denominator, 100)
    available = capacity(account, ledger)
    if (
        daily_state.version != VERSION
        or daily_state.account_ref != account.account_ref
        or type(daily_state.account_ref) is not str
        or type(daily_state.session) is not date
        or daily_state.session != account.session
        or exact(daily_state.frozen_equity, "frozen equity", True)
        != exact(account.equity, "equity", True)
        or type(daily_state.losing_streak) is not int
        or daily_state.losing_streak < 0
    ):
        raise ValueError("daily risk state binding mismatch")
    state_latched = flag(daily_state.latched) or daily_state.losing_streak >= 3

    def reject(reason: str) -> SizeDecision:
        return SizeDecision(
            account.account_ref,
            account.session,
            Fraction(account.equity),
            0,
            limit,
            stop,
            available,
            reason,
        )

    if stop <= 0 or stop >= limit:
        return reject("INVALID_STOP_TOPOLOGY")
    if trade > limit or offer > limit:
        return reject("NO_CHASE")
    if not all(
        [
            flag(v)
            for v in (
                account.funds_verified,
                account.account_eligible,
                account.symbol_tradable,
                account.one_x_verified,
                account.baseline_verified,
            )
        ]
    ):
        return reject("ACCOUNT_UNAVAILABLE")
    if (
        state_latched
        or flag(ledger.daily_stop_latched)
        or flag(ledger.uncertain_ownership)
    ):
        return reject("DAILY_OR_OWNERSHIP_VETO")
    if ledger.reservations:
        return reject("SLOT_OCCUPIED")
    funds = exact(account.spendable, "spendable funds", True)
    bound = int(funds // limit)
    if (
        not flag(fees.verified)
        or type(fees.evidence_ref) is not str
        or not fees.evidence_ref
    ):
        return reject("FEES_UNAVAILABLE")
    if type(fees.rows) is not tuple:
        raise ValueError("fee bounds must be immutable tuple")
    schedule: dict[int, tuple[Fraction, Fraction]] = {}
    for row in fees.rows:
        q = whole(row.quantity)
        if q == 0 or q in schedule:
            raise ValueError("duplicate or zero fee quantity")
        schedule[q] = (
            exact(row.entry, "entry fee", True),
            exact(row.stop_exit, "stop fee", True),
        )
    if any(q not in schedule for q in range(1, bound + 1)):
        return reject("FEES_INCOMPLETE")
    budget = min(exact(account.equity, "equity") / 200, available)
    quantity = 0
    # Enumerate every feasible q; no monotonic-fee assumption or ratio-only shortcut.
    for q in range(1, bound + 1):
        entry, exit_fee = schedule[q]
        if (
            q * limit + entry <= funds
            and q * (limit - stop) + entry + exit_fee <= budget
        ):
            quantity = q
    return SizeDecision(
        account.account_ref,
        account.session,
        Fraction(account.equity),
        quantity,
        limit,
        stop,
        available,
        "FEASIBLE" if quantity else "NO_FEASIBLE_QUANTITY",
    )


@dataclass(frozen=True)
class TargetDecision:
    target: Fraction | None
    incident_required: bool
    reason: str
    version: str = VERSION
    coverage: str = COVERAGE
    rule_ids: tuple[str, ...] = ("P11",)
    runtime_authorized: bool = False


def target_price(
    average_fill: Decimal,
    stop: Decimal,
    *,
    entry_terminal: bool,
    fills_reconciled: bool,
) -> TargetDecision:
    fill, frozen_stop = exact(average_fill, "average fill"), exact(stop, "stop")
    if min(fill, frozen_stop) <= 0:
        raise ValueError("prices must be positive")
    ready = [flag(entry_terminal), flag(fills_reconciled)]
    if not all(ready):
        return TargetDecision(None, False, "ENTRY_NOT_RECONCILED_TERMINAL")
    if fill <= frozen_stop:
        return TargetDecision(None, True, "INVALID_STOP_TOPOLOGY")
    raw = 3 * fill - 2 * frozen_stop
    target = Fraction(-(-raw.numerator * 100 // raw.denominator), 100)
    return TargetDecision(target, False, "TARGET_FIXED")


@dataclass(frozen=True)
class DailyRiskState:
    session: date
    account_ref: str
    frozen_equity: Decimal
    losing_streak: int = 0
    latched: bool = False
    completed_ids: tuple[str, ...] = ()
    version: str = VERSION
    coverage: str = COVERAGE
    rule_ids: tuple[str, ...] = ("P12",)
    runtime_authorized: bool = False


def update_daily_stop(
    state: DailyRiskState,
    account: RiskAccount,
    ledger: RiskLedger,
    *,
    reconciled_flat: bool,
    no_orphan_orders: bool,
    roundtrip_id: str | None = None,
    roundtrip_net: Decimal | None = None,
    roundtrip_reconciled: bool = False,
) -> DailyRiskState:
    """Reduce the canonical account/day state, preserving its verified E0.

    The durable upstream risk owner must retain this state and propagate its
    latch into RiskLedger.daily_stop_latched before any size_entry evaluation.
    Reconstructing an empty state within a day is not an authorized reset.
    """
    capacity(account, ledger)
    if (
        state.version != VERSION
        or type(state.session) is not date
        or type(state.losing_streak) is not int
        or state.losing_streak < 0
        or type(state.completed_ids) is not tuple
        or len(set(state.completed_ids)) != len(state.completed_ids)
        or not all(type(i) is str and i for i in state.completed_ids)
    ):
        raise ValueError("invalid daily state")
    exact(state.frozen_equity, "frozen equity", True)
    if (
        type(state.account_ref) is not str
        or not state.account_ref
        or state.account_ref != account.account_ref
    ):
        raise ValueError("daily state account mismatch")
    if account.session == state.session and state.frozen_equity != account.equity:
        raise ValueError("same-session frozen equity mismatch")
    flag(state.latched)
    flat, no_orphans = flag(reconciled_flat), flag(no_orphan_orders)
    if account.session < state.session:
        raise ValueError("session cannot go backwards")
    if account.session > state.session:
        if not flat or not no_orphans or ledger.reservations:
            raise ValueError("new date requires reconciled flat and no orphans")
        state = DailyRiskState(account.session, account.account_ref, account.equity)
    streak, ids = state.losing_streak, state.completed_ids
    if (roundtrip_id is None) != (roundtrip_net is None):
        raise ValueError("roundtrip identity and net must be supplied together")
    if roundtrip_id is not None and roundtrip_net is not None:
        if type(roundtrip_id) is not str or not roundtrip_id:
            raise ValueError("invalid roundtrip identity")
        net = exact(roundtrip_net, "net roundtrip")
        if (
            not flag(roundtrip_reconciled)
            or not flat
            or not no_orphans
            or ledger.reservations
        ):
            raise ValueError("roundtrip must return to reconciled flat")
        if roundtrip_id in ids:
            raise ValueError("duplicate roundtrip")
        ids += (roundtrip_id,)
        streak = streak + 1 if net < 0 else 0 if net > 0 else streak
    pnl = (
        exact(ledger.realized, "realized")
        + exact(ledger.unrealized_bid, "unrealized")
        - exact(ledger.accrued_charges, "accrued", True)
    )
    latched = state.latched or flag(ledger.daily_stop_latched) or streak >= 3
    latched = latched or pnl <= -exact(account.equity, "equity") / 100
    return DailyRiskState(
        account.session, account.account_ref, state.frozen_equity, streak, latched, ids
    )


@dataclass(frozen=True)
class ClosingDisposition:
    closing_due: bool
    cancel_unfilled_entries: bool
    require_verified_close_path: bool
    preserve_protective_exits: bool = True
    version: str = VERSION
    coverage: str = COVERAGE
    rule_ids: tuple[str, ...] = ("P11", "P12")
    runtime_authorized: bool = False


def closing_disposition(
    now: datetime, calendar_close: datetime, *, daily_stop_latched: bool
) -> ClosingDisposition:
    if (
        type(now) is not datetime
        or type(calendar_close) is not datetime
        or now.utcoffset() is None
        or calendar_close.utcoffset() is None
    ):
        raise ValueError("timezone-aware calendar times required")
    latched = flag(daily_stop_latched)
    due = now >= calendar_close - timedelta(minutes=5)
    return ClosingDisposition(due, due or latched, due)
