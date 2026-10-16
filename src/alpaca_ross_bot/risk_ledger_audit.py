"""Source-bound flat-account cash audit, without operational risk authority."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .journal import Journal
from .paper_order_contract import decode_broker_json
from .posted_ledger import PostedLedgerFacts, _exact, _timestamp, inspect_posted_ledger
from .risk_baseline import read_baseline, session_open_from_evidence


@dataclass(frozen=True, repr=False)
class RiskLedgerAudit:
    source_refs: tuple[str, ...]
    current: PostedLedgerFacts
    baseline_present: bool
    new_activity_count: int
    new_fill_count: int
    posted_buy_delta: Fraction | None
    posted_sell_delta: Fraction | None
    signed_funding_delta: Fraction | None
    raw_fee_delta: Fraction | None
    modelled_fee_delta: Fraction | None
    gross_pnl: Fraction | None
    cost_adjusted_net: Fraction | None
    cash_matches: bool | None
    issues: tuple[str, ...]
    runtime_authorized: bool = False

    def public_summary(self) -> dict[str, object]:
        return {
            "baseline_present": self.baseline_present,
            "new_activity_count": self.new_activity_count,
            "new_fill_count": self.new_fill_count,
            "pagination_exhausted": self.current.pagination_exhausted,
            "cash_matches": self.cash_matches,
            "issues": list(self.issues),
            "runtime_authorized": False,
        }


def _activities(journal: Journal, sequence: int) -> dict[str, dict[str, object]]:
    stored = journal.read_event(sequence)
    if stored is None:
        raise ValueError("activity capture missing")
    body = decode_broker_json(stored[1])
    assert isinstance(body, dict)
    result: dict[str, dict[str, object]] = {}
    requests = body["requests"]
    assert isinstance(requests, list)
    for request in requests:
        if urlsplit(request["path"]).path == "/v2/account/activities":
            rows = decode_broker_json(request["body"])
            assert isinstance(rows, list)
            for row in rows:
                result[row["id"]] = row
    return result


def audit_risk_ledger(
    journal: Journal,
    capture_sequence: int,
    calendar_sequence: int,
    *,
    account_ref: str,
    decision_at: datetime,
    max_age: timedelta,
    fee_policy: Path,
) -> RiskLedgerAudit:
    """Compare immutable full posted censuses to a genuinely frozen E0 capture.

    Agreement is only a flat-book posted cash equation. It cannot establish late
    activity finality, order ownership, current funds, or nonflat protection.
    """
    day = decision_at.astimezone(ZoneInfo("America/New_York")).date()
    opened = session_open_from_evidence(
        journal, calendar_sequence, account_ref, day, decision_at, max_age
    )
    current = inspect_posted_ledger(
        journal,
        capture_sequence,
        account_ref=account_ref,
        decision_at=decision_at,
        session_open=opened,
        max_age=max_age,
        fee_policy=fee_policy,
    )
    calendar = journal.read_event(calendar_sequence)
    assert calendar is not None
    refs = (*current.source_refs, calendar[0].payload_ref)
    issues = {
        "CURRENT_FUNDS_UNQUALIFIED",
        "OWNED_NONFLAT_RECONCILIATION_UNQUALIFIED",
        "POSTED_ACTIVITY_FINALITY_UNVERIFIED",
        "NONATOMIC_ACCOUNT_ACTIVITY_CAPTURE",
    }
    baseline = read_baseline(journal, account_ref=account_ref, session_day=day)
    if baseline is None:
        issues.add("PRESESSION_E0_MISSING")
        return RiskLedgerAudit(
            refs,
            current,
            False,
            0,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            tuple(sorted(issues)),
        )
    if (
        baseline.frozen_at is None
        or baseline.frozen_at > decision_at
        or baseline.qualified_at is None
        or baseline.qualified_at > decision_at
        or baseline.receipt_payload_ref is None
        or baseline.session_open != opened
    ):
        raise ValueError("baseline unavailable or official session differs")
    prior = inspect_posted_ledger(
        journal,
        baseline.account_sequence,
        account_ref=account_ref,
        decision_at=baseline.frozen_at,
        session_open=baseline.session_open,
        max_age=max_age,
        fee_policy=fee_policy,
    )
    refs = (*refs, *prior.source_refs, baseline.receipt_payload_ref)
    previous_rows = _activities(journal, baseline.account_sequence)
    current_rows = _activities(journal, capture_sequence)
    preserved = all(current_rows.get(key) == row for key, row in previous_rows.items())
    if not preserved:
        issues.add("BASELINE_ACTIVITY_CENSUS_CHANGED")
    new_rows = [row for key, row in current_rows.items() if key not in previous_rows]
    fills = [row for row in new_rows if row["activity_type"] == "FILL"]
    funding = sum(
        (
            _exact(row.get("net_amount"))
            for row in new_rows
            if row["activity_type"] in {"CSD", "CSW"}
        ),
        Fraction(0),
    )
    raw_fee = sum(
        (
            _exact(row.get("net_amount"))
            for row in new_rows
            if row["activity_type"] == "FEE"
        ),
        Fraction(0),
    )
    unknown = any(
        row["activity_type"] not in {"FILL", "CSD", "CSW"} for row in new_rows
    )
    if unknown:
        issues.add("NEW_NTA_CHARGE_CLASSIFICATION_UNQUALIFIED")
    if any(row["activity_type"] != "FILL" for row in new_rows):
        issues.add("NTA_INTRADAY_ATTRIBUTION_UNQUALIFIED")
    if fills:
        issues.add("FILL_OWNERSHIP_UNVERIFIED")
        if not journal.read_order_intent_ids():
            issues.add("FOREIGN_FILLS_IN_UNUSED_JOURNAL")

    def at_or_before_baseline(row: dict[str, object]) -> bool:
        when, remainder = _timestamp(row["transaction_time"])
        assert baseline.frozen_at is not None
        delta = when - baseline.frozen_at
        return Fraction(delta // timedelta(microseconds=1), 1_000_000) + remainder <= 0

    if any(at_or_before_baseline(row) for row in fills):
        issues.add("LATE_BASELINE_FILL_POSTING")
    quantities: dict[str, Fraction] = {}
    symbol_valid = True
    for row in sorted(fills, key=lambda item: _timestamp(item["transaction_time"])):
        symbol = row.get("symbol")
        if type(symbol) is not str or not symbol.strip():
            symbol_valid = False
            issues.add("FILL_SYMBOL_MISSING")
            continue
        delta = _exact(row.get("qty"), positive=True)
        quantities[symbol] = quantities.get(symbol, Fraction(0)) + (
            delta if row["side"] == "buy" else -delta
        )
        if quantities[symbol] < 0:
            symbol_valid = False
            issues.add("SHORT_SELL_PREFIX_UNQUALIFIED")
    if any(quantities.values()):
        symbol_valid = False
        issues.add("FILL_QUANTITIES_NOT_FLAT")
    flat = (
        prior.positions_count == current.positions_count == 0
        and prior.open_orders_count == current.open_orders_count == 0
    )
    if not flat:
        issues.add("NONFLAT_SNAPSHOT_RECONCILIATION_UNQUALIFIED")
    if journal.read_order_intent_ids():
        issues.add("LOCAL_ORDER_OWNERSHIP_RECONCILIATION_REQUIRED")
    exhausted = prior.pagination_exhausted and current.pagination_exhausted
    if not exhausted:
        issues.add("ACTIVITY_PAGINATION_NOT_EXHAUSTED")
    buy = current.gross_buy_cash - prior.gross_buy_cash
    sell = current.gross_sell_cash - prior.gross_sell_cash
    model_fee = (
        current.modelled_regulatory_fees.total - prior.modelled_regulatory_fees.total
    )
    comparable = (
        preserved
        and exhausted
        and flat
        and symbol_valid
        and not unknown
        and not journal.read_order_intent_ids()
        and "LATE_BASELINE_FILL_POSTING" not in issues
    )
    cash_matches = None
    gross = net = None
    if (
        comparable
        and prior.observed_cash is not None
        and current.observed_cash is not None
    ):
        cash_matches = (
            Fraction(current.observed_cash)
            == Fraction(prior.observed_cash) + sell - buy + funding
        )
        if not cash_matches:
            issues.add("POSTED_CASH_EQUATION_MISMATCH")
        else:
            gross = sell - buy
            net = gross - model_fee
    return RiskLedgerAudit(
        refs,
        current,
        True,
        len(new_rows),
        len(fills),
        buy,
        sell,
        funding,
        raw_fee,
        model_fee,
        gross,
        net,
        cash_matches,
        tuple(sorted(issues)),
    )
