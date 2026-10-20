"""Read-only proof of catalog request/return coverage, never trade eligibility."""

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError

_CATALOG_SCOPE = "CURRENT_ALPACA_ASSET_MASTER_NOT_HISTORICAL_UNIVERSE"
_CATALOG_QUERY = {"status": "active", "asset_class": "us_equity"}
_CATALOG_URL = "https://paper-api.alpaca.markets/v2/assets?" + urlencode(_CATALOG_QUERY)
_CATALOG_KIND = "ASSET_DIRECTORY_RAW_RESPONSE"
_BATCH_KIND = "IEX_SNAPSHOT_RAW_RESPONSE"
_MANIFEST_KIND = "IEX_ASSET_SNAPSHOT_SCAN_MANIFEST"


class ScannerDiscoveryAuditError(ValueError):
    """Journal evidence cannot establish the claimed discovery provenance."""


@dataclass(frozen=True)
class ScannerDiscoveryAudit:
    manifest_sequence: int
    catalog_sequence: int
    decision_at: datetime
    snapshot_batch_sequences: tuple[int, ...]
    catalog_symbols: tuple[str, ...]
    requested_symbols: tuple[str, ...]
    returned_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    missing_reasons: tuple[tuple[str, str], ...]
    returned_symbol_sources: tuple[tuple[str, int], ...]
    reasons: tuple[str, ...]
    coverage_scope: str = "ALPACA_CURRENT_ACTIVE_US_EQUITY_CATALOG"
    coverage_label: str = "NOT_SIP_EQUIVALENT"
    full_market_coverage: bool = False
    source_freshness: str = "UNVERIFIED_NOT_A_COVERAGE_ASSERTION"
    revision_status: str = "OBSERVED_AS_OF_DECISION_NOT_FINAL"
    strategy_eligibility: str = "BLOCKED_RESEARCH"

    @property
    def request_coverage_complete(self) -> bool:
        return not self.reasons and self.requested_symbols == self.catalog_symbols

    @property
    def returned_data_coverage_complete(self) -> bool:
        """Snapshot objects were returned; their usable/fresh fields are unproven."""
        return self.request_coverage_complete and not self.missing_symbols


@dataclass(frozen=True)
class ScannerEventCensus:
    """Stored event identities, not a natural-candidate or wall-clock census."""

    through_sequence: int
    decision_at: datetime
    event_refs: tuple[tuple[int, str, str], ...]
    catalog_event_count: int
    snapshot_event_count: int
    manifest_event_count: int
    manifest_audits: tuple[ScannerDiscoveryAudit, ...]
    audit_errors: tuple[tuple[int, str, str], ...]
    orphan_catalog_refs: tuple[tuple[int, str], ...]
    orphan_snapshot_refs: tuple[tuple[int, str], ...]
    excluded_kind_refs: tuple[tuple[int, str, str], ...]
    audited_unique_requested_observations: int
    audited_unique_returned_observations: int
    audited_unique_missing_observations: int
    scope: str = "VISIBLE_KNOWN_DISCOVERY_EVENT_IDENTITIES_NOT_ALL_SCANNER_ACTIVITY"
    coverage_label: str = "NOT_SIP_EQUIVALENT"
    strategy_eligibility: str = "BLOCKED_RESEARCH"
    runtime_authorized: bool = False
    limitations: tuple[str, ...] = (
        "NO_RECORDED_EVENT_DOES_NOT_PROVE_NO_NETWORK_ATTEMPT",
        "EVENT_COUNTS_DO_NOT_PROVE_SESSION_OR_WALL_CLOCK_COVERAGE",
        "FORMAL_ORIGIN_AND_NATURAL_ENROLLMENT_UNKNOWN",
        "SNAPSHOT_NOT_RETURNED_IS_NOT_A_STRATEGY_NO_ENTRY",
        "RAW_FRESHNESS_AND_STRATEGY_ELIGIBILITY_UNVERIFIED",
    )


