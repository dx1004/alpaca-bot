"""Source-bound incident risk observations; never execution acceptance."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .journal import Journal, JournalError
from .risk_baseline import session_open_from_evidence
from .risk_input_evidence import inspect_risk_inputs
from .risk_journal import RiskJournal


@dataclass(frozen=True)
class IncidentRiskRequest:
    capture_sequence: int
    calendar_sequence: int
    max_age: timedelta
    max_metadata_age: timedelta
    mark_refs: tuple[tuple[str, int, int, int], ...] = ()

    def to_dict(self) -> dict[str, object]:
        for value in (self.capture_sequence, self.calendar_sequence):
            if type(value) is not int or value <= 0:
                raise ValueError("positive source sequences required")
        for age in (self.max_age, self.max_metadata_age):
            if type(age) is not timedelta or age <= timedelta(0):
                raise ValueError("positive source age limits required")
        if type(self.mark_refs) is not tuple or any(
            type(row) is not tuple
            or len(row) != 4
            or type(row[0]) is not str
            or not row[0].strip()
            or any(type(v) is not int or v < 0 for v in row[1:])
            or row[1] == 0
            or row[3] == 0
            for row in self.mark_refs
        ):
            raise ValueError("immutable mark identities required")
        return {
            "capture_sequence": self.capture_sequence,
            "calendar_sequence": self.calendar_sequence,
            "max_age_us": self.max_age // timedelta(microseconds=1),
            "max_metadata_age_us": self.max_metadata_age // timedelta(microseconds=1),
            "mark_refs": [list(row) for row in self.mark_refs],
        }

    @classmethod
    def from_dict(cls, raw: object) -> "IncidentRiskRequest":
        if not isinstance(raw, dict) or set(raw) != {
            "capture_sequence",
            "calendar_sequence",
            "max_age_us",
            "max_metadata_age_us",
            "mark_refs",
        }:
            raise ValueError("invalid bound risk request")
        if any(
            type(raw[key]) is not int
            for key in (
                "capture_sequence",
                "calendar_sequence",
                "max_age_us",
                "max_metadata_age_us",
            )
        ) or not isinstance(raw["mark_refs"], list):
            raise ValueError("invalid bound risk request values")
        rows: list[tuple[str, int, int, int]] = []
        for row in raw["mark_refs"]:
            if not isinstance(row, list) or len(row) != 4:
                raise ValueError("invalid mark request")
            rows.append((row[0], row[1], row[2], row[3]))
        request = cls(
            raw["capture_sequence"],
            raw["calendar_sequence"],
            timedelta(microseconds=raw["max_age_us"]),
            timedelta(microseconds=raw["max_metadata_age_us"]),
            tuple(rows),
        )
        if request.to_dict() != raw:
            raise ValueError("noncanonical bound risk request")
        return request


def assess_incident_risk(
    journal: Journal,
    account_ref: str,
    request: IncidentRiskRequest,
    at: datetime,
    intent_census: object,
) -> dict[str, object]:
    """Derive facts under the caller's Journal transaction and on immutable replay.

    Existing daily-risk inputs are explicitly fixture-only. Their veto/latch can
    conservatively stop the incident; they cannot establish operational clearance.
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
    daily, daily_refs = RiskJournal.read_evidence_at(journal, account_ref, at)
    refs.update(daily_refs)
    daily_stop = False
    if daily is None:
        reasons.add("BOUND_RISK_DAILY_STATE_MISSING")
    else:
        reasons.add("BOUND_RISK_DAILY_STATE_OFFLINE_FIXTURE_ONLY")
        raw_daily = daily.get("daily")
        raw_at = daily.get("at")
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
    references: list[list[object]] = []
    for sequence in sorted(refs):
        record = journal.read_event(sequence)
        if record is None or not record[0].visible_at(at):
            raise JournalError("bound risk evidence is unavailable")
        references.append([sequence, record[0].payload_ref])
    return {
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
