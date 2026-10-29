"""Raw-source emergency reservation inputs; never broker admission or a sender.

The legacy path remains entry-only. Explicit native-account preparation replays
owned native groups and all local commitments without releasing their lots.
Status observations establish neither continuous coverage nor current no-halt.
"""

import hashlib
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .account_risk_owner import AccountRiskOwner
from .crossed_stop_source import derive_source_crossing
from .journal import Journal, JournalError
from .market_input_checks import _datetime_ns, _timestamp_ns
from .native_oco_requests import NativeOcoRequests, _causal_native_requests
from .native_risk_registry import POLICY as NATIVE_POLICY
from .native_risk_registry import TERMINAL, derive_native_registry
from .oco_journal import OcoJournal, OcoJournalView, _CausalOcoJournal, _json
from .paper_order_contract import decode_broker_json, validate_saved_request
from .posted_ledger import _exact
from .risk_account_inputs import KIND as ACCOUNT_CAPTURE_KIND
from .risk_account_inputs import SOURCE as ACCOUNT_CAPTURE_SOURCE
from .risk_evidence_request import IncidentRiskRequest
from .scanner_discovery_audit import _raw

HANDOFF_RESERVATION_CONTRACT = "SOURCE_CLOSE_INTENT_HANDOFF_V1"

_ASSET_URL = (
    "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"
)


def _source_ns(value: str) -> int:
    return _timestamp_ns(value[:-6] + "Z" if value.endswith("+00:00") else value)


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
    requests = (
        _causal_native_requests(owner)
        if isinstance(owner, _CausalOcoJournal)
        else NativeOcoRequests(owner)
    )
    prepared = requests.read_verified_at(horizon, at)
    if prepared is None or prepared["conflict_groups"]:
        raise JournalError("clean native preparation inventory required")
    source_refs = {tuple(ref) for ref in refs}
    request_refs = []
    request_events = (
        owner.journal._read_private_events("ALPACA_NATIVE_OCO_REQUEST", horizon)
        if isinstance(owner, _CausalOcoJournal)
        else owner.journal.read_native_oco_request_events()
    )
    for event, raw in request_events:
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
    return _derive_preparation(
        owner,
        horizon=horizon,
        at=at,
        request=request,
        require_current_census=_require_current_census,
    )


