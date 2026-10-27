"""Raw-source emergency reservation inputs; never broker admission or a sender.

The legacy path remains entry-only. Explicit native-account preparation replays
owned native groups and all local commitments without releasing their lots.
Status observations establish neither continuous coverage nor current no-halt.
"""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .account_risk_owner import AccountRiskOwner
from .crossed_stop_source import derive_source_crossing
from .journal import JournalError
from .market_input_checks import _datetime_ns, _timestamp_ns
from .native_oco_requests import NativeOcoRequests
from .native_risk_registry import POLICY as NATIVE_POLICY
from .native_risk_registry import TERMINAL, derive_native_registry
from .oco_journal import OcoJournal, OcoJournalView, _json
from .paper_order_contract import decode_broker_json
from .posted_ledger import _exact
from .risk_account_inputs import KIND as ACCOUNT_CAPTURE_KIND
from .risk_account_inputs import SOURCE as ACCOUNT_CAPTURE_SOURCE
from .risk_evidence_request import IncidentRiskRequest
from .scanner_discovery_audit import _raw

_ASSET_URL = (
    "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"
)


def preparation_request(
    asset_sequence: int,
    status_sequence: int,
    status_record_index: int,
    max_asset_age: timedelta,
    max_status_age: timedelta,
) -> dict[str, int]:
    """Explicit research observation bounds; no default or accepted runtime TTL."""
    if any(type(n) is not int or n <= 0 for n in (asset_sequence, status_sequence)):
        raise JournalError("positive source preparation references required")
    if type(status_record_index) is not int or status_record_index < 0:
        raise JournalError("nonnegative trading-status record index required")
    if any(
        type(age) is not timedelta or age <= timedelta(0)
        for age in (max_asset_age, max_status_age)
    ):
        raise JournalError("explicit positive research observation ages required")
    return {
        "asset_sequence": asset_sequence,
        "status_sequence": status_sequence,
        "status_record_index": status_record_index,
        "max_asset_age_us": max_asset_age // timedelta(microseconds=1),
        "max_status_age_us": max_status_age // timedelta(microseconds=1),
    }


def native_preparation_request(
    asset_sequence: int,
    status_sequence: int,
    status_record_index: int,
    max_asset_age: timedelta,
    max_status_age: timedelta,
) -> dict[str, Any]:
    """Explicit native-account observation; no broker reservation clearance."""
    return preparation_request(
        asset_sequence,
        status_sequence,
        status_record_index,
        max_asset_age,
        max_status_age,
    ) | {"ownership_policy": NATIVE_POLICY}


