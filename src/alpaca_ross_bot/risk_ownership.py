"""Source-bound reported buying-power cap and conservative inventory ownership.

This qualifies inputs only. It neither proves settlement nor grants execution.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from urllib.parse import parse_qs, urlsplit

from .journal import Journal
from .native_risk_registry import TERMINAL as NATIVE_TERMINAL
from .native_risk_registry import NativeRiskRegistry
from .order_lifecycle import OrderProjection
from .order_reconciliation import OrderReconciler
from .paper_order_contract import decode_broker_json, validate_order_snapshot
from .risk_account_inputs import inspect_account_inputs

PROFILE = "ALPACA_PAPER_REPORTED_1X_AVAILABILITY_V1"


@dataclass(frozen=True, repr=False)
class OwnershipFacts:
    source_refs: tuple[int, ...]
    funds_profile: str
    reported_available_funds: Decimal | None
    ownership_verified: bool
    owned_long_positions: tuple[tuple[str, Fraction], ...]
    issues: tuple[str, ...]
    limitations: tuple[str, ...]
    runtime_authorized: bool = False

    def public_summary(self) -> dict[str, object]:
        return {
            "funds_profile": self.funds_profile,
            "available_funds_qualified": self.reported_available_funds is not None,
            "ownership_verified": self.ownership_verified,
            "owned_position_count": len(self.owned_long_positions),
            "issues": self.issues,
            "limitations": self.limitations,
            "runtime_authorized": False,
        }


def _positive(value: object) -> Fraction:
    if type(value) is not str:
        raise ValueError("quantity must be exact decimal text")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise ValueError("quantity must be positive and finite")
    return Fraction(number)


def qualify_ownership(
    journal: Journal,
    capture_sequence: int,
    *,
    account_ref: str,
    decision_at: datetime,
    session_open: datetime,
    max_age: timedelta,
    _intent_ids: tuple[str, ...] | None = None,
    _source_horizon: int | None = None,
    _native_registry: NativeRiskRegistry | None = None,
) -> OwnershipFacts:
    """Inspect immutable account bytes and independently replayed lifecycle proof.

    Broker buying power already accounts for broker holds: do not subtract those
    again. Unsent local intents instead veto qualification. Optional transfer
    omissions remain unknown, never invented zeros or proof of settled cash.
    """
    if _source_horizon is not None and (
        type(_source_horizon) is not int
        or _source_horizon < 0
        or capture_sequence > _source_horizon
    ):
        raise ValueError("account capture exceeds immutable source horizon")
    facts = inspect_account_inputs(
        journal,
        capture_sequence,
        account_ref=account_ref,
        decision_at=decision_at,
        session_open=session_open,
        max_age=max_age,
    )
    stored = journal.read_event(capture_sequence)
    assert stored is not None  # inspector verified existence and hash
    envelope = decode_broker_json(stored[1])
    assert isinstance(envelope, dict)
    requests = envelope["requests"]
    assert isinstance(requests, list)
    account: dict[str, object] = {}
    positions_views: list[list[object]] = []
    orders_views: list[list[object]] = []
    issues: set[str] = set()
    if _native_registry is not None:
        issues.update(_native_registry.issues)
    limitations = {"NOT_SETTLEMENT_PROOF", "NON_ATOMIC_HTTP_SNAPSHOTS"}
    for request in requests:
        assert isinstance(request, dict)
        path = str(request["path"])
        parsed = urlsplit(path)
        body = decode_broker_json(str(request["body"]))
        if path == "/v2/account":
            assert isinstance(body, dict)
            account = body
        elif parsed.path == "/v2/positions":
            if path != "/v2/positions":
                issues.add("FILTERED_POSITION_VIEW")
            assert isinstance(body, list)
            positions_views.append(body)
        elif parsed.path == "/v2/orders":
            query = parse_qs(parsed.query, keep_blank_values=True)
            if not (
                query.get("status") == ["open"]
                and query.get("limit") == ["500"]
                and query.get("nested") == ["false"]
                and query.get("direction", ["asc"]) == ["asc"]
                and set(query) <= {"status", "limit", "nested", "direction"}
            ):
                issues.add("FILTERED_OR_TRUNCATED_ORDER_VIEW")
            assert isinstance(body, list)
            orders_views.append(body)
            if len(body) >= 500:
                issues.add("ORDER_VIEW_PAGINATION_UNPROVEN")
    if len(positions_views) != 1 or len(orders_views) != 1:
        issues.add("ACCOUNT_COLLECTION_COVERAGE")
    money = dict(facts.money)
    cash, power, equity = (money[key] for key in ("cash", "buying_power", "equity"))
    funds_ok = (
        facts.active_usd_flags_clear
        and account.get("multiplier") == "1"
        and cash is not None
        and cash >= 0
        and power is not None
        and power >= 0
        and equity is not None
        and equity > 0
    )
    if not funds_ok:
        issues.add("REPORTED_1X_FUNDS_UNQUALIFIED")
    for field in ("pending_transfer_in", "pending_transfer_out", "unsettled_funds"):
        if money[field] is None:
            limitations.add("UNKNOWN_" + field.upper())
        elif money[field] != 0:
            issues.add("PENDING_CASHFLOW_REQUIRES_VERIFICATION")
    observed: dict[str, Fraction] = {}
    for row in positions_views[0] if len(positions_views) == 1 else []:
        if (
            not isinstance(row, dict)
            or row.get("asset_class") != "us_equity"
            or row.get("side") != "long"
            or type(row.get("symbol")) is not str
            or not str(row["symbol"]).strip()
            or row["symbol"] in observed
        ):
            issues.add("POSITION_SHAPE_OR_DUPLICATE")
            continue
        try:
            observed[str(row["symbol"])] = _positive(row.get("qty"))
        except (ValueError, ArithmeticError):
            issues.add("POSITION_QUANTITY_UNQUALIFIED")
    raw_orders = orders_views[0] if len(orders_views) == 1 else []
    # Only the incident binder supplies a census captured under the same private
    # write lock and preserved in its immutable event. Ordinary observations use
    # the current census. This seam never grants runtime qualification.
    intents = journal.read_order_intent_ids() if _intent_ids is None else _intent_ids
    if (
        type(intents) is not tuple
        or any(type(cid) is not str or not cid.strip() for cid in intents)
        or len(set(intents)) != len(intents)
        or not set(intents) <= set(journal.read_order_intent_ids())
    ):
        raise ValueError("invalid immutable intent census")
    refs: tuple[int, ...] = tuple(
        sorted(
            {
                capture_sequence,
                *(() if _native_registry is None else _native_registry.source_refs),
            }
        )
    )
    if observed or raw_orders or intents:
        state, lifecycle_refs = OrderReconciler.read_evidence_at(
            journal, account_ref, decision_at, through_sequence=_source_horizon
        )
        refs = tuple(sorted(set(refs + lifecycle_refs)))
        report = state.get("last_report")
        if (
            state.get("baseline") != {}
            or state.get("stream_gap") is not False
            or state.get("stream_status") != "SUBSCRIBED"
            or state.get("issues")
            or not isinstance(report, dict)
            or report.get("status")
            not in (
                {"MATCHED"} if _native_registry is None else {"MATCHED", "DIFFERENCES"}
            )
            or (_native_registry is None and report.get("differences"))
            or report.get("watermark") != state.get("watermark")
        ):
            issues.add("LIFECYCLE_OWNERSHIP_UNQUALIFIED")
        watermark = state.get("watermark")
        last = journal.read_event(watermark) if type(watermark) is int else None
        if (
            last is None
            or (_source_horizon is not None and last[0].sequence > _source_horizon)
            or decision_at - last[0].available_at > max_age
        ):
            issues.add("LIFECYCLE_EVIDENCE_STALE_OR_MISSING")
        else:
            last_item = decode_broker_json(last[1])
            if (
                not isinstance(last_item, dict)
                or last_item.get("kind") != "REST_BATCH"
                or not isinstance(last_item.get("intents"), dict)
                or set(last_item["intents"]) != set(intents)
            ):
                issues.add("LOCAL_INTENT_SOURCE_COVERAGE")
            if _native_registry is not None and (
                not isinstance(last_item, dict)
                or not _native_registry.covers_batch(last_item.get("batch"), observed)
            ):
                issues.add("LIFECYCLE_NATIVE_SOURCE_DISAGREEMENT")
        orders = state.get("orders")
        if not isinstance(orders, dict):
            issues.add("LIFECYCLE_ORDERS_UNQUALIFIED")
            orders = {}
        if set(orders) != set(intents):
            issues.add("LOCAL_INTENT_COVERAGE")
        projections = {
            cid: OrderProjection.from_dict(row)
            for cid, row in orders.items()
            if isinstance(row, dict)
        }
        expected: dict[str, Fraction] = {}
        expected_open: set[str] = set()
        for cid, projection in projections.items():
            if (
                projection.account_ref != account_ref
                or projection.issues
                or projection.state.value == "UNKNOWN"
            ):
                issues.add("UNRESOLVED_ORDER_OWNERSHIP")
            if projection.side not in {"buy", "sell"}:
                issues.add("UNSUPPORTED_ORDER_SIDE")
            signed = Fraction(projection.filled_quantity) * (
                1 if projection.side == "buy" else -1
            )
            expected[projection.symbol] = (
                expected.get(projection.symbol, Fraction()) + signed
            )
            if projection.state.value not in {
                "FILLED",
                "CANCELED",
                "EXPIRED",
                "REJECTED",
            }:
                expected_open.add(cid)
        if _native_registry is not None:
            if isinstance(report, dict) and any(
                not _native_registry.covers_difference(row, expected, observed)
                for row in report.get("differences", [])
            ):
                issues.add("LIFECYCLE_NATIVE_DIFFERENCE_UNCOVERED")
            for fill in _native_registry.fills:
                expected[fill.symbol] = (
                    expected.get(fill.symbol, Fraction()) - fill.quantity
                )
            expected_open.update(
                order.client_order_id
                for order in _native_registry.orders
                if order.status not in NATIVE_TERMINAL
            )
        if {key: value for key, value in expected.items() if value} != observed:
            issues.add("OWNED_POSITION_MISMATCH")
        seen: set[str] = set()
        for row in raw_orders:
            if not isinstance(row, dict) or type(row.get("client_order_id")) is not str:
                issues.add("OPEN_ORDER_INVALID")
                continue
            cid = str(row["client_order_id"])
            if cid in seen:
                issues.add("DUPLICATE_OPEN_ORDER")
            seen.add(cid)
            if _native_registry is not None and any(
                order.order_id == row.get("id") and order.client_order_id == cid
                for order in _native_registry.orders
            ):
                continue  # Independently exact-checked by the native registry.
            intent = journal.read_order_intent(cid)
            open_projection = projections.get(cid)
            if not isinstance(intent, dict) or open_projection is None:
                issues.add("FOREIGN_OPEN_ORDER")
                continue
            snapshot = validate_order_snapshot(
                str(intent.get("request_body")), json.dumps(row).encode()
            )
            if (
                snapshot.broker_order_id != open_projection.broker_order_id
                or snapshot.filled_quantity != open_projection.filled_quantity
                or snapshot.status != open_projection.raw_status
            ):
                issues.add("OPEN_ORDER_LIFECYCLE_MISMATCH")
        if seen != expected_open:
            issues.add("OPEN_ORDER_COVERAGE")
    ownership_issues = issues - {
        "REPORTED_1X_FUNDS_UNQUALIFIED",
        "PENDING_CASHFLOW_REQUIRES_VERIFICATION",
    }
    owned = not ownership_issues
    qualified = funds_ok and not issues and owned
    return OwnershipFacts(
        refs,
        PROFILE if qualified else "UNQUALIFIED",
        min(cash, power)
        if qualified and cash is not None and power is not None
        else None,
        owned,
        tuple(sorted(observed.items())) if owned else (),
        tuple(sorted(issues)),
        tuple(sorted(limitations)),
    )
