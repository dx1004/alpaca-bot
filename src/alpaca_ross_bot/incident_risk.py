"""Source-bound incident risk observations; never execution acceptance."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .account_risk_owner import AccountRiskOwner
from .journal import Journal, JournalError
from .risk_baseline import session_open_from_evidence
from .risk_evidence_request import IncidentRiskRequest as IncidentRiskRequest
from .risk_input_evidence import inspect_risk_inputs
from .risk_journal import RiskJournal


def assess_incident_risk(
    journal: Journal,
    account_ref: str,
    request: IncidentRiskRequest,
    at: datetime,
    intent_census: object,
    account_risk_fee_policy: Path | None = None,
    source_horizon: int | None = None,
) -> dict[str, object]:
    """Derive facts under the caller's Journal transaction and on immutable replay.

    Default daily-risk inputs remain explicitly fixture-only. An explicit fee
    policy selects the verified actual source owner; neither grants release.
    """
    request.to_dict()
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise ValueError("UTC incident risk clock required")
    if not isinstance(intent_census, list):
        raise JournalError("bound intent census missing")
    ids: list[str] = []
    for row in intent_census:
        if (
            not isinstance(row, list)
            or len(row) != 2
            or not isinstance(row[0], str)
            or not row[0].strip()
            or not isinstance(row[1], str)
        ):
            raise JournalError("bound intent census invalid")
        intent = journal.read_order_intent(row[0])
        digest = hashlib.sha256(
            json.dumps(intent, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        if intent is None or digest != row[1]:
            raise JournalError("bound intent source conflicts")
        ids.append(row[0])
    if ids != sorted(set(ids)):
        raise JournalError("bound intent census must be unique and sorted")
    reasons = {
        "BOUND_RISK_RUNTIME_OWNER_UNQUALIFIED",
        "BOUND_RISK_ACCOUNTING_UNQUALIFIED",
        "BOUND_RISK_DATA_CONTRACT_UNQUALIFIED",
    }
    refs: set[int] = set()
    qualified = funds = ownership = bid = False
    positions: list[list[str]] = []
    try:
        opened = session_open_from_evidence(
            journal,
            request.calendar_sequence,
            account_ref,
            at.astimezone(ZoneInfo("America/New_York")).date(),
            at,
            request.max_age,
        )
        facts = inspect_risk_inputs(
            journal,
            request.capture_sequence,
            account_ref=account_ref,
            decision_at=at,
            session_open=opened,
            max_age=request.max_age,
            mark_refs=request.mark_refs,
            max_metadata_age=request.max_metadata_age,
            _intent_ids=tuple(ids),
            _source_horizon=source_horizon,
        )
        refs.update(sequence for sequence, _ in facts.source_refs)
        refs.add(request.calendar_sequence)
        qualified = facts.reported_inputs_qualified
        funds = facts.ownership.reported_available_funds is not None
        ownership = facts.ownership.ownership_verified
        bid = facts.total_bid_value is not None
        positions = [
            [symbol, str(quantity)]
            for symbol, quantity in facts.ownership.owned_long_positions
        ]
        if not qualified:
            reasons.add("BOUND_RISK_REPORTED_INPUTS_UNQUALIFIED")
    except JournalError:
        raise
    except ValueError:
        reasons.add("BOUND_RISK_SOURCE_INVALID_OR_UNAVAILABLE")
    if account_risk_fee_policy is None:
        daily, daily_refs = RiskJournal.read_evidence_at(journal, account_ref, at)
    else:
        daily, daily_refs = AccountRiskOwner.read_evidence_at(
            journal,
            account_ref,
            at,
            account_risk_fee_policy,
            through_sequence=source_horizon,
        )
        reasons.discard("BOUND_RISK_RUNTIME_OWNER_UNQUALIFIED")
        reasons.add("BOUND_RISK_RUNTIME_RELEASE_UNACCEPTED")
    refs.update(daily_refs)
    daily_stop = False
    if daily is None:
        reasons.add("BOUND_RISK_DAILY_STATE_MISSING")
    else:
        if account_risk_fee_policy is None:
            reasons.add("BOUND_RISK_DAILY_STATE_OFFLINE_FIXTURE_ONLY")
            raw_daily = daily.get("daily")
            raw_at = daily.get("at")
        else:
            raw_daily = {
                "session": daily.get("session"),
                "latched": daily.get("daily_stop_latched"),
            }
            snapshot = daily.get("last_snapshot")
            raw_at = snapshot.get("observed_at") if isinstance(snapshot, dict) else None
            selected = snapshot.get("selection") if isinstance(snapshot, dict) else None
            source_matches = isinstance(selected, dict) and all(
                selected.get(key) == request.to_dict()[key]
                for key in ("capture_sequence", "calendar_sequence", "mark_refs")
            )
            if not source_matches:
                reasons.add("BOUND_RISK_DAILY_SOURCE_SELECTION_MISMATCH")
            if (
                daily.get("source_snapshot_qualified") is True
                and qualified
                and source_matches
            ):
                reasons.discard("BOUND_RISK_ACCOUNTING_UNQUALIFIED")
            else:
                reasons.add("BOUND_RISK_DAILY_SOURCE_UNQUALIFIED")
            if daily.get("issues"):
                reasons.add("BOUND_RISK_OWNER_VETO")
        if not isinstance(raw_daily, dict) or not isinstance(raw_at, str):
            raise JournalError("invalid daily risk source projection")
        daily_stop = raw_daily.get("latched") is True or daily.get("veto") is True
        if (
            raw_daily.get("session")
            != at.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        ):
            reasons.add("BOUND_RISK_DAILY_SESSION_MISMATCH")
            daily_stop = False
        if daily_stop:
            reasons.add("BOUND_RISK_DAILY_STOP_OBSERVED")
        daily_at = datetime.fromisoformat(raw_at)
        if at - daily_at > request.max_age:
            reasons.add("BOUND_RISK_DAILY_STATE_STALE")
            if account_risk_fee_policy is not None:
                reasons.add("BOUND_RISK_ACCOUNTING_UNQUALIFIED")
    references: list[list[object]] = []
    for sequence in sorted(refs):
        record = journal.read_event(sequence)
        if record is None or not record[0].visible_at(at):
            raise JournalError("bound risk evidence is unavailable")
        references.append([sequence, record[0].payload_ref])
    result: dict[str, object] = {
        "reported_inputs_qualified": qualified,
        "available_funds_qualified": funds,
        "ownership_verified": ownership,
        "bid_valuation_qualified": bid,
        "owned_positions": positions,
        "daily_state_present": daily is not None,
        "daily_stop_observed": daily_stop,
        "source_refs": references,
        "reasons": sorted(reasons),
        "runtime_authorized": False,
    }
    if account_risk_fee_policy is not None:
        result["daily_state_source"] = "SOURCE_BOUND_ACCOUNT_RISK"
    return result