def _native_ownership(
    owner: OcoJournal,
    view: OcoJournalView,
    risk: dict[str, Any],
    *,
    horizon: int,
    at: datetime,
    risk_horizon: int,
) -> dict[str, Any]:
    model, bound = view.model, view.entry_binding
    snapshot, held = risk["last_snapshot"], risk["held"]
    native = snapshot.get("native")
    owned = held.get("native_ownership")
    if (
        bound is None
        or bound.get("ownership_policy") != NATIVE_POLICY
        or set(view.issues) - {"FINAL_TARGET_TOPOLOGY_INVALID"}
        or risk.get("_native_policy") != NATIVE_POLICY
        or not isinstance(native, dict)
        or native.get("policy") != NATIVE_POLICY
        or native.get("runtime_authorized") is not False
        or not isinstance(native.get("context"), dict)
        or native["context"].get("policy") != NATIVE_POLICY
        or native["context"].get("active_scope") != model.entry_scope
        or not isinstance(owned, dict)
        or owned.get("policy") != NATIVE_POLICY
        or owned.get("runtime_authorized") is not False
        or "quantity_incident" in held
    ):
        raise JournalError("explicit current native-account ownership required")
    selection = IncidentRiskRequest.from_dict(snapshot["selection"])
    account_captures = owner.journal._db.execute(
        "SELECT sequence FROM events WHERE source_id=? AND feed=? "
        "AND event_kind=? AND sequence>? AND sequence<=? ORDER BY sequence",
        (
            ACCOUNT_CAPTURE_SOURCE,
            "paper",
            ACCOUNT_CAPTURE_KIND,
            selection.capture_sequence,
            horizon,
        ),
    ).fetchall()
    for (sequence,) in account_captures:
        stored = owner.journal.read_event(sequence)
        assert stored is not None
        event, raw = stored
        document = decode_broker_json(raw)
        if (
            not event.visible_at(at)
            or event.external_clock_conflict
            or not isinstance(document, dict)
            or document.get("account_ref") != model.account_ref
        ):
            raise JournalError("new account capture binding or clock differs")
        raise JournalError("new account capture requires source risk refresh")
    registry = derive_native_registry(
        owner.journal,
        native["context"],
        capture_sequence=selection.capture_sequence,
        decision_at=at,
        max_age=selection.max_age,
        through_sequence=horizon,
    )
    if registry.issues or set(registry.risk_issues) - {"FINAL_TARGET_TOPOLOGY_INVALID"}:
        raise JournalError("native source ownership remains quarantined")
    refs = []
    for sequence in registry.source_refs:
        stored = owner.journal.read_event(sequence)
        if stored is None or not stored[0].visible_at(at):
            raise JournalError("native preparation source unavailable")
        refs.append([sequence, stored[0].payload_ref])
    census = {
        scope: [list(ref) for ref in scoped] for scope, scoped in registry.census_refs
    }
    if (
        refs != native.get("source_refs")
        or census != native.get("census_refs")
        or {scope: str(qty) for scope, qty in registry.exit_by_scope}
        != native.get("exit_by_scope")
        or [order.order_id for order in registry.orders] != native.get("order_ids")
        or native.get("risk_issues") != list(registry.risk_issues)
    ):
        raise JournalError("native census advanced; refresh source risk")
    if dict(registry.exit_by_scope).get(model.entry_scope) != model.exit_quantity:
        raise JournalError("native exit quantities differ from owned lot book")
    groups = {group.group_id for group in model.groups}
    bindings = {receipt["group_id"]: receipt for receipt in view.native_exit_bindings}
    if not groups or set(bindings) != groups:
        raise JournalError("exact native group ownership coverage required")
    seen_groups = set()
    for sequence, digest in dict(registry.census_refs).get(model.entry_scope, ()):
        stored = owner.journal.read_event(sequence)
        if stored is None or stored[0].payload_ref != digest:
            raise JournalError("native source census reference unavailable")
        item = decode_broker_json(stored[1])
        if (
            not isinstance(item, dict)
            or item.get("binding") != owner.binding
            or item.get("kind") != "BOUND_NATIVE_EXIT"
            or item["operation"]["outcome"] != "BOUND_NATIVE_EXIT_CENSUS"
        ):
            raise JournalError("native scope census differs")
        group = item["operation"]["request"]["group_id"]
        if group in seen_groups or item["operation"]["receipt"] != bindings.get(group):
            raise JournalError("native group source census differs")
        seen_groups.add(group)
    if seen_groups != groups:
        raise JournalError("native group source census incomplete")
    order_groups = {}
    capture_horizons = {}
    for group, receipt in bindings.items():
        for name in ("root_order_id", "stop_order_id"):
            order_id = receipt["identity"][name]
            if not isinstance(order_id, str) or order_id in order_groups:
                raise JournalError("native order group identity differs")
            order_groups[order_id] = group
            capture_horizons[order_id] = receipt["capture_ref"]["sequence"]
    working = []
    for order in registry.orders:
        if order.status == "filled" and order.filled_quantity != order.quantity:
            raise JournalError("native filled status disagrees with quantity")
        if order.status in TERMINAL:
            continue
        if (
            order.status not in {"new", "accepted", "partially_filled"}
            or (order.status in {"new", "accepted"} and order.filled_quantity != 0)
            or (
                order.status == "partially_filled"
                and not 0 < order.filled_quantity < order.quantity
            )
        ):
            raise JournalError("stable working native order evidence required")
        group = order_groups.get(order.order_id)
        if (
            group is None
            or not 0
            <= order.quantity - order.filled_quantity
            <= model.group_residual(group)
        ):
            raise JournalError("working native order exceeds its group residual")
        working.append(order.order_id)
    requests = NativeOcoRequests(owner)
    prepared = requests.read_verified_at(horizon, at)
    if prepared is None or prepared["conflict_groups"]:
        raise JournalError("clean native preparation inventory required")
    source_refs = {tuple(ref) for ref in refs}
    request_refs = []
    for event, raw in owner.journal.read_native_oco_request_events():
        if event.sequence > horizon:
            continue
        if not event.visible_at(at):
            raise JournalError("native preparation inventory is not decision-visible")
        item = requests._validate_item(event, raw)
        reference = [event.sequence, event.payload_ref]
        if (
            item["outcome"] == "REQUEST_IDENTITY_CONFLICT"
            or tuple(reference) not in source_refs
        ):
            raise JournalError("native preparation commitment is not source-owned")
        request_refs.append(reference)
    # A raw newer group observation cannot be silently ignored merely because
    # it has not yet been adopted by the OCO owner or account-risk snapshot.
    rows = owner.journal._db.execute(
        "SELECT sequence FROM events WHERE source_id=? AND feed=? "
        "AND event_kind=? AND sequence<=? ORDER BY sequence",
        ("ALPACA_NATIVE_OCO", "paper", "NATIVE_OCO_CAPTURE", horizon),
    ).fetchall()
    for (sequence,) in rows:
        stored = owner.journal.read_event(sequence)
        assert stored is not None
        event, raw = stored
        if not event.visible_at(at) or event.external_clock_conflict:
            raise JournalError("native raw capture is not decision-visible")
        capture = decode_broker_json(raw)
        if (
            not isinstance(capture, dict)
            or capture.get("account_ref") != model.account_ref
        ):
            raise JournalError("native raw capture account differs")
        requested = capture.get("requested_order_id")
        if type(requested) is not str:
            raise JournalError("native raw capture order identity missing")
        allowed = capture_horizons.get(requested)
        if (allowed is not None and sequence > allowed) or (
            allowed is None and sequence > risk_horizon
        ):
            raise JournalError("new native raw capture requires source reconciliation")
    return {
        "policy": NATIVE_POLICY,
        "transport_origin": registry.origin,
        "source_refs": refs,
        "census_refs": census,
        "native_request_refs": request_refs,
        "model_issues": list(view.issues),
        "risk_issues": list(registry.risk_issues),
        "observed_exit_quantity": str(model.exit_quantity),
        "unallocated_quantity": str(model.unallocated_quantity),
        "working_order_ids": sorted(working),
        "runtime_authorized": False,
    }


