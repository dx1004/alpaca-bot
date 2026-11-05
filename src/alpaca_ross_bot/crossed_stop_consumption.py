"""Bounded reported source-FILL consumption proof; never risk release."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from ._causal_risk_memo import causal_risk_verification_scope
from .journal import Journal, JournalError
from .oco_research import OcoResearchState

POLICY = "SOURCE_HTTP_FILL_CONSUMPTION_V1"
AUTHORITY = {
    "operational_ownership_accepted": False,
    "broker_fill_finality_accepted": False,
    "risk_release_accepted": False,
    "runtime_authorized": False,
    "order_authority": False,
    "protection_accepted": False,
}
_ALLOWED_RISK_ISSUES = {
    "SOURCE_CLOSE_OWNERSHIP_PENDING",
    "NATIVE_FINALITY_PENDING",
    "DAILY_STOP_LATCHED",
    "FINAL_TARGET_TOPOLOGY_INVALID",
    "UNSUPPORTED_FRACTIONAL_RISK_QUANTITY",
}


def derive_source_close_consumption(
    journal: Journal,
    initial: OcoResearchState,
    *,
    close_id: str,
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            return _derive(journal, initial, close_id, through_sequence, decision_at)
    finally:
        if owns:
            journal._db.rollback()


def _derive(
    journal: Journal,
    initial: OcoResearchState,
    close_id: str,
    horizon: int,
    at: datetime,
) -> dict[str, Any]:
    from .account_risk_owner import AccountRiskOwner
    from .crossed_stop import _text, _time
    from .crossed_stop_attribution import read_source_http_close_attributions_at
    from .crossed_stop_contract import MANIFEST_SHA256, VERSION
    from .crossed_stop_preparation import source_close_handoff_lineage
    from .native_oco_requests import _copy, _digest, _json
    from .oco_journal import _causal_oco_reader
    from .risk_account_inputs import KIND as ACCOUNT_KIND
    from .risk_account_inputs import SOURCE as ACCOUNT_SOURCE
    from .risk_evidence_request import IncidentRiskRequest
    from .source_close_http import ORIGIN, read_source_close_http_at
    from .source_risk_registry import POLICY as SOURCE_POLICY

    _text(close_id)
    _time(at)
    journal.require_private_order_state()
    if type(horizon) is not int or not 0 <= horizon <= journal.read_evidence_sequence():
        raise JournalError("bounded source consumption horizon required")
    owner = _causal_oco_reader(journal, initial)
    binding = dict(
        owner.binding,
        emergency_version=VERSION,
        emergency_manifest_sha256=MANIFEST_SHA256,
    )
    cid = "cs-" + hashlib.sha256(_json([binding, close_id])).hexdigest()[:40]
    lineage = source_close_handoff_lineage(
        journal, cid, through_sequence=horizon, at=at
    )
    if (
        lineage is None
        or lineage["binding"] != binding
        or lineage["close_id"] != close_id
    ):
        raise JournalError("original source consumption handoff required")
    view = owner.read_verified_at(horizon, at)
    if view is None or view.entry_binding is None or view.conflict_keys:
        raise JournalError("clean original source lot owner required")
    fee_policy = Path(view.entry_binding["fee_policy"])
    risk, risk_refs = AccountRiskOwner._read_native_causal_evidence_at(
        journal, initial.account_ref, at, fee_policy, through_sequence=horizon
    )
    if (
        risk is None
        or not risk_refs
        or risk["veto"] is not False
        or risk.get("_source_policy") != SOURCE_POLICY
    ):
        raise JournalError("latest source account owner required")
    saved = journal.read_event(risk_refs[-1])
    assert saved is not None
    event, raw = saved
    item = json.loads(raw)
    snapshot = risk.get("last_snapshot")
    held = risk.get("held")
    if (
        risk.get("watermark") != event.sequence
        or item.get("selection") is None
        or item.get("_source_policy") != SOURCE_POLICY
        or not isinstance(snapshot, dict)
        or snapshot.get("qualified") is not True
        or snapshot != item.get("snapshot")
        or not isinstance(held, dict)
        or held.get("client_order_id") != initial.entry_scope
        or held.get("symbol") != initial.symbol
        or set(risk.get("issues", [])) - _ALLOWED_RISK_ISSUES
        or held.get("source_account_observation", {}).get("reported_account_agreement")
        is not True
    ):
        raise JournalError(
            "latest qualified same-slot source account snapshot required"
        )
    bound = held.get("source_close_bindings", {}).get(cid)
    if not isinstance(bound, dict) or any(
        bound.get(key) != lineage[key]
        for key in (
            "handoff_ref",
            "preparation_ref",
            "entry_reservation_ref",
            "portions",
        )
    ):
        raise JournalError("source consumption risk basket differs")
    selection = IncidentRiskRequest.from_dict(item["selection"])
    captures = [
        event.sequence
        for event, _raw in journal._read_private_events(ACCOUNT_SOURCE, horizon)
        if event.event_kind == ACCOUNT_KIND and event.visible_at(at)
    ]
    if not captures or max(captures) != selection.capture_sequence:
        raise JournalError("newer account capture requires source risk observation")
    # Requalify current evidence with the ORIGINAL canonical risk selection and
    # age bounds, not caller data or a current risk-owner constructor.
    reader = object.__new__(AccountRiskOwner)
    reader.journal, reader.account_ref, reader.fee_policy = (
        journal,
        initial.account_ref,
        fee_policy,
    )
    current_item = dict(item, at=at.isoformat(), source_horizon=horizon)
    current = reader._snapshot(current_item)
    if current.get("qualified") is not True or not isinstance(
        current.get("source"), dict
    ):
        raise JournalError("current source account evidence unqualified or stale")
    index = read_source_http_close_attributions_at(
        journal, initial, through_sequence=horizon, decision_at=at
    )
    if index["latched"] or index["conflicts"] or cid not in index["cid_claims"]:
        raise JournalError("current source attribution index required")
    claim = index["cid_claims"][cid]
    operation = next(
        row
        for row in index["observations"]
        if row["observation_ref"] == claim["latest_ref"]
    )
    proof = operation["proof"]
    attempts = read_source_close_http_at(
        journal, initial, through_sequence=horizon, decision_at=at
    )
    matches = [row for row in attempts if row["precall"]["client_order_id"] == cid]
    if len(matches) != 1:
        raise JournalError("one canonical source HTTP attempt required")
    attempt = matches[0]
    pre, outcome = attempt["precall"], attempt["outcome"]
    if (
        pre["origin"] != ORIGIN
        or ORIGIN != "INJECTED_EXCHANGE"
        or outcome is None
        or outcome["result_kind"] != "HTTP_RESPONSE"
        or outcome["http_status"] not in {200, 201}
        or attempt["attempt_ref"] != proof["attempt_ref"]
        or attempt["outcome_ref"] != proof["outcome_ref"]
        or proof["origin"] != ORIGIN
        or proof["account_ref"] != initial.account_ref
        or proof["entry_scope"] != initial.entry_scope
        or proof["client_order_id"] != cid
        or proof["original_portions"] != lineage["portions"]
        or any(
            proof[key] != lineage[key]
            for key in (
                "handoff_ref",
                "preparation_ref",
                "entry_reservation_ref",
                "emergency_binding_ref",
                "prior_risk_ref",
            )
        )
        or not proof["component_attributed"]
        or proof["issues"]
    ):
        raise JournalError("confirmed same-origin source consumption identity required")
    orders = [
        row for row in current["source"]["orders"] if row["client_order_id"] == cid
    ]
    quantity = sum(
        (Fraction(row["quantity"]) for row in proof["executions"]), Fraction()
    )
    if (
        len(orders) != 1
        or orders[0]["broker_order_id"] != proof["broker_order_id"]
        or Fraction(orders[0]["filled_quantity"]) != quantity
        or quantity != Fraction(proof["cumulative_fill_quantity"])
        or not 0 < quantity <= Fraction(proof["requested_quantity"])
    ):
        raise JournalError("source executions do not match current account order facts")
    capture = journal.read_event(selection.capture_sequence)
    assert capture is not None
    return cast(
        dict[str, Any],
        _copy(
            {
                "schema": 1,
                "policy": POLICY,
                "classification": (
                    "SOURCE_HTTP_REPORTED_FILL_CONSUMPTION_NOT_RISK_RELEASE"
                ),
                "origin": ORIGIN,
                "binding": binding,
                "account_ref": initial.account_ref,
                "entry_scope": initial.entry_scope,
                "close_id": close_id,
                "client_order_id": cid,
                "broker_order_id": proof["broker_order_id"],
                "attempt_id": pre["command"]["attempt_id"],
                "attempt_ref": attempt["attempt_ref"],
                "outcome_ref": attempt["outcome_ref"],
                "capture_refs": proof["capture_refs"],
                "attribution_ref": claim["latest_ref"],
                "account_risk_ref": [event.sequence, event.payload_ref],
                "account_capture_ref": [capture[0].sequence, capture[0].payload_ref],
                "account_selection": item["selection"],
                "account_assessment_hash": _digest(_json(current)),
                "intent_census": item["census"],
                "source_refs": current["source_refs"],
                "handoff_ref": lineage["handoff_ref"],
                "preparation_ref": lineage["preparation_ref"],
                "entry_reservation_ref": lineage["entry_reservation_ref"],
                "emergency_binding_ref": lineage["emergency_binding_ref"],
                "prior_risk_ref": lineage["prior_risk_ref"],
                "original_portions": lineage["portions"],
                "requested_quantity": proof["requested_quantity"],
                "cumulative_fill_quantity": str(quantity),
                "executions": proof["executions"],
                "retained_risk_issues": risk["issues"],
                "retained_quantity_incident": held.get("quantity_incident"),
                "decision_at": at.isoformat(),
                "source_horizon": horizon,
                "basket_policy": "PRESERVE_ENTIRE_ORIGINAL_BASKET",
                "authority": dict(AUTHORITY),
            }
        ),
    )


def read_source_close_consumptions_at(
    journal: Journal,
    initial: OcoResearchState,
    *,
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    """Read the canonical close projection at one immutable causal prefix."""
    from .crossed_stop import CrossedStopJournal
    from .oco_journal import _causal_oco_reader

    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            journal.require_private_order_state()
            reader = object.__new__(CrossedStopJournal)
            reader._initialize(_causal_oco_reader(journal, initial))
            return reader._replay(through_sequence, decision_at)[0]
    finally:
        if owns:
            journal._db.rollback()
