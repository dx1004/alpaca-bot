"""Raw-source emergency reservation inputs; never broker admission or a sender.

Only the entry-only ownership seam is supported. Native/declared exit groups
cannot be promoted into source ownership. Status observations do not establish
continuous coverage or the current absence of an exchange halt.
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
from .oco_journal import OcoJournal, _json
from .paper_order_contract import decode_broker_json
from .posted_ledger import _exact
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


def derive_source_preparation(
    owner: OcoJournal,
    *,
    horizon: int,
    at: datetime,
    request: dict[str, Any],
    _require_current_census: bool = False,
) -> dict[str, Any]:
    """Called under the existing owner transaction at the immutable horizon."""
    if set(request) != {
        "asset_sequence",
        "status_sequence",
        "status_record_index",
        "max_asset_age_us",
        "max_status_age_us",
    } or any(type(value) is not int for value in request.values()):
        raise JournalError("exact source preparation request required")
    rebuilt = preparation_request(
        request["asset_sequence"],
        request["status_sequence"],
        request["status_record_index"],
        timedelta(microseconds=request["max_asset_age_us"]),
        timedelta(microseconds=request["max_status_age_us"]),
    )
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
    if model.groups or any(e.role != "ENTRY" for e in model.executions):
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
        or not set(risk["issues"]) <= {"DAILY_STOP_LATCHED"}
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
    if responses.get("/v2/orders") != []:
        raise JournalError("working orders remain; preserve protection and reconcile")
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
    return {
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
