"""Offline, as-observed screen for the explicitly accepted IEX adaptation."""

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

from .scanner_contract import (
    AcceptedScannerContract,
    validate_accepted_scanner_contract,
    validate_sequence_refs,
)
from .scanner_input_audit import (
    DatedDailyVolume,
    ScannerInputAuditError,
    VolumeContract,
    audit_daily_volume_history,
)

PROFILE_ID = "GENERAL_IEX_DAILY30_SCREEN_V1"
CLASSIFICATION = "USER_ACCEPTED_ADAPTATION_NOT_ROSS_REPLICA"
SESSION_SCOPE = "FULL_NEW_YORK_CALENDAR_DAY"
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ScreenContext:
    decision_at: datetime
    calendar_sessions: tuple[date, ...]
    calendar_available_at: datetime
    catalog_symbols: tuple[str, ...]
    catalog_available_at: datetime
    contract: AcceptedScannerContract
    input_sequence_refs: tuple[int, ...] = ()


@dataclass(frozen=True)
class ClockedPrice:
    symbol: str
    value: Decimal
    session_date: date
    event_at: datetime
    available_at: datetime
    source_id: str
    feed: str
    adjustment: str


@dataclass(frozen=True)
class ScreenInput:
    symbol: str
    current_price: ClockedPrice
    prior_close: ClockedPrice | None
    current_volume: DatedDailyVolume | None
    history: tuple[DatedDailyVolume, ...]
    history_available_at: datetime
    pagination_exhausted: bool
    news_available_at: datetime | None = None
    float_available_at: datetime | None = None
    input_sequence_refs: tuple[int, ...] = ()


@dataclass(frozen=True)
class _UnboundResult:
    symbol: str
    status: str
    reasons: tuple[str, ...]
    rvol_numerator: int | None
    rvol_denominator: int | None
    percent_change: Fraction | None
    news_preference: str
    float_preference: str
    profile_id: str = PROFILE_ID
    classification: str = CLASSIFICATION
    coverage_label: str = "NOT_SIP_EQUIVALENT"
    full_market_coverage: bool = False
    strategy_eligibility: str = "BLOCKED_RESEARCH"
    order_authority: bool = False
    scope: str = "CALLER_SUPPLIED_ACTIVE_CATALOG_OFFLINE_ONLY"
    history_completion_basis: str = "ELAPSED_NY_DAY_NOT_VENDOR_IMMUTABILITY"


@dataclass(frozen=True, kw_only=True)
class ScreenResult(_UnboundResult):
    contract_sha256: str
    rule_ids: tuple[str, ...]
    input_sequence_refs: tuple[int, ...]


def _utc(stamp: datetime) -> bool:
    return (
        isinstance(stamp, datetime)
        and stamp.tzinfo is not None
        and (stamp.utcoffset() == timedelta(0))
    )


def _context_reasons(context: ScreenContext) -> set[str]:
    validate_accepted_scanner_contract(context.contract)
    validate_sequence_refs(context.input_sequence_refs)
    stamps = (
        context.decision_at,
        context.calendar_available_at,
        context.catalog_available_at,
    )
    if not all(_utc(stamp) for stamp in stamps):
        raise ValueError("context clocks must be timezone-aware UTC")
    if (
        not isinstance(context.calendar_sessions, tuple)
        or any(type(day) is not date for day in context.calendar_sessions)
        or tuple(sorted(set(context.calendar_sessions))) != context.calendar_sessions
    ):
        raise ValueError("calendar dates must be unique and sorted")
    if (
        not isinstance(context.catalog_symbols, tuple)
        or any(not isinstance(s, str) or not s.strip() for s in context.catalog_symbols)
        or len(set(context.catalog_symbols)) != len(context.catalog_symbols)
    ):
        raise ValueError("catalog identities must be unique nonempty strings")
    return {
        label
        for stamp, label in (
            (context.calendar_available_at, "CALENDAR_UNAVAILABLE_AT_DECISION"),
            (context.catalog_available_at, "CATALOG_UNAVAILABLE_AT_DECISION"),
        )
        if stamp > context.decision_at
    }


@dataclass(frozen=True)
class _PreparedContext:
    reasons: frozenset[str]
    today: date
    earlier: tuple[date, ...]
    catalog: frozenset[str]


