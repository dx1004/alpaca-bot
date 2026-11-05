"""Source-derived final-target semantics; no PATCH bytes or dispatch authority."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ._causal_risk_memo import causal_risk_verification_scope
from .journal import Journal, JournalError
from .oco_research import OcoResearchState

AUTHORITY = {
    "patch_bytes_validated": False,
    "order_authority": False,
    "acknowledgement_adopted": False,
    "protection_accepted": False,
    "sibling_stop_continuity_verified": False,
    "risk_release_authorized": False,
    "runtime_authorized": False,
    "formal_paper_authorized": False,
}


def derive_final_target_preparation(
    journal: Journal,
    initial: OcoResearchState,
    *,
    group_id: str,
    decision_at: datetime,
    max_age: timedelta,
    through_sequence: int,
) -> dict[str, Any]:
    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            return _derive(
                journal, initial, group_id, decision_at, max_age, through_sequence
            )
    finally:
        if owns:
            journal._db.rollback()


def _derive(
    journal: Journal,
    initial: OcoResearchState,
    group_id: str,
    at: datetime,
    max_age: timedelta,
    horizon: int,
) -> dict[str, Any]:
    from .account_risk_owner import AccountRiskOwner
    from .native_oco_evidence import inspect_native_oco_capture
    from .native_oco_requests import _causal_native_requests, _json, _text, _utc
    from .oco_entry_evidence import derive_native_owned_entry_census
    from .oco_exit_evidence import derive_native_exit_census
    from .oco_journal import _causal_oco_reader
    from .paper_order_contract import decode_broker_json
    from .risk_account_inputs import KIND as ACCOUNT_KIND
    from .risk_account_inputs import SOURCE as ACCOUNT_SOURCE

    _text(group_id)
    _utc(at)
    journal.require_private_order_state()
    if (
        type(horizon) is not int
        or not 0 <= horizon <= journal.read_evidence_sequence()
        or type(max_age) is not timedelta
        or max_age <= timedelta(0)
    ):
        raise JournalError("bounded final-target prefix and positive age required")
    owner = _causal_oco_reader(journal, initial)
    view = owner.read_verified_at(horizon, at)
    if view is None or view.entry_binding is None or view.issues:
        raise JournalError("clean source-bound final-target owner required")
    model = view.model
    target = model.final_target
    group = next((row for row in model.groups if row.group_id == group_id), None)
    if group is None or not model.entry_terminal_reconciled or target is None:
        raise JournalError(
            "source terminal entry and allocated final-target group required"
        )
    entry = derive_native_owned_entry_census(
        journal,
        initial,
        decision_at=at,
        through_sequence=horizon,
        fee_policy=Path(view.entry_binding["fee_policy"]),
    )
    if (
        not entry["entry"]["terminal_observed"]
        or entry["entry"] != view.entry_binding["entry"]
        or entry["reservation_ref"] != view.entry_binding["reservation_ref"]
    ):
        raise JournalError("new or changed entry census requires canonical rebinding")
    risk_ref = entry["risk_refs"][-1]
    risk_record = journal.read_event(risk_ref[0])
    assert risk_record is not None
    risk_item = json.loads(risk_record[1])
    current_account = [
        event.sequence
        for event, _ in journal._read_private_events(ACCOUNT_SOURCE, horizon)
        if event.event_kind == ACCOUNT_KIND and event.visible_at(at)
    ]
    if not current_account or max(current_account) != entry["capture_ref"][0]:
        raise JournalError("newest account capture requires native risk observation")
    reader = object.__new__(AccountRiskOwner)
    reader.journal, reader.account_ref, reader.fee_policy = (
        journal,
        initial.account_ref,
        Path(entry["fee_policy"]),
    )
    current = reader._snapshot(
        dict(risk_item, at=at.isoformat(), source_horizon=horizon)
    )
    if (
        current.get("qualified") is not True
        or not isinstance(current.get("native"), dict)
        or current["native"]["risk_issues"]
    ):
        raise JournalError("current native account evidence unqualified or stale")
    bound = next(
        (row for row in view.native_exit_bindings if row["group_id"] == group_id), None
    )
    if bound is None or bound["status"] != "BOUND_NATIVE_EXIT_CENSUS":
        raise JournalError("canonical native group census required")
    identity = bound["identity"]
    relevant_captures = []
    for event, raw in journal._read_private_events("ALPACA_NATIVE_OCO", horizon):
        packet = decode_broker_json(raw)
        if type(packet) is not dict:
            raise JournalError("native capture identity malformed")
        if packet.get("requested_order_id") in {
            identity["root_order_id"],
            identity["stop_order_id"],
        }:
            if not event.visible_at(at) or event.external_clock_conflict:
                raise JournalError("latest native capture is not decision-visible")
            relevant_captures.append(event.sequence)
    if (
        not relevant_captures
        or max(relevant_captures) != bound["capture_ref"]["sequence"]
    ):
        raise JournalError("newest native capture requires canonical group census")
    census = derive_native_exit_census(
        journal,
        initial,
        group_id=group_id,
        capture_sequence=bound["capture_ref"]["sequence"],
        decision_at=at,
        max_age=max_age,
        through_sequence=horizon,
        observation_policy=bound.get("observation_policy"),
    )
    if (
        census["status"] != "BOUND_NATIVE_EXIT_CENSUS"
        or census["identity"] != identity
        or census["executions"] != bound["executions"]
    ):
        raise JournalError("current native source census differs from canonical owner")
    facts = inspect_native_oco_capture(
        journal,
        bound["capture_ref"]["sequence"],
        account_ref=initial.account_ref,
        decision_at=at,
        max_age=max_age,
        through_sequence=horizon,
    )
    if not facts.accounting_complete or len(facts.orders) != 2:
        raise JournalError(
            "complete fresh native target and companion-stop source required"
        )
    root, stop = facts.orders
    residual = model.group_residual(group_id)
    if (
        residual <= 0
        or root.order_id != identity["root_order_id"]
        or stop.order_id != identity["stop_order_id"]
        or root.status not in {"new", "accepted", "partially_filled"}
        or stop.status not in {"new", "accepted", "partially_filled"}
        or root.quantity - root.filled_quantity != residual
        or stop.quantity - stop.filled_quantity != residual
        or root.stop_price is not None
        or root.limit_price != model.provisional_target
        or stop.stop_price != model.stop
        or root.replaces
        or root.replaced_by
        or stop.replaces
        or stop.replaced_by
    ):
        raise JournalError("working original target/stop and exact residual required")
    prepared = _causal_native_requests(owner).read_verified_at(horizon, at)
    if prepared is None or prepared["conflict_groups"]:
        raise JournalError("clean native preintent source required")
    requests = [
        row["receipt"]
        for row in prepared["requests"]
        if row["receipt"]["group_id"] == group_id
    ]
    if len(requests) != 1:
        raise JournalError("one original native group preintent required")
    original = requests[0]
    if (
        original["reservation_ref"] != entry["reservation_ref"]
        or original["binding"] != owner.binding
        or root.client_order_id != original["client_order_id"]
    ):
        raise JournalError(
            "final-target preintent or original risk reservation differs"
        )
    return {
        "schema": 1,
        "classification": "SEMANTIC_FINAL_TARGET_PROPOSAL_NOT_PATCH_BYTES",
        "binding": owner.binding,
        "group_id": group_id,
        "decision_at": at.isoformat(),
        "source_horizon": horizon,
        "rule": "OCO_FIRST_EXECUTION_RESEARCH_V1_FINAL_TARGET_FROM_TERMINAL_ENTRY",
        "target_order_id": root.order_id,
        "companion_stop_order_id": stop.order_id,
        "broker_group_id": root.group_id,
        "original_portions": [
            {
                "entry_execution_id": row.entry_execution_id,
                "quantity": str(row.quantity),
            }
            for row in group.portions
        ],
        "original_group_quantity": str(group.quantity),
        "residual_quantity": str(residual),
        "provisional_target": str(model.provisional_target),
        "final_target": str(target),
        "frozen_stop": str(model.stop),
        "average_entry": str(model.average_entry),
        "entry_census": entry,
        "account_risk_ref": risk_ref,
        "account_assessment_hash": "sha256:"
        + hashlib.sha256(_json(current)).hexdigest(),
        "native_census_ref": bound["capture_ref"],
        "prepared_ref": bound["prepared_ref"],
        "attempt_ref": bound["attempt_ref"],
        "outcome_ref": bound["outcome_ref"],
        "allocation_ref": original["allocation_ref"],
        "original_entry_binding_ref": original["entry_binding_ref"],
        "target_observation": root.to_dict(),
        "stop_observation": stop.to_dict(),
        "continuity": "UNKNOWN",
        "authority": dict(AUTHORITY),
    }