def _source_close_metadata_bridge(
    owner: OcoJournal,
    lineage: dict[str, Any],
    risk: dict[str, Any],
    risk_refs: tuple[int, ...],
    *,
    at: datetime,
    horizon: int,
    fee_policy: Path,
    native_mode: bool,
) -> dict[str, Any]:
    """Prove one local unsent binding; never refresh the selected source snapshot."""
    cid = validate_saved_request(lineage["intent"]["request_body"])["client_order_id"]
    last = owner.journal.read_event(risk_refs[-1])
    assert last is not None
    event, raw = last
    item = decode_broker_json(raw)
    assert isinstance(item, dict)
    if (
        item["kind"] != "BIND_EXIT"
        or item["exit_id"] != cid
        or item["role"] != "CLOSE"
        or item["selection"] is not None
        or item["exit_request"]
        != validate_saved_request(lineage["intent"]["request_body"])
    ):
        raise JournalError("source close requires its single unsent metadata bridge")
    before, refs = AccountRiskOwner._read_native_causal_evidence_at(
        owner.journal,
        owner.initial.account_ref,
        at,
        fee_policy,
        through_sequence=event.sequence - 1,
    )
    allowed = {"DAILY_STOP_LATCHED"}
    if native_mode:
        allowed |= {"NATIVE_FINALITY_PENDING", "FINAL_TARGET_TOPOLOGY_INVALID"}
    if (
        before is None
        or not refs
        or risk_refs != (*refs, event.sequence)
        or before.get("veto") is not False
        or before.get("source_snapshot_qualified") is not True
        or not set(before.get("issues", [])) <= allowed
        or not isinstance(before.get("last_snapshot"), dict)
        or before["last_snapshot"].get("qualified") is not True
        or not isinstance(before.get("held"), dict)
        or before["held"].get("exits") != {}
        or before["held"].get("source_close_bindings")
    ):
        raise JournalError("source close pre-binding risk state is unqualified")
    previous = owner.journal.read_event(refs[-1])
    assert previous is not None
    previous_item = decode_broker_json(previous[1])
    assert isinstance(previous_item, dict)
    if (
        previous_item.get("selection") is None
        or previous_item.get("snapshot") != before["last_snapshot"]
    ):
        raise JournalError("source close requires the original qualified observation")
    expected_binding = {
        key: lineage[key]
        for key in (
            "handoff_ref",
            "preparation_ref",
            "entry_reservation_ref",
            "portions",
        )
    } | {"source_fill_ownership_accepted": False, "runtime_authorized": False}
    expected = decode_broker_json(_json(before))
    assert isinstance(expected, dict)
    expected["held"]["exits"] = {cid: "CLOSE"}
    expected["held"]["source_close_bindings"] = {cid: expected_binding}
    expected.update(
        at=item["at"],
        watermark=event.sequence,
        capacity="0",
        issues=sorted(
            {"REFRESH_REQUIRED"}
            | ({"DAILY_STOP_LATCHED"} if before["daily_stop_latched"] else set())
        ),
    )
    if expected != risk:
        raise JournalError("source close binding changed more than local metadata")
    old_census = previous_item["census"]
    added = {
        "id": cid,
        "hash": hashlib.sha256(_json(lineage["intent"])).hexdigest(),
        "send_started_at": None,
    }
    if any(row["id"] == cid for row in old_census) or item["census"] != sorted(
        [*old_census, added], key=lambda row: row["id"]
    ):
        raise JournalError("source close metadata bridge census differs")
    # These sources can change account/order ownership without changing the local
    # intent list. An old qualified selection cannot hide unassessed newer input.
    sources = (
        ACCOUNT_CAPTURE_SOURCE,
        "ALPACA_ORDER_LIFECYCLE",
        "ALPACA_PAPER",
        "ALPACA_NATIVE_OCO",
    )
    rows = owner.journal._db.execute(
        "SELECT sequence FROM events WHERE source_id IN (?,?,?,?) "
        "AND sequence>? AND sequence<=?",
        (*sources, previous_item["source_horizon"], horizon),
    ).fetchall()
    if rows:
        raise JournalError("source close metadata bridge has newer unassessed input")
    return {
        "policy": "SOURCE_CLOSE_UNSENT_METADATA_BRIDGE_V1",
        "observation_ref": [previous[0].sequence, previous[0].payload_ref],
        "binding_ref": [event.sequence, event.payload_ref],
        "before_sha256": hashlib.sha256(_json(before)).hexdigest(),
        "after_sha256": hashlib.sha256(_json(risk)).hexdigest(),
        "census_sha256": hashlib.sha256(_json(item["census"])).hexdigest(),
        "snapshot_observed_at": before["last_snapshot"]["observed_at"],
        "snapshot_refreshed": False,
        "dispatch_authorized": False,
    }


