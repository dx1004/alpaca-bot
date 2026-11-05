"""Pure injected source-CLOSE attribution; no accounting adapter or lot debit.

An exact execution history belongs to the entire original reservation basket.
Partial quantities do not choose entry lots or release any part of that basket.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from ._causal_risk_memo import causal_risk_verification_scope
from .crossed_stop_attempts import ORIGIN, read_source_close_attempts_at
from .crossed_stop_capture import (
    AUTHORITY,
    GET_ORIGIN,
    _stamp,
    read_source_close_captures_at,
)
from .crossed_stop_preparation import source_close_handoff_lineage
from .journal import Journal, JournalError
from .native_oco_evidence import _tagged
from .native_oco_http import native_http_inventory
from .native_oco_requests import _clock, _copy, _digest, _json, _text, _utc
from .oco_journal import OcoJournalView, _causal_oco_reader
from .oco_research import OcoResearchState, load_oco_research_contract
from .order_reconciliation import OrderReconciler
from .paper_order_contract import (
    decode_broker_json,
    validate_order_snapshot,
    validate_saved_request,
)
from .source_close_http import ORIGIN as HTTP_ORIGIN
from .source_close_http import read_source_close_http_at
from .source_close_http_capture import read_source_close_http_captures_at


@dataclass(frozen=True)
class _FillFamily:
    origin: str
    capture_origin: str
    response_kind: str
    association: str
    classification: str
    attempts: Callable[..., list[dict[str, Any]]]
    captures: Callable[..., list[dict[str, Any]]]
    http: bool = False


_CALLBACK_FAMILY = _FillFamily(
    ORIGIN,
    GET_ORIGIN,
    "INJECTED_RESPONSE",
    "INJECTED_POST_GET_IDENTITY_CONSISTENT",
    "INJECTED_SOURCE_CLOSE_FILL_ATTRIBUTION_COMPONENT",
    read_source_close_attempts_at,
    read_source_close_captures_at,
)
_HTTP_FAMILY = _FillFamily(
    HTTP_ORIGIN,
    HTTP_ORIGIN,
    "HTTP_RESPONSE",
    "HTTP_POST_GET_IDENTITY_CONSISTENT",
    "INJECTED_SOURCE_HTTP_CLOSE_FILL_ATTRIBUTION_COMPONENT",
    read_source_close_http_at,
    read_source_close_http_captures_at,
    True,
)


@dataclass(frozen=True)
class _HttpCorroboration:
    intent: dict[str, Any]
    hashes: dict[str, str]
    posted: dict[str, Any]
    filled_quantity: Fraction


def _raw_fills(
    packet: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    result = []
    for index, request in enumerate(packet["requests"]):
        if urlsplit(request["path"]).path != "/v2/account/activities":
            continue
        if request["status"] != 200 or request["error"] is not None:
            raise JournalError("source FILL page failed or is incomplete")
        body = base64.b64decode(request["body_base64"], validate=True)
        rows = decode_broker_json(body)
        if type(rows) is not list:
            raise JournalError("source FILL page is not a list")
        for record_index, row in enumerate(rows):
            if type(row) is not dict or type(row.get("id")) is not str:
                raise JournalError("source FILL identity is unresolved")
            ref = {
                "capture_ref": packet["capture_ref"],
                "request_index": index,
                "record_index": record_index,
                "body_sha256": _digest(body),
            }
            result.append((row, ref, _digest(_json(_tagged(row)))))
    return result


def _order_clocks(packet: dict[str, Any], posted: dict[str, Any]) -> None:
    expected = tuple(_stamp(posted.get(key)) for key in ("created_at", "submitted_at"))
    observed = 0
    for request in packet["requests"]:
        if not urlsplit(request["path"]).path.startswith("/v2/orders"):
            continue
        if request["status"] != 200 or request["error"] is not None:
            raise JournalError("source order identity timestamps unavailable")
        order = decode_broker_json(
            base64.b64decode(request["body_base64"], validate=True)
        )
        if (
            type(order) is not dict
            or tuple(_stamp(order.get(key)) for key in ("created_at", "submitted_at"))
            != expected
        ):
            raise JournalError("immutable order timestamps differ from original POST")
        observed += 1
    if observed != 2:
        raise JournalError("exact source lookup and detail timestamps required")


def _source_inventory(packets: list[dict[str, Any]]) -> None:
    """Candidates may veto reuse; they cannot establish attribution."""
    orders: dict[str, int] = {}
    activities: dict[str, tuple[int, object, str]] = {}
    for packet in packets:
        attempt = packet["pin"]["attempt_ref"]["sequence"]
        for key in ("post_reported_order_id", "get_order_id"):
            identity = packet["assessment"][key]
            if identity is not None:
                if orders.setdefault(identity, attempt) != attempt:
                    raise JournalError("source order UUID reused across attempts")
        for row, _, digest in _raw_fills(packet):
            identity = row["id"]
            claim = (attempt, row.get("order_id"), digest)
            if activities.setdefault(identity, claim) != claim:
                raise JournalError("source FILL identity reused or contents changed")


def _models(
    journal: Journal,
    initial: OcoResearchState,
    horizon: int,
    at: datetime,
) -> dict[str, OcoJournalView]:
    initials = {initial.entry_scope: initial}
    for event, raw in journal.read_oco_research_events():
        if event.sequence > horizon or event.event_kind != "BIND":
            continue
        item = decode_broker_json(raw)
        if not isinstance(item, dict) or not isinstance(item.get("binding"), dict):
            raise JournalError("account OCO binding is malformed")
        binding = item["binding"]
        if binding["account_ref"] != initial.account_ref:
            raise JournalError("account OCO binding differs")
        initials.setdefault(
            binding["entry_scope"],
            OcoResearchState(
                load_oco_research_contract(),
                binding["account_ref"],
                binding["symbol"],
                binding["entry_scope"],
                Fraction(binding["limit"]),
                Fraction(binding["stop"]),
            ),
        )
    result = {}
    for scope, empty in initials.items():
        view = _causal_oco_reader(journal, empty).read_verified_at(horizon, at)
        if view is None or view.conflict_keys or view.entry_binding is None:
            raise JournalError("clean account source OCO prefix required")
        result[scope] = view
    return result


def _basket_guard(
    journal: Journal,
    initial: OcoResearchState,
    lineage: dict[str, Any],
    models: dict[str, OcoJournalView],
    activity_ids: set[str],
    order_id: str,
    horizon: int,
    at: datetime,
) -> list[str]:
    view = models[initial.entry_scope]
    entries = {
        e.execution_id: e.quantity for e in view.model.executions if e.role == "ENTRY"
    }
    committed = dict.fromkeys(entries, Fraction(0))
    for group in view.model.groups:
        for portion in group.portions:
            committed[portion.entry_execution_id] += portion.quantity
    seen_handoffs = set()
    for event, raw in journal.read_crossed_stop_events():
        if event.sequence > horizon or event.event_kind != "SOURCE_BIND_INTENT":
            continue
        item = decode_broker_json(raw)
        if not isinstance(item, dict):
            raise JournalError("source handoff inventory is malformed")
        if item["binding"]["entry_scope"] != initial.entry_scope:
            continue
        cid = decode_broker_json(item["data"]["receipt"]["intent"]["request_body"])[
            "client_order_id"
        ]  # type: ignore[index]
        bound = source_close_handoff_lineage(
            journal, cid, through_sequence=horizon, at=at
        )
        if bound is None or cid in seen_handoffs:
            raise JournalError("source basket handoff is ambiguous")
        seen_handoffs.add(cid)
        for identity, raw_quantity in bound["portions"].items():
            if identity not in committed or Fraction(raw_quantity) <= 0:
                raise JournalError("source basket differs from original entry lots")
            committed[identity] += Fraction(raw_quantity)
    if not lineage["portions"] or not seen_handoffs:
        raise JournalError("original source basket is missing")
    issues = []
    if any(committed[key] > quantity for key, quantity in entries.items()):
        issues.append("ORIGINAL_BASKET_NATIVE_OR_CLOSE_OVERLAP")
    seen_entries: dict[str, str] = {}
    entry_orders: dict[str, str] = {}
    for scope, other in models.items():
        assert other.entry_binding is not None
        entry_order = other.entry_binding["entry"]["broker_order_id"]
        if entry_order == order_id:
            issues.append("SOURCE_ORDER_UUID_REUSES_ENTRY")
        if entry_orders.setdefault(entry_order, scope) != scope:
            issues.append("ENTRY_ORDER_UUID_REUSED_ACROSS_SCOPES")
        for execution in other.entry_binding["entry"]["executions"]:
            identity = execution["execution_id"]
            if identity in activity_ids:
                issues.append("SOURCE_FILL_REUSES_ENTRY_ACTIVITY_ID")
            if seen_entries.setdefault(identity, scope) != scope:
                issues.append("ENTRY_ACTIVITY_REUSED_ACROSS_SCOPES")
    return issues


def _simple_guard(
    journal: Journal,
    account_ref: str,
    cid: str,
    order_id: str,
    activity_ids: set[str],
    horizon: int,
    at: datetime,
    *,
    corroboration: _HttpCorroboration | None = None,
) -> list[str]:
    # This canonical reader retains its current nonrecursive transport-integrity
    # check. Only its bounded DTO/raw prefix contributes identity claims here.
    state, _ = OrderReconciler.read_evidence_at(
        journal, account_ref, at, through_sequence=horizon
    )
    orders = state["orders"]
    if type(orders) is not dict:
        raise JournalError("bounded simple lifecycle orders are malformed")
    issues = []
    for other_cid, projection in orders.items():
        if (
            type(projection) is not dict
            or projection.get("client_order_id") != other_cid
        ):
            raise JournalError("bounded simple lifecycle order identity differs")
        if corroboration is not None and other_cid == cid:
            issues.append("SOURCE_CID_ALREADY_SIMPLE_PROJECTED")
        if other_cid != cid and projection.get("broker_order_id") == order_id:
            issues.append("SIMPLE_ORDER_UUID_REUSE")
        if other_cid == cid and projection.get("broker_order_id") != order_id:
            issues.append("SOURCE_LIFECYCLE_UUID_CONFLICT")
        if (other_cid == cid or projection.get("broker_order_id") == order_id) and (
            projection.get("issues")
            or projection.get("state") in {"UNKNOWN", "CONFLICT"}
        ):
            issues.append("SIMPLE_LIFECYCLE_IDENTITY_UNRESOLVED")
    raw_issues = state.get("issues")
    if type(raw_issues) is not list:
        raise JournalError("bounded simple lifecycle issue list is malformed")
    if any(type(issue) is not str for issue in raw_issues):
        raise JournalError("bounded simple lifecycle issue identity is malformed")
    if any(
        issue == "OWNED_BROKER_ID_REUSED" or issue.startswith("BROKER_ID_CONFLICT:")
        for issue in raw_issues
    ):
        issues.append("SIMPLE_LIFECYCLE_IDENTITY_UNRESOLVED")
    for event, raw in journal.read_order_events(through_sequence=horizon):
        if not event.visible_at(at):
            continue
        item = decode_broker_json(raw)
        if type(item) is not dict:
            raise JournalError("bounded simple lifecycle evidence is malformed")
        if item["kind"] == "REST_BATCH":
            if corroboration is not None:
                issues += _http_rest_order_claims(item, cid, order_id, corroboration)
            for group, rows in item["batch"]["activities"].items():
                for row in rows:
                    if type(row) is not dict:
                        raise JournalError(
                            "bounded REST activity identity is malformed"
                        )
                    if row.get("id") in activity_ids:
                        if corroboration is None or not _http_corroborates(
                            item, group, row, cid, order_id, corroboration
                        ):
                            issues.append("SIMPLE_REST_ACTIVITY_ID_REUSE")
                    elif corroboration is not None and (
                        group == order_id or row.get("order_id") == order_id
                    ):
                        issues.append("SOURCE_REST_ACTIVITY_UNBOUND")
        elif item["kind"] == "FRAME":
            frame = decode_broker_json(base64.b64decode(item["raw"], validate=True))
            if type(frame) is dict and frame.get("stream") == "trade_updates":
                data = frame.get("data")
                if type(data) is dict and data.get("execution_id") in activity_ids:
                    # Equality is conservative ambiguity, not a claim that the
                    # account-activity and stream execution namespaces coincide.
                    issues.append("STREAM_EXECUTION_ACTIVITY_ID_AMBIGUITY")
    return issues


def _http_corroborates(
    item: dict[str, Any],
    group: object,
    row: dict[str, Any],
    cid: str,
    order_id: str,
    context: _HttpCorroboration,
) -> bool:
    local = item.get("intents")
    record = local.get(cid) if type(local) is dict else None
    identity = row.get("id")
    return (
        type(identity) is str
        and type(record) is dict
        and record.get("sent") is False
        and _json(record.get("intent")) == _json(context.intent)
        and group == order_id
        and row.get("order_id") == order_id
        and context.hashes.get(identity) == _digest(_json(_tagged(row)))
    )


def _http_rest_order_claims(
    item: dict[str, Any],
    cid: str,
    order_id: str,
    context: _HttpCorroboration,
) -> list[str]:
    issues: list[str] = []
    local = item.get("intents")
    record = local.get(cid) if type(local) is dict else None
    batch = item["batch"]

    def source_order(raw: bytes) -> None:
        snapshot = validate_order_snapshot(context.intent["request_body"], raw)
        order = decode_broker_json(raw)
        if type(order) is not dict:
            raise ValueError
        if snapshot.broker_order_id != order_id:
            issues.append("SOURCE_REST_UUID_CONFLICT")
        if tuple(
            _stamp(order.get(key)) for key in ("created_at", "submitted_at")
        ) != tuple(
            _stamp(context.posted.get(key)) for key in ("created_at", "submitted_at")
        ):
            issues.append("SOURCE_REST_IMMUTABLE_IDENTITY_CONFLICT")
        if Fraction(snapshot.filled_quantity) > context.filled_quantity:
            issues.append("SOURCE_REST_FILLED_QUANTITY_UNBOUND")

    observed_source_order = False
    for other_cid, response in batch["lookups"].items():
        if type(response) is not dict or response.get("status") not in (200, 201):
            continue
        try:
            raw = base64.b64decode(response["body"], validate=True)
            order = decode_broker_json(raw)
            if type(order) is not dict:
                raise ValueError
            if order.get("id") == order_id and other_cid != cid:
                issues.append("SIMPLE_REST_ORDER_UUID_REUSE")
            if other_cid == cid:
                observed_source_order = True
                source_order(raw)
        except (ValueError, TypeError, KeyError):
            issues.append("SIMPLE_REST_ORDER_IDENTITY_UNRESOLVED")
    for order in batch["open_orders"]:
        if type(order) is not dict:
            raise JournalError("raw REST open-order identity is malformed")
        if order.get("id") == order_id:
            observed_source_order = True
            if order.get("client_order_id") != cid:
                issues.append("SIMPLE_REST_ORDER_UUID_REUSE")
            else:
                try:
                    # Quantity validation stays exact; optional numeric fields
                    # are not used as a source-activity identity signature.
                    source_order(
                        json.dumps(order, default=str, allow_nan=False).encode()
                    )
                except (ValueError, TypeError, KeyError):
                    issues.append("SOURCE_REST_ORDER_IDENTITY_UNRESOLVED")
    if (observed_source_order or record is not None) and (
        type(record) is not dict
        or record.get("sent") is not False
        or _json(record.get("intent")) != _json(context.intent)
    ):
        issues.append("SOURCE_REST_INTENT_OR_SEND_CONFLICT")
    return issues


def _native_guard(
    journal: Journal,
    models: dict[str, OcoJournalView],
    order_id: str,
    activity_ids: set[str],
    horizon: int,
    at: datetime,
) -> list[str]:
    """Current transport integrity remains nonrecursive; claims stay bounded."""
    http = native_http_inventory(journal)
    issues: list[str] = []
    known_orders: set[str] = set()
    native_activities: set[str] = set()

    def order_ids(value: object) -> None:
        if type(value) is not dict or type(value.get("id")) is not str:
            raise ValueError
        known_orders.add(str(UUID(value["id"])))
        legs = value.get("legs")
        if legs is not None:
            if type(legs) is not list:
                raise ValueError
            for leg in legs:
                order_ids(leg)

    for row in http:
        if row["attempt_ref"]["sequence"] > horizon:
            continue
        pre = row["precall"]
        out, ref = row["outcome"], row["outcome_ref"]
        if (
            _clock(pre["started_at"]) > at
            or ref is None
            or ref["sequence"] > horizon
            or out is None
            or _clock(out["completed_at"]) > at
            or out["result_kind"] != "HTTP_RESPONSE"
            or out["http_status"] not in (200, 201)
        ):
            issues.append("NATIVE_POST_IDENTITY_UNRESOLVED")
            continue
        try:
            order_ids(
                decode_broker_json(base64.b64decode(out["body_base64"], validate=True))
            )
        except (ValueError, TypeError, KeyError):
            issues.append("NATIVE_POST_IDENTITY_UNRESOLVED")
    for view in models.values():
        backed = {
            execution["execution_id"]
            for bound in view.native_exit_bindings
            for execution in bound["executions"]
        }
        if any(
            execution.role != "ENTRY" and execution.execution_id not in backed
            for execution in view.model.executions
        ):
            issues.append("DECLARED_NONNATIVE_EXIT_IDENTITY_UNRESOLVED")
        if view.issues:
            issues.append("OCO_MODEL_ISSUES_UNRESOLVED")
        for bound in view.native_exit_bindings:
            known_orders.update(
                value
                for key, value in bound["identity"].items()
                if key in {"root_order_id", "stop_order_id"} and value is not None
            )
            native_activities.update(row["activity_id"] for row in bound["executions"])
            if bound["status"] != "BOUND_NATIVE_EXIT_CENSUS" or bound["issues"]:
                issues.append("NATIVE_CENSUS_UNRESOLVED")
    for event, raw in journal._read_private_events("ALPACA_NATIVE_OCO", horizon):
        if not event.visible_at(at):
            continue
        try:
            packet = decode_broker_json(raw)
            if (
                type(packet) is not dict
                or packet.get("schema") != 2
                or packet.get("account_ref") != journal.read_paper_account_ref()
                or packet.get("transport_origin")
                not in {"BUILTIN_PAPER_HTTPS", "INJECTED_EXCHANGE"}
                or event.external_clock_conflict
            ):
                raise ValueError
            requested = str(UUID(packet["requested_order_id"]))
            claimed_before = set(known_orders)
            for request in packet["requests"]:
                path = urlsplit(request["path"]).path
                if not (
                    path.startswith("/v2/orders/") or path == "/v2/account/activities"
                ):
                    continue
                if request["status"] != 200 or request["error"] is not None:
                    raise ValueError
                decoded = decode_broker_json(
                    base64.b64decode(request["body"], validate=True)
                )
                if path.startswith("/v2/orders/"):
                    order_ids(decoded)
                else:
                    if type(decoded) is not list:
                        raise ValueError
                    for fill in decoded:
                        if type(fill) is not dict or type(fill.get("id")) is not str:
                            raise ValueError
                        native_activities.add(fill["id"])
                        if fill.get("order_id") == order_id:
                            issues.append("NATIVE_ORDER_UUID_REUSE")
            if requested not in claimed_before:
                issues.append("UNBOUND_NATIVE_CAPTURE_UNRESOLVED")
        except (ValueError, TypeError, KeyError):
            issues.append("NATIVE_CAPTURE_IDENTITY_UNRESOLVED")
    if order_id in known_orders:
        issues.append("NATIVE_ORDER_UUID_REUSE")
    if activity_ids & native_activities:
        issues.append("NATIVE_FILL_ACTIVITY_ID_REUSE")
    if any(
        event.sequence <= horizon and event.visible_at(at)
        for event, _ in journal.read_native_oco_attempt_events()
    ):
        issues.append("FAKE_NATIVE_ATTEMPT_OWNERSHIP_UNRESOLVED")
    return issues


def derive_injected_source_close_fills(
    journal: Journal,
    initial: OcoResearchState,
    *,
    client_order_id: str,
    capture_sequences: tuple[int, ...],
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    """Derive the original callback component without changing its schema."""
    return _derive_at(
        journal,
        initial,
        client_order_id=client_order_id,
        capture_sequences=capture_sequences,
        through_sequence=through_sequence,
        decision_at=decision_at,
        family=_CALLBACK_FAMILY,
    )


def derive_source_http_close_fills(
    journal: Journal,
    initial: OcoResearchState,
    *,
    client_order_id: str,
    capture_sequences: tuple[int, ...],
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    """Derive authenticated HTTP-family facts with exact account corroboration."""
    return _derive_at(
        journal,
        initial,
        client_order_id=client_order_id,
        capture_sequences=capture_sequences,
        through_sequence=through_sequence,
        decision_at=decision_at,
        family=_HTTP_FAMILY,
    )


def _derive_at(
    journal: Journal,
    initial: OcoResearchState,
    *,
    client_order_id: str,
    capture_sequences: tuple[int, ...],
    through_sequence: int,
    decision_at: datetime,
    family: _FillFamily,
) -> dict[str, Any]:
    at, cid = _utc(decision_at), _text(client_order_id)
    if (
        type(capture_sequences) is not tuple
        or not capture_sequences
        or any(
            type(sequence) is not int or sequence < 1 for sequence in capture_sequences
        )
        or tuple(sorted(set(capture_sequences))) != capture_sequences
        or type(through_sequence) is not int
        or not capture_sequences[-1]
        <= through_sequence
        <= journal.read_evidence_sequence()
    ):
        raise JournalError(
            "strict ordered source capture references and horizon required"
        )
    journal.require_private_order_state()
    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            return _derive(
                journal, initial, cid, capture_sequences, through_sequence, at, family
            )
    finally:
        if owns:
            journal._db.rollback()


def _derive(
    journal: Journal,
    initial: OcoResearchState,
    cid: str,
    selected: tuple[int, ...],
    horizon: int,
    at: datetime,
    family: _FillFamily = _CALLBACK_FAMILY,
) -> dict[str, Any]:
    attempts = family.attempts(
        journal, initial, through_sequence=horizon, decision_at=at
    )
    packets = family.captures(
        journal, initial, through_sequence=horizon, decision_at=at
    )
    foreign = (_CALLBACK_FAMILY if family.http else _HTTP_FAMILY).captures(
        journal, initial, through_sequence=horizon, decision_at=at
    )
    found = [row for row in attempts if row["precall"]["client_order_id"] == cid]
    if len(found) != 1:
        raise JournalError("one canonical source attempt required")
    row = found[0]
    pre, out = row["precall"], row["outcome"]
    if (
        pre["origin"] != family.origin
        or pre["account_ref"] != initial.account_ref
        or pre["entry_scope"] != initial.entry_scope
        or out is None
        or out["result_kind"] != family.response_kind
        or out["http_status"] not in (200, 201)
    ):
        raise JournalError("confirmed injected source POST response required")
    lineage = pre["qualification"]["handoff"]
    if (
        source_close_handoff_lineage(
            journal,
            cid,
            through_sequence=pre["source_horizon"],
            at=_clock(pre["qualified_at"]),
        )
        != lineage
    ):
        raise JournalError("original source handoff differs")
    _source_inventory(packets + foreign)
    history = [
        packet
        for packet in packets
        if packet["pin"]["attempt_ref"] == row["attempt_ref"]
    ]
    selected_packets = [
        packet for packet in history if packet["capture_ref"]["sequence"] in selected
    ]
    if (
        tuple(packet["capture_ref"]["sequence"] for packet in selected_packets)
        != selected
        or not history
        or history[-1]["capture_ref"]["sequence"] != selected[-1]
    ):
        raise JournalError(
            "selected captures missing or omit newer source observations"
        )
    order_id = selected_packets[-1]["assessment"]["post_reported_order_id"]
    if type(order_id) is not str:
        raise JournalError("selected capture cannot reconstruct a POST acknowledgement")
    posted = decode_broker_json(base64.b64decode(out["body_base64"], validate=True))
    if type(posted) is not dict:
        raise JournalError("original source POST identity is malformed")
    prior: dict[str, str] = {}
    prior_quantity, prior_updated = Fraction(0), -1
    prior_status: str | None = None
    observed: dict[str, dict[str, Any]] = {}
    for packet in history:
        pin, assessment = packet["pin"], packet["assessment"]
        if (
            packet["origin"] != family.capture_origin
            or pin["account_ref"] != initial.account_ref
            or pin["entry_scope"] != initial.entry_scope
            or pin["request_body"] != pre["request_body"]
            or assessment["get_order_id"] != order_id
            or assessment["issues"]
            or assessment["fill_pages_exhausted"] is not True
            or assessment["fill_component_consistent"] is not True
            or assessment["order_snapshot"] is None
        ):
            raise JournalError(
                "source capture identity, pages or arithmetic is unqualified"
            )
        _order_clocks(packet, posted)
        snapshot = assessment["order_snapshot"]
        current = {fill["id"]: digest for fill, _, digest in _raw_fills(packet)}
        quantity = Fraction(assessment["fill_quantity"])
        if (
            any(current.get(key) != value for key, value in prior.items())
            or quantity < prior_quantity
            or snapshot["updated_at_ns"] < prior_updated
            or (
                prior_status in {"filled", "canceled", "expired", "rejected"}
                and snapshot["status"] != prior_status
            )
        ):
            raise JournalError("source FILL history regressed or changed")
        prior, prior_quantity, prior_updated = (
            current,
            quantity,
            snapshot["updated_at_ns"],
        )
        prior_status = snapshot["status"]
        if packet["capture_ref"]["sequence"] not in selected:
            continue
        if (
            pin["outcome_ref"] != row["outcome_ref"]
            or assessment["association"] != family.association
            or assessment["post_reported_order_id"] != order_id
        ):
            raise JournalError("selected source capture lacks exact POST association")
        for fill, raw_ref, digest in _raw_fills(packet):
            identity = fill["id"]
            execution = observed.setdefault(
                identity,
                {
                    "activity_id": identity,
                    "broker_order_id": order_id,
                    "quantity": str(Fraction(fill["qty"])),
                    "price": str(Fraction(fill["price"])),
                    "transaction_time": fill["transaction_time"],
                    "content_hash": digest,
                    "raw_refs": [],
                },
            )
            if execution["content_hash"] != digest:
                raise JournalError("source FILL contents changed across captures")
            execution["raw_refs"].append(raw_ref)
    request = validate_saved_request(pre["request_body"])
    requested = Fraction(str(request["qty"]))
    total = sum(
        (Fraction(execution["quantity"]) for execution in observed.values()),
        Fraction(0),
    )
    basket = sum(
        (Fraction(quantity) for quantity in lineage["portions"].values()), Fraction(0)
    )
    if total != prior_quantity or not 0 <= total <= requested == basket:
        raise JournalError("source cumulative quantity differs from original basket")
    models = _models(journal, initial, horizon, at)
    issues = _basket_guard(
        journal, initial, lineage, models, set(observed), order_id, horizon, at
    )
    issues += _simple_guard(
        journal,
        initial.account_ref,
        cid,
        order_id,
        set(observed),
        horizon,
        at,
        corroboration=_HttpCorroboration(
            lineage["intent"],
            {key: value["content_hash"] for key, value in observed.items()},
            posted,
            total,
        )
        if family.http
        else None,
    )
    issues += _native_guard(journal, models, order_id, set(observed), horizon, at)
    return cast(
        dict[str, Any],
        _copy(
            {
                "schema": 1,
                "classification": family.classification,
                "origin": family.origin,
                "account_ref": initial.account_ref,
                "entry_scope": initial.entry_scope,
                "client_order_id": cid,
                "broker_order_id": order_id,
                "source_horizon": horizon,
                "decision_at": at.isoformat(),
                "attempt_ref": row["attempt_ref"],
                "outcome_ref": row["outcome_ref"],
                "capture_refs": [packet["capture_ref"] for packet in selected_packets],
                "handoff_ref": lineage["handoff_ref"],
                "preparation_ref": lineage["preparation_ref"],
                "entry_reservation_ref": lineage["entry_reservation_ref"],
                "emergency_binding_ref": lineage["emergency_binding_ref"],
                "prior_risk_ref": lineage["prior_risk_ref"],
                "original_portions": lineage["portions"],
                "requested_quantity": str(requested),
                "cumulative_fill_quantity": str(total),
                "remaining_quantity": str(requested - total),
                "executions": [observed[key] for key in sorted(observed)],
                "component_attributed": not issues,
                "issues": sorted(set(issues)),
                "finality": "UNKNOWN",
                "authority": dict(AUTHORITY),
                "basket_policy": "PRESERVE_ENTIRE_ORIGINAL_BASKET",
                "limitations": [
                    "INJECTED_EXCHANGE_NOT_BROKER_PROOF"
                    if family.http
                    else "INJECTED_CALLBACKS_NOT_BROKER_PROOF",
                    "NO_ACCOUNTING_ADAPTER",
                    "NO_PER_LOT_FILL_ALLOCATION",
                    "NO_POSTING_FINALITY_OR_RISK_RELEASE",
                ],
            }
        ),
    )
