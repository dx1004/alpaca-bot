"""Exact observed owned-equity conservation; not executable risk acceptance."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from .equity_fees import verify_fee_policy
from .journal import Journal
from .order_lifecycle import OrderProjection
from .order_reconciliation import OrderReconciler
from .paper_order_contract import decode_broker_json
from .posted_ledger import _exact, _timestamp, inspect_posted_ledger
from .risk_baseline import read_baseline, session_open_from_evidence
from .risk_input_evidence import inspect_risk_inputs
from .risk_ledger_audit import _activities


@dataclass(frozen=True, repr=False)
class OwnedEquityAudit:
    source_refs: tuple[tuple[int, str], ...]
    fee_document_ref: str
    baseline_present: bool
    cash_matches: bool | None
    quantity_matches: bool
    observed_equity_at_bid: Fraction | None
    gross_since_baseline: Fraction | None
    modelled_fee_delta: Fraction | None
    modelled_net_since_baseline: Fraction | None
    issues: tuple[str, ...]

    @property
    def reported_conservation_verified(self) -> bool:
        return self.cash_matches is True and self.quantity_matches and not self.issues

    @property
    def runtime_authorized(self) -> bool:
        return False

    def public_summary(self) -> dict[str, object]:
        return {
            "baseline_present": self.baseline_present,
            "cash_matches": self.cash_matches,
            "quantity_matches": self.quantity_matches,
            "reported_conservation_verified": self.reported_conservation_verified,
            "issues": self.issues,
            "runtime_authorized": False,
            "limitations": (
                "REPORTED_OBSERVATIONS_NOT_ATOMIC_OR_FINAL",
                "NOT_SETTLED_FUNDS_OR_LIQUIDATION_PROCEEDS",
                "NOT_RESERVATION_OR_PROTECTION_ACCEPTANCE",
            ),
        }


def audit_owned_equity(
    journal: Journal,
    capture_sequence: int,
    calendar_sequence: int,
    *,
    account_ref: str,
    decision_at: datetime,
    max_age: timedelta,
    fee_policy: Path,
    mark_refs: tuple[tuple[str, int, int, int], ...] = (),
    max_metadata_age: timedelta,
    _intent_ids: tuple[str, ...] | None = None,
    _source_horizon: int | None = None,
) -> OwnedEquityAudit:
    """Reconcile a flat-E0 paper account's observed fills, cash and owned bids.

    Uses execution ``qty``, never cumulative quantity or provider position marks.
    New NTAs are unsupported, not inferred as zero funding or fees. Modelled paper
    costs are a separate adjustment, never relabelled as broker-posted charges.
    """
    if _source_horizon is not None and (
        type(_source_horizon) is not int
        or _source_horizon < 0
        or capture_sequence > _source_horizon
        or calendar_sequence > _source_horizon
    ):
        raise ValueError("owned-equity reference exceeds immutable source horizon")
    day = decision_at.astimezone(ZoneInfo("America/New_York")).date()
    fee_document_ref = verify_fee_policy(fee_policy)
    opened = session_open_from_evidence(
        journal, calendar_sequence, account_ref, day, decision_at, max_age
    )
    inputs = inspect_risk_inputs(
        journal,
        capture_sequence,
        account_ref=account_ref,
        decision_at=decision_at,
        session_open=opened,
        max_age=max_age,
        mark_refs=mark_refs,
        max_metadata_age=max_metadata_age,
        _intent_ids=_intent_ids,
        _source_horizon=_source_horizon,
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
    refs = {seq for seq, _ in inputs.source_refs} | {calendar_sequence}
    issues: set[str] = set()
    if not inputs.reported_inputs_qualified:
        issues.add("REPORTED_INPUTS_UNQUALIFIED")
    if not current.pagination_exhausted:
        issues.add("ACTIVITY_CENSUS_INCOMPLETE")
    baseline = read_baseline(
        journal,
        account_ref=account_ref,
        session_day=day,
        decision_at=decision_at,
        through_sequence=_source_horizon,
    )
    cash_matches: bool | None = None
    quantity_matches = False
    equity = gross = fee_delta = net = None
    if baseline is None:
        issues.add("PRESESSION_E0_MISSING")
    else:
        if (
            baseline.frozen_at is None
            or baseline.qualified_at is None
            or baseline.frozen_at > decision_at
            or baseline.qualified_at > decision_at
            or baseline.session_open != opened
            or baseline.receipt_sequence is None
        ):
            raise ValueError("baseline is not available for this observed session")
        refs.update(
            (
                baseline.account_sequence,
                baseline.calendar_sequence,
                baseline.receipt_sequence,
            )
        )
        prior = inspect_posted_ledger(
            journal,
            baseline.account_sequence,
            account_ref=account_ref,
            decision_at=baseline.frozen_at,
            session_open=opened,
            max_age=max_age,
            fee_policy=fee_policy,
        )
        old_rows = _activities(journal, baseline.account_sequence)
        rows = _activities(journal, capture_sequence)
        if not prior.pagination_exhausted:
            issues.add("BASELINE_ACTIVITY_CENSUS_INCOMPLETE")
        if any(rows.get(key) != row for key, row in old_rows.items()):
            issues.add("BASELINE_CENSUS_CHANGED")
        new_rows = [row for key, row in rows.items() if key not in old_rows]
        if any(row["activity_type"] != "FILL" for row in new_rows):
            issues.add("NEW_NTA_REQUIRES_ATTRIBUTION")
        state, lifecycle_refs = OrderReconciler.read_evidence_at(
            journal, account_ref, decision_at, through_sequence=_source_horizon
        )
        refs.update(lifecycle_refs)
        raw_orders = state.get("orders")
        if not isinstance(raw_orders, dict):
            raise ValueError("lifecycle orders unavailable")
        orders = {
            row.broker_order_id: row
            for row in (OrderProjection.from_dict(raw) for raw in raw_orders.values())
        }
        if len(orders) != len(raw_orders):
            issues.add("BROKER_ORDER_ID_COLLISION")
        if orders:
            watermark = state.get("watermark")
            source = journal.read_event(watermark) if type(watermark) is int else None
            if source is None:
                raise ValueError("lifecycle activity source is unavailable")
            item = decode_broker_json(source[1])
            if not isinstance(item, dict) or not isinstance(item.get("batch"), dict):
                raise ValueError("lifecycle activity batch is unavailable")
            activities = item["batch"].get("activities")
            if not isinstance(activities, dict):
                raise ValueError("lifecycle execution census is unavailable")

            def signature(row: dict[str, object]) -> tuple[object, ...]:
                return (
                    row["order_id"],
                    row["symbol"],
                    row["side"],
                    _exact(row["qty"], positive=True),
                    _exact(row["price"], positive=True),
                    _timestamp(row["transaction_time"]),
                )

            target: dict[str, tuple[object, ...]] = {}
            for batch_rows in activities.values():
                for row in batch_rows:
                    identity = str(row["id"])
                    sig = signature(row)
                    if identity in target and target[identity] != sig:
                        issues.add("LIFECYCLE_EXECUTION_ID_CONFLICT")
                    target[identity] = sig
            posted = {
                str(row["id"]): signature(row)
                for row in rows.values()
                if row["activity_type"] == "FILL" and str(row["order_id"]) in orders
            }
            if posted != target:
                issues.add("FILL_SOURCE_DISAGREEMENT")
        by_order: dict[str, Fraction] = {}
        # Lifecycle quantities and its activity census cover each owned order's
        # lifetime. E0 only partitions cash and position changes for this session.
        for row in rows.values():
            if row["activity_type"] != "FILL":
                continue
            order_id = str(row["order_id"])
            if order_id in orders:
                by_order[order_id] = by_order.get(order_id, Fraction(0)) + _exact(
                    row["qty"], positive=True
                )
        by_symbol: dict[str, Fraction] = {}
        cash_delta = Fraction(0)
        for row in new_rows:
            if row["activity_type"] != "FILL":
                continue
            at, remainder = _timestamp(row["transaction_time"])
            elapsed = (at - baseline.frozen_at) // timedelta(microseconds=1)
            if Fraction(elapsed, 1_000_000) + remainder <= 0:
                issues.add("LATE_BASELINE_FILL")
            order_id = str(row["order_id"])
            projection = orders.get(order_id)
            symbol = str(row.get("symbol", ""))
            if (
                projection is None
                or projection.symbol != symbol
                or projection.side != row["side"]
            ):
                issues.add("FILL_OWNERSHIP_MISMATCH")
            qty = _exact(row["qty"], positive=True)
            notional = qty * _exact(row["price"], positive=True)
            sign = 1 if row["side"] == "buy" else -1
            by_symbol[symbol] = by_symbol.get(symbol, Fraction(0)) + sign * qty
            cash_delta -= sign * notional
        if any(
            by_order.get(key, Fraction(0)) != Fraction(row.filled_quantity)
            for key, row in orders.items()
        ):
            issues.add("FILL_CUMULATIVE_QUANTITY_MISMATCH")
        observed = dict(inputs.ownership.owned_long_positions)
        expected = {symbol: qty for symbol, qty in by_symbol.items() if qty}
        quantity_matches = inputs.ownership.ownership_verified and expected == observed
        if not quantity_matches:
            issues.add("OWNED_QUANTITY_CONSERVATION_MISMATCH")
        if any(qty < 0 for qty in expected.values()):
            issues.add("REVERSE_POSITION_UNQUALIFIED")
        if current.observed_cash is None or prior.observed_cash is None:
            issues.add("CASH_SOURCE_MISSING")
        elif not issues:
            cash_matches = (
                Fraction(current.observed_cash)
                == Fraction(prior.observed_cash) + cash_delta
            )
            if not cash_matches:
                issues.add("OBSERVED_CASH_CONSERVATION_MISMATCH")
        if cash_matches is True and inputs.total_bid_value is not None:
            assert current.observed_cash is not None
            fee_delta = (
                current.modelled_regulatory_fees.total
                - prior.modelled_regulatory_fees.total
            )
            if fee_delta < 0:
                issues.add("MODELLED_FEE_CENSUS_REGRESSED")
            else:
                equity = Fraction(current.observed_cash) + inputs.total_bid_value
                gross = equity - Fraction(baseline.equity)
                net = gross - fee_delta
    sources = []
    for seq in sorted(refs):
        record = journal.read_event(seq)
        if (
            record is None
            or (_source_horizon is not None and seq > _source_horizon)
            or not record[0].visible_at(decision_at)
        ):
            raise ValueError("owned-equity evidence is unavailable")
        sources.append((seq, record[0].payload_ref))
    return OwnedEquityAudit(
        tuple(sources),
        fee_document_ref,
        baseline is not None,
        cash_matches,
        quantity_matches,
        equity,
        gross,
        fee_delta,
        net,
        tuple(sorted(issues)),
    )
