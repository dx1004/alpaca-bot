"""Interpret finite native OCO GET evidence without granting execution authority.

Local parser requirements are intentionally stronger than optional broker fields.
Failure to meet them means locally unqualified evidence, not an invalid broker
order. Even complete capture accounting proves neither owned inventory nor stop
coverage, and never proves a complete replacement or account-wide history.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from .journal import Journal, JournalError
from .paper_order_contract import decode_broker_json, paper_account_ref
from .posted_ledger import _exact, _timestamp

_AUTHORITY = (
    "source_admission_accepted",
    "protection_accepted",
    "runtime_authorized",
    "dispatch_authorized",
    "local_ownership_accepted",
    "formal_sample_eligible",
)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise ValueError("UTC native OCO decision clock required")
    return value


def _stamp(value: object) -> tuple[datetime, Fraction]:
    stamp, remainder = _timestamp(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError("UTC native OCO source clock required")
    return stamp, remainder


def _instant(value: object) -> Fraction:
    stamp, remainder = _stamp(value)
    epoch = datetime(1970, 1, 1, tzinfo=stamp.tzinfo)
    return Fraction((stamp - epoch) // timedelta(microseconds=1), 1_000_000) + remainder


def _uuid(value: object) -> str:
    if type(value) is not str:
        raise ValueError("native OCO UUID required")
    return str(UUID(value))


def _text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("native OCO exact nonempty text required")
    return value


def _rational(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


@dataclass(frozen=True, repr=False)
class NativeOrderFact:
    order_id: str
    client_order_id: str
    group_id: str
    group_role: str
    observed_order_class: str
    symbol: str
    side: str
    order_type: str
    time_in_force: str
    status: str
    quantity: Fraction
    filled_quantity: Fraction
    limit_price: Fraction | None
    stop_price: Fraction | None
    created_at: str
    updated_at: str
    submitted_at: str | None
    replaces: str | None
    replaced_by: str | None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = dict(self.__dict__)
        for key in ("quantity", "filled_quantity", "limit_price", "stop_price"):
            value = result[key]
            result[key] = None if value is None else _rational(value)
        result["reported_remaining_quantity"] = _rational(
            self.quantity - self.filled_quantity
        )
        return result


@dataclass(frozen=True, repr=False)
class NativeFillFact:
    activity_id: str
    order_id: str
    symbol: str
    side: str
    quantity: Fraction
    price: Fraction
    transaction_time: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__) | {
            "quantity": _rational(self.quantity),
            "price": _rational(self.price),
        }


@dataclass(frozen=True, repr=False)
class NativeOcoEvidence:
    account_ref: str
    evidence_ref: str
    capture_sequence: int
    source_horizon: int
    requested_order_id: str
    orders: tuple[NativeOrderFact, ...]
    fills: tuple[NativeFillFact, ...]
    exhausted_order_ids: tuple[str, ...]
    topology_observed: bool
    issues: tuple[str, ...]

    @property
    def accounting_complete(self) -> bool:
        """Only capture-local pagination/snapshot agreement; no owned accounting."""
        return self.topology_observed and not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "classification": "REPORTED_NATIVE_OCO_CAPTURE_NOT_OWNERSHIP",
            "account_ref": self.account_ref,
            "source_ref": {
                "sequence": self.capture_sequence,
                "hash": self.evidence_ref,
            },
            "source_horizon": self.source_horizon,
            "requested_order_id": self.requested_order_id,
            "orders": [order.to_dict() for order in self.orders],
            "fills": [fill.to_dict() for fill in self.fills],
            "fill_census": [
                {
                    "order_id": order.order_id,
                    "exhausted": order.order_id in self.exhausted_order_ids,
                    "known_fill_quantity": _rational(
                        sum(
                            (
                                f.quantity
                                for f in self.fills
                                if f.order_id == order.order_id
                            ),
                            Fraction(),
                        )
                    ),
                    "snapshot_filled_quantity": _rational(order.filled_quantity),
                }
                for order in self.orders
            ],
            "topology_observed": self.topology_observed,
            "accounting_complete": self.accounting_complete,
            "accounting_scope": "CAPTURE_ONLY_NOT_REPLACEMENT_OR_ACCOUNT_HISTORY",
            "issues": list(self.issues),
            "authority": dict.fromkeys(_AUTHORITY, False),
        }

    def public_summary(self) -> dict[str, Any]:
        return {
            "classification": "REPORTED_NATIVE_OCO_CAPTURE_NOT_OWNERSHIP",
            "order_count": len(self.orders),
            "known_fill_count": len(self.fills),
            "exhausted_order_count": len(self.exhausted_order_ids),
            "topology_observed": self.topology_observed,
            "capture_accounting_complete": self.accounting_complete,
            "issues": list(self.issues),
            "authority": dict.fromkeys(_AUTHORITY, False),
        }


def _order(row: object) -> NativeOrderFact:
    if not isinstance(row, dict):
        raise ValueError("native OCO order object missing")
    quantity, filled = (
        _exact(row.get("qty"), positive=True),
        _exact(row.get("filled_qty")),
    )
    if filled < 0:
        raise ValueError("negative reported filled quantity")
    order_type = _text(row.get("type"))
    limit = (
        None
        if row.get("limit_price") is None
        else _exact(row["limit_price"], positive=True)
    )
    stop = (
        None
        if row.get("stop_price") is None
        else _exact(row["stop_price"], positive=True)
    )
    if order_type not in {"limit", "stop", "stop_limit"}:
        raise ValueError("native OCO order type locally unsupported")
    if order_type in {"limit", "stop_limit"} and limit is None:
        raise ValueError("native OCO limit price missing")
    if order_type in {"stop", "stop_limit"} and stop is None:
        raise ValueError("native OCO stop price missing")
    created, updated = _text(row.get("created_at")), _text(row.get("updated_at"))
    if _instant(created) > _instant(updated):
        raise ValueError("native OCO order source clock reversed")
    submitted = row.get("submitted_at")
    if submitted is not None:
        submitted = _text(submitted)
        _stamp(submitted)
    return NativeOrderFact(
        _uuid(row.get("id")),
        _text(row.get("client_order_id")),
        _uuid(row.get("group_id")),
        _text(row.get("group_role")),
        _text(row.get("order_class")),
        _text(row.get("symbol")),
        _text(row.get("side")),
        order_type,
        _text(row.get("time_in_force")),
        _text(row.get("status")),
        quantity,
        filled,
        limit,
        stop,
        created,
        updated,
        submitted,
        None if row.get("replaces") is None else _uuid(row["replaces"]),
        None if row.get("replaced_by") is None else _uuid(row["replaced_by"]),
    )


def _topology(
    raw: bytes, requested_order_id: str
) -> tuple[tuple[NativeOrderFact, ...], tuple[str, ...]]:
    requested = _uuid(requested_order_id)
    issues: list[str] = []
    orders: list[NativeOrderFact] = []
    root = decode_broker_json(raw)
    if not isinstance(root, dict):
        return (), ("LOCAL_OCO_ROOT_UNQUALIFIED",)
    try:
        target = _order(root)
        orders.append(target)
        if (
            target.order_id != requested
            or target.observed_order_class != "oco"
            or target.group_role != "take_profit"
            or target.side != "sell"
            or target.order_type != "limit"
        ):
            issues.append("LOCAL_OCO_ROOT_UNQUALIFIED")
    except ValueError:
        issues.append("LOCAL_OCO_ROOT_UNQUALIFIED")
    legs = root.get("legs")
    if not isinstance(legs, list) or len(legs) != 1:
        issues.append("LOCAL_OCO_STOP_LEG_UNQUALIFIED")
    else:
        try:
            child = _order(legs[0])
            orders.append(child)
            if (
                not isinstance(legs[0], dict)
                or legs[0].get("legs") not in (None, [])
                or child.group_role != "stop_loss"
                or child.side != "sell"
                or child.order_type not in {"stop", "stop_limit"}
            ):
                issues.append("LOCAL_OCO_STOP_LEG_UNQUALIFIED")
        except ValueError:
            issues.append("LOCAL_OCO_STOP_LEG_UNQUALIFIED")
    if len(orders) == 2:
        target, child = orders
        if (
            target.order_id == child.order_id
            or target.group_id != child.group_id
            or target.symbol != child.symbol
        ):
            issues.append("LOCAL_OCO_GROUP_TOPOLOGY_UNQUALIFIED")
    return tuple(orders), tuple(dict.fromkeys(issues))


def native_oco_order_ids(raw: bytes, requested_order_id: str) -> tuple[str, ...]:
    """Return reported root then stop IDs, or refuse local parser eligibility."""
    orders, issues = _topology(raw, requested_order_id)
    if issues:
        raise ValueError("native OCO locally unqualified: " + ",".join(issues))
    return tuple(order.order_id for order in orders)


def _tagged(value: object) -> object:
    """Hash all activity fields without conflating decimal JSON and text values."""
    if isinstance(value, dict):
        return ["object", [[key, _tagged(v)] for key, v in sorted(value.items())]]
    if isinstance(value, list):
        return ["array", [_tagged(v) for v in value]]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    return [type(value).__name__, value]


def _fill(row: object, order: NativeOrderFact, decision: Fraction) -> NativeFillFact:
    if not isinstance(row, dict):
        raise ValueError("native OCO FILL object required")
    identity = _text(row.get("id"))
    if (
        row.get("activity_type") != "FILL"
        or _uuid(row.get("order_id")) != order.order_id
        or row.get("symbol") != order.symbol
        or row.get("side") != "sell"
    ):
        raise ValueError("native OCO FILL identity mismatch")
    timestamp = _text(row.get("transaction_time"))
    if _instant(timestamp) > decision:
        raise ValueError("native OCO FILL is future")
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(_tagged(row), separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    return NativeFillFact(
        identity,
        order.order_id,
        order.symbol,
        "sell",
        _exact(row.get("qty"), positive=True),
        _exact(row.get("price"), positive=True),
        timestamp,
        digest,
    )


@dataclass(frozen=True)
class _Request:
    path: str
    status: int | None
    body: bytes | None
    error: str | None
    completed_at: Fraction


def inspect_native_oco_capture(
    journal: Journal,
    capture_sequence: int,
    *,
    account_ref: str,
    decision_at: datetime,
    max_age: timedelta,
    through_sequence: int | None = None,
) -> NativeOcoEvidence:
    """Inspect a fixed immutable private capture; never mutate local/broker state.

    A partial collection preserves known positive fills, not a zero-fill census.
    All timestamps include sub-microsecond source remainders when applicable.
    """
    journal.require_private_order_state()
    _utc(decision_at)
    if type(max_age) is not timedelta or max_age <= timedelta(0):
        raise ValueError("positive native OCO capture age required")
    if type(capture_sequence) is not int or capture_sequence < 1:
        raise ValueError("positive native OCO capture sequence required")
    if type(account_ref) is not str or journal.read_paper_account_ref() != account_ref:
        raise JournalError("native OCO account is not bound to journal")
    tail = journal.read_evidence_sequence()
    horizon = tail if through_sequence is None else through_sequence
    if type(horizon) is not int or not capture_sequence <= horizon <= tail:
        raise ValueError("native OCO capture is outside source horizon")
    stored = journal.read_event(capture_sequence)
    if stored is None:
        raise JournalError("native OCO capture is missing")
    event, raw = stored
    if event.payload_ref != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise JournalError("native OCO capture payload hash differs")
    if (event.source_id, event.feed, event.event_kind) != (
        "ALPACA_NATIVE_OCO",
        "paper",
        "NATIVE_OCO_CAPTURE",
    ):
        raise JournalError("native OCO capture source mismatch")
    if not event.visible_at(decision_at) or event.external_clock_conflict:
        raise JournalError("native OCO capture unavailable at decision")
    capture = decode_broker_json(raw)
    fields = {
        "schema",
        "paper_endpoint",
        "account_ref",
        "requested_order_id",
        "started_at",
        "completed_at",
        "requests",
    }
    if not isinstance(capture, dict):
        raise JournalError("native OCO capture envelope mismatch")
    schema = capture.get("schema")
    if type(schema) is not int or schema not in {1, 2}:
        raise JournalError("native OCO capture version mismatch")
    if schema == 2:
        fields.add("transport_origin")
        origin = capture.get("transport_origin")
        if type(origin) is not str or origin not in {
            "BUILTIN_PAPER_HTTPS",
            "INJECTED_EXCHANGE",
        }:
            raise JournalError("native OCO capture transport origin mismatch")
    if set(capture) != fields:
        raise JournalError("native OCO capture envelope mismatch")
    if (
        capture["paper_endpoint"] != "https://paper-api.alpaca.markets"
        or capture["account_ref"] != account_ref
    ):
        raise JournalError("native OCO capture account/endpoint mismatch")
    requested = _uuid(capture["requested_order_id"])
    decision = _instant(decision_at.isoformat())
    start, completed = (
        _instant(capture["started_at"]),
        _instant(capture["completed_at"]),
    )
    if not (
        start <= completed <= _instant(event.processed_at.isoformat()) <= decision
        and start <= _instant(event.system_first_seen_at.isoformat())
    ):
        raise JournalError("native OCO capture chronology mismatch")
    requests = capture["requests"]
    if not isinstance(requests, list) or not requests:
        raise JournalError("native OCO capture requests missing")
    parsed: list[_Request] = []
    issues: list[str] = []
    previous = start
    age = Fraction(max_age // timedelta(microseconds=1), 1_000_000)
    for request in requests:
        if not isinstance(request, dict) or set(request) != {
            "method",
            "path",
            "status",
            "body",
            "error",
            "started_at",
            "completed_at",
        }:
            raise JournalError("native OCO request envelope mismatch")
        began, ended = (
            _instant(request["started_at"]),
            _instant(request["completed_at"]),
        )
        if not previous <= began <= ended <= completed <= decision:
            raise JournalError("native OCO request chronology mismatch")
        if decision - began > age or decision - ended > age:
            issues.append("CAPTURE_REQUEST_STALE")
        previous = ended
        if request["method"] != "GET" or type(request["path"]) is not str:
            raise JournalError("native OCO request method/path mismatch")
        status, error, body = request["status"], request["error"], request["body"]
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise JournalError("native OCO request status mismatch")
        if error is not None:
            _text(error)
        if (status is None) != (body is None) or (status is None) != (
            error is not None
        ):
            raise JournalError("native OCO request response/error mismatch")
        if body is not None and type(body) is not str:
            raise JournalError("native OCO request body encoding mismatch")
        try:
            decoded = None if body is None else base64.b64decode(body, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise JournalError("native OCO request body is not strict base64") from exc
        parsed.append(_Request(request["path"], status, decoded, error, ended))

    orders: tuple[NativeOrderFact, ...] = ()
    fills: dict[str, NativeFillFact] = {}
    exhausted: list[str] = []
    topology = False
    account = parsed[0]
    if account.path != "/v2/account":
        raise JournalError("native OCO first request is not account verification")
    if account.status != 200 or account.body is None:
        issues.append("ACCOUNT_READ_UNAVAILABLE")
    else:
        try:
            observed_account = paper_account_ref(account.body)
        except ValueError:
            issues.append("ACCOUNT_BODY_UNQUALIFIED")
        else:
            if observed_account != account_ref:
                raise JournalError("native OCO response account mismatch")
    authenticated = not any(code.startswith("ACCOUNT_") for code in issues)
    if not authenticated:
        if len(parsed) > 1:
            issues.append("REQUESTS_AFTER_UNQUALIFIED_ACCOUNT")
    elif len(parsed) < 2:
        issues.append("ORDER_READ_MISSING")
    else:
        order_request = parsed[1]
        if order_request.path != f"/v2/orders/{requested}?nested=true":
            raise JournalError("native OCO nested order request mismatch")
        if order_request.status != 200 or order_request.body is None:
            issues.append("ORDER_READ_UNAVAILABLE")
        else:
            try:
                orders, topology_issues = _topology(order_request.body, requested)
                issues.extend(topology_issues)
                topology = not topology_issues
            except ValueError:
                issues.append("ORDER_BODY_UNQUALIFIED")
        for order in orders:
            if order.replaces or order.replaced_by:
                issues.append("REPLACEMENT_HISTORY_UNRESOLVED")
            clocks = (order.created_at, order.updated_at, order.submitted_at)
            if any(_instant(clock) > decision for clock in clocks if clock is not None):
                issues.append("ORDER_SOURCE_CLOCK_FUTURE")
            if any(
                _instant(clock) > order_request.completed_at
                for clock in clocks
                if clock is not None
            ):
                issues.append("ORDER_SOURCE_CLOCK_AFTER_OBSERVATION")
            if order.filled_quantity > order.quantity:
                issues.append("SNAPSHOT_FILLED_EXCEEDS_REPORTED_QUANTITY")
        cursor_index = 2
        if topology:
            for order in orders:
                cursor: str | None = None
                seen_cursors: set[str] = set()
                while cursor_index < len(parsed):
                    query = [
                        ("activity_types", "FILL"),
                        ("order_id", order.order_id),
                        ("direction", "asc"),
                        ("page_size", "100"),
                    ]
                    if cursor is not None:
                        query.append(("page_token", cursor))
                    expected = "/v2/account/activities?" + urlencode(query)
                    page = parsed[cursor_index]
                    if page.path != expected:
                        break
                    cursor_index += 1
                    if page.status != 200 or page.body is None:
                        issues.append("FILL_PAGE_UNAVAILABLE")
                        break
                    try:
                        rows = decode_broker_json(page.body)
                        if not isinstance(rows, list) or len(rows) > 100:
                            raise ValueError("native FILL page shape")
                    except ValueError:
                        issues.append("FILL_PAGE_UNQUALIFIED")
                        break
                    if not rows:
                        exhausted.append(order.order_id)
                        break
                    for row in rows:
                        try:
                            fill = _fill(row, order, decision)
                        except ValueError:
                            issues.append("FILL_FACT_UNQUALIFIED")
                            continue
                        # A later decision must not erase a conflict with the
                        # original observations. Retain the reported fill; do
                        # not infer which provider/local clock is correct.
                        instant = _instant(fill.transaction_time)
                        if instant > page.completed_at:
                            issues.append("FILL_SOURCE_CLOCK_AFTER_OBSERVATION")
                        if instant > order_request.completed_at:
                            issues.append("FILL_AFTER_ORDER_SNAPSHOT")
                        prior = fills.get(fill.activity_id)
                        if prior is not None and prior != fill:
                            issues.append("FILL_IDENTITY_CONFLICT")
                        elif prior is None:
                            fills[fill.activity_id] = fill
                    try:
                        last = rows[-1]
                        next_cursor = _text(
                            last.get("id") if isinstance(last, dict) else None
                        )
                    except ValueError:
                        issues.append("FILL_CURSOR_UNQUALIFIED")
                        break
                    if next_cursor in seen_cursors:
                        issues.append("FILL_CURSOR_REPEATED")
                        break
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
                if order.order_id not in exhausted:
                    issues.append("FILL_CENSUS_INCOMPLETE")
                known = sum(
                    (
                        fill.quantity
                        for fill in fills.values()
                        if fill.order_id == order.order_id
                    ),
                    Fraction(),
                )
                if order.order_id in exhausted and known != order.filled_quantity:
                    issues.append("FILL_SNAPSHOT_QUANTITY_DISAGREEMENT")
            if cursor_index != len(parsed):
                issues.append("UNEXPECTED_CAPTURE_REQUESTS")
            totals = {
                order.order_id: sum(
                    (
                        fill.quantity
                        for fill in fills.values()
                        if fill.order_id == order.order_id
                    ),
                    Fraction(),
                )
                for order in orders
            }
            if sum(totals.values(), Fraction()) > orders[0].quantity:
                issues.append("GROUP_FILLS_EXCEED_ROOT_REPORTED_QUANTITY")
        elif len(parsed) > 2:
            issues.append("FILL_REQUESTS_WITHOUT_QUALIFIED_TOPOLOGY")
    return NativeOcoEvidence(
        account_ref,
        event.payload_ref,
        capture_sequence,
        horizon,
        requested,
        orders,
        tuple(fills.values()),
        tuple(exhausted),
        topology,
        tuple(dict.fromkeys(issues)),
    )
