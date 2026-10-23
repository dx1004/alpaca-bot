"""Bounded observed ledger rows and simple engineering roundtrip provenance.

This is not an input adapter for ``paper_performance``: source timestamp text
and its exact remainder survive, without inventing a study origin, execution
ordering, fee classification, account-day completeness or posting finality.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .account_risk_owner import AccountRiskOwner
from .incident_risk import IncidentRiskRequest
from .journal import Journal, JournalError
from .order_lifecycle import OrderProjection
from .order_reconciliation import OrderReconciler
from .paper_order_contract import decode_broker_json, validate_saved_request
from .posted_ledger import _exact, _timestamp, inspect_posted_ledger
from .risk_account_inputs import ENDPOINT, inspect_account_inputs


@dataclass(frozen=True)
class LedgerSource:
    declared_journal_scope: str
    sequence: int
    payload_ref: str
    available_at: datetime


@dataclass(frozen=True, repr=False)
class ObservedActivity:
    source: LedgerSource
    occurrences: tuple[tuple[int, int], ...]
    canonical_row: bytes
    row_hash: str
    activity_id: str | None
    activity_type: str | None
    broker_order_id: str | None
    symbol: str | None
    side: str | None
    quantity: Fraction | None
    price: Fraction | None
    net_amount: Fraction | None
    transaction_time: str | None
    parsed_time: datetime | None
    submicrosecond_remainder: Fraction | None
    issues: tuple[str, ...]


@dataclass(frozen=True, repr=False)
class ObservedRoundtrip:
    entry_client_order_id: str
    symbol: str
    filled_quantity: Fraction
    client_broker_ids: tuple[tuple[str, str], ...]
    execution_ids: tuple[str, ...]
    source_refs: tuple[LedgerSource, ...]
    origin: str = "ENGINEERING_TEST"
    classification: str = "OBSERVED_SIMPLE_ENGINEERING_ROUNDTRIP_NOT_STUDY_OWNERSHIP"


@dataclass(frozen=True, repr=False)
class PaperLedgerSources:
    declared_journal_scope: str
    account_ref: str
    through_sequence: int
    decision_at: datetime
    capture_source: LedgerSource
    source_refs: tuple[LedgerSource, ...]
    activities: tuple[ObservedActivity, ...]
    observed_roundtrips: tuple[ObservedRoundtrip, ...]
    issues: tuple[str, ...]
    classification: str = "OBSERVED_LEDGER_NOT_FORMAL_PERFORMANCE_INPUTS"
    journal_scope_authenticated: bool = False
    formal_origin: None = None
    activity_finality_accepted: bool = False
    account_day_census_accepted: bool = False
    source_admission_accepted: bool = False
    formal_sample_eligible: bool = False
    runtime_authorized: bool = False
    dispatch_authorized: bool = False

    def public_summary(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "activity_observation_count": len(self.activities),
            "observed_engineering_roundtrip_count": len(self.observed_roundtrips),
            "issues": list(self.issues),
            "journal_scope_authenticated": False,
            "formal_origin": None,
            "activity_finality_accepted": False,
            "account_day_census_accepted": False,
            "source_admission_accepted": False,
            "formal_sample_eligible": False,
            "runtime_authorized": False,
            "dispatch_authorized": False,
        }


def _json(value: object) -> bytes:
    # Broker decoder uses Decimal for JSON fractional numbers. Preserve their
    # numeric type and exact value; never float-coerce or turn them into strings.
    if type(value) is Decimal:
        if not value.is_finite():
            raise ValueError("nonfinite raw JSON number")
        return str(value).encode()
    if isinstance(value, dict):
        return (
            b"{"
            + b",".join(
                _json(key) + b":" + _json(item) for key, item in sorted(value.items())
            )
            + b"}"
        )
    if isinstance(value, list):
        return b"[" + b",".join(_json(item) for item in value) + b"]"
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def _text(value: object) -> str | None:
    return value if type(value) is str and value and value == value.strip() else None


def _activity(
    row: dict[str, Any],
    source: LedgerSource,
    occurrences: tuple[tuple[int, int], ...],
    decision_at: datetime,
) -> ObservedActivity:
    issues: set[str] = set()
    identity, kind = _text(row.get("id")), _text(row.get("activity_type"))
    if identity is None or kind is None:
        issues.add("ACTIVITY_IDENTITY_UNQUALIFIED")
    qty = price = net = remainder = None
    stamp = None
    raw_time = _text(row.get("transaction_time"))
    if kind == "FILL":
        try:
            qty, price = (
                _exact(row.get("qty"), positive=True),
                _exact(row.get("price"), positive=True),
            )
            stamp, remainder = _timestamp(raw_time)
            delta = stamp - decision_at
            if Fraction(delta // timedelta(microseconds=1), 1_000_000) + remainder > 0:
                issues.add("FUTURE_EXECUTION")
        except (ValueError, ArithmeticError):
            issues.add("FILL_VALUES_UNQUALIFIED")
        if (
            _text(row.get("order_id")) is None
            or _text(row.get("symbol")) is None
            or row.get("side") not in {"buy", "sell"}
        ):
            issues.add("FILL_IDENTITY_UNQUALIFIED")
    else:
        issues.add("NTA_ATTRIBUTION_UNQUALIFIED")
        try:
            net = _exact(row.get("net_amount"))
        except (ValueError, ArithmeticError):
            issues.add("NTA_AMOUNT_UNQUALIFIED")
        if kind not in {"FEE", "CSD", "CSW"}:
            issues.add("UNKNOWN_NTA_CLASSIFICATION")
    raw = _json(row)
    return ObservedActivity(
        source,
        occurrences,
        raw,
        "sha256:" + hashlib.sha256(raw).hexdigest(),
        identity,
        kind,
        _text(row.get("order_id")),
        _text(row.get("symbol")),
        _text(row.get("side")),
        qty,
        price,
        net,
        raw_time,
        stamp,
        remainder,
        tuple(sorted(issues)),
    )


def _signature(row: dict[str, Any]) -> tuple[object, ...]:
    return (
        row["order_id"],
        row["symbol"],
        row["side"],
        _exact(row["qty"], positive=True),
        _exact(row["price"], positive=True),
        _timestamp(row["transaction_time"]),
    )


def inspect_paper_ledger_sources(
    journal: Journal,
    capture_sequence: int,
    *,
    journal_scope: str,
    account_ref: str,
    decision_at: datetime,
    through_sequence: int,
    max_age: timedelta,
    fee_policy: Path,
) -> PaperLedgerSources:
    """Read one snapshot; every outward reference carries the declared scope.

    Integrity/account/visibility errors raise. Valid raw rows remain observable
    when ownership or accounting qualification fails. A caller-owned transaction
    is retained; this function never writes, commits or repairs projections.
    """
    if (
        _text(journal_scope) is None
        or any(ord(char) < 32 or ord(char) == 127 for char in journal_scope)
        or type(decision_at) is not datetime
        or decision_at.utcoffset() != timedelta(0)
        or type(max_age) is not timedelta
        or max_age <= timedelta(0)
    ):
        raise JournalError("exact scope, UTC decision and positive age required")
    owned_transaction = not journal._db.in_transaction
    if owned_transaction:
        journal._db.execute("BEGIN")
    try:
        journal.require_private_order_state()
        if (
            journal.read_paper_account_ref() != account_ref
            or type(through_sequence) is not int
            or not 1 <= through_sequence <= journal.read_evidence_sequence()
            or type(capture_sequence) is not int
            or not 1 <= capture_sequence <= through_sequence
        ):
            raise JournalError("bound account and existing source horizon required")

        def source(sequence: int) -> LedgerSource:
            stored = journal.read_event(sequence)
            if (
                stored is None
                or sequence > through_sequence
                or not stored[0].visible_at(decision_at)
                or stored[0].external_clock_conflict
            ):
                raise JournalError("ledger source unavailable at fixed prefix")
            event = stored[0]
            return LedgerSource(
                journal_scope, sequence, event.payload_ref, event.available_at
            )

        capture_source = source(capture_sequence)
        # Only the account/envelope validator is used here. Its before-open
        # interpretation is not consumed, and no session boundary is invented.
        inspect_account_inputs(
            journal,
            capture_sequence,
            account_ref=account_ref,
            decision_at=decision_at,
            session_open=decision_at,
            max_age=max_age,
        )
        stored = journal.read_event(capture_sequence)
        assert stored is not None
        envelope = decode_broker_json(stored[1])
        assert isinstance(envelope, dict)
        rows: dict[bytes, tuple[dict[str, Any], list[tuple[int, int]]]] = {}
        for request_index, request in enumerate(envelope["requests"]):
            if urlsplit(request["path"]).path != "/v2/account/activities":
                continue
            decoded = decode_broker_json(request["body"])
            assert isinstance(decoded, list)
            for row_index, row in enumerate(decoded):
                raw = _json(row)
                rows.setdefault(raw, (row, []))[1].append((request_index, row_index))
        activities = tuple(
            _activity(row, capture_source, tuple(occurrences), decision_at)
            for row, occurrences in rows.values()
        )
        issues = {issue for row in activities for issue in row.issues}
        by_id: dict[str, ObservedActivity] = {}
        for row in activities:
            if row.activity_id is not None:
                if row.activity_id in by_id:
                    issues.add("ACTIVITY_ID_CONFLICT")
                by_id[row.activity_id] = row
        risk, risk_refs = AccountRiskOwner.read_evidence_at(
            journal,
            account_ref,
            decision_at,
            fee_policy,
            through_sequence=through_sequence,
        )
        lifecycle, lifecycle_refs = OrderReconciler.read_evidence_at(
            journal,
            account_ref,
            decision_at,
            through_sequence=through_sequence,
        )
        refs = {capture_sequence, *risk_refs, *lifecycle_refs}
        expected_risk = {
            event.sequence
            for event, _ in journal.read_account_risk_events()
            if event.sequence <= through_sequence
        }
        expected_lifecycle = {
            event.sequence
            for event, _ in journal.read_order_events(through_sequence=through_sequence)
        }
        if set(risk_refs) != expected_risk or not expected_lifecycle <= set(
            lifecycle_refs
        ):
            issues.add("SOURCE_PREFIX_UNAVAILABLE")
        snapshot = None if risk is None else risk.get("last_snapshot")
        if isinstance(snapshot, dict):
            refs.update(row[0] for row in snapshot.get("source_refs", []))
        sources = tuple(source(seq) for seq in sorted(refs))
        linked: tuple[ObservedRoundtrip, ...] = ()
        if not issues:
            linked = _roundtrips(
                journal,
                account_ref,
                decision_at,
                through_sequence,
                max_age,
                fee_policy,
                capture_sequence,
                risk,
                risk_refs,
                lifecycle,
                activities,
                sources,
                issues,
            )
        if not linked:
            issues.add("NO_QUALIFIED_OBSERVED_ROUNDTRIP")
        if risk is not None and risk.get("daily_stop_latched") is True:
            issues.add("RISK_DAILY_STOP_LATCHED_NO_TRADING_CLEARANCE")
        issues.update(
            {
                "POSTED_ACTIVITY_FINALITY_UNVERIFIED",
                "ACCOUNT_DAY_CENSUS_UNVERIFIED",
                "FORMAL_ORIGIN_UNVERIFIED",
                "NATIVE_GROUP_OWNERSHIP_UNSUPPORTED",
            }
        )
        return PaperLedgerSources(
            journal_scope,
            account_ref,
            through_sequence,
            decision_at,
            capture_source,
            sources,
            activities,
            linked,
            tuple(sorted(issues)),
        )
    finally:
        if owned_transaction:
            journal._db.rollback()


def _roundtrips(
    journal: Journal,
    account_ref: str,
    decision_at: datetime,
    horizon: int,
    max_age: timedelta,
    fee_policy: Path,
    capture_sequence: int,
    risk: dict[str, Any] | None,
    risk_refs: tuple[int, ...],
    lifecycle: dict[str, object],
    activities: tuple[ObservedActivity, ...],
    sources: tuple[LedgerSource, ...],
    issues: set[str],
) -> tuple[ObservedRoundtrip, ...]:
    snapshot = None if risk is None else risk.get("last_snapshot")
    if (
        risk is None
        or not isinstance(snapshot, dict)
        or not snapshot.get("qualified")
        or snapshot.get("issues") != []
        or risk.get("source_snapshot_qualified") is not True
        or not set(risk.get("issues", [])) <= {"DAILY_STOP_LATCHED"}
        or risk.get("veto") is not False
    ):
        issues.add("RISK_OWNERSHIP_UNQUALIFIED")
        return ()
    selection = IncidentRiskRequest.from_dict(snapshot["selection"])
    if selection.capture_sequence != capture_sequence:
        issues.add("OWNER_CAPTURE_DIFFERS")
        return ()
    if any(
        not timedelta(0) <= decision_at - datetime.fromisoformat(stamp) <= max_age
        for stamp in (snapshot["observed_at"], risk["at"])
    ):
        issues.add("RISK_SNAPSHOT_STALE_OR_FUTURE")
        return ()
    posted = inspect_posted_ledger(
        journal,
        capture_sequence,
        account_ref=account_ref,
        decision_at=decision_at,
        session_open=decision_at,
        max_age=max_age,
        fee_policy=fee_policy,
    )
    if not posted.pagination_exhausted:
        issues.add("ACTIVITY_CENSUS_INCOMPLETE")
    if lifecycle.get("orders") != snapshot.get("orders"):
        issues.add("OWNER_LIFECYCLE_DISAGREEMENT")
    raw_orders = lifecycle["orders"]
    assert isinstance(raw_orders, dict)
    orders = {cid: OrderProjection.from_dict(row) for cid, row in raw_orders.items()}
    if len({order.broker_order_id for order in orders.values()}) != len(orders):
        issues.add("BROKER_ID_CONFLICT")
    watermark = lifecycle.get("watermark")
    stored = journal.read_event(watermark) if type(watermark) is int else None
    lifecycle_item = None if stored is None else decode_broker_json(stored[1])
    batch = lifecycle_item.get("batch") if isinstance(lifecycle_item, dict) else None
    if not isinstance(batch, dict) or not isinstance(batch.get("activities"), dict):
        issues.add("LIFECYCLE_CENSUS_UNAVAILABLE")
        return ()
    target: dict[str, tuple[object, ...]] = {}
    for batch_rows in batch["activities"].values():
        for row in batch_rows:
            identity, signature = row["id"], _signature(row)
            if identity in target and target[identity] != signature:
                issues.add("LIFECYCLE_EXECUTION_ID_CONFLICT")
            target[identity] = signature
    actual = {
        row.activity_id: _signature(json.loads(row.canonical_row))
        for row in activities
        if row.activity_type == "FILL"
    }
    if actual != target:
        issues.add("FILL_SOURCE_DISAGREEMENT")
    by_broker = {order.broker_order_id: order for order in orders.values()}
    for row in activities:
        order = by_broker.get(row.broker_order_id or "")
        if order is None or (row.symbol, row.side) != (order.symbol, order.side):
            issues.add("UNOWNED_EXECUTION")
    if issues:
        return ()
    result = []
    for cid, completed in risk["completed"].items():
        if completed["filled"] <= 0:
            continue
        origin = _original_held(journal, account_ref, cid, risk_refs, fee_policy)
        if origin is None:
            issues.add("POSITIVE_RESERVATION_ORIGIN_UNAVAILABLE")
            continue
        ids = set(completed["order_ids"])
        selected = [
            row
            for row in activities
            if row.broker_order_id
            in {orders[key].broker_order_id for key in ids if key in orders}
        ]
        if (
            ids != {cid, *origin["exits"]}
            or not ids <= orders.keys()
            or any(
                orders[key].state.value
                not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
                or orders[key].issues
                for key in ids
            )
        ):
            issues.add("COMPLETED_SCOPE_LIFECYCLE_UNQUALIFIED")
            continue
        valid = True
        for key in ids:
            order = orders[key]
            fills = [
                row for row in selected if row.broker_order_id == order.broker_order_id
            ]
            if (
                order.account_ref != account_ref
                or order.symbol != origin["symbol"]
                or order.side != ("buy" if key == cid else "sell")
                or sum((row.quantity or Fraction(0) for row in fills), Fraction(0))
                != Fraction(order.filled_quantity)
            ):
                valid = False
        bought = sum(
            (row.quantity or Fraction(0) for row in selected if row.side == "buy"),
            Fraction(0),
        )
        sold = sum(
            (row.quantity or Fraction(0) for row in selected if row.side == "sell"),
            Fraction(0),
        )
        if not valid or bought != sold or bought != completed["filled"]:
            issues.add("COMPLETED_FILL_CENSUS_DISAGREEMENT")
            continue
        result.append(
            ObservedRoundtrip(
                cid,
                origin["symbol"],
                bought,
                tuple((key, orders[key].broker_order_id) for key in sorted(ids)),
                tuple(
                    row.activity_id for row in selected if row.activity_id is not None
                ),
                sources,
            )
        )
    return tuple(result)


def _original_held(
    journal: Journal,
    account_ref: str,
    cid: str,
    risk_refs: tuple[int, ...],
    fee_policy: Path,
) -> dict[str, Any] | None:
    """Recover canonical historical held state, including explicitly bound exits."""
    held: dict[str, Any] | None = None
    created = False
    for seq in risk_refs:
        stored = journal.read_event(seq)
        assert stored is not None
        state, _ = AccountRiskOwner.read_evidence_at(
            journal,
            account_ref,
            stored[0].available_at,
            fee_policy,
            through_sequence=seq,
        )
        current = None if state is None else state.get("held")
        if isinstance(current, dict) and current.get("client_order_id") == cid:
            if held is None:
                item = decode_broker_json(stored[1])
                assert isinstance(item, dict)
                created = (
                    item.get("kind") == "RESERVE"
                    and state is not None
                    and state["decision"].get("quantity", 0) > 0
                    and state.get("issues") == []
                )
            held = current
        if state is not None and cid in state["completed"]:
            # Intent hashes are witnessed in the immutable completion census;
            # the canonical reader already checks them against current storage.
            item = decode_broker_json(stored[1])
            assert isinstance(item, dict)
            census = {row["id"]: row for row in item["census"]}
            if not created or held is None:
                return None
            for key in {cid, *held["exits"]}:
                intent = journal.read_order_intent(key)
                if (
                    key not in census
                    or not isinstance(intent, dict)
                    or intent.get("purpose") != "ENGINEERING_TEST"
                    or intent.get("paper_endpoint") != ENDPOINT
                    or intent.get("paper_account_ref") != account_ref
                ):
                    return None
                request = validate_saved_request(intent["request_body"])
                if request["client_order_id"] != key:
                    return None
            return held
    return None