def audit_scanner_event_census(
    journal: Journal, *, through_sequence: int, decision_at: datetime
) -> ScannerEventCensus:
    """Enumerate the whole fixed-source prefix, retaining unresolved raw events.

    Only visible, request-complete audited manifests resolve their dependencies.
    Raw references shared by several manifests count once in observation totals;
    each manifest's own request/return denominator stays in ``manifest_audits``.
    This neither assigns an experiment origin nor creates formal candidate IDs.
    """
    _utc(decision_at)
    known = {_CATALOG_KIND, _BATCH_KIND, _MANIFEST_KIND}
    events: dict[int, EventEnvelope] = {}
    excluded: list[tuple[int, str, str]] = []
    errors: list[tuple[int, str, str]] = []
    audits: list[ScannerDiscoveryAudit] = []
    raw_batches: dict[int, tuple[dict[str, Any], Any]] = {}

    def retain_error(sequence: int, kind: str, exc: ScannerDiscoveryAuditError) -> None:
        # These faults invalidate trust in bytes, not just a collector's outcome.
        if str(exc) in {"JOURNAL_INTEGRITY_INVALID", "RAW_DIGEST_OR_SIZE_INVALID"}:
            raise exc
        errors.append((sequence, kind, str(exc)))

    for event, raw in journal.read_scanner_events(through_sequence=through_sequence):
        if event.payload_ref != "sha256:" + hashlib.sha256(raw).hexdigest():
            raise JournalError("scanner source payload failed integrity check")
        if event.available_at > decision_at:
            continue
        if event.event_kind not in known:
            excluded.append((event.sequence, event.payload_ref, event.event_kind))
            continue
        events[event.sequence] = event
        if event.event_kind == _MANIFEST_KIND:
            continue
        try:
            _, metadata, response = _raw(
                journal,
                event.sequence,
                event.event_kind,
                "asset_master" if event.event_kind == _CATALOG_KIND else "iex",
                decision_at,
            )
            if event.event_kind == _BATCH_KIND:
                raw_batches[event.sequence] = metadata, response
        except ScannerDiscoveryAuditError as exc:
            retain_error(event.sequence, event.event_kind, exc)
    used_catalogs: set[int] = set()
    used_batches: set[int] = set()
    requested: set[tuple[int, str]] = set()
    returned: set[tuple[int, str]] = set()
    missing: set[tuple[int, str]] = set()
    for event in events.values():
        if event.event_kind != _MANIFEST_KIND:
            continue
        try:
            audit = audit_iex_asset_discovery(
                journal, manifest_sequence=event.sequence, decision_at=decision_at
            )
        except ScannerDiscoveryAuditError as exc:
            retain_error(event.sequence, event.event_kind, exc)
            continue
        audits.append(audit)
        errors.extend(
            (event.sequence, event.event_kind, reason) for reason in audit.reasons
        )
        if not audit.request_coverage_complete:
            continue
        if audit.catalog_sequence not in events or any(
            sequence not in raw_batches for sequence in audit.snapshot_batch_sequences
        ):
            errors.append(
                (event.sequence, event.event_kind, "DEPENDENCY_OUTSIDE_VISIBLE_PREFIX")
            )
            continue
        used_catalogs.add(audit.catalog_sequence)
        used_batches.update(audit.snapshot_batch_sequences)
        for sequence in audit.snapshot_batch_sequences:
            metadata, response = raw_batches[sequence]
            for symbol in _symbols(metadata.get("requested_symbols")):
                key = sequence, symbol
                requested.add(key)
                if symbol in response:
                    returned.add(key)
                else:
                    missing.add(key)
    return ScannerEventCensus(
        through_sequence,
        decision_at,
        tuple(
            (event.sequence, event.payload_ref, event.event_kind)
            for event in events.values()
        ),
        sum(event.event_kind == _CATALOG_KIND for event in events.values()),
        sum(event.event_kind == _BATCH_KIND for event in events.values()),
        sum(event.event_kind == _MANIFEST_KIND for event in events.values()),
        tuple(audits),
        tuple(errors),
        tuple(
            (event.sequence, event.payload_ref)
            for event in events.values()
            if event.event_kind == _CATALOG_KIND and event.sequence not in used_catalogs
        ),
        tuple(
            (event.sequence, event.payload_ref)
            for event in events.values()
            if event.event_kind == _BATCH_KIND and event.sequence not in used_batches
        ),
        tuple(excluded),
        len(requested),
        len(returned),
        len(missing),
    )


