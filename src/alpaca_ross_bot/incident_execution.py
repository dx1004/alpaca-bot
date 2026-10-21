"""Reported execution incident evidence; never recovery or dispatch authority.

The source horizon is supplied by the journal writer's existing transaction.
Unknown roles are quarantined by identity and retained through raw references,
not guessed from order side. The simple-equity source seam does not establish
native group protection, fractional execution eligibility, or formal enrollment.
"""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from .account_risk_owner import AccountRiskOwner
from .incident_risk import IncidentRiskRequest
from .incident_state import (
    IncidentObservation,
    IncidentOrder,
    IncidentPolicy,
    OrderRole,
)
from .journal import Journal, JournalError
from .oco_research import CANDIDATE
from .order_lifecycle import LifecycleState, OrderProjection
from .order_reconciliation import OrderReconciler
from .research_risk_binding import OCO_EXECUTION_IDENTITY


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _utc(value: datetime) -> None:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise JournalError("UTC execution incident decision required")


def _text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise JournalError("exact execution incident identity required")
    return value


def _owned_scope(
    journal: Journal,
    policy: IncidentPolicy,
    risk: dict[str, Any] | None,
    risk_refs: tuple[int, ...],
    fee_policy: Path,
    reasons: set[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if risk is None:
        reasons.add("SOURCE_RISK_MISSING")
        return None, None
    completed = risk.get("completed")
    if not isinstance(completed, dict):
        raise JournalError("source completed-scope census unavailable")
    held = risk.get("held")
    completion = completed.get(policy.scope_id)
    scoped: dict[str, Any] | None
    if isinstance(held, dict) and held.get("client_order_id") == policy.scope_id:
        scoped = held
    elif isinstance(completion, dict):
        scoped = None
        for sequence in risk_refs:
            record = journal.read_event(sequence)
            assert record is not None
            prior, _ = AccountRiskOwner.read_evidence_at(
                journal,
                policy.account_ref,
                record[0].available_at,
                fee_policy,
                through_sequence=sequence,
            )
            candidate = None if prior is None else prior.get("held")
            if (
                isinstance(candidate, dict)
                and candidate.get("client_order_id") == policy.scope_id
            ):
                scoped = candidate
        if scoped is None:
            raise JournalError("completed scope lacks original held ownership")
        if set(completion.get("order_ids", ())) != {policy.scope_id, *scoped["exits"]}:
            raise JournalError("completed scope roles differ from original ownership")
    elif isinstance(held, dict) or completed:
        raise JournalError(
            "incident scope is neither current nor a completed owned entry"
        )
    else:
        reasons.add("OWNED_SCOPE_UNAVAILABLE")
        return None, None
    declaration = scoped.get("research_signal")
    if (
        scoped.get("symbol") != policy.symbol
        or not isinstance(declaration, dict)
        or _json(declaration.get("execution_research_binding"))
        != _json(OCO_EXECUTION_IDENTITY)
        or declaration.get("version")
        != OCO_EXECUTION_IDENTITY["entry_candidate_version"]
    ):
        raise JournalError("incident symbol/parent/OCO research declaration differs")
    _text(declaration.get("signal_id"))
    return scoped, completion


def derive_execution_incident(
    journal: Journal,
    policy: IncidentPolicy,
    *,
    event_id: str,
    decision_at: datetime,
    through_sequence: int,
    fee_policy: Path,
) -> dict[str, Any]:
    """Read one exact prefix, preserving quarantined facts without changing owners."""
    _utc(decision_at)
    _text(event_id)
    if (
        type(through_sequence) is not int
        or not 0 < through_sequence <= journal.read_evidence_sequence()
    ):
        raise JournalError("positive execution incident source horizon required")
    policy = IncidentPolicy.from_dict(policy.to_dict())
    if (
        policy.version != CANDIDATE
        or policy.oco_contract is None
        or policy.observation_contract is None
        or not {"P11", "P12", "P13"} <= set(policy.rule_refs)
        or policy.runtime_risk_accepted
        or policy.protection_contract_accepted
    ):
        raise JournalError(
            "explicit non-runtime OCO execution incident policy required"
        )
    risk, risk_refs = AccountRiskOwner.read_evidence_at(
        journal,
        policy.account_ref,
        decision_at,
        fee_policy,
        through_sequence=through_sequence,
    )
    lifecycle, life_refs = OrderReconciler.read_evidence_at(
        journal,
        policy.account_ref,
        decision_at,
        through_sequence=through_sequence,
    )
    reasons = {
        "SOURCE_ADMISSION_UNQUALIFIED",
        "PROTECTION_UNQUALIFIED",
        "CLOSING_CALENDAR_UNQUALIFIED",
    }
    risk_rows = tuple(
        event.sequence
        for event, _ in journal.read_account_risk_events()
        if event.sequence <= through_sequence
    )
    life_rows = tuple(
        event.sequence
        for event, _ in journal.read_order_events(through_sequence=through_sequence)
    )
    complete_prefix = set(risk_rows) <= set(risk_refs) and set(life_rows) <= set(
        life_refs
    )
    if not complete_prefix:
        reasons.add("SOURCE_PREFIX_UNAVAILABLE")
    scoped, completion = _owned_scope(
        journal, policy, risk, risk_refs, fee_policy, reasons
    )
    raw_orders = lifecycle.get("orders")
    if not isinstance(raw_orders, dict):
        raise JournalError("execution incident lifecycle orders unavailable")
    orders = {cid: OrderProjection.from_dict(row) for cid, row in raw_orders.items()}
    if any(
        order.account_ref != policy.account_ref or order.client_order_id != cid
        for cid, order in orders.items()
    ):
        raise JournalError("execution incident lifecycle account/identity differs")
    roles: dict[str, OrderRole] = {}
    if scoped is not None:
        roles[policy.scope_id] = OrderRole.ENTRY
        exits = scoped.get("exits")
        if not isinstance(exits, dict):
            raise JournalError("explicit owned exit roles unavailable")
        for cid, role in exits.items():
            if role not in {"STOP", "TARGET", "CLOSE"} or cid in roles:
                raise JournalError("unsupported or conflicting owned exit role")
            roles[_text(cid)] = OrderRole(role)
    observed_orders = []
    for cid, role in sorted(roles.items()):
        order = orders.get(cid)
        if order is None:
            reasons.add("OWNED_ORDER_EVIDENCE_MISSING:" + cid)
            continue
        if order.symbol != policy.symbol or order.side != (
            "buy" if role == OrderRole.ENTRY else "sell"
        ):
            raise JournalError("owned incident order symbol/side differs")
        if order.quantity % 1 or order.filled_quantity % 1:
            reasons.add("FRACTIONAL_SOURCE_SEAM_UNQUALIFIED")
        observed_orders.append(
            IncidentOrder(
                order, role, False, order.state == LifecycleState.CANCEL_PENDING
            )
        )
    for cid in sorted(set(orders) - set(roles)):
        reasons.add("FOREIGN_OR_UNOWNED_LIFECYCLE_ORDER:" + cid)
    baseline = lifecycle.get("baseline")
    baseline_verified = isinstance(baseline, dict)
    baseline_quantity = Decimal(0)
    if baseline_verified:
        assert isinstance(baseline, dict)
        raw = baseline.get(policy.symbol, "0")
        if type(raw) is not str:
            raise JournalError("recorded incident baseline quantity is invalid")
        baseline_quantity = Decimal(raw)
        if not baseline_quantity.is_finite() or baseline_quantity < 0:
            raise JournalError("recorded incident baseline is unrepresentable")
    else:
        reasons.add("RECORDED_BASELINE_MISSING")
    refs = set(risk_refs) | set(life_refs)
    expected_refs: dict[int, str] = {}
    intent_referenced = False
    for sequence in risk_refs:
        record = journal.read_event(sequence)
        assert record is not None
        item = json.loads(record[1])
        intent_referenced = intent_referenced or any(
            row.get("id") == policy.scope_id for row in item.get("census", [])
        )
        snapshot = item.get("snapshot")
        if isinstance(snapshot, dict):
            for source_sequence, digest in snapshot.get("source_refs", []):
                if type(source_sequence) is not int or type(digest) is not str:
                    raise JournalError("invalid incident source reference")
                refs.add(source_sequence)
                if (
                    source_sequence in expected_refs
                    and expected_refs[source_sequence] != digest
                ):
                    raise JournalError("conflicting incident source reference")
                expected_refs[source_sequence] = digest
    position: Decimal | None = None
    snapshot = None if risk is None else risk.get("last_snapshot")
    if isinstance(snapshot, dict) and isinstance(snapshot.get("positions"), dict):
        for symbol, quantity in snapshot["positions"].items():
            if symbol != policy.symbol and Fraction(quantity):
                reasons.add("FOREIGN_REPORTED_POSITION:" + symbol)
    if (
        isinstance(snapshot, dict)
        and snapshot.get("qualified") is True
        and complete_prefix
    ):
        assert risk is not None
        selection = IncidentRiskRequest.from_dict(snapshot["selection"])
        observed = datetime.fromisoformat(snapshot["observed_at"])
        _utc(observed)
        current_at = datetime.fromisoformat(risk["at"])
        _utc(current_at)
        if not all(
            timedelta(0) <= decision_at - stamp <= selection.max_age
            for stamp in (observed, current_at)
        ):
            reasons.add("SOURCE_POSITION_STALE_OR_FUTURE")
        elif any(
            snapshot.get("orders", {}).get(cid) != raw_orders.get(cid) for cid in roles
        ):
            reasons.add("SOURCE_POSITION_LIFECYCLE_MISMATCH")
        elif (
            isinstance(risk.get("held"), dict)
            and risk["held"]["client_order_id"] != policy.scope_id
            and risk["held"]["symbol"] == policy.symbol
        ):
            reasons.add("SOURCE_POSITION_SCOPE_UNRESOLVED")
        else:
            value = Fraction(snapshot["positions"].get(policy.symbol, "0"))
            if value.denominator != 1:
                reasons.add("FRACTIONAL_SOURCE_SEAM_UNQUALIFIED")
            else:
                position = Decimal(value.numerator)
    else:
        reasons.add("SOURCE_POSITION_UNQUALIFIED")
    if risk is not None and (
        risk.get("issues") or risk.get("veto") or risk.get("daily_stop_latched")
    ):
        reasons.add("SOURCE_RISK_QUARANTINED")
    if completion is not None and (
        (position is not None and position != 0)
        or policy.scope_id not in orders
        or Fraction(orders[policy.scope_id].filled_quantity) != completion["filled"]
    ):
        reasons.add("COMPLETED_SCOPE_LATE_ENTRY_OR_NONFLAT")
    connected = (
        complete_prefix
        and lifecycle.get("stream_status") == "SUBSCRIBED"
        and lifecycle.get("stream_gap") is False
    )
    if not connected:
        reasons.add("STREAM_CONTINUITY_UNQUALIFIED")
    if lifecycle.get("issues"):
        reasons.add("LIFECYCLE_QUARANTINED")
    intent = journal.read_order_intent(policy.scope_id) if intent_referenced else None
    if (
        intent_referenced
        and isinstance(intent, dict)
        and intent.get("purpose") == "ENGINEERING_TEST"
    ):
        reasons.add("ENGINEERING_TEST_SOURCE_NOT_FORMAL_SAMPLE")
    else:
        reasons.add("INTENT_ORIGIN_UNQUALIFIED")
    source_refs = []
    for sequence in sorted(refs):
        record = journal.read_event(sequence)
        if (
            record is None
            or sequence > through_sequence
            or not record[0].visible_at(decision_at)
        ):
            raise JournalError("execution incident source reference unavailable")
        if (
            sequence in expected_refs
            and record[0].payload_ref != expected_refs[sequence]
        ):
            raise JournalError("execution incident source hash differs")
        source_refs.append({"sequence": sequence, "hash": record[0].payload_ref})
    request = {
        "event_id": event_id,
        "decision_at": decision_at.isoformat(),
        "fee_policy": str(fee_policy.resolve()),
    }
    evidence_ref = (
        "sha256:"
        + hashlib.sha256(
            _json(
                {
                    "request": request,
                    "policy": policy.to_dict(),
                    "source_horizon": through_sequence,
                    "source_refs": source_refs,
                }
            )
        ).hexdigest()
    )
    unknown = (
        not complete_prefix
        or bool(lifecycle.get("issues"))
        or bool(risk and risk.get("veto"))
        or any(
            order.state in {LifecycleState.UNKNOWN, LifecycleState.CANCEL_PENDING}
            or order.issues
            or (
                not connected
                and order.state
                not in {
                    LifecycleState.FILLED,
                    LifecycleState.CANCELED,
                    LifecycleState.EXPIRED,
                    LifecycleState.REJECTED,
                }
            )
            for order in orders.values()
        )
    )
    observation = IncidentObservation(
        event_id,
        policy.account_ref,
        policy.version,
        policy.scope_id,
        evidence_ref,
        through_sequence,
        baseline_quantity,
        baseline_verified,
        position,
        tuple(observed_orders),
        stream_connected=connected,
        outcome_unknown=unknown,
        daily_stop=bool(risk and risk.get("daily_stop_latched") is True),
    )
    observation = IncidentObservation.from_dict(observation.to_dict())
    return {
        "schema": 1,
        "classification": "LOCAL_REPORTED_EXECUTION_INCIDENT_NOT_SOURCE_ADMISSION",
        "request": request,
        "source_horizon": through_sequence,
        "source_refs": source_refs,
        "observation": observation.to_dict(),
        "quarantine_reasons": sorted(reasons),
        "authority": {
            "source_admission_accepted": False,
            "protection_accepted": False,
            "runtime_authorized": False,
            "dispatch_authorized": False,
            "formal_sample_eligible": False,
        },
    }
