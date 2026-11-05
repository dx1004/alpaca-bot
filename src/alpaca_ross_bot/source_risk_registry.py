"""Origin-qualified reported source-close facts; never completion or release."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any, cast
from urllib.parse import urlsplit

from .journal import Journal, JournalError
from .native_risk_registry import NativeRiskRegistry
from .paper_order_contract import decode_broker_json

POLICY = "SOURCE_HTTP_ACCOUNT_RISK_OBSERVATION_V1"
TERMINAL = {"filled", "canceled", "expired", "rejected"}


@dataclass(frozen=True, repr=False)
class SourceOrderFact:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    status: str
    quantity: Fraction
    filled_quantity: Fraction
    raw: dict[str, Any]


@dataclass(frozen=True, repr=False)
class SourceRiskRegistry:
    context: dict[str, Any]
    source_orders: tuple[SourceOrderFact, ...]
    source_fills: tuple[Any, ...]
    source_refs: tuple[int, ...]
    issues: tuple[str, ...]
    origin: str
    native: NativeRiskRegistry | None
    exit_by_scope: tuple[tuple[str, Fraction], ...]

    @property
    def orders(self) -> tuple[Any, ...]:
        return (() if self.native is None else self.native.orders) + self.source_orders

    @property
    def fills(self) -> tuple[Any, ...]:
        return (() if self.native is None else self.native.fills) + self.source_fills

    @property
    def source_cids(self) -> set[str]:
        return {row.client_order_id for row in self.source_orders}

    def covers_difference(
        self,
        difference: dict[str, str],
        simple_expected: dict[str, Fraction],
        observed: dict[str, Fraction],
    ) -> bool:
        if self.issues:
            return False
        if difference.get("code") == "INTENT_NOT_SENT":
            return difference.get("subject") in self.source_cids
        if difference.get("code") == "EXTERNAL_ORDER":
            return difference.get("subject") in {
                row.client_order_id for row in self.orders if row.status not in TERMINAL
            }
        if difference.get("code") == "POSITION_MISMATCH":
            symbol = difference.get("subject", "")
            exited = sum(
                (fill.quantity for fill in self.fills if fill.symbol == symbol),
                Fraction(),
            )
            try:
                return (
                    exited > 0
                    and Fraction(difference["expected"])
                    == simple_expected.get(symbol, Fraction())
                    and Fraction(difference["observed"])
                    == observed.get(symbol, Fraction())
                    and simple_expected.get(symbol, Fraction()) - exited
                    == observed.get(symbol, Fraction())
                )
            except (KeyError, ValueError):
                return False
        return False

    def covers_batch(self, batch: object, observed: dict[str, Fraction]) -> bool:
        if (
            self.issues
            or not isinstance(batch, dict)
            or not isinstance(batch.get("open_orders"), list)
        ):
            return False
        try:
            positions = {
                row["symbol"]: Fraction(row["qty"])
                for row in batch["positions"]
                if row["side"] == "long" and row["asset_class"] == "us_equity"
            }
            if positions != observed or len(positions) != len(batch["positions"]):
                return False
            if self.native is not None and not self.native.covers_batch(
                batch, observed
            ):
                return False
            known = {order.order_id: order for order in self.source_orders}
            seen: set[str] = set()
            for row in batch["open_orders"]:
                if row.get("id") in known:
                    order = known[row["id"]]
                    if (
                        row != order.raw
                        or order.status in TERMINAL
                        or order.order_id in seen
                    ):
                        return False
                    seen.add(order.order_id)
            return seen == {
                order.order_id
                for order in self.source_orders
                if order.status not in TERMINAL
            }
        except (KeyError, ValueError, TypeError):
            return False


def _account_activities(capture: dict[str, Any]) -> dict[str, dict[str, object]]:
    from .native_oco_evidence import _tagged

    rows_by_id: dict[str, dict[str, object]] = {}
    signatures: dict[str, object] = {}
    for request in capture["requests"]:
        if urlsplit(request["path"]).path != "/v2/account/activities":
            continue
        rows = decode_broker_json(request["body"])
        if not isinstance(rows, list):
            raise JournalError("source account activity page malformed")
        for row in rows:
            if not isinstance(row, dict) or type(row.get("id")) is not str:
                raise JournalError("source account activity identity malformed")
            identity = row["id"]
            signature = _tagged(row)
            if identity in signatures and signatures[identity] != signature:
                raise JournalError("source account activity contents changed")
            signatures[identity] = signature
            rows_by_id[identity] = row
    return rows_by_id


def _require_fresh_capture(
    packet: dict[str, Any], decision_at: datetime, max_age: timedelta
) -> None:
    # Raw clocks/schema are already checked by the canonical capture reader.
    # A fresh final page cannot refresh an older account/order observation.
    clocks = [packet["completed_at"]]
    for request in packet["requests"]:
        clocks.extend((request["started_at"], request["completed_at"]))
    if any(
        not timedelta(0) <= decision_at - datetime.fromisoformat(clock) <= max_age
        for clock in clocks
    ):
        raise JournalError("source GET evidence stale")


def derive_source_registry(
    journal: Journal,
    context: dict[str, Any],
    *,
    capture_sequence: int,
    decision_at: datetime,
    max_age: timedelta,
    through_sequence: int,
    native: NativeRiskRegistry | None = None,
) -> SourceRiskRegistry:
    from ._causal_risk_memo import causal_risk_verification_scope

    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            return _derive_source_registry(
                journal,
                context,
                capture_sequence=capture_sequence,
                decision_at=decision_at,
                max_age=max_age,
                through_sequence=through_sequence,
                native=native,
            )
    finally:
        if owns:
            journal._db.rollback()


def _derive_source_registry(
    journal: Journal,
    context: dict[str, Any],
    *,
    capture_sequence: int,
    decision_at: datetime,
    max_age: timedelta,
    through_sequence: int,
    native: NativeRiskRegistry | None = None,
) -> SourceRiskRegistry:
    # Pure bounded readers, never a current-risk or OCO owner constructor.
    from .crossed_stop_attribution import (
        _initial,
        read_source_http_close_attributions_at,
    )
    from .crossed_stop_fills import derive_source_http_close_fills
    from .native_oco_evidence import NativeFillFact, _fill, _instant
    from .risk_account_inputs import KIND as ACCOUNT_KIND
    from .risk_account_inputs import SOURCE as ACCOUNT_SOURCE
    from .source_close_http import read_source_close_http_at
    from .source_close_http_capture import read_source_close_http_captures_at

    if (
        type(max_age) is not timedelta
        or max_age <= timedelta(0)
        or type(through_sequence) is not int
        or not 0 <= through_sequence <= journal.read_evidence_sequence()
    ):
        raise JournalError("positive source age and bounded horizon required")
    if context.get("policy") != POLICY:
        raise JournalError("versioned source risk context required")
    stored = journal.read_event(capture_sequence)
    if (
        stored is None
        or capture_sequence > through_sequence
        or not stored[0].visible_at(decision_at)
        or (stored[0].source_id, stored[0].feed, stored[0].event_kind)
        != (ACCOUNT_SOURCE, "paper", ACCOUNT_KIND)
        or stored[0].external_clock_conflict
        or stored[0].payload_ref != "sha256:" + hashlib.sha256(stored[1]).hexdigest()
    ):
        raise JournalError("source account capture unavailable")
    capture = decode_broker_json(stored[1])
    if (
        not isinstance(capture, dict)
        or capture.get("schema") != "PAPER_RISK_INPUT_CAPTURE_V2"
        or capture.get("transport_origin") != "INJECTED_EXCHANGE"
        or capture.get("account_ref") != journal.read_paper_account_ref()
    ):
        raise JournalError("source accounting requires matching V2 HTTP origin")
    if native is not None and native.origin != capture["transport_origin"]:
        raise JournalError("source/native account origin differs")
    refs = {capture_sequence, *(() if native is None else native.source_refs)}
    issues = set(() if native is None else native.issues)
    orders: dict[str, SourceOrderFact] = {}
    fills: dict[str, Any] = {}
    exits: dict[str, Fraction] = {}
    episodes = context.get("episodes")
    if (
        type(episodes) is not list
        or not episodes
        or len({row["entry_scope"] for row in episodes}) != len(episodes)
    ):
        raise JournalError("original source risk episodes required")
    all_attempts: list[dict[str, Any]] | None = None
    all_claims: set[str] = set()
    for episode in episodes:
        initial = _initial(journal, episode["binding"])
        if all_attempts is None:
            all_attempts = read_source_close_http_at(
                journal,
                initial,
                through_sequence=through_sequence,
                decision_at=decision_at,
            )
        index = read_source_http_close_attributions_at(
            journal, initial, through_sequence=through_sequence, decision_at=decision_at
        )
        all_claims.update(index["cid_claims"])
        if index["latched"] or index["conflicts"]:
            raise JournalError("source attribution index conflict")
        packets = read_source_close_http_captures_at(
            journal, initial, through_sequence=through_sequence, decision_at=decision_at
        )
        expected = episode["source_close_bindings"]
        scoped = {
            cid: claim
            for cid, claim in index["cid_claims"].items()
            if claim["binding"]["entry_scope"] == initial.entry_scope
        }
        if set(scoped) != set(expected):
            raise JournalError("source attribution claim coverage differs")
        for cid, claim in scoped.items():
            operation = next(
                (
                    row
                    for row in index["observations"]
                    if row["observation_ref"] == claim["latest_ref"]
                ),
                None,
            )
            if operation is None:
                raise JournalError("source attribution observation missing")
            proof = derive_source_http_close_fills(
                journal,
                initial,
                client_order_id=cid,
                capture_sequences=tuple(operation["command"]["capture_sequences"]),
                through_sequence=through_sequence,
                decision_at=decision_at,
            )
            if (
                not proof["component_attributed"]
                or proof["issues"]
                or proof["origin"] != capture["transport_origin"]
            ):
                raise JournalError("source attribution current proof unqualified")
            prior = operation["proof"]
            for key in (
                "broker_order_id",
                "original_portions",
                "requested_quantity",
                "executions",
                "capture_refs",
                "attempt_ref",
                "outcome_ref",
                "handoff_ref",
                "preparation_ref",
                "entry_reservation_ref",
            ):
                if proof[key] != prior[key]:
                    raise JournalError("source attribution index is stale or changed")
            bound = expected[cid]
            if proof["original_portions"] != bound["portions"] or any(
                proof[key] != bound[key]
                for key in ("handoff_ref", "preparation_ref", "entry_reservation_ref")
            ):
                raise JournalError("source attribution basket reparented")
            packet = next(
                packet
                for packet in packets
                if packet["capture_ref"] == proof["capture_refs"][-1]
            )
            _require_fresh_capture(packet, decision_at, max_age)
            detail = next(
                row
                for row in packet["requests"]
                if row["path"] == f"/v2/orders/{proof['broker_order_id']}?nested=true"
            )
            raw = decode_broker_json(
                base64.b64decode(detail["body_base64"], validate=True)
            )
            if not isinstance(raw, dict):
                raise JournalError("source raw order missing")
            order = SourceOrderFact(
                proof["broker_order_id"],
                cid,
                initial.symbol,
                "sell",
                raw["status"],
                Fraction(raw["qty"]),
                Fraction(raw["filled_qty"]),
                raw,
            )
            if order.order_id in orders or any(
                other.client_order_id == cid for other in orders.values()
            ):
                raise JournalError("duplicate source order identity")
            orders[order.order_id] = order
            exits[initial.entry_scope] = (
                exits.get(initial.entry_scope, Fraction()) + order.filled_quantity
            )
            for execution in proof["executions"]:
                fill = NativeFillFact(
                    execution["activity_id"],
                    order.order_id,
                    order.symbol,
                    "sell",
                    Fraction(execution["quantity"]),
                    Fraction(execution["price"]),
                    execution["transaction_time"],
                    execution["content_hash"],
                )
                if fill.activity_id in fills:
                    raise JournalError("duplicate source activity identity")
                fills[fill.activity_id] = fill
            refs.add(claim["latest_ref"]["sequence"])
            for ref in (
                proof["handoff_ref"],
                proof["preparation_ref"],
                proof["entry_reservation_ref"],
                proof["emergency_binding_ref"],
                proof["prior_risk_ref"],
            ):
                refs.add(ref[0])
            for ref in (
                proof["attempt_ref"],
                proof["outcome_ref"],
                *proof["capture_refs"],
            ):
                refs.add(ref["sequence"])
    covered_cids = {order.client_order_id for order in orders.values()}
    if (
        all_claims != covered_cids
        or {row["precall"]["client_order_id"] for row in all_attempts or []}
        != covered_cids
    ):
        raise JournalError("source account HTTP commitments or claims uncovered")
    if native is not None and (
        {row.order_id for row in native.orders} & set(orders)
        or {fill.activity_id for fill in native.fills} & set(fills)
    ):
        raise JournalError("source/native execution identity collision")
    rows = _account_activities(capture)
    for identity, fill in fills.items():
        row = rows.get(identity)
        try:
            if (
                row is None
                or _fill(
                    row,
                    cast(Any, orders[fill.order_id]),
                    _instant(decision_at.isoformat()),
                )
                != fill
            ):
                issues.add("SOURCE_ACCOUNT_ACTIVITY_DISAGREEMENT")
        except ValueError:
            issues.add("SOURCE_ACCOUNT_ACTIVITY_DISAGREEMENT")
    if any(
        row.get("activity_type") == "FILL"
        and row.get("order_id") in orders
        and identity not in fills
        for identity, row in rows.items()
    ):
        issues.add("UNBOUND_OR_LATE_SOURCE_ACTIVITY")
    registry = SourceRiskRegistry(
        context,
        tuple(orders.values()),
        tuple(fills.values()),
        tuple(sorted(refs)),
        tuple(sorted(issues)),
        str(capture["transport_origin"]),
        native,
        tuple(sorted(exits.items())),
    )
    raw_open: list[Any] = []
    raw_positions: list[Any] = []
    for request in capture["requests"]:
        decoded = decode_broker_json(request["body"])
        if request["path"].startswith("/v2/orders?"):
            raw_open.append(decoded)
        if request["path"] == "/v2/positions":
            raw_positions.append(decoded)
    if (
        len(raw_open) != 1
        or len(raw_positions) != 1
        or not isinstance(raw_positions[0], list)
        or not registry.covers_batch(
            {"positions": raw_positions[0], "open_orders": raw_open[0]},
            {row["symbol"]: Fraction(row["qty"]) for row in raw_positions[0]},
        )
    ):
        issues.add("SOURCE_ACCOUNT_OPEN_BOOK_DISAGREEMENT")
    return SourceRiskRegistry(
        context,
        registry.source_orders,
        registry.source_fills,
        registry.source_refs,
        tuple(sorted(issues)),
        registry.origin,
        native,
        registry.exit_by_scope,
    )