def audit_iex_asset_discovery(
    journal: Journal, *, manifest_sequence: int, decision_at: datetime
) -> ScannerDiscoveryAudit:
    """Recheck exact raw evidence available at a decision, without modifying it."""
    _utc(decision_at)
    manifest_envelope, manifest = _event(
        journal,
        manifest_sequence,
        "IEX_ASSET_SNAPSHOT_SCAN_MANIFEST",
        "iex",
        decision_at,
    )
    if not isinstance(manifest, dict):
        raise ScannerDiscoveryAuditError("MANIFEST_SHAPE_INVALID")
    _coverage(manifest)
    catalog_sequence = _sequence(manifest.get("asset_directory_sequence"))
    batch_ids = manifest.get("snapshot_batch_sequences")
    if not isinstance(batch_ids, list):
        raise ScannerDiscoveryAuditError("MANIFEST_BATCH_SEQUENCES_INVALID")
    batches = tuple(_sequence(sequence) for sequence in batch_ids)
    if (
        tuple(sorted(set(batches))) != batches
        or any(
            not catalog_sequence < sequence < manifest_sequence for sequence in batches
        )
        or catalog_sequence >= manifest_sequence
    ):
        raise ScannerDiscoveryAuditError("MANIFEST_SEQUENCE_ORDER_INVALID")
    max_batch = manifest.get("max_symbols_per_batch")
    count = manifest.get("requested_symbol_count")
    if (
        type(max_batch) is not int
        or max_batch < 1
        or type(count) is not int
        or count < 0
    ):
        raise ScannerDiscoveryAuditError("MANIFEST_COUNTS_INVALID")
    catalog_event, catalog_metadata, catalog = _raw(
        journal,
        catalog_sequence,
        "ASSET_DIRECTORY_RAW_RESPONSE",
        "asset_master",
        decision_at,
    )
    if (
        catalog_metadata.get("query") != _CATALOG_QUERY
        or catalog_metadata.get("request_url") != _CATALOG_URL
        or catalog_metadata.get("catalog_scope") != _CATALOG_SCOPE
        or manifest.get("asset_directory_scope") != _CATALOG_SCOPE
        or _clock(manifest.get("asset_directory_received_at"))
        != catalog_event.system_first_seen_at
        or not isinstance(catalog, list)
    ):
        raise ScannerDiscoveryAuditError("CATALOG_FILTER_OR_PROVENANCE_INVALID")
    catalog_symbols: list[str] = []
    for record in catalog:
        if (
            not isinstance(record, dict)
            or record.get("class") != "us_equity"
            or record.get("status") != "active"
        ):
            raise ScannerDiscoveryAuditError("CATALOG_ROW_FILTER_INVALID")
        catalog_symbols.extend(_symbols([record.get("symbol")]))
    if len(set(catalog_symbols)) != len(catalog_symbols):
        raise ScannerDiscoveryAuditError("CATALOG_SYMBOL_DUPLICATE")
    requested: list[str] = []
    returned: list[str] = []
    missing: list[str] = []
    sources: list[tuple[str, int]] = []
    chunks: list[tuple[str, ...]] = []
    previous_received = catalog_event.available_at
    for sequence in batches:
        envelope, metadata, response = _raw(
            journal, sequence, "IEX_SNAPSHOT_RAW_RESPONSE", "iex", decision_at
        )
        _coverage(metadata)
        symbols = _symbols(metadata.get("requested_symbols"))
        expected_url = "https://data.alpaca.markets/v2/stocks/snapshots?" + urlencode(
            {"symbols": ",".join(symbols), "feed": "iex"}
        )
        if (
            not symbols
            or metadata.get("feed") != "iex"
            or metadata.get("universe_scope") != "EXPLICIT_CALLER_SYMBOLS_ONLY"
            or metadata.get("request_url") != expected_url
            or _clock(metadata.get("requested_at")) < previous_received
            or envelope.available_at > manifest_envelope.system_first_seen_at
            or not isinstance(response, dict)
        ):
            raise ScannerDiscoveryAuditError("BATCH_PROVENANCE_OR_CLOCK_INVALID")
        if set(response) - set(symbols) or any(
            not isinstance(row, dict) for row in response.values()
        ):
            raise ScannerDiscoveryAuditError("BATCH_FOREIGN_OR_MALFORMED_SYMBOL")
        previous_received = envelope.available_at
        chunks.append(symbols)
        requested.extend(symbols)
        for symbol in symbols:
            if symbol in response:
                returned.append(symbol)
                sources.append((symbol, sequence))
            else:
                missing.append(symbol)
    if catalog_event.available_at > manifest_envelope.system_first_seen_at:
        raise ScannerDiscoveryAuditError("CATALOG_AFTER_MANIFEST")
    reasons: list[str] = []
    if len(set(requested)) != len(requested):
        reasons.append("REQUESTED_SYMBOL_DUPLICATE")
    if requested != catalog_symbols:
        reasons.append("REQUESTED_CATALOG_COVERAGE_MISMATCH")
    expected_chunks = [
        tuple(catalog_symbols[i : i + max_batch])
        for i in range(0, len(catalog_symbols), max_batch)
    ]
    if chunks != expected_chunks:
        reasons.append("BATCH_CHUNK_BOUNDARY_MISMATCH")
    if count != len(catalog_symbols) or count != len(requested):
        reasons.append("MANIFEST_REQUEST_COUNT_MISMATCH")
    if _symbols(manifest.get("missing_observation_symbols")) != tuple(missing):
        reasons.append("MANIFEST_MISSING_LIST_MISMATCH")
    return ScannerDiscoveryAudit(
        manifest_sequence,
        catalog_sequence,
        decision_at,
        batches,
        tuple(catalog_symbols),
        tuple(requested),
        tuple(returned),
        tuple(missing),
        tuple((symbol, "SNAPSHOT_NOT_RETURNED") for symbol in missing),
        tuple(sources),
        tuple(reasons),
    )


