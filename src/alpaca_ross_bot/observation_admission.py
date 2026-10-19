"""Independent snapshot acquisition-age component; no source-age or trade grant."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .journal import Journal
from .market_input_checks import _datetime_ns, _timestamp_ns
from .scanner_discovery_audit import (
    _clock,
    _json,
    _raw,
    _symbols,
    audit_iex_asset_discovery,
)

VERSION = "IEX_SNAPSHOT_OBSERVATION_AGE_RESEARCH_V1"
CANDIDATE_VERSION = "HYBRID_500_IEX_OBSERVATION_AGE_RESEARCH_V1"
CONTRACT_SHA256 = "f5439d49ac9354abbe56b7b1fcb603c915942dee647efedebf48060e63b49e8c"
PATH = "evidence/iex-snapshot-observation-age-research-v1.json"
NY = ZoneInfo("America/New_York")
OPEN_GATES = (
    "SOURCE_CONDITION_POLICY",
    "FEATURE_HISTORY_AND_REVISIONS",
    "FROM_OPEN_CONTINUITY_AND_FIRST_TRADE",
    "ENTRY_RISK_VERSION_BINDING_FOR_NEW_CANDIDATE",
    "ACCOUNT_AND_RISK_QUALIFICATION",
    "CONTINUOUS_PROTECTION",
    "BROKER_ORDER_CYCLE",
    "EXECUTABLE_RELEASE_FREEZE",
    "FORMAL_PAPER_PROTOCOL",
)


@dataclass(frozen=True)
class ObservationContract:
    version: str = VERSION
    candidate_version: str = CANDIDATE_VERSION
    sha256: str = CONTRACT_SHA256
    max_age_seconds: int = 300
    runtime_authorized: bool = False


def load_observation_contract(raw: bytes) -> ObservationContract:
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError("bounded observation contract bytes required")
    body = _json(raw)
    if not isinstance(body, dict) or hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise ValueError("independent observation contract differs from pinned bytes")
    return ObservationContract()


@dataclass(frozen=True)
class SnapshotAdmission:
    symbol: str
    raw_sequence: int
    source_refs: tuple[tuple[int, str], ...]
    source_horizon: int
    decision_at: datetime
    page_requested_at: datetime
    acquisition_started_at: datetime
    volume: int | None
    daily_bucket_timestamp: str | None
    daily_bucket_day: date | None
    reasons: tuple[str, ...]
    version: str = VERSION
    candidate_version: str = CANDIDATE_VERSION
    scope: str = "OBSERVATION_AGE_COMPONENT_ONLY"
    coverage: str = "NOT_SIP_EQUIVALENT"
    volume_source_updated_at: str = "UNKNOWN"
    volume_source_age: str = "UNKNOWN"
    unresolved_gates: tuple[str, ...] = OPEN_GATES
    strategy_eligible: bool = False
    runtime_authorized: bool = False
    order_authority: bool = False

    @property
    def observation_age_component_qualified(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class SweepAdmission:
    manifest_sequence: int
    source_horizon: int
    source_refs: tuple[tuple[int, str], ...]
    request_coverage_complete: bool
    returned_data_coverage_complete: bool
    missing_symbols: tuple[str, ...]
    members: tuple[SnapshotAdmission, ...]
    reasons: tuple[str, ...]
    version: str = VERSION
    runtime_authorized: bool = False


def _context(
    journal: Journal, contract: ObservationContract, at: datetime, horizon: int | None
) -> int:
    if type(contract) is not ObservationContract or contract != ObservationContract():
        raise ValueError("independent pinned observation contract required")
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise ValueError("UTC observation decision required")
    if horizon is not None and (type(horizon) is not int or horizon < 0):
        raise ValueError("nonnegative integer source horizon required")
    actual = journal.read_evidence_sequence()
    if horizon is not None and horizon > actual:
        raise ValueError("source horizon exceeds current immutable evidence")
    return actual if horizon is None else horizon


def _reference(journal: Journal, sequence: int, at: datetime, horizon: int) -> str:
    if type(sequence) is not int or not 0 < sequence <= horizon:
        raise ValueError("source reference exceeds observation horizon")
    saved = journal.read_event(sequence)
    if saved is None:
        raise ValueError("observation source is missing")
    event, raw = saved
    if (
        event.sequence != sequence
        or not event.visible_at(at)
        or event.external_clock_conflict
        or event.payload_ref != "sha256:" + hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("observation source identity, clocks or digest differs")
    return event.payload_ref


def _page(
    journal: Journal, sequence: int, at: datetime, horizon: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    _reference(journal, sequence, at, horizon)
    _, metadata, response = _raw(
        journal, sequence, "IEX_SNAPSHOT_RAW_RESPONSE", "iex", at
    )
    symbols = _symbols(metadata.get("requested_symbols"))
    if (
        set(metadata)
        != {
            "requested_symbols",
            "feed",
            "coverage_label",
            "full_market_coverage",
            "universe_scope",
            "request_method",
            "request_url",
            "requested_at",
            "received_at",
            "response_bytes",
            "response_sha256",
            "raw_response_base64",
            "continuity_proven",
        }
        or metadata.get("continuity_proven") is not False
        or not symbols
        or len(set(symbols)) != len(symbols)
        or metadata.get("request_url")
        != "https://data.alpaca.markets/v2/stocks/snapshots?"
        + urlencode({"symbols": ",".join(symbols), "feed": "iex"})
        or metadata.get("feed") != "iex"
        or metadata.get("universe_scope") != "EXPLICIT_CALLER_SYMBOLS_ONLY"
        or metadata.get("coverage_label") != "NOT_SIP_EQUIVALENT"
        or metadata.get("full_market_coverage") is not False
        or not isinstance(response, dict)
        or set(response) - set(symbols)
        or any(not isinstance(row, dict) for row in response.values())
    ):
        raise ValueError("snapshot acquisition source identity differs")
    return metadata, response


def _member(
    journal: Journal,
    symbol: str,
    sequence: int,
    at: datetime,
    horizon: int,
    *,
    observation_sequence: int | None = None,
    discovery_refs: tuple[int, ...] = (),
    catalog_requested_at: datetime | None = None,
) -> SnapshotAdmission:
    if type(symbol) is not str or not symbol.strip() or symbol != symbol.strip():
        raise ValueError("exact observation symbol required")
    metadata, response = _page(journal, sequence, at, horizon)
    if symbol not in metadata["requested_symbols"]:
        raise ValueError("symbol was not requested by this acquisition")
    refs = {sequence, *discovery_refs}
    if observation_sequence is not None:
        _reference(journal, observation_sequence, at, horizon)
        saved, source = (
            journal.read_event(observation_sequence),
            journal.read_event(sequence),
        )
        assert saved is not None and source is not None
        event, raw = saved
        body = _json(raw)
        if (
            (event.source_id, event.feed, event.event_kind)
            != ("alpaca_iex", "iex", "IEX_SYMBOL_SNAPSHOT_OBSERVATION")
            or not isinstance(body, dict)
            or body.get("record_type") != "IEX_SYMBOL_SNAPSHOT_OBSERVATION"
            or body.get("symbol") != symbol
            or type(body.get("source_response_sequence")) is not int
            or body.get("source_response_sequence") != sequence
            or observation_sequence <= sequence
            or body.get("snapshot") != response.get(symbol)
            or body.get("coverage_label") != "NOT_SIP_EQUIVALENT"
            or body.get("full_market_coverage") is not False
            or event.system_first_seen_at != source[0].system_first_seen_at
            or event.processed_at < source[0].available_at
        ):
            raise ValueError(
                "derived cache cannot change acquisition identity or clock"
            )
        refs.add(observation_sequence)
    requested = _clock(metadata["requested_at"])
    earliest = (
        requested
        if catalog_requested_at is None
        else min(requested, catalog_requested_at)
    )
    reasons: list[str] = []
    if not timedelta(0) <= at - earliest <= timedelta(seconds=300):
        reasons.append("OBSERVATION_AGE_EXCEEDS_300_SECONDS")
    volume: int | None = None
    bucket: str | None = None
    day: date | None = None
    snapshot = response.get(symbol)
    if snapshot is None:
        reasons.append("SNAPSHOT_NOT_RETURNED")
    else:
        daily = snapshot.get("dailyBar")
        if (
            not isinstance(daily, dict)
            or type(daily.get("v")) is not int
            or daily["v"] < 0
        ):
            reasons.append("CURRENT_DAILY_VOLUME_MISSING_OR_INVALID")
        else:
            try:
                raw_stamp = daily["t"]
                stamp = _timestamp_ns(raw_stamp)
                day = (
                    datetime.fromtimestamp(stamp // 1_000_000_000, UTC)
                    .astimezone(NY)
                    .date()
                )
                if stamp > _datetime_ns(at) or day != at.astimezone(NY).date():
                    raise ValueError("daily bucket is not current NY day")
                volume, bucket = daily["v"], raw_stamp
            except (KeyError, ValueError, TypeError, OverflowError):
                reasons.append("CURRENT_NY_DAY_BUCKET_UNQUALIFIED")
                day = None
    return SnapshotAdmission(
        symbol,
        sequence,
        tuple((seq, _reference(journal, seq, at, horizon)) for seq in sorted(refs)),
        horizon,
        at,
        requested,
        earliest,
        volume,
        bucket,
        day,
        tuple(reasons),
    )


def admit_snapshot_observation(
    journal: Journal,
    contract: ObservationContract,
    *,
    symbol: str,
    raw_sequence: int,
    decision_at: datetime,
    observation_sequence: int | None = None,
    through_sequence: int | None = None,
) -> SnapshotAdmission:
    """Explicit-symbol acquisition check; does not rebuild a monitored pool."""
    horizon = _context(journal, contract, decision_at, through_sequence)
    return _member(
        journal,
        symbol,
        raw_sequence,
        decision_at,
        horizon,
        observation_sequence=observation_sequence,
    )


def admit_completed_sweep(
    journal: Journal,
    contract: ObservationContract,
    *,
    manifest_sequence: int,
    decision_at: datetime,
    through_sequence: int | None = None,
) -> SweepAdmission:
    """Identity-check a completed catalog sweep; qualify each acquisition separately.

    Existing raw collector clocks are stricter (immediate response journaling).
    Decision and derived-observation processing delays do not reset those clocks.
    No caller catalog, parsed price, volume or eligible flag is accepted here.
    """
    horizon = _context(journal, contract, decision_at, through_sequence)
    _reference(journal, manifest_sequence, decision_at, horizon)
    report = audit_iex_asset_discovery(
        journal, manifest_sequence=manifest_sequence, decision_at=decision_at
    )
    all_refs = (
        manifest_sequence,
        report.catalog_sequence,
        *report.snapshot_batch_sequences,
    )
    for sequence in all_refs:
        _reference(journal, sequence, decision_at, horizon)
    _, catalog, _ = _raw(
        journal,
        report.catalog_sequence,
        "ASSET_DIRECTORY_RAW_RESPONSE",
        "asset_master",
        decision_at,
    )
    sources = dict(report.returned_symbol_sources)
    members: list[SnapshotAdmission] = []
    if report.request_coverage_complete:
        for symbol in report.catalog_symbols:
            if symbol in sources:
                members.append(
                    _member(
                        journal,
                        symbol,
                        sources[symbol],
                        decision_at,
                        horizon,
                        discovery_refs=(manifest_sequence, report.catalog_sequence),
                        catalog_requested_at=_clock(catalog["requested_at"]),
                    )
                )
    return SweepAdmission(
        manifest_sequence,
        horizon,
        tuple(
            (seq, _reference(journal, seq, decision_at, horizon))
            for seq in sorted(set(all_refs))
        ),
        report.request_coverage_complete,
        report.returned_data_coverage_complete,
        report.missing_symbols,
        tuple(members),
        report.reasons,
    )
