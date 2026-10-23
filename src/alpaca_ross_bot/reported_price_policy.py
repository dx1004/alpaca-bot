"""Pinned reported-price component; never history, entry or runtime admission."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from .journal import Journal
from .market_input_checks import (
    _datetime_ns,
    _object,
    _timestamp_ns,
    assess_market_record,
)
from .risk_marks import inspect_bid_mark
from .strategy_features import Session, utc

SHA256 = "8877ee66435684584a89f5b572cb94f5e3bbdf6950ccdaac5b3d6e0e905f6879"
OBSERVATION_SHA256 = "f5439d49ac9354abbe56b7b1fcb603c915942dee647efedebf48060e63b49e8c"
OCO_SHA256 = "fa6e29ede5a92428308ad19ba794be5e6e3f0b2a3c216818a54f3ffc9a112d80"


@dataclass(frozen=True, repr=False)
class ReportedPricePolicy:
    raw: bytes
    observation_parent: bytes
    oco_parent: bytes
    version: str
    rows: tuple[tuple[str, str, bool, bool, bool], ...]
    purposes: tuple[str, ...]
    sha256: str = SHA256


def load_reported_price_policy(
    raw: bytes, *, observation_parent: bytes, oco_parent: bytes
) -> ReportedPricePolicy:
    for data, digest in (
        (raw, SHA256),
        (observation_parent, OBSERVATION_SHA256),
        (oco_parent, OCO_SHA256),
    ):
        if type(data) is not bytes or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("reported-price policy or parent digest mismatch")
    document = json.loads(raw, object_pairs_hook=_object)
    rows = tuple(
        (
            tape,
            row["code"],
            row["minute_open_close"],
            row["minute_high_low"],
            row["volume"],
        )
        for row in document["minute_condition_rows"]
        for tape in row["tapes"]
    )
    return ReportedPricePolicy(
        raw,
        observation_parent,
        oco_parent,
        document["version_id"],
        rows,
        tuple(document["intraday_price_purposes"]),
    )


def _validate(policy: ReportedPricePolicy) -> None:
    if type(policy) is not ReportedPricePolicy or policy != load_reported_price_policy(
        policy.raw,
        observation_parent=policy.observation_parent,
        oco_parent=policy.oco_parent,
    ):
        raise ValueError("altered reported-price policy identity")


@dataclass(frozen=True)
class MinuteConditions:
    minute_open_close: bool | None
    minute_high_low: bool | None
    volume: bool | None
    issues: tuple[str, ...]


def classify_minute_conditions(
    policy: ReportedPricePolicy, *, tape: object, conditions: object
) -> MinuteConditions:
    _validate(policy)
    matrix = {(item[0], item[1]): item[2:] for item in policy.rows}
    if (
        type(tape) is not str
        or tape not in ("A", "B", "C")
        or type(conditions) is not list
        or not conditions
        or any(
            type(code) is not str or (tape, code) not in matrix for code in conditions
        )
    ):
        return MinuteConditions(None, None, None, ("UNKNOWN_CONDITION_OR_TAPE",))
    rows = [matrix[tape, code] for code in conditions]
    return MinuteConditions(
        all(row[0] for row in rows),
        all(row[1] for row in rows),
        all(row[2] for row in rows),
        (),
    )


@dataclass(frozen=True)
class ScopedPriceSource:
    journal_scope: str
    sequence: int
    payload_ref: str
    record_index: int | None
    available_at: datetime


@dataclass(frozen=True)
class ReportedPriceAssessment:
    contract_sha256: str
    through_sequence: int
    sources: tuple[ScopedPriceSource, ...]
    declared_calendar_ref: str
    purpose: str
    symbol: str | None
    kind: str | None
    component_qualified: bool
    symbol_derivation_blocked: bool
    conditions: MinuteConditions
    event_age_ns: int | None
    receipt_age_ns: int
    reported_price: Fraction | None
    reported_bid: Fraction | None
    reported_ask: Fraction | None
    issues: tuple[str, ...]
    coverage_label: str = "NOT_SIP_EQUIVALENT"
    source_history_qualified: bool = False
    whole_source_admission_qualified: bool = False
    strategy_eligible: bool = False
    runtime_authorized: bool = False
    order_authority: bool = False
    formal_paper_authorized: bool = False
    limitations: tuple[str, ...] = (
        "PROVIDER_REPORTED_CLOCK_NOT_EXCHANGE_CLOCK_QUALIFICATION",
        "DECLARED_SESSION_NOT_AUTHENTICATED_CALENDAR",
        "NO_NBBO_OR_EXECUTABLE_LIQUIDITY_GUARANTEE",
        "REPORTED_SIZES_NOT_INFERRED_SHARE_DEPTH",
    )


def assess_reported_price_record(
    journal: Journal,
    policy: ReportedPricePolicy,
    *,
    journal_scope: str,
    through_sequence: int,
    sequence: int,
    record_index: int,
    payload_ref: str,
    decision_at: datetime,
    session: Session,
    purpose: str,
    metadata_sequence: int | None = None,
    max_metadata_age: timedelta | None = None,
) -> ReportedPriceAssessment:
    """Read one fixed-prefix record; original raw clocks/indices stay authoritative."""
    _validate(policy)
    utc(decision_at)
    if (
        type(journal_scope) is not str
        or not journal_scope.strip()
        or journal_scope != journal_scope.strip()
    ):
        raise ValueError("exact declared journal scope required")
    if type(session) is not Session or purpose not in (*policy.purposes, "ENTRY_QUOTE"):
        raise ValueError("declared session and supported price purpose required")
    owns_read = not journal._db.in_transaction
    if owns_read:
        journal._db.execute("BEGIN")
    try:
        if (
            type(through_sequence) is not int
            or not 1 <= through_sequence <= journal.read_evidence_sequence()
            or type(sequence) is not int
            or not 1 <= sequence <= through_sequence
            or (
                metadata_sequence is not None
                and (
                    type(metadata_sequence) is not int
                    or not 1 <= metadata_sequence <= through_sequence
                )
            )
        ):
            raise ValueError("reported-price source reference outside horizon")
        stored = journal.read_event(sequence)
        if stored is None:
            raise ValueError("reported-price raw evidence missing")
        event, raw = stored
        if (
            event.sequence != sequence
            or event.payload_ref != payload_ref
            or payload_ref != "sha256:" + hashlib.sha256(raw).hexdigest()
        ):
            raise ValueError("reported-price raw identity mismatch")
        assessed = assess_market_record(
            journal,
            sequence=sequence,
            record_index=record_index,
            payload_ref=payload_ref,
            decision_at=decision_at,
        )
        records = json.loads(raw, parse_float=Decimal, object_pairs_hook=_object)
        record = records[record_index]
        sources = [
            ScopedPriceSource(
                journal_scope, sequence, payload_ref, record_index, event.available_at
            )
        ]
        issues: list[str] = []
        if not assessed.schema_valid:
            issues.append("INVALID_RAW_TRADE_QUOTE_SCHEMA")
        if not assessed.measured_fresh:
            issues.append("EVENT_OR_RECEIPT_AGE_OUT_OF_BOUNDS")
        if event.external_clock_conflict:
            issues.append("RAW_EXTERNAL_CLOCK_CONFLICT")
        if session.available_at > decision_at:
            issues.append("SESSION_NOT_VISIBLE_AT_DECISION")
        try:
            event_ns = _timestamp_ns(record.get("t"))
            in_rth = (
                _datetime_ns(session.open_at)
                <= event_ns
                < _datetime_ns(session.close_at)
            )
        except ValueError:
            in_rth = False
        if not in_rth:
            issues.append("OUTSIDE_DECLARED_RTH_INTERVAL")
        codes = record.get("c")
        extended_conflict = (
            assessed.kind == "t"
            and in_rth
            and isinstance(codes, list)
            and any(code in ("T", "U") for code in codes)
        )
        if extended_conflict:
            issues.append("EXTENDED_HOURS_CODE_IN_DECLARED_RTH_AFFECTED_SYMBOL")
        conditions = MinuteConditions(None, None, None, ())
        price = bid = ask = None
        if assessed.revision_observed:
            issues.append("REVISION_NOT_A_NEW_LIVE_PRINT")
        if purpose == "ENTRY_QUOTE":
            if (
                assessed.kind != "q"
                or metadata_sequence is None
                or max_metadata_age is None
            ):
                issues.append("QUOTE_METADATA_AND_RAW_QUOTE_REQUIRED")
            elif not issues:
                try:
                    metadata = journal.read_event(metadata_sequence)
                    if (
                        metadata is None
                        or metadata[0].sequence != metadata_sequence
                        or metadata[0].payload_ref
                        != "sha256:" + hashlib.sha256(metadata[1]).hexdigest()
                        or not metadata[0].visible_at(decision_at)
                    ):
                        raise ValueError("quote metadata immutable identity mismatch")
                    sources.append(
                        ScopedPriceSource(
                            journal_scope,
                            metadata_sequence,
                            metadata[0].payload_ref,
                            None,
                            metadata[0].available_at,
                        )
                    )
                    mark = inspect_bid_mark(
                        journal,
                        symbol=assessed.symbol or "",
                        sequence=sequence,
                        record_index=record_index,
                        metadata_sequence=metadata_sequence,
                        decision_at=decision_at,
                        max_metadata_age=max_metadata_age,
                        _source_horizon=through_sequence,
                    )
                    bid, ask = mark.bid, mark.ask
                except ValueError:
                    issues.append("QUOTE_COMPONENT_OR_METADATA_UNAVAILABLE")
        else:
            if assessed.kind != "t":
                issues.append("TRADE_PURPOSE_REQUIRES_LIVE_TRADE")
            conditions = classify_minute_conditions(
                policy, tape=record.get("z"), conditions=codes
            )
            issues.extend(conditions.issues)
            if (
                conditions.minute_open_close is not True
                or conditions.minute_high_low is not True
            ):
                issues.append("MINUTE_PRICE_FIELDS_NOT_ELIGIBLE")
            if assessed.schema_valid and assessed.kind == "t":
                price = Fraction(record["p"])
        return ReportedPriceAssessment(
            policy.sha256,
            through_sequence,
            tuple(sources),
            session.calendar_ref,
            purpose,
            assessed.symbol,
            assessed.kind,
            not issues,
            extended_conflict,
            conditions,
            assessed.event_age_ns,
            assessed.receipt_age_ns,
            price,
            bid,
            ask,
            tuple(issues),
        )
    finally:
        if owns_read:
            journal._db.rollback()