def _prepare(context: ScreenContext) -> _PreparedContext:
    reasons = _context_reasons(context)
    today = context.decision_at.astimezone(_NY).date()
    earlier = tuple(day for day in context.calendar_sessions if day < today)
    if today not in context.calendar_sessions:
        reasons.add("CURRENT_DAY_NOT_TRADING_SESSION")
    if len(earlier[-30:]) != 30:
        reasons.add("INSUFFICIENT_CALENDAR_HISTORY")
    return _PreparedContext(
        frozenset(reasons), today, earlier, frozenset(context.catalog_symbols)
    )


def evaluate_one(context: ScreenContext, item: ScreenInput) -> ScreenResult:
    """Keep malformed symbol evidence from aborting the catalog evaluation."""
    return _evaluate_safe(context, item, _prepare(context))


def _evaluate_safe(
    context: ScreenContext, item: ScreenInput, prepared: _PreparedContext
) -> ScreenResult:
    validate_sequence_refs(item.input_sequence_refs)
    return _bind(
        _evaluate_unbound(context, item, prepared), context, item.input_sequence_refs
    )


def _bind(
    result: _UnboundResult, context: ScreenContext, refs: tuple[int, ...] = ()
) -> ScreenResult:
    return ScreenResult(
        **{field.name: getattr(result, field.name) for field in fields(_UnboundResult)},
        contract_sha256=context.contract.sha256,
        rule_ids=context.contract.rule_ids,
        input_sequence_refs=tuple(sorted(set(context.input_sequence_refs) | set(refs))),
    )


def _evaluate_unbound(
    context: ScreenContext, item: ScreenInput, prepared: _PreparedContext
) -> _UnboundResult:
    dates = [item.current_price.session_date]
    if item.prior_close is not None:
        dates.append(item.prior_close.session_date)
    if item.current_volume is not None:
        dates.append(item.current_volume.session_date)
    dates.extend(bar.session_date for bar in item.history)
    if any(type(day) is not date for day in dates):
        return _unavailable(item.symbol, "INVALID_SESSION_DATE")
    try:
        return _evaluate_one(context, item, prepared)
    except ScannerInputAuditError:
        return _unavailable(item.symbol, "INVALID_VOLUME_INPUT_CONTRACT")


def _unavailable(symbol: str, reason: str) -> _UnboundResult:
    return _UnboundResult(
        symbol,
        "INPUT_UNAVAILABLE",
        (reason,),
        None,
        None,
        None,
        "UNAVAILABLE",
        "UNAVAILABLE",
    )


