import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.scanner_data import AlpacaScannerData, IexAssetSnapshotRun
from alpaca_ross_bot.scanner_discovery_audit import (
    ScannerDiscoveryAuditError,
    audit_iex_asset_discovery,
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