def derive_source_preparation(
    owner: OcoJournal,
    *,
    horizon: int,
    at: datetime,
    request: dict[str, Any],
    _require_current_census: bool = False,
) -> dict[str, Any]:
    """Called under the existing owner transaction at the immutable horizon."""
    native_mode = "ownership_policy" in request
    fields = {
        "asset_sequence",
        "status_sequence",
        "status_record_index",
        "max_asset_age_us",
        "max_status_age_us",
    }
    if native_mode:
        fields.add("ownership_policy")
    if (
        set(request) != fields
        or any(
            type(value) is not int
            for name, value in request.items()
            if name != "ownership_policy"
        )
        or (
            native_mode
            and (
                type(request["ownership_policy"]) is not str
                or request["ownership_policy"] != NATIVE_POLICY
            )
        )
    ):
        raise JournalError("exact source preparation request required")
    rebuilt: dict[str, Any] = preparation_request(
        request["asset_sequence"],
        request["status_sequence"],
        request["status_record_index"],
        timedelta(microseconds=request["max_asset_age_us"]),
        timedelta(microseconds=request["max_status_age_us"]),
    )
    if native_mode:
        rebuilt["ownership_policy"] = NATIVE_POLICY
    if rebuilt != request:
        raise JournalError("noncanonical source preparation request")
    crossing = derive_source_crossing(owner, horizon=horizon, at=at)
    if not crossing["component_qualified"] or not crossing.get(
        "emergency_risk_policy_opt_in_observed"
    ):
        raise JournalError("qualified current source and explicit risk opt-in required")
    view = owner.read_verified_at(horizon, at)
    assert view is not None and view.entry_binding is not None
    model = view.model
    if not native_mode and (
        model.groups or any(e.role != "ENTRY" for e in model.executions)
    ):
        raise JournalError("native/declared exit ownership remains unqualified")
    if not model.entry_terminal_reconciled:
        raise JournalError("source terminal entry census required")
    fee_policy = Path(view.entry_binding["fee_policy"])
    risk, risk_refs = AccountRiskOwner.read_evidence_at(
        owner.journal,
        model.account_ref,
        at,
        fee_policy=fee_policy,
        through_sequence=horizon,
    )
    assert risk is not None
    risk_record = owner.journal.read_event(risk_refs[-1])
    assert risk_record is not None
    risk_item = decode_broker_json(risk_record[1])
    assert isinstance(risk_item, dict)
    census = risk_item["census"]
    if _require_current_census and _json(census) != _json(
        AccountRiskOwner(owner.journal, model.account_ref, fee_policy)._census()
    ):
        raise JournalError("local intent/send census changed; refresh source risk")
    completed_ids = {
        identity for row in risk["completed"].values() for identity in row["order_ids"]
    }
    if any(row["id"] not in completed_ids | {model.entry_scope} for row in census):
        raise JournalError("unresolved local order commitment outside entry seam")
    snapshot, held = risk["last_snapshot"], risk["held"]
    if (
        risk["veto"]
        or held.get("quantity_incident") is not None
        or held.get("exits") != {}
        or not set(risk["issues"])
        <= (
            {
                "DAILY_STOP_LATCHED",
                "NATIVE_FINALITY_PENDING",
                "FINAL_TARGET_TOPOLOGY_INVALID",
            }
            if native_mode
            else {"DAILY_STOP_LATCHED"}
        )
    ):
        raise JournalError("source ownership/identity risk veto remains")
    entry = snapshot["orders"].get(model.entry_scope)
    if entry is None or entry["state"] not in {
        "FILLED",
        "CANCELED",
        "EXPIRED",
        "REJECTED",
    }:
        raise JournalError("observed terminal entry, not cancel ACK, required")
    native_proof = None
    if native_mode:
        native_proof = _native_ownership(
            owner,
            view,
            risk,
            horizon=horizon,
            at=at,
            risk_horizon=risk_item["source_horizon"],
        )
    capture_sequence = snapshot["selection"]["capture_sequence"]
    capture = owner.journal.read_event(capture_sequence)
    assert capture is not None
    envelope = decode_broker_json(capture[1])
    assert isinstance(envelope, dict)
    responses: dict[str, Any] = {}
    for row in envelope["requests"]:
        path = urlsplit(row["path"]).path
        if path in {"/v2/positions", "/v2/orders"}:
            if path in responses:
                raise JournalError("ambiguous position/open-order source responses")
            responses[path] = decode_broker_json(row["body"])
    if native_proof is None:
        if responses.get("/v2/orders") != []:
            raise JournalError(
                "working orders remain; preserve protection and reconcile"
            )
    else:
        opened = responses.get("/v2/orders")
        if (
            not isinstance(opened, list)
            or any(not isinstance(row, dict) for row in opened)
            or len(opened) != len(native_proof["working_order_ids"])
            or {row.get("id") for row in opened}
            != set(native_proof["working_order_ids"])
        ):
            raise JournalError("exact working native open-order source required")
    positions = responses.get("/v2/positions")
    if not isinstance(positions, list) or len(positions) != 1:
        raise JournalError("one owned position source required")
    position = positions[0]
    if not isinstance(position, dict) or position.get("symbol") != model.symbol:
        raise JournalError("source position scope differs")
    try:
        quantity = _exact(position.get("qty"), positive=True)
        available = _exact(position.get("qty_available"))
    except (ValueError, ArithmeticError) as exc:
        raise JournalError("reported available quantity missing or invalid") from exc
    if quantity != model.owned_quantity or not 0 <= available <= quantity:
        raise JournalError("source position/lot or availability differs")
    refs = {sequence: digest for sequence, digest in crossing["source_refs"]}
    if native_proof is not None:
        refs.update(native_proof["source_refs"])
        refs.update(native_proof["native_request_refs"])
    for name in ("asset_sequence", "status_sequence"):
        sequence = request[name]
        stored = owner.journal.read_event(sequence)
        if (
            stored is None
            or sequence > horizon
            or not stored[0].visible_at(at)
            or stored[0].external_clock_conflict
            or stored[0].payload_ref
            != "sha256:" + hashlib.sha256(stored[1]).hexdigest()
        ):
            raise JournalError("preparation source outside immutable visible prefix")
        refs[sequence] = stored[0].payload_ref
    asset_event, metadata, assets = _raw(
        owner.journal,
        request["asset_sequence"],
        "ASSET_DIRECTORY_RAW_RESPONSE",
        "asset_master",
        at,
    )
    if (
        metadata.get("request_url") != _ASSET_URL
        or metadata.get("query") != {"status": "active", "asset_class": "us_equity"}
        or not timedelta(0)
        <= at - datetime.fromisoformat(metadata["requested_at"])
        <= timedelta(microseconds=request["max_asset_age_us"])
        or not isinstance(assets, list)
        or any(not isinstance(row, dict) for row in assets)
    ):
        raise JournalError("asset source scope or observation age invalid")
    matching = [row for row in assets if row.get("symbol") == model.symbol]
    if (
        len(matching) != 1
        or any(
            matching[0].get(key) != value
            for key, value in (
                ("class", "us_equity"),
                ("status", "active"),
                ("tradable", True),
            )
        )
        or type(matching[0].get("tradable")) is not bool
    ):
        raise JournalError("one reported active tradable asset required")
    for newer, _ in owner.journal.read_scanner_events(through_sequence=horizon):
        if (
            newer.sequence > asset_event.sequence
            and newer.visible_at(at)
            and newer.feed == "asset_master"
            and newer.event_kind == "ASSET_DIRECTORY_RAW_RESPONSE"
        ):
            raise JournalError("asset source superseded")
    status_stored = owner.journal.read_event(request["status_sequence"])
    assert status_stored is not None
    status_event, status_raw = status_stored
    if (status_event.source_id, status_event.feed, status_event.event_kind) != (
        "alpaca_market_data",
        "iex",
        "RAW_FRAME",
    ):
        raise JournalError("IEX raw trading-status source required")
    records = decode_broker_json(status_raw)
    index = request["status_record_index"]
    if not isinstance(records, list) or index >= len(records):
        raise JournalError("trading-status record unavailable")
    status = records[index]
    quote_stored = owner.journal.read_event(crossing["quote_ref"][0])
    assert quote_stored is not None
    quote_records = decode_broker_json(quote_stored[1])
    assert isinstance(quote_records, list)
    quote = quote_records[crossing["quote_ref"][2]]
    if (
        not isinstance(status, dict)
        or status.get("T") != "s"
        or status.get("S") != model.symbol
        or status.get("z") not in {"A", "B", "C", "O"}
        or status.get("z") != quote["z"]
        or any(
            type(status.get(key)) is not str for key in ("sc", "sm", "rc", "rm", "t")
        )
        or status["sc"] != ("3" if status["z"] in {"A", "B"} else "T")
    ):
        raise JournalError(
            "reported trading resumption required; quote/asset is insufficient"
        )
    try:
        age_ns = _datetime_ns(at) - _timestamp_ns(status["t"])
    except ValueError as exc:
        raise JournalError("invalid trading-status timestamp") from exc
    if not 0 <= age_ns <= request["max_status_age_us"] * 1000 or not timedelta(0) <= (
        at - status_event.system_first_seen_at
    ) <= timedelta(microseconds=request["max_status_age_us"]):
        raise JournalError("trading-status observation stale or future")
    for event, raw in owner.journal.read_market_events():
        if event.sequence > horizon or not event.visible_at(at) or event.feed != "iex":
            continue
        rows = decode_broker_json(raw)
        if not isinstance(rows, list):
            raise JournalError("ambiguous visible status frame")
        for offset, row in enumerate(rows):
            if (
                isinstance(row, dict)
                and row.get("T") == "s"
                and row.get("S") == model.symbol
            ):
                if (event.sequence, offset) > (status_event.sequence, index):
                    raise JournalError("trading-status observation superseded")
    receipt = {
        "classification": "RAW_SOURCE_REFERENCED_PREPARATION_NOT_BROKER_CLEARANCE",
        "source_horizon": horizon,
        "decision_at": at.isoformat(),
        "source_refs": [
            [sequence, digest] for sequence, digest in sorted(refs.items())
        ],
        "local_intent_census": census,
        "local_intent_census_ref": [risk_refs[-1], risk_record[0].payload_ref],
        "crossing": crossing,
        "reported_position_quantity": str(quantity),
        "reported_available_quantity": str(available),
        "asset_ref": [request["asset_sequence"], refs[request["asset_sequence"]]],
        "status_ref": [
            request["status_sequence"],
            refs[request["status_sequence"]],
            index,
        ],
        "reported_status_code": status["sc"],
        "reported_status_timestamp": status["t"],
        "continuous_status_coverage_proven": False,
        "current_no_halt_proven": False,
        "broker_reservation_atomicity_proven": False,
        "source_admission_accepted": False,
        "dispatch_authorized": False,
        "runtime_authorized": False,
        "formal_paper_authorized": False,
    }
    if native_proof is not None:
        receipt["ownership_policy"] = NATIVE_POLICY
        receipt["native_ownership"] = native_proof
        receipt["classification"] = (
            "RAW_NATIVE_ACCOUNT_PREPARATION_NOT_BROKER_CLEARANCE"
        )
    return receipt