def _evaluate_one(
    context: ScreenContext, item: ScreenInput, prepared: _PreparedContext
) -> _UnboundResult:
    """Evaluate supplied observations; neither collection nor live freshness proof.

    Available preferences mean supplied information exists, not that low float or
    a qualifying catalyst is established. No preference can veto this screen.
    Invalid context structure raises; missing/unusable evidence yields unavailable.
    """
    reasons = set(prepared.reasons)
    today, earlier = prepared.today, prepared.earlier
    expected = earlier[-30:]
    if item.symbol not in prepared.catalog:
        reasons.add("SYMBOL_OUTSIDE_DECLARED_CATALOG")
    price = item.current_price
    price_valid = (
        isinstance(price.value, Decimal)
        and price.value.is_finite()
        and price.value > 0
        and price.symbol == item.symbol
        and price.session_date == today
        and (price.source_id, price.feed, price.adjustment) == ("alpaca", "iex", "raw")
        and _utc(price.event_at)
        and _utc(price.available_at)
        and price.event_at <= price.available_at <= context.decision_at
        and price.event_at.astimezone(_NY).date() == today
    )
    # A proven mandatory price failure cannot be rescued by missing RVOL inputs.
    if not reasons and price_valid and not Decimal(1) <= price.value < Decimal(20):
        return _UnboundResult(
            item.symbol,
            "RULE_NOT_MET",
            ("PRICE_OUTSIDE_1_TO_UNDER_20",),
            None,
            None,
            None,
            "UNAVAILABLE",
            "UNAVAILABLE",
        )
    if item.prior_close is None or item.current_volume is None:
        return _UnboundResult(
            item.symbol,
            "INPUT_UNAVAILABLE",
            tuple(sorted(reasons | {"REQUIRED_MARKET_INPUT_MISSING"})),
            None,
            None,
            None,
            "UNAVAILABLE",
            "UNAVAILABLE",
        )
    contract = VolumeContract("alpaca", item.symbol, "iex", "raw", SESSION_SCOPE)
    for label, price, day in (
        ("CURRENT_PRICE", item.current_price, today),
        ("PRIOR_CLOSE", item.prior_close, earlier[-1] if earlier else None),
    ):
        if price.symbol != item.symbol:
            reasons.add(f"{label}_SYMBOL_MISMATCH")
        if (
            not isinstance(price.value, Decimal)
            or not price.value.is_finite()
            or price.value <= 0
        ):
            reasons.add(f"{label}_INVALID_VALUE")
        if price.session_date != day:
            reasons.add(f"{label}_SESSION_MISMATCH")
        if (price.source_id, price.feed, price.adjustment) != ("alpaca", "iex", "raw"):
            reasons.add(f"{label}_CONTRACT_MISMATCH")
        if not _utc(price.event_at) or not _utc(price.available_at):
            reasons.add(f"{label}_INVALID_CLOCK")
        elif not (price.event_at <= price.available_at <= context.decision_at):
            reasons.add(f"{label}_UNAVAILABLE_AT_DECISION")
        elif price.event_at.astimezone(_NY).date() != price.session_date:
            reasons.add(f"{label}_EVENT_SESSION_MISMATCH")
        if label == "PRIOR_CLOSE" and _utc(price.available_at):
            elapsed = datetime.combine(
                price.session_date + timedelta(days=1), time.min, _NY
            ).astimezone(UTC)
            if price.available_at < elapsed:
                reasons.add("PRIOR_CLOSE_BUCKET_NOT_ELAPSED_AT_AVAILABILITY")
    if item.current_volume.session_date != today:
        reasons.add("CURRENT_VOLUME_SESSION_MISMATCH")
    for bar in item.history:
        elapsed = datetime.combine(
            bar.session_date + timedelta(days=1), time.min, _NY
        ).astimezone(UTC)
        if bar.session_completed_at != elapsed:
            reasons.add("HISTORY_COMPLETION_NOT_NEXT_NY_MIDNIGHT")
    if not _utc(item.history_available_at):
        reasons.add("HISTORY_INVALID_AVAILABLE_CLOCK")
    elif expected:
        audit = audit_daily_volume_history(
            item.history,
            expected_sessions=expected,
            contract=contract,
            first_seen_at=item.history_available_at,
            processed_at=item.history_available_at,
            decision_at=context.decision_at,
            pagination_exhausted=item.pagination_exhausted,
            current=item.current_volume,
        )
        reasons.update(audit.reasons)
    numerator = denominator = None
    change = None
    if not reasons:
        numerator = item.current_volume.volume * 30
        denominator = sum(bar.volume for bar in item.history)
        change = (
            Fraction(item.current_price.value) / Fraction(item.prior_close.value) - 1
        ) * 100
        if not Decimal(1) <= item.current_price.value < Decimal(20):
            reasons.add("PRICE_OUTSIDE_1_TO_UNDER_20")
        if numerator < 5 * denominator:
            reasons.add("RVOL_BELOW_5")
        status = "RULE_NOT_MET" if reasons else "SCREEN_MATCH"
    else:
        status = "INPUT_UNAVAILABLE"
    return _UnboundResult(
        item.symbol,
        status,
        tuple(sorted(reasons)),
        numerator,
        denominator,
        change,
        "AVAILABLE_NOT_QUALIFIED"
        if item.news_available_at is not None
        and _utc(item.news_available_at)
        and item.news_available_at <= context.decision_at
        else "UNAVAILABLE",
        "AVAILABLE_NOT_QUALIFIED"
        if item.float_available_at is not None
        and _utc(item.float_available_at)
        and item.float_available_at <= context.decision_at
        else "UNAVAILABLE",
    )


def evaluate_all(
    context: ScreenContext, items: tuple[ScreenInput, ...]
) -> tuple[ScreenResult, ...]:
    """Rank matches by exact percentage change; retain every nonmatch explicitly.

    This does not assert catalog coverage: absent supplied inputs are unavailable.
    """
    prepared = _prepare(context)
    if len({item.symbol for item in items}) != len(items):
        raise ValueError("duplicate input identity")
    results = [_evaluate_safe(context, item, prepared) for item in items]
    present = {item.symbol for item in items}
    for symbol in prepared.catalog - present:
        results.append(
            _bind(
                _UnboundResult(
                    symbol,
                    "INPUT_UNAVAILABLE",
                    ("OBSERVATION_MISSING",),
                    None,
                    None,
                    None,
                    "UNAVAILABLE",
                    "UNAVAILABLE",
                ),
                context,
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda result: (
                result.status != "SCREEN_MATCH",
                -result.percent_change
                if result.status == "SCREEN_MATCH" and result.percent_change is not None
                else Fraction(0),
                result.symbol,
            ),
        )
    )