def _event(
    journal: Journal, sequence: int, kind: str, feed: str, decision_at: datetime
) -> tuple[EventEnvelope, Any]:
    _sequence(sequence)
    try:
        event = journal.read_event(sequence)
    except JournalError as exc:
        raise ScannerDiscoveryAuditError("JOURNAL_INTEGRITY_INVALID") from exc
    if event is None:
        raise ScannerDiscoveryAuditError("REFERENCED_EVENT_MISSING")
    envelope, stored = event
    if (
        envelope.sequence != sequence
        or envelope.source_id != "alpaca_scanner_data"
        or envelope.feed != feed
        or envelope.event_kind != kind
        or envelope.payload_ref != "sha256:" + hashlib.sha256(stored).hexdigest()
        or envelope.available_at > decision_at
        or envelope.source_event_at is not None
        or envelope.provider_received_at is not None
    ):
        raise ScannerDiscoveryAuditError("EVENT_PROVENANCE_OR_AVAILABILITY_INVALID")
    return envelope, _json(stored)


def _raw(
    journal: Journal, sequence: int, kind: str, feed: str, decision_at: datetime
) -> tuple[EventEnvelope, dict[str, Any], Any]:
    envelope, metadata = _event(journal, sequence, kind, feed, decision_at)
    if not isinstance(metadata, dict) or metadata.get("request_method") != "GET":
        raise ScannerDiscoveryAuditError("RAW_REQUEST_METADATA_INVALID")
    requested, received = (
        _clock(metadata.get("requested_at")),
        _clock(metadata.get("received_at")),
    )
    if (
        requested > received
        or received != envelope.system_first_seen_at
        or received != envelope.available_at
        or received != envelope.processed_at
    ):
        raise ScannerDiscoveryAuditError("RAW_LOCAL_CLOCK_CONFLICT")
    try:
        encoded = metadata["raw_response_base64"]
        if not isinstance(encoded, str):
            raise ValueError
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise ScannerDiscoveryAuditError("RAW_ENCODING_INVALID") from exc
    if (
        hashlib.sha256(raw).hexdigest() != metadata.get("response_sha256")
        or type(metadata.get("response_bytes")) is not int
        or len(raw) != metadata["response_bytes"]
    ):
        raise ScannerDiscoveryAuditError("RAW_DIGEST_OR_SIZE_INVALID")
    return envelope, metadata, _json(raw)


def _coverage(payload: dict[str, Any]) -> None:
    if (
        payload.get("coverage_label") != "NOT_SIP_EQUIVALENT"
        or payload.get("full_market_coverage") is not False
    ):
        raise ScannerDiscoveryAuditError("COVERAGE_LABEL_INVALID")


def _symbols(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(symbol, str)
        or not symbol.strip()
        or symbol != symbol.strip()
        or "," in symbol
        for symbol in value
    ):
        raise ScannerDiscoveryAuditError("SYMBOL_LIST_INVALID")
    return tuple(value)


def _sequence(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ScannerDiscoveryAuditError("EVENT_SEQUENCE_INVALID")
    return value


def _clock(value: object) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        result = datetime.fromisoformat(value)
        _utc(result)
        return result
    except ValueError as exc:
        raise ScannerDiscoveryAuditError("LOCAL_CLOCK_INVALID") from exc


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ScannerDiscoveryAuditError("DECISION_CLOCK_NOT_UTC")


def _json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw, parse_float=Decimal, parse_constant=_reject, object_pairs_hook=_unique
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise ScannerDiscoveryAuditError("STRICT_JSON_INVALID") from exc


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject(value: str) -> None:
    raise ValueError("nonfinite JSON constant")
