"""Observed simple-equity entry census only; not protection/source admission.

The current source owner supports whole-filled simple orders. Fractional entry
executions and native grouped exits are not qualified by this adapter. It never
creates an intent, changes a reservation, or promotes declared exits/ACKs.
"""

import json
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from .account_risk_owner import AccountRiskOwner
from .incident_risk import IncidentRiskRequest
from .journal import Journal, JournalError
from .oco_research import OcoResearchState
from .order_lifecycle import LifecycleState, OrderProjection
from .posted_ledger import _exact, _timestamp
from .research_risk_binding import OCO_EXECUTION_IDENTITY
from .risk_ledger_audit import _activities


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _utc(value: datetime) -> None:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise JournalError("UTC entry-census decision required")


def _identity(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise JournalError("exact entry-census identity required")
    return value


def derive_entry_census(
    journal: Journal,
    initial: OcoResearchState,
    *,
    decision_at: datetime,
    through_sequence: int,
    fee_policy: Path,
) -> dict[str, Any]:
    """Derive a receipt at one immutable prefix; reported observations aren't final."""
    _utc(decision_at)
    if (
        type(through_sequence) is not int
        or not 0 <= through_sequence <= journal.read_evidence_sequence()
    ):
        raise JournalError("invalid entry-census source horizon")
    risk, risk_refs = AccountRiskOwner.read_evidence_at(
        journal,
        initial.account_ref,
        decision_at,
        fee_policy,
        through_sequence=through_sequence,
    )
    all_risk = tuple(
        event.sequence
        for event, _ in journal.read_account_risk_events()
        if event.sequence <= through_sequence
    )
    if risk is None or not risk_refs or all_risk != risk_refs:
        raise JournalError("latest source risk prefix missing or not decision-visible")
    snapshot, held = risk.get("last_snapshot"), risk.get("held")
    if (
        not isinstance(snapshot, dict)
        or not isinstance(held, dict)
        or snapshot.get("qualified") is not True
        or snapshot.get("issues") != []
        or risk.get("issues") != []
        or risk.get("source_snapshot_qualified") is not True
        or risk.get("veto") is not False
        or risk.get("daily_stop_latched") is not False
        or risk.get("runtime_authorized") is not False
    ):
        raise JournalError("current held source risk is missing, vetoed or unqualified")
    selection = IncidentRiskRequest.from_dict(snapshot["selection"])
    observed = datetime.fromisoformat(snapshot["observed_at"])
    risk_at = datetime.fromisoformat(risk["at"])
    for stamp in (observed, risk_at):
        _utc(stamp)
        if not timedelta(0) <= decision_at - stamp <= selection.max_age:
            raise JournalError("stale or future entry-census risk snapshot")
    declaration = held.get("research_signal")
    if (
        held.get("client_order_id") != initial.entry_scope
        or held.get("symbol") != initial.symbol
        or _exact(held.get("limit"), positive=True) != initial.limit
        or _exact(held.get("stop"), positive=True) != initial.stop
        or type(held.get("quantity")) is not int
        or held["quantity"] <= 0
        or not isinstance(declaration, dict)
        or _json(declaration.get("execution_research_binding"))
        != _json(OCO_EXECUTION_IDENTITY)
    ):
        raise JournalError("held entry scope/config/execution research binding differs")
    orders = snapshot.get("orders")
    if not isinstance(orders, dict) or initial.entry_scope not in orders:
        raise JournalError("source-owned entry lifecycle missing")
    entry = OrderProjection.from_dict(orders[initial.entry_scope])
    if (
        entry.account_ref != initial.account_ref
        or entry.symbol != initial.symbol
        or entry.client_order_id != initial.entry_scope
        or entry.side != "buy"
        or Fraction(entry.quantity) != held["quantity"]
        or entry.issues
    ):
        raise JournalError("source entry lifecycle identity/quantity differs")
    capture_sequence = selection.capture_sequence
    capture = journal.read_event(capture_sequence)
    if (
        capture is None
        or capture_sequence > through_sequence
        or not capture[0].visible_at(decision_at)
    ):
        raise JournalError("entry activity capture unavailable at source prefix")
    executions = []
    quantity = Fraction(0)
    for activity_id, row in _activities(journal, capture_sequence).items():
        if (
            row.get("activity_type") != "FILL"
            or row.get("order_id") != entry.broker_order_id
        ):
            continue
        if (
            _identity(row.get("id")) != activity_id
            or row.get("symbol") != initial.symbol
            or row.get("side") != "buy"
        ):
            raise JournalError("source entry execution identity/side/symbol differs")
        qty, price = (
            _exact(row.get("qty"), positive=True),
            _exact(row.get("price"), positive=True),
        )
        if qty.denominator != 1:
            raise JournalError("fractional entry source seam unqualified; no rounding")
        stamp, remainder = _timestamp(row.get("transaction_time"))
        delta = stamp - decision_at
        if Fraction(delta // timedelta(microseconds=1), 1_000_000) + remainder > 0:
            raise JournalError("future entry execution unavailable")
        quantity += qty
        executions.append(
            {
                "execution_id": activity_id,
                "account_ref": initial.account_ref,
                "symbol": initial.symbol,
                "entry_scope": initial.entry_scope,
                "role": "ENTRY",
                "quantity": str(qty),
                "price": str(price),
                "group_id": None,
                "transaction_time": row["transaction_time"],
            }
        )
    if (
        type(held.get("filled")) is not int
        or held["filled"] < 0
        or quantity != Fraction(entry.filled_quantity)
        or quantity != held["filled"]
        or quantity > held["quantity"]
    ):
        raise JournalError(
            "entry execution qty disagrees with lifecycle/held reservation"
        )
    # Canonical census order is not proof of venue/receipt sequencing.
    executions.sort(
        key=lambda row: (_timestamp(row["transaction_time"]), row["execution_id"])
    )
    reservation_ref = None
    reservation = None
    for sequence in risk_refs:
        record = journal.read_event(sequence)
        assert record is not None
        item = json.loads(record[1])
        plan = item.get("plan")
        if (
            item.get("kind") == "RESERVE"
            and isinstance(plan, dict)
            and plan.get("client_order_id") == initial.entry_scope
            and _json(item.get("research_signal")) == _json(declaration)
        ):
            # A matching command is not necessarily the positive slot origin.
            before, _ = AccountRiskOwner.read_evidence_at(
                journal,
                initial.account_ref,
                record[0].available_at,
                fee_policy,
                through_sequence=sequence - 1,
            )
            created, _ = AccountRiskOwner.read_evidence_at(
                journal,
                initial.account_ref,
                record[0].available_at,
                fee_policy,
                through_sequence=sequence,
            )
            created_held = None if created is None else created.get("held")
            if (
                (before is not None and before.get("held") is not None)
                or not isinstance(created_held, dict)
                or created_held.get("client_order_id") != initial.entry_scope
                or created_held.get("quantity") != held["quantity"]
                or created is None
                or created.get("decision", {}).get("quantity") != held["quantity"]
                or created.get("veto") is not False
                or created.get("issues") != []
                or _json(created_held.get("research_signal")) != _json(declaration)
            ):
                continue
            reservation_ref = [sequence, record[0].payload_ref]
            reservation = {
                "event_id": item["key"],
                "plan": plan,
                "research_signal": declaration,
                "quantity": held["quantity"],
            }
            break
    if reservation is None:
        raise JournalError("original OCO-bound reservation source missing")
    intent = journal.read_order_intent(initial.entry_scope)
    latest_risk = journal.read_event(risk_refs[-1])
    assert latest_risk is not None
    intent_census = json.loads(latest_risk[1])["census"]
    intent_row = next(
        (row for row in intent_census if row["id"] == initial.entry_scope), None
    )
    if (
        not isinstance(intent, dict)
        or intent.get("purpose") != "ENGINEERING_TEST"
        or intent_row is None
    ):
        raise JournalError("immutable engineering intent origin unavailable")

    def references(sequences: tuple[int, ...]) -> list[list[object]]:
        result: list[list[object]] = []
        for sequence in sequences:
            record = journal.read_event(sequence)
            if (
                record is None
                or sequence > through_sequence
                or not record[0].visible_at(decision_at)
            ):
                raise JournalError(
                    "entry-census reference unavailable at source prefix"
                )
            result.append([sequence, record[0].payload_ref])
        return result

    source_refs = snapshot.get("source_refs")
    if not isinstance(source_refs, list):
        raise JournalError("entry-census audited source references missing")
    exact_sources = references(tuple(row[0] for row in source_refs))
    if exact_sources != source_refs:
        raise JournalError("entry-census source reference hashes differ")
    return {
        "schema": 1,
        "classification": "LOCAL_REPORTED_ENTRY_CENSUS_NOT_SOURCE_ADMISSION",
        "formal_sample_eligible": False,
        "intent_origin": {
            "purpose": intent["purpose"],
            "paper_endpoint": intent["paper_endpoint"],
            "paper_account_ref": intent["paper_account_ref"],
            "intent_sha256": intent_row["hash"],
            "census_ref": [risk_refs[-1], latest_risk[0].payload_ref],
        },
        "decision_at": decision_at.isoformat(),
        "source_horizon": through_sequence,
        "fee_policy": str(fee_policy.resolve()),
        "fee_document_ref": snapshot["fee_document_ref"],
        "risk_refs": references(risk_refs),
        "source_refs": exact_sources,
        "capture_ref": [capture_sequence, capture[0].payload_ref],
        "reservation_ref": reservation_ref,
        "reservation": reservation,
        "entry": {
            "client_order_id": entry.client_order_id,
            "broker_order_id": entry.broker_order_id,
            "reserved_quantity": held["quantity"],
            "filled_quantity": str(quantity),
            "lifecycle_state": entry.state.value,
            "terminal_observed": entry.state
            in {
                LifecycleState.FILLED,
                LifecycleState.CANCELED,
                LifecycleState.EXPIRED,
                LifecycleState.REJECTED,
            },
            "executions": executions,
        },
        "reported_entry_census_bound": True,
        "authority": initial.authority,
        "limitations": [
            "OBSERVED_PREFIX_NOT_FINAL",
            "SIMPLE_EQUITY_WHOLE_FILL_SEAM_ONLY",
            "CANONICAL_ENTRY_CENSUS_NOT_VENUE_SEQUENCE",
            "DECLARED_EXITS_AND_ACKS_NOT_PROMOTED",
            "FULL_SOURCE_AND_PROTECTION_ACCEPTANCE_UNQUALIFIED",
        ],
    }
