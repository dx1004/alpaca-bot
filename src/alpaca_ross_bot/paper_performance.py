"""Exact descriptive DRAFT performance from declared, separately sourced ledgers.

This is not an evidence adapter. Scope, full account/day censuses, fee attribution,
flat snapshots and supplied execution ordering remain unverified declarations.
In particular, generic broker FEE rows are never classified from their amount or
cash difference. No value here decides study acceptance, sizing or order action.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from .equity_fees import ExecutedFeeLot, FeeTotals, daily_fees, verify_fee_policy

NY = ZoneInfo("America/New_York")
REGULATORY = {"SEC_FEE", "TAF_FEE", "CAT_FEE"}
CLASSIFIED = REGULATORY | {"OTHER_COST", "FUNDING"}


@dataclass(frozen=True)
class ScopeKey:
    account_ref: str
    state_namespace: str
    manifest_ref: str
    window_id: str
    origin: str


@dataclass(frozen=True)
class PerformanceWindow:
    scope: ScopeKey
    start: datetime
    end: datetime
    expected_fee_days: tuple[date, ...]


@dataclass(frozen=True)
class Execution:
    execution_id: str
    scope: ScopeKey
    roundtrip_id: str
    symbol: str
    side: str
    quantity: Fraction
    price: Fraction
    at: datetime
    ownership_ref: str | None


@dataclass(frozen=True)
class BrokerPosting:
    activity_id: str
    scope: ScopeKey
    classification: str
    signed_cash: Fraction  # Debit negative; explicit attributable refunds positive.
    posted_at: datetime
    attribution_ref: str | None


@dataclass(frozen=True)
class DayCensus:
    day: date
    execution_ids: tuple[str, ...] | None
    full_account_day_ref: str | None


@dataclass(frozen=True)
class BrokerCensus:
    activity_ids: tuple[str, ...] | None
    study_attribution_ref: str | None


@dataclass(frozen=True)
class FlatCashSnapshot:
    scope: ScopeKey
    at: datetime
    cash: Fraction | None
    owned_flat_ref: str | None


@dataclass(frozen=True)
class EquitySample:
    sample_id: str
    scope: ScopeKey
    at: datetime
    marked_equity: Fraction
    basis_ref: str
    source_ref: str
    basis_kind: str


@dataclass(frozen=True)
class SampledDrawdown:
    sample_count: int
    start: datetime | None
    end: datetime | None
    basis_ref: str | None
    maximum_absolute: Fraction | None
    maximum_fraction: Fraction | None
    covers_declared_endpoints: bool
    issues: tuple[str, ...]
    scope: str = "SUPPLIED_SAMPLED_EQUITY_ONLY_NOT_CONTINUOUS_MAXIMUM"


@dataclass(frozen=True)
class RoundtripGross:
    roundtrip_id: str
    symbol: str
    execution_ids: tuple[str, ...]
    gross: Fraction


@dataclass(frozen=True)
class DayFees:
    day: date
    observed_execution_count: int
    fees_on_declared_census: FeeTotals


@dataclass(frozen=True)
class PerformanceDescription:
    executions: tuple[Execution, ...]
    broker_postings: tuple[BrokerPosting, ...]
    roundtrips: tuple[RoundtripGross, ...]
    modelled_days: tuple[DayFees, ...]
    broker_cash_delta: Fraction | None
    gross_pnl: Fraction | None
    broker_net_pnl: Fraction | None
    posted_regulatory_cost: Fraction | None
    other_posted_cost: Fraction | None
    signed_funding: Fraction | None
    modelled_regulatory_cost: Fraction | None
    adjusted_net_pnl: Fraction | None
    issues: tuple[str, ...]
    drawdown: SampledDrawdown
    fee_document_ref: str
    source_evidence_verified: bool = False
    runtime_authorized: bool = False
    limitations: tuple[str, ...] = (
        "DRAFT_DESCRIPTIVE_CALCULATION_ONLY_NOT_STUDY_ACCEPTANCE",
        "SOURCE_ADAPTER_AND_ACCOUNT_DAY_CENSUS_VERIFICATION_REQUIRED",
        "FEE_ATTRIBUTION_AND_ACCOUNT_SCHEDULE_APPLICABILITY_UNVERIFIED",
        "DECLARED_EXECUTION_ORDER_AND_ROUNDTRIP_OWNERSHIP_UNVERIFIED",
        "NO_PER_TRADE_ALLOCATION_OF_DAILY_ROUNDED_FEES",
        "SAMPLED_DRAWDOWN_IS_NOT_CONTINUOUS_OR_CLOSED_TRADE_DRAWDOWN",
        "OBSERVED_ACCOUNT_EQUITY_CURVE_RETAINS_FUNDING_EFFECTS",
    )


def _text(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _utc(at: datetime) -> None:
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC performance timestamp required")


def _number(value: Fraction, *, positive: bool = False) -> None:
    if type(value) is not Fraction or (positive and value <= 0):
        raise ValueError("exact Fraction amount required")


def _same_scope(actual: ScopeKey, expected: ScopeKey) -> None:
    if actual != expected:
        raise ValueError("performance scopes cannot mix accounts, releases or origins")


def _ids(ids: tuple[str, ...] | None, expected: set[str]) -> bool:
    return (
        type(ids) is tuple
        and all(_text(item) for item in ids)
        and len(set(ids)) == len(ids)
        and set(ids) == expected
    )


def _drawdown(
    window: PerformanceWindow, samples: tuple[EquitySample, ...]
) -> SampledDrawdown:
    seen: dict[str, EquitySample] = {}
    points: list[EquitySample] = []
    for sample in samples:
        _same_scope(sample.scope, window.scope)
        _utc(sample.at)
        _number(sample.marked_equity)
        if not all(
            _text(value)
            for value in (sample.sample_id, sample.source_ref, sample.basis_ref)
        ):
            raise ValueError("explicit sampled equity identity and basis required")
        if sample.sample_id in seen:
            if seen[sample.sample_id] != sample:
                raise ValueError("equity sample identity conflict")
            continue
        seen[sample.sample_id] = sample
        if not window.start <= sample.at <= window.end:
            raise ValueError("equity sample is outside the declared study window")
        if points:
            if sample.at < points[-1].at:
                raise ValueError("supplied equity curve must already be chronological")
            if (sample.basis_ref, sample.basis_kind) != (
                points[-1].basis_ref,
                points[-1].basis_kind,
            ):
                raise ValueError("equity valuation bases cannot be mixed")
            if sample.at == points[-1].at:
                if sample.marked_equity != points[-1].marked_equity:
                    raise ValueError("same-instant equity observations conflict")
                continue
        points.append(sample)
    if len(points) < 2:
        return SampledDrawdown(
            len(points),
            points[0].at if points else None,
            points[-1].at if points else None,
            points[0].basis_ref if points else None,
            None,
            None,
            False,
            ("EQUITY_CURVE_UNKNOWN_OR_INSUFFICIENT",),
        )
    if any(point.basis_kind != "OBSERVED_ACCOUNT_EQUITY" for point in points):
        return SampledDrawdown(
            len(points),
            points[0].at,
            points[-1].at,
            points[0].basis_ref,
            None,
            None,
            False,
            ("OBSERVED_ACCOUNT_EQUITY_BASIS_UNAVAILABLE",),
        )
    peak = points[0].marked_equity
    absolute = fraction = Fraction(0)
    fraction_known = peak > 0
    for point in points:
        peak = max(peak, point.marked_equity)
        absolute = max(absolute, peak - point.marked_equity)
        if peak > 0:
            fraction = max(fraction, (peak - point.marked_equity) / peak)
        else:
            fraction_known = False
    return SampledDrawdown(
        len(points),
        points[0].at,
        points[-1].at,
        points[0].basis_ref,
        absolute,
        fraction if fraction_known else None,
        points[0].at == window.start and points[-1].at == window.end,
        () if fraction_known else ("NONPOSITIVE_EQUITY_PEAK_PERCENTAGE_UNKNOWN",),
    )


def describe_paper_performance(
    window: PerformanceWindow,
    executions: tuple[Execution, ...],
    postings: tuple[BrokerPosting, ...],
    *,
    day_censuses: tuple[DayCensus, ...],
    broker_census: BrokerCensus,
    opening: FlatCashSnapshot,
    closing: FlatCashSnapshot,
    fee_policy: Path,
    execution_order_ref: str | None = None,
    equity_curve: tuple[EquitySample, ...] = (),
) -> PerformanceDescription:
    """Reconcile declared flat-book cash and replace identified SEC/TAF/CAT only.

    Executions are consumed in their supplied immutable order, never sorted by
    order ID or assigned by FIFO. Opposite-side ties in one roundtrip need an
    explicit ordering reference; that reference is still not authenticated here.
    Cash is never an equity curve. Modelled daily fees are not actual charges.
    """
    if window.scope.origin != "FORMAL_PAPER" or not all(
        _text(value)
        for value in (
            window.scope.account_ref,
            window.scope.state_namespace,
            window.scope.manifest_ref,
            window.scope.window_id,
        )
    ):
        raise ValueError("one explicitly declared formal-paper scope required")
    for at in (window.start, window.end, opening.at, closing.at):
        _utc(at)
    if not (opening.at == window.start < window.end == closing.at):
        raise ValueError(
            "flat cash observations must match exact declared window endpoints"
        )
    if any(
        type(value) is not tuple
        for value in (
            executions,
            postings,
            day_censuses,
            equity_curve,
            window.expected_fee_days,
        )
    ):
        raise ValueError("immutable performance censuses required")
    if (
        not window.expected_fee_days
        or len(set(window.expected_fee_days)) != len(window.expected_fee_days)
        or any(type(day) is not date for day in window.expected_fee_days)
    ):
        raise ValueError("explicit unique account fee-day scope required")
    for snapshot in (opening, closing):
        _same_scope(snapshot.scope, window.scope)
        if snapshot.cash is not None:
            _number(snapshot.cash)
    blocking: set[str] = set()
    review: set[str] = set()
    if (
        opening.cash is None
        or closing.cash is None
        or not _text(opening.owned_flat_ref)
        or not _text(closing.owned_flat_ref)
    ):
        blocking.add("FLAT_CASH_BOUNDARY_UNQUALIFIED")
    unique: dict[str, Execution] = {}
    groups: dict[str, list[Execution]] = {}
    last_at: datetime | None = None
    for fill in executions:
        _same_scope(fill.scope, window.scope)
        _utc(fill.at)
        _number(fill.quantity, positive=True)
        _number(fill.price, positive=True)
        if not all(
            _text(value)
            for value in (fill.execution_id, fill.roundtrip_id, fill.symbol)
        ) or fill.side not in {"buy", "sell"}:
            raise ValueError("owned-long execution assignment required")
        if fill.execution_id in unique:
            if unique[fill.execution_id] != fill:
                raise ValueError("execution identity conflict")
            continue
        if not window.start <= fill.at <= window.end or (
            last_at is not None and fill.at < last_at
        ):
            raise ValueError("execution order or declared window differs")
        last_at = fill.at
        if not _text(fill.ownership_ref):
            blocking.add("EXECUTION_OWNERSHIP_UNVERIFIED")
        if fill.at.astimezone(NY).date() not in window.expected_fee_days:
            blocking.add("EXECUTION_DAY_NOT_DECLARED")
        unique[fill.execution_id] = fill
        groups.setdefault(fill.roundtrip_id, []).append(fill)
    roundtrips: list[RoundtripGross] = []
    gross = Fraction(0)
    for rid, fills in groups.items():
        position = cash = Fraction(0)
        symbols = {fill.symbol for fill in fills}
        healthy = len(symbols) == 1
        previous: Execution | None = None
        for index, fill in enumerate(fills):
            if (
                previous is not None
                and fill.at == previous.at
                and fill.side != previous.side
                and not _text(execution_order_ref)
            ):
                healthy = False
                blocking.add("OPPOSITE_SIDE_ORDERING_UNKNOWN")
            signed = fill.quantity if fill.side == "buy" else -fill.quantity
            position += signed
            cash -= signed * fill.price
            if position < 0 or (position == 0 and index != len(fills) - 1):
                healthy = False
            previous = fill
        if not healthy or position != 0:
            blocking.add("ROUNDTRIP_NOT_ONE_OWNED_LONG_TO_FLAT")
        else:
            roundtrips.append(
                RoundtripGross(
                    rid,
                    fills[0].symbol,
                    tuple(fill.execution_id for fill in fills),
                    cash,
                )
            )
            gross += cash
    censuses: dict[date, DayCensus] = {}
    for census in day_censuses:
        if census.day in censuses:
            if censuses[census.day] != census:
                raise ValueError("account/day census conflict")
            continue
        censuses[census.day] = census
    fee_census_ok = set(censuses) == set(window.expected_fee_days)
    modelled: list[DayFees] = []
    for day in window.expected_fee_days:
        day_fills = tuple(
            fill for fill in unique.values() if fill.at.astimezone(NY).date() == day
        )
        selected_census = censuses.get(day)
        if (
            selected_census is None
            or not _text(selected_census.full_account_day_ref)
            or not _ids(
                selected_census.execution_ids, {fill.execution_id for fill in day_fills}
            )
        ):
            fee_census_ok = False
        modelled.append(
            DayFees(
                day,
                len(day_fills),
                daily_fees(
                    fee_policy,
                    tuple(
                        ExecutedFeeLot(
                            fill.execution_id, fill.side, fill.quantity, fill.price
                        )
                        for fill in day_fills
                    ),
                ),
            )
        )
    if not fee_census_ok:
        blocking.add("COMPLETE_ACCOUNT_DAY_EXECUTION_CENSUS_MISSING")
    raw_postings: dict[str, BrokerPosting] = {}
    regulatory = other = funding = Fraction(0)
    for posting in postings:
        _same_scope(posting.scope, window.scope)
        _utc(posting.posted_at)
        _number(posting.signed_cash)
        if not _text(posting.activity_id):
            raise ValueError("broker posting identity required")
        if posting.activity_id in raw_postings:
            if raw_postings[posting.activity_id] != posting:
                raise ValueError("broker posting identity conflict")
            continue
        raw_postings[posting.activity_id] = posting
        if posting.classification == "FUNDING" and posting.signed_cash:
            review.add("FUNDING_REQUIRES_STUDY_REVIEW")
        if not opening.at <= posting.posted_at <= closing.at:
            blocking.add("POSTING_OUTSIDE_CASH_RECONCILIATION_SCOPE")
        if posting.classification not in CLASSIFIED or not _text(
            posting.attribution_ref
        ):
            blocking.add("BROKER_POSTING_CLASSIFICATION_OR_ATTRIBUTION_UNKNOWN")
            continue
        if posting.classification in REGULATORY:
            regulatory -= posting.signed_cash
        elif posting.classification == "OTHER_COST":
            other -= posting.signed_cash
        else:
            funding += posting.signed_cash
    posting_census_ok = _text(broker_census.study_attribution_ref) and _ids(
        broker_census.activity_ids, set(raw_postings)
    )
    if not posting_census_ok:
        blocking.add("COMPLETE_BROKER_POSTING_CENSUS_MISSING")
    delta = (
        None
        if opening.cash is None or closing.cash is None
        else closing.cash - opening.cash
    )
    if not blocking and delta != gross - regulatory - other + funding:
        blocking.add("DECLARED_CASH_CONSERVATION_MISMATCH")
    all_model = (
        sum((row.fees_on_declared_census.total for row in modelled), Fraction(0))
        if fee_census_ok
        else None
    )
    broker_net = delta - funding if not blocking and delta is not None else None
    adjusted = (
        broker_net + regulatory - all_model
        if broker_net is not None and all_model is not None
        else None
    )
    fee_postings_known = posting_census_ok and not (
        blocking
        & {
            "BROKER_POSTING_CLASSIFICATION_OR_ATTRIBUTION_UNKNOWN",
            "POSTING_OUTSIDE_CASH_RECONCILIATION_SCOPE",
        }
    )
    return PerformanceDescription(
        tuple(unique.values()),
        tuple(raw_postings.values()),
        tuple(roundtrips),
        tuple(modelled),
        delta,
        gross if not blocking else None,
        broker_net,
        regulatory if fee_postings_known else None,
        other if fee_postings_known else None,
        funding if fee_postings_known else None,
        all_model,
        adjusted,
        tuple(sorted(blocking | review)),
        _drawdown(window, equity_curve),
        verify_fee_policy(fee_policy),
    )
