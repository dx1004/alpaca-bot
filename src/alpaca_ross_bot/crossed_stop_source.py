"""Rebuild a crossing from immutable risk-selected IEX evidence, never clearance.

The entry bridge remains the existing whole-fill simple-order source seam. Native
exit allocations, actual broker ownership and dispatch are NOT qualified here.
"""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .account_risk_owner import AccountRiskOwner
from .journal import JournalError
from .oco_journal import OcoJournal, _json
from .posted_ledger import _exact, _timestamp
from .reported_price_policy import (
    assess_reported_price_record,
    load_reported_price_policy,
)
from .risk_baseline import session_interval_from_evidence
from .risk_evidence_request import IncidentRiskRequest
from .risk_ledger_audit import _activities
from .strategy_features import Session

_ROOT = Path(__file__).resolve().parents[2]


def derive_source_crossing(
    owner: OcoJournal,
    *,
    horizon: int,
    at: datetime,
) -> dict[str, Any]:
    """Caller holds a snapshot/write lock; every accepted reference predates it."""
    view = owner.read_verified_at(horizon, at)
    if view is None or view.conflict_keys or view.entry_binding is None:
        raise JournalError("source-bound entry and clean immutable prefix required")
    bound = view.entry_binding
    model = view.model
    fee_policy = Path(bound["fee_policy"])
    risk, risk_refs = AccountRiskOwner.read_evidence_at(
        owner.journal,
        model.account_ref,
        at,
        fee_policy,
        through_sequence=horizon,
    )
    all_risk = tuple(
        event.sequence
        for event, _ in owner.journal.read_account_risk_events()
        if event.sequence <= horizon
    )
    if risk is None or not risk_refs or risk_refs != all_risk:
        raise JournalError("current source risk prefix missing")
    snapshot, held = risk.get("last_snapshot"), risk.get("held")
    if not isinstance(snapshot, dict) or not isinstance(held, dict):
        raise JournalError("current source snapshot and original held slot required")
    if (
        held.get("client_order_id") != model.entry_scope
        or held.get("symbol") != model.symbol
        or _exact(held.get("limit"), positive=True) != model.limit
        or _exact(held.get("stop"), positive=True) != model.stop
        or _json(held.get("research_signal"))
        != _json(bound["reservation"]["research_signal"])
    ):
        raise JournalError("current source slot differs from bound entry")
    if type(held.get("filled")) is not int or held["filled"] != model.entry_quantity:
        raise JournalError("source entry census advanced; rebind before observation")
    selection = IncidentRiskRequest.from_dict(snapshot["selection"])
    # Equal cumulative quantity alone cannot establish that the owned lots are
    # still the same executions. Preserve exact identity/price/time ancestry.
    current_entries = {}
    for identity, row in _activities(owner.journal, selection.capture_sequence).items():
        if (
            row.get("activity_type") == "FILL"
            and row.get("order_id") == bound["entry"]["broker_order_id"]
        ):
            if row.get("symbol") != model.symbol or row.get("side") != "buy":
                raise JournalError("source entry execution scope differs")
            current_entries[identity] = (
                _exact(row.get("qty"), positive=True),
                _exact(row.get("price"), positive=True),
                _timestamp(row.get("transaction_time")),
            )
    bound_entries = {
        row["execution_id"]: (
            _exact(row["quantity"], positive=True),
            _exact(row["price"], positive=True),
            _timestamp(row["transaction_time"]),
        )
        for row in bound["entry"]["executions"]
    }
    if current_entries != bound_entries:
        raise JournalError("source entry executions changed; rebind before observation")
    for stamp in (
        datetime.fromisoformat(snapshot["observed_at"]),
        datetime.fromisoformat(risk["at"]),
    ):
        if not timedelta(0) <= at - stamp <= selection.max_age:
            raise JournalError("source crossing risk snapshot stale or future")
    selected = [row for row in selection.mark_refs if row[0] == model.symbol]
    if len(selected) != 1:
        raise JournalError("one immutable risk-selected quote required")
    _, sequence, index, metadata = selected[0]
    refs = set(risk_refs) | {
        sequence,
        metadata,
        selection.calendar_sequence,
        selection.capture_sequence,
    }
    refs.add(bound["reservation_ref"][0])
    refs.add(bound["capture_ref"][0])
    sources = []
    for ref in sorted(refs):
        stored = owner.journal.read_event(ref)
        if stored is None or ref > horizon or not stored[0].visible_at(at):
            raise JournalError("source crossing reference outside visible prefix")
        sources.append([ref, stored[0].payload_ref])
    day = at.astimezone(ZoneInfo("America/New_York")).date()
    opened, closed = session_interval_from_evidence(
        owner.journal,
        selection.calendar_sequence,
        model.account_ref,
        day,
        at,
        selection.max_age,
    )
    calendar = owner.journal.read_event(selection.calendar_sequence)
    quote = owner.journal.read_event(sequence)
    assert calendar is not None and quote is not None
    policy = load_reported_price_policy(
        (_ROOT / "evidence/alpaca-reported-intraday-price-policy-v1.json").read_bytes(),
        observation_parent=(
            _ROOT / "evidence/iex-snapshot-observation-age-research-v1.json"
        ).read_bytes(),
        oco_parent=(
            _ROOT / "evidence/oco-first-execution-research-v1.json"
        ).read_bytes(),
    )
    price = assess_reported_price_record(
        owner.journal,
        policy,
        journal_scope="account-entry:"
        + _json([model.account_ref, model.entry_scope]).decode(),
        through_sequence=horizon,
        sequence=sequence,
        record_index=index,
        payload_ref=quote[0].payload_ref,
        decision_at=at,
        session=Session(
            day, opened, closed, calendar[0].available_at, calendar[0].payload_ref
        ),
        purpose="ENTRY_QUOTE",
        metadata_sequence=metadata,
        max_metadata_age=selection.max_metadata_age,
    )
    issues = list(price.issues)
    if price.symbol != model.symbol or price.kind != "q":
        issues.append("SOURCE_QUOTE_SYMBOL_OR_KIND_MISMATCH")
    if not opened <= at < closed:
        issues.append("DECISION_OUTSIDE_OFFICIAL_RTH")
    if (
        snapshot.get("qualified") is not True
        or snapshot.get("issues") != []
        or risk.get("source_snapshot_qualified") is not True
    ):
        issues.append("REPORTED_RISK_SNAPSHOT_UNQUALIFIED")
    qualified = price.component_qualified and not issues
    return {
        "schema": 1,
        "classification": "RAW_PRICE_AND_ENTRY_REFERENCED_CROSSING_NOT_ADMISSION",
        "source_horizon": horizon,
        "decision_at": at.isoformat(),
        "account_ref": model.account_ref,
        "entry_scope": model.entry_scope,
        "symbol": model.symbol,
        "source_refs": sources,
        "entry_reservation_ref": bound["reservation_ref"],
        "entry_capture_ref": bound["capture_ref"],
        "entry_binding_sha256": hashlib.sha256(_json(bound)).hexdigest(),
        "price_policy_sha256": price.contract_sha256,
        "quote_ref": [sequence, quote[0].payload_ref, index],
        "calendar_ref": [selection.calendar_sequence, calendar[0].payload_ref],
        "open_at": opened.isoformat(),
        "close_at": closed.isoformat(),
        "stop": str(model.stop),
        "reported_bid": None if price.reported_bid is None else str(price.reported_bid),
        "component_qualified": qualified,
        "crossing_observed": (
            qualified
            and model.unallocated_quantity > 0
            and price.reported_bid is not None
            and price.reported_bid <= model.stop
        ),
        "issues": sorted(set(issues)),
        "risk_veto_observed": risk.get("veto"),
        "daily_stop_latched_observed": risk.get("daily_stop_latched"),
        "coverage_label": "NOT_SIP_EQUIVALENT",
        "source_admission_accepted": False,
        "order_authority": False,
        "runtime_authorized": False,
        "formal_paper_authorized": False,
        "limitations": [
            "REPORTED_IEX_BID_NOT_BROKER_BASE_OR_NBBO",
            "WHOLE_FILL_SIMPLE_ENTRY_SOURCE_SEAM_ONLY",
            "NATIVE_EXIT_OWNERSHIP_AND_RESERVATIONS_UNQUALIFIED",
            "NO_FRESHNESS_SESSION_OR_RISK_CLEARANCE_FOR_DISPATCH",
        ],
    }