def derive_source_close_precall(
    owner: OcoJournal,
    *,
    client_order_id: str,
    horizon: int,
    at: datetime,
    request: dict[str, Any],
    _require_current_census: bool = False,
) -> dict[str, Any]:
    """Fresh post-handoff source qualification, never dispatch clearance."""
    lineage = source_close_handoff_lineage(
        owner.journal, client_order_id, through_sequence=horizon, at=at
    )
    if lineage is None:
        raise JournalError("anchored source close handoff required")
    stored = owner.journal.read_event(lineage["preparation_ref"][0])
    assert stored is not None
    original = decode_broker_json(stored[1])
    assert isinstance(original, dict)
    old = _derive_preparation(
        owner,
        horizon=original["horizon"],
        at=datetime.fromisoformat(original["at"]),
        request=original["data"]["request"],
        causal_risk=True,
    )
    saved = dict(original["data"]["receipt"])
    saved.pop("reservation", None)
    if old != saved:
        raise JournalError("source close original preparation replay differs")
    result = _derive_preparation(
        owner,
        horizon=horizon,
        at=at,
        request=request,
        require_current_census=_require_current_census,
        causal_risk=True,
        lineage=lineage,
    )
    result["classification"] = (
        "POST_HANDOFF_SOURCE_CLOSE_PRECALL_NOT_DISPATCH_CLEARANCE"
    )
    result["handoff"] = lineage
    return result


