import base64
import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.scanner_data import AlpacaScannerData, IexAssetSnapshotRun
from alpaca_ross_bot.scanner_discovery_audit import (
    ScannerDiscoveryAuditError,
    audit_iex_asset_discovery,
    audit_scanner_event_census,
)

NOW = datetime(2026, 10, 15, 16, tzinfo=UTC)
SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "MISSING")


class SyntheticFetcher:
    def __init__(self, symbols: tuple[str, ...]) -> None:
        self.symbols = symbols

    def __call__(
        self, url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        if "/v2/assets?" in url:
            return json.dumps(
                [
                    {"symbol": symbol, "class": "us_equity", "status": "active"}
                    for symbol in self.symbols
                ]
            ).encode()
        requested = parse_qs(urlsplit(url).query)["symbols"][0].split(",")
        return json.dumps(
            {
                symbol: {"latestTrade": {"t": "1990-01-01T15:00:00Z"}}
                for symbol in requested
                if symbol != "MISSING"
            }
        ).encode()


class JournalView(Journal):
    """Alter the read-only view, without changing the append-only source journal."""

    def __init__(
        self, journal: Journal, changes: dict[int, tuple[EventEnvelope, bytes]]
    ) -> None:
        self.journal = journal
        self.changes = changes

    def read_event(self, sequence: int) -> tuple[EventEnvelope, bytes] | None:
        return self.changes.get(sequence) or self.journal.read_event(sequence)


def _scan(
    tmp_path: Path, symbols: tuple[str, ...] = SYMBOLS
) -> tuple[Journal, IexAssetSnapshotRun]:
    journal = Journal(tmp_path / "runtime" / "journal.sqlite3", tmp_path / "runtime")
    client = AlpacaScannerData(
        PaperCredentials(SecretValue("synthetic-key"), SecretValue("synthetic-secret")),
        journal,
        timeout_seconds=1,
        fetch=SyntheticFetcher(symbols),
        clock=lambda: NOW,
    )
    return journal, client.collect_current_asset_iex_snapshots(max_symbols_per_batch=2)


def _metadata_view(
    journal: Journal, sequence: int, change: Callable[[dict[str, Any]], None]
) -> JournalView:
    original = journal.read_event(sequence)
    assert original is not None
    event, raw = original
    value = json.loads(raw)
    change(value)
    updated = json.dumps(value).encode()
    return JournalView(
        journal,
        {
            sequence: (
                replace(
                    event, payload_ref="sha256:" + hashlib.sha256(updated).hexdigest()
                ),
                updated,
            )
        },
    )


def _response_view(journal: Journal, sequence: int, raw: bytes) -> JournalView:
    def change(metadata: dict[str, Any]) -> None:
        metadata.update(
            raw_response_base64=base64.b64encode(raw).decode(),
            response_sha256=hashlib.sha256(raw).hexdigest(),
            response_bytes=len(raw),
        )

    return _metadata_view(journal, sequence, change)


def test_real_collector_manifest_proves_requests_separately_from_returns(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path)
    report = audit_iex_asset_discovery(
        journal, manifest_sequence=run.manifest_sequence, decision_at=NOW
    )
    assert report.catalog_symbols == report.requested_symbols == SYMBOLS
    assert report.request_coverage_complete
    assert not report.returned_data_coverage_complete
    assert report.returned_symbols == SYMBOLS[:-1]
    assert report.missing_symbols == ("MISSING",)
    assert report.missing_reasons == (("MISSING", "SNAPSHOT_NOT_RETURNED"),)
    assert report.snapshot_batch_sequences == tuple(
        batch.sequence for batch in run.batches
    )
    assert report.returned_symbol_sources[0] == ("AAA", run.batches[0].sequence)
    assert journal.read_event(run.manifest_sequence + 1) is None
    journal.close()


def test_complete_return_coverage_does_not_accept_stale_or_strategy_data(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path, ("AAA", "BBB"))
    report = audit_iex_asset_discovery(
        journal, manifest_sequence=run.manifest_sequence, decision_at=NOW
    )
    assert report.request_coverage_complete and report.returned_data_coverage_complete
    assert report.coverage_scope == "ALPACA_CURRENT_ACTIVE_US_EQUITY_CATALOG"
    assert report.coverage_label == "NOT_SIP_EQUIVALENT"
    assert report.full_market_coverage is False
    assert report.strategy_eligibility == "BLOCKED_RESEARCH"
    assert report.source_freshness == "UNVERIFIED_NOT_A_COVERAGE_ASSERTION"
    assert report.revision_status == "OBSERVED_AS_OF_DECISION_NOT_FINAL"
    with pytest.raises(FrozenInstanceError):
        report.missing_symbols = ()  # type: ignore[misc]
    journal.close()


@pytest.mark.parametrize("component", ["catalog", "batch", "manifest"])
def test_future_referenced_envelopes_are_unavailable(
    tmp_path: Path, component: str
) -> None:
    journal, run = _scan(tmp_path)
    sequence = {
        "catalog": run.asset_directory.sequence,
        "batch": run.batches[0].sequence,
        "manifest": run.manifest_sequence,
    }[component]
    original = journal.read_event(sequence)
    assert original is not None
    envelope, stored = original
    future = NOW + timedelta(seconds=1)
    view = JournalView(
        journal,
        {
            sequence: (
                replace(
                    envelope,
                    system_first_seen_at=future,
                    processed_at=future,
                    available_at=future,
                ),
                stored,
            )
        },
    )
    with pytest.raises(ScannerDiscoveryAuditError, match="AVAILABILITY"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize("component", ["catalog", "batch", "manifest"])
def test_stored_payload_digest_is_independently_checked(
    tmp_path: Path, component: str
) -> None:
    journal, run = _scan(tmp_path)
    sequence = {
        "catalog": run.asset_directory.sequence,
        "batch": run.batches[0].sequence,
        "manifest": run.manifest_sequence,
    }[component]
    original = journal.read_event(sequence)
    assert original is not None
    envelope, stored = original
    view = JournalView(journal, {sequence: (envelope, stored + b" ")})
    with pytest.raises(ScannerDiscoveryAuditError, match="PROVENANCE"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("response_sha256", "0" * 64),
        ("response_bytes", True),
        ("raw_response_base64", "bad encoding"),
        ("request_method", "POST"),
    ],
)
def test_raw_catalog_hash_encoding_and_method_are_checked(
    tmp_path: Path, field: str, value: object
) -> None:
    journal, run = _scan(tmp_path)
    view = _metadata_view(
        journal, run.asset_directory.sequence, lambda row: row.update({field: value})
    )
    with pytest.raises(ScannerDiscoveryAuditError):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query", {"status": "inactive", "asset_class": "us_equity"}),
        ("request_url", "https://example.com/assets"),
        ("catalog_scope", "ALL_MARKETS"),
    ],
)
def test_catalog_filter_metadata_cannot_be_tampered(
    tmp_path: Path, field: str, value: object
) -> None:
    journal, run = _scan(tmp_path)
    view = _metadata_view(
        journal, run.asset_directory.sequence, lambda row: row.update({field: value})
    )
    with pytest.raises(ScannerDiscoveryAuditError, match="CATALOG_FILTER"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize(
    "records",
    [
        [{"symbol": "AAA", "class": "crypto", "status": "active"}],
        [{"symbol": "AAA", "class": "us_equity", "status": "inactive"}],
        [{"symbol": "AAA", "class": "us_equity", "status": "active"}] * 2,
    ],
)
def test_catalog_rows_must_match_filters_and_have_unique_identities(
    tmp_path: Path, records: object
) -> None:
    journal, run = _scan(tmp_path)
    view = _response_view(
        journal, run.asset_directory.sequence, json.dumps(records).encode()
    )
    with pytest.raises(ScannerDiscoveryAuditError, match="CATALOG_"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize("raw", [b'{"AAA":{},"AAA":{}}', b'{"AAA":{"bad":NaN}}'])
def test_duplicate_keys_and_nonfinite_source_json_are_rejected(
    tmp_path: Path, raw: bytes
) -> None:
    journal, run = _scan(tmp_path)
    view = _response_view(journal, run.batches[0].sequence, raw)
    with pytest.raises(ScannerDiscoveryAuditError, match="STRICT_JSON"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


def test_foreign_returned_symbol_is_not_catalog_coverage(tmp_path: Path) -> None:
    journal, run = _scan(tmp_path)
    view = _response_view(journal, run.batches[0].sequence, b'{"FOREIGN":{}}')
    with pytest.raises(ScannerDiscoveryAuditError, match="FOREIGN"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize("change", ["omit", "duplicate", "reverse"])
def test_batch_manifest_references_must_be_exhaustive_and_canonical(
    tmp_path: Path, change: str
) -> None:
    journal, run = _scan(tmp_path)
    sequences = [batch.sequence for batch in run.batches]
    values = {
        "omit": sequences[:-1],
        "duplicate": [sequences[0], sequences[0]],
        "reverse": sequences[::-1],
    }
    view = _metadata_view(
        journal,
        run.manifest_sequence,
        lambda row: row.update(snapshot_batch_sequences=values[change]),
    )
    if change == "omit":
        report = audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
        assert not report.request_coverage_complete
        assert "REQUESTED_CATALOG_COVERAGE_MISMATCH" in report.reasons
        assert "MANIFEST_REQUEST_COUNT_MISMATCH" in report.reasons
    else:
        with pytest.raises(ScannerDiscoveryAuditError, match="SEQUENCE_ORDER"):
            audit_iex_asset_discovery(
                view, manifest_sequence=run.manifest_sequence, decision_at=NOW
            )
    journal.close()


def test_overlapping_requests_are_reported_without_silently_deduplicating(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path)
    sequence = run.batches[1].sequence
    view = _response_view(journal, sequence, b'{"BBB":{},"CCC":{}}')

    def change(row: dict[str, Any]) -> None:
        row["requested_symbols"] = ["BBB", "CCC"]
        row["request_url"] = (
            "https://data.alpaca.markets/v2/stocks/snapshots?"
            + urlencode({"symbols": "BBB,CCC", "feed": "iex"})
        )

    view = _metadata_view(view, sequence, change)
    report = audit_iex_asset_discovery(
        view, manifest_sequence=run.manifest_sequence, decision_at=NOW
    )
    assert report.requested_symbols == ("AAA", "BBB", "BBB", "CCC", "MISSING")
    assert "REQUESTED_SYMBOL_DUPLICATE" in report.reasons
    assert "BATCH_CHUNK_BOUNDARY_MISMATCH" in report.reasons
    assert not report.request_coverage_complete
    journal.close()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("requested_symbol_count", 99, "MANIFEST_REQUEST_COUNT_MISMATCH"),
        ("missing_observation_symbols", [], "MANIFEST_MISSING_LIST_MISMATCH"),
        ("max_symbols_per_batch", 3, "BATCH_CHUNK_BOUNDARY_MISMATCH"),
    ],
)
def test_manifest_counts_missing_lists_and_declared_chunks_are_verified(
    tmp_path: Path, field: str, value: object, reason: str
) -> None:
    journal, run = _scan(tmp_path)
    view = _metadata_view(
        journal, run.manifest_sequence, lambda row: row.update({field: value})
    )
    report = audit_iex_asset_discovery(
        view, manifest_sequence=run.manifest_sequence, decision_at=NOW
    )
    assert reason in report.reasons and not report.request_coverage_complete
    journal.close()


@pytest.mark.parametrize("component", ["catalog", "batch"])
def test_request_receipt_and_dependency_clock_conflicts_are_rejected(
    tmp_path: Path, component: str
) -> None:
    journal, run = _scan(tmp_path)
    sequence = (
        run.asset_directory.sequence
        if component == "catalog"
        else run.batches[0].sequence
    )
    requested = (
        NOW + timedelta(seconds=1)
        if component == "catalog"
        else NOW - timedelta(seconds=1)
    )
    view = _metadata_view(
        journal, sequence, lambda row: row.update(requested_at=requested.isoformat())
    )
    with pytest.raises(ScannerDiscoveryAuditError, match="CLOCK"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


def test_empty_catalog_has_vacuous_request_and_return_coverage(tmp_path: Path) -> None:
    journal, run = _scan(tmp_path, ())
    report = audit_iex_asset_discovery(
        journal, manifest_sequence=run.manifest_sequence, decision_at=NOW
    )
    assert (
        report.catalog_symbols
        == report.requested_symbols
        == report.returned_symbols
        == ()
    )
    assert report.request_coverage_complete and report.returned_data_coverage_complete
    assert report.strategy_eligibility == "BLOCKED_RESEARCH"
    journal.close()


@pytest.mark.parametrize("field", ["source_id", "feed", "event_kind"])
def test_batch_envelope_cannot_claim_foreign_source_provenance(
    tmp_path: Path, field: str
) -> None:
    journal, run = _scan(tmp_path)
    sequence = run.batches[0].sequence
    original = journal.read_event(sequence)
    assert original is not None
    envelope, stored = original
    if field == "source_id":
        altered = replace(envelope, source_id="other_provider")
    elif field == "feed":
        altered = replace(envelope, feed="sip")
    else:
        altered = replace(envelope, event_kind="SOMETHING_PARSED")
    view = JournalView(journal, {sequence: (altered, stored)})
    with pytest.raises(ScannerDiscoveryAuditError, match="PROVENANCE"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_symbol_count", True),
        ("max_symbols_per_batch", True),
        ("max_symbols_per_batch", 0),
    ],
)
def test_boolean_or_invalid_manifest_counts_are_not_numeric_evidence(
    tmp_path: Path, field: str, value: object
) -> None:
    journal, run = _scan(tmp_path)
    view = _metadata_view(
        journal, run.manifest_sequence, lambda row: row.update({field: value})
    )
    with pytest.raises(ScannerDiscoveryAuditError, match="COUNTS_INVALID"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


@pytest.mark.parametrize("component", ["batch", "manifest"])
def test_existing_evidence_cannot_be_promoted_to_full_market_coverage(
    tmp_path: Path, component: str
) -> None:
    journal, run = _scan(tmp_path)
    sequence = (
        run.batches[0].sequence if component == "batch" else run.manifest_sequence
    )
    view = _metadata_view(
        journal, sequence, lambda row: row.update(full_market_coverage=True)
    )
    with pytest.raises(ScannerDiscoveryAuditError, match="COVERAGE_LABEL"):
        audit_iex_asset_discovery(
            view, manifest_sequence=run.manifest_sequence, decision_at=NOW
        )
    journal.close()


def test_decision_before_manifest_availability_cannot_use_the_run(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path)
    with pytest.raises(ScannerDiscoveryAuditError, match="AVAILABILITY"):
        audit_iex_asset_discovery(
            journal,
            manifest_sequence=run.manifest_sequence,
            decision_at=NOW - timedelta(seconds=1),
        )
    journal.close()


def _raw_prefix(tmp_path: Path) -> tuple[Journal, IexAssetSnapshotRun, bytes]:
    source, run = _scan(tmp_path / "complete")
    journal = Journal(tmp_path / "prefix/journal.sqlite3", tmp_path / "prefix")
    for sequence in range(1, run.manifest_sequence):
        stored = source.read_event(sequence)
        assert stored is not None
        event, raw = stored
        copied = journal.append_next(
            source_id=event.source_id,
            feed=event.feed,
            event_kind=event.event_kind,
            payload=raw,
            system_first_seen_at=event.system_first_seen_at,
            processed_at=event.processed_at,
        )
        assert copied.sequence == event.sequence
    manifest = source.read_event(run.manifest_sequence)
    assert manifest is not None
    source.close()
    return journal, run, manifest[1]


def _manifest(journal: Journal, raw: bytes, at: datetime = NOW) -> int:
    return journal.append_next(
        source_id="alpaca_scanner_data",
        feed="iex",
        event_kind="IEX_ASSET_SNAPSHOT_SCAN_MANIFEST",
        payload=raw,
        system_first_seen_at=at,
        processed_at=at,
    ).sequence


def test_event_census_retains_missing_returns_without_strategy_or_time_claims(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path)
    try:
        report = audit_scanner_event_census(
            journal, through_sequence=run.manifest_sequence, decision_at=NOW
        )
        assert (
            report.catalog_event_count,
            report.snapshot_event_count,
            report.manifest_event_count,
        ) == (1, 3, 1)
        assert report.audited_unique_requested_observations == 5
        assert report.audited_unique_returned_observations == 4
        assert report.audited_unique_missing_observations == 1
        assert (
            not report.audit_errors
            and not report.orphan_catalog_refs
            and not report.orphan_snapshot_refs
        )
        assert report.manifest_audits[0].missing_symbols == ("MISSING",)
        assert len(report.event_refs) == 5
        assert (
            report.coverage_label == "NOT_SIP_EQUIVALENT"
            and not report.runtime_authorized
        )
        assert report.strategy_eligibility == "BLOCKED_RESEARCH"
        assert "FORMAL_ORIGIN_AND_NATURAL_ENROLLMENT_UNKNOWN" in report.limitations
    finally:
        journal.close()


def test_visible_unfinalized_raw_catalog_and_batches_remain_unresolved(
    tmp_path: Path,
) -> None:
    journal, run, _ = _raw_prefix(tmp_path)
    try:
        report = audit_scanner_event_census(
            journal, through_sequence=journal.read_evidence_sequence(), decision_at=NOW
        )
        assert report.manifest_event_count == 0 and not report.manifest_audits
        assert [row[0] for row in report.orphan_catalog_refs] == [
            run.asset_directory.sequence
        ]
        assert [row[0] for row in report.orphan_snapshot_refs] == [
            row.sequence for row in run.batches
        ]
        assert report.audited_unique_requested_observations == 0
        assert (
            "NO_RECORDED_EVENT_DOES_NOT_PROVE_NO_NETWORK_ATTEMPT" in report.limitations
        )
    finally:
        journal.close()


@pytest.mark.parametrize(
    "bad", [b"{}", b"not-json", b"[]", b'{"requested_symbol_count":true}']
)
def test_malformed_manifests_remain_errors_and_never_consume_raw_orphans(
    tmp_path: Path, bad: bytes
) -> None:
    journal, run, _ = _raw_prefix(tmp_path)
    try:
        seq = _manifest(journal, bad)
        report = audit_scanner_event_census(
            journal, through_sequence=seq, decision_at=NOW
        )
        assert report.manifest_event_count == 1 and not report.manifest_audits
        assert report.audit_errors and report.audit_errors[0][0] == seq
        assert len(report.orphan_catalog_refs) == 1 and len(
            report.orphan_snapshot_refs
        ) == len(run.batches)
        assert report.audited_unique_returned_observations == 0
    finally:
        journal.close()


def test_structured_manifest_with_bad_counts_also_leaves_coverage_unresolved(
    tmp_path: Path,
) -> None:
    journal, _, raw = _raw_prefix(tmp_path)
    try:
        body = json.loads(raw) | {"requested_symbol_count": 99}
        seq = _manifest(journal, json.dumps(body).encode())
        report = audit_scanner_event_census(
            journal, through_sequence=seq, decision_at=NOW
        )
        assert len(report.manifest_audits) == 1
        assert "MANIFEST_REQUEST_COUNT_MISMATCH" in report.manifest_audits[0].reasons
        assert (
            report.audit_errors
            and report.orphan_catalog_refs
            and report.orphan_snapshot_refs
        )
        assert report.audited_unique_requested_observations == 0
    finally:
        journal.close()


def test_shared_raw_sources_count_once_while_both_manifest_denominators_survive(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path)
    try:
        raw = journal.read_event(run.manifest_sequence)
        assert raw is not None
        duplicate = _manifest(journal, raw[1])
        report = audit_scanner_event_census(
            journal, through_sequence=duplicate, decision_at=NOW
        )
        assert report.manifest_event_count == len(report.manifest_audits) == 2
        assert (
            sum(len(audit.requested_symbols) for audit in report.manifest_audits) == 10
        )
        assert report.audited_unique_requested_observations == 5
        assert report.audited_unique_returned_observations == 4
        assert report.audited_unique_missing_observations == 1
        assert not report.orphan_catalog_refs and not report.orphan_snapshot_refs
    finally:
        journal.close()


def test_two_distinct_raw_sweeps_are_not_deduped_as_one_symbol_day_candidate(
    tmp_path: Path,
) -> None:
    journal, _ = _scan(tmp_path)
    try:
        client = AlpacaScannerData(
            PaperCredentials(SecretValue("test-key"), SecretValue("test-secret")),
            journal,
            timeout_seconds=1,
            fetch=SyntheticFetcher(SYMBOLS),
            clock=lambda: NOW,
        )
        later = client.collect_current_asset_iex_snapshots(max_symbols_per_batch=2)
        report = audit_scanner_event_census(
            journal, through_sequence=later.manifest_sequence, decision_at=NOW
        )
        assert (
            report.catalog_event_count,
            report.snapshot_event_count,
            report.manifest_event_count,
        ) == (2, 6, 2)
        assert report.audited_unique_requested_observations == 10
        assert report.audited_unique_returned_observations == 8
        assert not report.audit_errors
    finally:
        journal.close()


def test_future_or_after_horizon_manifest_cannot_retroactively_resolve_prefix_orphans(
    tmp_path: Path,
) -> None:
    journal, _, raw = _raw_prefix(tmp_path)
    try:
        prefix = journal.read_evidence_sequence()
        before = audit_scanner_event_census(
            journal, through_sequence=prefix, decision_at=NOW
        )
        future = NOW + timedelta(seconds=1)
        final = _manifest(journal, raw, future)
        at_old_time = audit_scanner_event_census(
            journal, through_sequence=final, decision_at=NOW
        )
        assert at_old_time.event_refs == before.event_refs
        assert at_old_time.orphan_catalog_refs == before.orphan_catalog_refs
        assert at_old_time.orphan_snapshot_refs == before.orphan_snapshot_refs
        old_horizon = audit_scanner_event_census(
            journal, through_sequence=prefix, decision_at=future
        )
        assert old_horizon.orphan_catalog_refs == before.orphan_catalog_refs
        assert old_horizon.orphan_snapshot_refs == before.orphan_snapshot_refs
        current = audit_scanner_event_census(
            journal, through_sequence=final, decision_at=future
        )
        assert current.manifest_event_count == 1 and not current.orphan_snapshot_refs
    finally:
        journal.close()


@pytest.mark.parametrize("component", ["catalog", "batch", "manifest"])
def test_storage_hash_fault_aborts_the_entire_census_instead_of_reporting_zero(
    tmp_path: Path, component: str
) -> None:
    journal, run = _scan(tmp_path)
    seq = {
        "catalog": run.asset_directory.sequence,
        "batch": run.batches[0].sequence,
        "manifest": run.manifest_sequence,
    }[component]
    try:
        with sqlite3.connect(tmp_path / "runtime/journal.sqlite3") as db:
            db.execute("DROP TRIGGER events_no_update")
            db.execute("UPDATE events SET payload=? WHERE sequence=?", (b"{}", seq))
        with pytest.raises(JournalError, match="integrity"):
            audit_scanner_event_census(
                journal, through_sequence=run.manifest_sequence, decision_at=NOW
            )
    finally:
        journal.close()


def test_vendor_raw_digest_fault_is_not_downgraded_to_a_manifest_outcome(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path)
    try:
        saved = journal.read_event(run.batches[0].sequence)
        assert saved is not None
        raw = json.dumps(json.loads(saved[1]) | {"response_sha256": "0" * 64}).encode()
        with sqlite3.connect(tmp_path / "runtime/journal.sqlite3") as db:
            db.execute("DROP TRIGGER events_no_update")
            db.execute(
                "UPDATE events SET payload=?,payload_ref=? WHERE sequence=?",
                (
                    raw,
                    "sha256:" + hashlib.sha256(raw).hexdigest(),
                    run.batches[0].sequence,
                ),
            )
        with pytest.raises(
            ScannerDiscoveryAuditError, match="RAW_DIGEST_OR_SIZE_INVALID"
        ):
            audit_scanner_event_census(
                journal, through_sequence=run.manifest_sequence, decision_at=NOW
            )
    finally:
        journal.close()


def test_wrong_source_is_excluded_and_unknown_scanner_kind_is_explicitly_listed(
    tmp_path: Path,
) -> None:
    journal, run = _scan(tmp_path)
    try:
        other = journal.append_next(
            source_id="foreign-scanner",
            feed="iex",
            event_kind="IEX_ASSET_SNAPSHOT_SCAN_MANIFEST",
            payload=b"not-json",
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        unknown = journal.append_next(
            source_id="alpaca_scanner_data",
            feed="iex",
            event_kind="FUTURE_UNKNOWN_EVENT_KIND",
            payload=b"not-json",
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        report = audit_scanner_event_census(
            journal, through_sequence=unknown.sequence, decision_at=NOW
        )
        assert len(report.event_refs) == 2 + len(run.batches)
        assert all(row[0] != other.sequence for row in report.event_refs)
        assert report.excluded_kind_refs == (
            (unknown.sequence, unknown.payload_ref, unknown.event_kind),
        )
        assert not report.audit_errors
    finally:
        journal.close()


def test_census_reader_and_aggregate_are_strictly_read_only(tmp_path: Path) -> None:
    journal, run = _scan(tmp_path)
    attempts: list[int] = []

    def authorize(
        action: int,
        _a: str | None,
        _b: str | None,
        _db: str | None,
        _trigger: str | None,
    ) -> int:
        if action in {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_TRANSACTION,
        }:
            attempts.append(action)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        journal._db.set_authorizer(authorize)
        before = journal.read_evidence_sequence()
        report = audit_scanner_event_census(
            journal, through_sequence=before, decision_at=NOW
        )
        assert report.manifest_event_count == 1 and not attempts
        assert journal.read_evidence_sequence() == before == run.manifest_sequence
    finally:
        journal.close()


@pytest.mark.parametrize("horizon", [-1, True, 1000])
def test_scanner_census_horizon_cannot_be_implicit_invalid_or_future(
    tmp_path: Path, horizon: int
) -> None:
    journal, _ = _scan(tmp_path)
    try:
        with pytest.raises(JournalError, match="horizon"):
            audit_scanner_event_census(
                journal, through_sequence=horizon, decision_at=NOW
            )
    finally:
        journal.close()


def test_empty_prefix_is_only_zero_stored_events_not_no_attempts_or_formal_candidates(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "journal.sqlite3", tmp_path)
    try:
        report = audit_scanner_event_census(
            journal, through_sequence=0, decision_at=NOW
        )
        assert report.event_refs == ()
        assert report.manifest_audits == ()
        assert report.audited_unique_requested_observations == 0
        assert (
            report.scope
            == "VISIBLE_KNOWN_DISCOVERY_EVENT_IDENTITIES_NOT_ALL_SCANNER_ACTIVITY"
        )
        assert (
            "EVENT_COUNTS_DO_NOT_PROVE_SESSION_OR_WALL_CLOCK_COVERAGE"
            in report.limitations
        )
        with pytest.raises(ScannerDiscoveryAuditError, match="UTC"):
            audit_scanner_event_census(
                journal, through_sequence=0, decision_at=NOW.replace(tzinfo=None)
            )
    finally:
        journal.close()
