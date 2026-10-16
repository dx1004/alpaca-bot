"""Exact posted-activity observations; never a reconciled risk ledger grant."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .equity_fees import ExecutedFeeLot, FeeTotals, daily_fees
from .journal import Journal
from .paper_order_contract import decode_broker_json
from .risk_account_inputs import inspect_account_inputs


@dataclass(frozen=True, repr=False)
class PostedLedgerFacts:
    source_refs: tuple[str, ...]
    gross_buy_cash: Fraction
    gross_sell_cash: Fraction
    signed_nta_totals: tuple[tuple[str, Fraction], ...]
    modelled_regulatory_fees: FeeTotals
    activity_count: int
    fill_count: int
    current_day_fill_count: int
    historical_fill_count: int
    positions_count: int | None
    open_orders_count: int | None
    position_market_value: Fraction | None
    observed_cash: Decimal | None
    pagination_exhausted: bool
    issues: tuple[str, ...]
    runtime_authorized: bool = False


def _exact(value: object, *, positive: bool = False) -> Fraction:
    if type(value) is not str or not value.strip():
        raise ValueError("activity amount must be exact decimal text")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid activity amount") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise ValueError("invalid activity amount")
    return Fraction(number)


def _identity(row: dict[str, object], field: str) -> str:
    value = row.get(field)
    if type(value) is not str or not value.strip():
        raise ValueError("activity identity missing")
    return value


def _timestamp(value: object) -> tuple[datetime, Fraction]:
    if type(value) is not str:
        raise ValueError("activity timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("activity timestamp is naive")
    match = re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})",
        value,
    )
    if match is None:
        raise ValueError("activity timestamp is not RFC3339")
    fractional = Fraction("0." + match[1]) if match[1] else Fraction(0)
    return parsed, fractional - Fraction(parsed.microsecond, 1_000_000)


def inspect_posted_ledger(
    journal: Journal,
    capture_sequence: int,
    *,
    account_ref: str,
    decision_at: datetime,
    session_open: datetime,
    max_age: timedelta,
    fee_policy: Path,
) -> PostedLedgerFacts:
    """Inspect raw GET evidence, retaining unknown finality and attribution.

    Cash amounts cover the retained census, whereas fee aggregation covers the
    session's New York calendar day only. Neither a terminal empty page nor a
    current account balance proves atomic observation or absence of late posts.
    """
    account = inspect_account_inputs(
        journal,
        capture_sequence,
        account_ref=account_ref,
        decision_at=decision_at,
        session_open=session_open,
        max_age=max_age,
    )
    stored = journal.read_event(capture_sequence)
    if stored is None:
        raise ValueError("capture evidence missing")
    event, raw = stored
    capture = decode_broker_json(raw)
    assert isinstance(capture, dict)
    requests = capture["requests"]
    assert isinstance(requests, list)
    seen: dict[str, dict[str, object]] = {}
    cursor: str | None = None
    cursors: set[str] = set()
    page_count = 0
    exhausted = False
    positions: list[dict[str, object]] | None = None
    orders: list[dict[str, object]] | None = None
    issues = {
        "BASELINE_UNVERIFIED",
        "CURRENT_FUNDS_UNQUALIFIED",
        "RESERVATION_OWNERSHIP_UNVERIFIED",
        "POSTED_ACTIVITY_FINALITY_UNVERIFIED",
        "NONATOMIC_ACCOUNT_ACTIVITY_CAPTURE",
    }
    for request in requests:
        assert isinstance(request, dict)
        path = request["path"]
        assert isinstance(path, str)
        parsed = urlsplit(path)
        rows = decode_broker_json(request["body"])
        if parsed.path == "/v2/positions":
            if parsed.query or positions is not None:
                raise ValueError("position census is filtered or duplicated")
            assert isinstance(rows, list)
            positions = rows
        elif parsed.path == "/v2/orders":
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (
                orders is not None
                or query.get("status") != ["open"]
                or query.get("limit") != ["500"]
                or query.get("nested") != ["false"]
                or set(query) - {"status", "limit", "nested", "direction"}
                or query.get("direction", ["asc"]) != ["asc"]
            ):
                raise ValueError("open order census is filtered or duplicated")
            assert isinstance(rows, list)
            orders = rows
            if len(rows) >= 500:
                issues.add("OPEN_ORDER_CENSUS_POSSIBLY_TRUNCATED")
        elif parsed.path == "/v2/account/activities":
            query = parse_qs(parsed.query, keep_blank_values=True)
            expected = {"direction": ["desc"], "page_size": ["100"]}
            if cursor is not None:
                expected["page_token"] = [cursor]
            if query != expected or exhausted:
                raise ValueError("activity pagination contract differs")
            assert isinstance(rows, list)
            if len(rows) > 100:
                raise ValueError("activity page exceeds requested size")
            page_count += 1
            if not rows:
                exhausted = True
                continue
            for row in rows:
                assert isinstance(row, dict)
                activity_id = _identity(row, "id")
                if activity_id in seen and seen[activity_id] != row:
                    raise ValueError("changed duplicate activity")
                seen[activity_id] = row
            next_cursor = _identity(rows[-1], "id")
            if next_cursor in cursors:
                raise ValueError("activity pagination made no progress")
            cursors.add(next_cursor)
            cursor = next_cursor
    if not page_count or not exhausted:
        issues.add("ACTIVITY_PAGINATION_NOT_EXHAUSTED")
    if positions is None:
        issues.add("POSITION_CENSUS_MISSING")
    if orders is None:
        issues.add("OPEN_ORDER_CENSUS_MISSING")
    market_value = (
        None
        if positions is None
        else sum((_exact(row.get("market_value")) for row in positions), Fraction(0))
    )
    buy = sell = Fraction(0)
    nta: dict[str, Fraction] = {}
    lots: list[ExecutedFeeLot] = []
    fill_count = historical = 0
    day = session_open.astimezone(ZoneInfo("America/New_York")).date()
    for activity_id, row in seen.items():
        kind = _identity(row, "activity_type")
        if kind == "FILL":
            _identity(row, "order_id")
            side = row.get("side")
            if side not in ("buy", "sell"):
                raise ValueError("unsupported fill side")
            price, qty = (
                _exact(row.get("price"), positive=True),
                _exact(row.get("qty"), positive=True),
            )
            when, remainder = _timestamp(row.get("transaction_time"))
            delta = when - decision_at
            elapsed = Fraction(delta // timedelta(microseconds=1), 1_000_000)
            if elapsed + remainder > 0:
                raise ValueError("future fill cannot enter posted ledger")
            fill_day = when.astimezone(ZoneInfo("America/New_York")).date()
            if fill_day > day:
                raise ValueError("fill belongs to a future session")
            fill_count += 1
            if side == "buy":
                buy += price * qty
            else:
                sell += price * qty
            if fill_day == day:
                lots.append(ExecutedFeeLot(activity_id, str(side), qty, price))
            else:
                historical += 1
        else:
            amount = _exact(row.get("net_amount"))
            nta[kind] = nta.get(kind, Fraction(0)) + amount
            # NTA date can be occurrence OR settlement; never assign it to
            # intraday realized PnL or fee accrual from that date alone.
            issues.add("NTA_INTRADAY_ATTRIBUTION_UNQUALIFIED")
            if kind not in {"FEE", "CSD", "CSW"}:
                issues.add("UNKNOWN_NTA_CLASSIFICATION")
    return PostedLedgerFacts(
        (event.payload_ref,),
        buy,
        sell,
        tuple(sorted(nta.items())),
        daily_fees(fee_policy, tuple(lots)),
        len(seen),
        fill_count,
        len(lots),
        historical,
        None if positions is None else len(positions),
        None if orders is None else len(orders),
        market_value,
        dict(account.money)["cash"],
        exhausted,
        tuple(sorted(issues)),
    )