def _derive_preparation(
    owner: OcoJournal,
    *,
    horizon: int,
    at: datetime,
    request: dict[str, Any],
    require_current_census: bool = False,
    causal_risk: bool = False,
    lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    handoff = "reservation_contract" in request
    if handoff:
        fields.add("reservation_contract")
    if (
        set(request) != fields
        or any(
            type(value) is not int
            for name, value in request.items()
            if name not in {"ownership_policy", "reservation_contract"}
        )
        or (
            native_mode
            and (
                type(request["ownership_policy"]) is not str
                or request["ownership_policy"] != NATIVE_POLICY
            )
        )
        or (
            handoff
            and (
                type(request["reservation_contract"]) is not str
                or request["reservation_contract"] != HANDOFF_RESERVATION_CONTRACT
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
    if handoff:
        rebuilt["reservation_contract"] = HANDOFF_RESERVATION_CONTRACT
    if rebuilt != request:
        raise JournalError("noncanonical source preparation request")
    crossing = derive_source_crossing(
        owner, horizon=horizon, at=at, _causal_risk=causal_risk
    )
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
    reader = (
        AccountRiskOwner._read_native_causal_evidence_at
        if causal_risk
        else AccountRiskOwner.read_evidence_at
    )
    risk, risk_refs = reader(
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
    if require_current_census and _json(census) != _json(
        AccountRiskOwner(owner.journal, model.account_ref, fee_policy)._census()
    ):
        raise JournalError("local intent/send census changed; refresh source risk")
    completed_ids = {
        identity for row in risk["completed"].values() for identity in row["order_ids"]
    }
    close_ids = (
        set()
        if lineage is None
        else {
            validate_saved_request(lineage["intent"]["request_body"])["client_order_id"]
        }
    )
    if any(
        row["id"] not in completed_ids | {model.entry_scope} | close_ids
        for row in census
    ):
        raise JournalError("unresolved local order commitment outside entry seam")
    snapshot, held = risk["last_snapshot"], risk["held"]
    extra_issues: set[str] = set()
    expected_exits = {}
    if lineage is not None:
        cid = next(iter(close_ids))
        expected_exits = {cid: "CLOSE"}
        expected_binding = {
            key: lineage[key]
            for key in (
                "handoff_ref",
                "preparation_ref",
                "entry_reservation_ref",
                "portions",
            )
        } | {"source_fill_ownership_accepted": False, "runtime_authorized": False}
        if (
            lineage["binding"]["account_ref"] != model.account_ref
            or lineage["binding"]["entry_scope"] != model.entry_scope
            or lineage["binding"]["symbol"] != model.symbol
            or held.get("source_close_bindings") != {cid: expected_binding}
            or held.get("emergency_execution_binding", {}).get("binding_ref")
            != lineage["emergency_binding_ref"]
            or view.entry_binding["reservation_ref"] != lineage["entry_reservation_ref"]
            or any(
                row["id"] == cid and row["send_started_at"] is not None
                for row in census
            )
        ):
            raise JournalError("source close exact current CLOSE binding required")
        assigned: dict[str, Fraction] = {}
        for group in model.groups:
            for part in group.portions:
                assigned[part.entry_execution_id] = (
                    assigned.get(part.entry_execution_id, Fraction(0)) + part.quantity
                )
        available_lots = {
            execution.execution_id: execution.quantity
            - assigned.get(execution.execution_id, Fraction(0))
            for execution in model.executions
            if execution.role == "ENTRY"
        }
        portions = {key: Fraction(value) for key, value in lineage["portions"].items()}
        if (
            not portions
            or any(
                qty <= 0 or qty > available_lots.get(key, Fraction(0))
                for key, qty in portions.items()
            )
            or sum(portions.values())
            != Fraction(
                str(validate_saved_request(lineage["intent"]["request_body"])["qty"])
            )
        ):
            raise JournalError("source close anchored portions are no longer available")
        bridge = _source_close_metadata_bridge(
            owner,
            lineage,
            risk,
            risk_refs,
            at=at,
            horizon=horizon,
            fee_policy=fee_policy,
            native_mode=native_mode,
        )
        extra_issues.add("REFRESH_REQUIRED")
    if (
        risk["veto"]
        or held.get("quantity_incident") is not None
        or held.get("exits") != expected_exits
        or not set(risk["issues"])
        <= extra_issues
        | (
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
    if lineage is not None and available < sum(
        (Fraction(value) for value in lineage["portions"].values()), Fraction(0)
    ):
        raise JournalError("source close reported availability below anchored quantity")
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
    if lineage is not None:
        receipt["metadata_bridge"] = bridge
        selection = IncidentRiskRequest.from_dict(snapshot["selection"])
        age_ns = selection.max_age // timedelta(microseconds=1) * 1000
        quote_event = quote_stored[0]
        mark = next(row for row in selection.mark_refs if row[0] == model.symbol)
        mark_metadata = owner.journal.read_event(mark[3])
        calendar = owner.journal.read_event(selection.calendar_sequence)
        assert mark_metadata is not None and calendar is not None
        calendar_body = decode_broker_json(calendar[1])
        assert isinstance(calendar_body, dict)
        # Reuse the existing quote policy's one-second event/receipt bound and
        # the caller's already validated account, metadata, asset/status bounds.
        deadlines = (
            _source_ns(quote["t"]) + 1_000_000_000,
            _datetime_ns(quote_event.system_first_seen_at) + 1_000_000_000,
            _source_ns(snapshot["observed_at"]) + age_ns,
            _source_ns(risk["at"]) + age_ns,
            _source_ns(calendar_body["request"]["started_at"]) + age_ns,
            _datetime_ns(mark_metadata[0].system_first_seen_at)
            + selection.max_metadata_age // timedelta(microseconds=1) * 1000,
            _source_ns(metadata["requested_at"]) + request["max_asset_age_us"] * 1000,
            _source_ns(status["t"]) + request["max_status_age_us"] * 1000,
            _datetime_ns(status_event.system_first_seen_at)
            + request["max_status_age_us"] * 1000,
        )
        receipt["freshness_not_after_ns"] = str(min(deadlines))
        receipt["session_before_ns"] = str(_source_ns(crossing["close_at"]))
    if native_proof is not None:
        receipt["ownership_policy"] = NATIVE_POLICY
        receipt["native_ownership"] = native_proof
        receipt["classification"] = (
            "RAW_NATIVE_ACCOUNT_PREPARATION_NOT_BROKER_CLEARANCE"
        )
    return receipt


def source_close_handoff_lineage(
    journal: Journal,
    client_order_id: str,
    *,
    through_sequence: int,
    at: datetime,
) -> dict[str, Any] | None:
    """Slot/lot lineage only; no current source or fill-ownership qualification."""
    from .crossed_stop import _validate_crossed_stop_item

    binding_keys = {
        "account_ref",
        "symbol",
        "entry_scope",
        "limit",
        "stop",
        "version",
        "candidate",
        "manifest_sha256",
        "parent_candidate",
        "parent_sha256",
        "emergency_version",
        "emergency_manifest_sha256",
    }
    rows = []
    relevant = []
    for event, raw in journal._read_private_events(
        "ALPACA_CROSSED_STOP_RESEARCH", through_sequence
    ):
        item = decode_broker_json(raw)
        if not isinstance(item, dict) or set(item) != {
            "binding",
            "kind",
            "key",
            "at",
            "data",
            "horizon",
        }:
            raise JournalError("source close lineage exact event schema differs")
        binding = item["binding"]
        if (
            not isinstance(binding, dict)
            or set(binding) != binding_keys
            or any(
                type(value) is not str or not value or value != value.strip()
                for value in binding.values()
            )
        ):
            raise JournalError("source close lineage binding schema differs")
        _validate_crossed_stop_item(binding, item)
        if (
            _json(item) != raw
            or event.feed != "paper"
            or event.event_kind != item["kind"]
            or type(item["horizon"]) is not int
            or not 0 <= item["horizon"] < event.sequence
        ):
            raise JournalError("source close lineage event metadata differs")
        rows.append((event, item))
        if item["kind"] in {"SOURCE_PREPARE", "SOURCE_BIND_INTENT"}:
            expected = (
                "cs-"
                + hashlib.sha256(
                    _json([binding, item["data"]["close_id"]])
                ).hexdigest()[:40]
            )
            if expected == client_order_id:
                relevant.append(binding)
    if not relevant:
        return None
    if any(binding != relevant[0] for binding in relevant):
        raise JournalError("source close lineage is ambiguous")
    binding = relevant[0]
    scoped = [(event, item) for event, item in rows if item["binding"] == binding]
    seen: dict[str, dict[str, Any]] = {}
    for event, item in scoped:
        if (
            not event.visible_at(at)
            or event.external_clock_conflict
            or item["at"] != event.available_at.isoformat()
        ):
            raise JournalError("source close lineage is not decision-visible")
        stamp = datetime.fromisoformat(item["at"])
        if stamp.utcoffset() != timedelta(0) or stamp.isoformat() != item["at"]:
            raise JournalError("source close lineage clock differs")
        key = item["key"]
        if key in seen:
            raise JournalError("source close lineage has duplicate/conflicting claims")
        seen[key] = {"hash": event.payload_ref, "sequence": event.sequence}
    cache = {}
    for key, value in journal.read_crossed_stop_evidence_cache(
        binding["account_ref"]
    ).items():
        identity = decode_broker_json(key)
        if (
            not isinstance(identity, list)
            or len(identity) != 3
            or any(type(part) is not str for part in identity)
            or _json(identity).decode() != key
            or not isinstance(value, dict)
            or set(value) != {"hash", "sequence"}
            or type(value["sequence"]) is not int
            or type(value["hash"]) is not str
        ):
            raise JournalError("source close lineage deduplication schema differs")
        if (
            identity[0] == binding["entry_scope"]
            and value["sequence"] <= through_sequence
        ):
            cache[key] = value
    if cache != seen:
        raise JournalError("source close lineage deduplication differs")
    matches = [
        (event, item)
        for event, item in scoped
        if item["kind"] == "SOURCE_BIND_INTENT"
        and "cs-"
        + hashlib.sha256(_json([binding, item["data"]["close_id"]])).hexdigest()[:40]
        == client_order_id
    ]
    if len(matches) != 1:
        raise JournalError("source close handoff lineage is missing or ambiguous")
    event, item = matches[0]
    receipt = item["data"]["receipt"]
    receipt_keys = {
        "schema",
        "classification",
        "reservation_contract",
        "decision_at",
        "source_horizon",
        "binding",
        "close_id",
        "preparation_ref",
        "entry_reservation_ref",
        "emergency_binding_ref",
        "prior_risk_ref",
        "portions",
        "intent",
        "source_fill_ownership_accepted",
        "dispatch_authorized",
        "runtime_authorized",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != receipt_keys
        or type(receipt["schema"]) is not int
        or receipt["schema"] != 1
        or receipt["classification"]
        != "LOCAL_SOURCE_CLOSE_INTENT_NOT_SEND_OR_FILL_OWNERSHIP"
        or receipt["reservation_contract"] != HANDOFF_RESERVATION_CONTRACT
        or receipt["binding"] != binding
        or receipt["close_id"] != item["data"]["close_id"]
        or receipt["decision_at"] != item["at"]
        or type(receipt["source_horizon"]) is not int
        or receipt["source_horizon"] != item["horizon"]
    ):
        raise JournalError("source close handoff exact receipt differs")
    for flag in (
        "source_fill_ownership_accepted",
        "dispatch_authorized",
        "runtime_authorized",
    ):
        if receipt[flag] is not False:
            raise JournalError("source close handoff cannot grant authority")
    intent = receipt["intent"]
    if (
        not isinstance(intent, dict)
        or set(intent)
        != {"paper_endpoint", "paper_account_ref", "purpose", "request_body"}
        or intent != journal.read_order_intent(client_order_id)
    ):
        raise JournalError("source close canonical intent changed")
    request = validate_saved_request(intent["request_body"])
    if (
        _json(request).decode() != intent["request_body"]
        or request["client_order_id"] != client_order_id
        or request["symbol"] != binding["symbol"]
        or request["side"] != "sell"
        or request["type"] != "market"
        or intent["paper_account_ref"] != binding["account_ref"]
        or intent["paper_endpoint"] != "https://paper-api.alpaca.markets"
        or intent["purpose"] != "ENGINEERING_TEST"
    ):
        raise JournalError("source close canonical request differs")
    for name in (
        "preparation_ref",
        "entry_reservation_ref",
        "emergency_binding_ref",
        "prior_risk_ref",
    ):
        ref = receipt[name]
        if (
            not isinstance(ref, list)
            or len(ref) != 2
            or type(ref[0]) is not int
            or not 0 < ref[0] <= item["horizon"]
            or type(ref[1]) is not str
        ):
            raise JournalError("source close lineage reference schema differs")
        source = journal.read_event(ref[0])
        if (
            source is None
            or source[0].payload_ref != ref[1]
            or not source[0].visible_at(at)
            or source[0].external_clock_conflict
        ):
            raise JournalError("source close lineage reference unavailable")
    prep_saved = journal.read_event(receipt["preparation_ref"][0])
    assert prep_saved is not None
    prep = decode_broker_json(prep_saved[1])
    if (
        not isinstance(prep, dict)
        or prep_saved[0].source_id != "ALPACA_CROSSED_STOP_RESEARCH"
        or prep_saved[0].event_kind != "SOURCE_PREPARE"
        or prep.get("binding") != binding
        or prep["data"]["close_id"] != receipt["close_id"]
        or prep["data"]["receipt"]["crossing"]["entry_reservation_ref"]
        != receipt["entry_reservation_ref"]
        or prep["data"]["receipt"]["crossing"]["emergency_risk_binding"]["binding_ref"]
        != receipt["emergency_binding_ref"]
    ):
        raise JournalError("source close original preparation linkage differs")
    reservation = prep["data"]["receipt"].get("reservation")
    if (
        not isinstance(reservation, dict)
        or set(reservation)
        != {"contract", "close_id", "portions", "quantity", "request"}
        or reservation["contract"] != HANDOFF_RESERVATION_CONTRACT
        or reservation["close_id"] != receipt["close_id"]
        or reservation["portions"] != receipt["portions"]
        or reservation["request"] != request
        or Fraction(reservation["quantity"]) != Fraction(str(request["qty"]))
    ):
        raise JournalError("source close exact anchored reservation differs")
    return receipt | {"handoff_ref": [event.sequence, event.payload_ref]}
