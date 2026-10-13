import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.replay import ReplayMode, replay_at
from alpaca_ross_bot.scanner_data import AlpacaScannerData, IexSnapshotBatch
from alpaca_ross_bot.scanner_snapshot import (
    SnapshotObservationError,
    journal_iex_snapshot_observations,
)

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)


def _snapshot(trade_at: str) -> dict[str, object]:
    return {
        "latestTrade": {"t": trade_at, "p": 12.5},
        "latestQuote": {"t": "2026-10-13T15:59:59.900000Z"},
        "minuteBar": {"t": "2026-10-13T15:59:00Z"},
        "dailyBar": {"t": "2026-10-13T04:00:00Z"},
        "prevDailyBar": {"t": "2026-10-12T04:00:00Z"},
    }


def test_snapshot_symbols_keep_individual_source_time_and_raw_provenance(
    tmp_path: Path,
) -> None:
    snapshots = {
        "AAA": _snapshot("2026-10-13T15:59:59.100000Z"),
        "BBB": _snapshot("2026-10-13T15:59:58.900000Z"),
    }
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    client = AlpacaScannerData(
        PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
        journal,
        timeout_seconds=2,
        fetch=lambda *_: json.dumps(snapshots).encode(),
        clock=lambda: NOW,
    )
    batch = client.collect_iex_snapshot_batch(("AAA", "BBB", "MISSING"))

    results = journal_iex_snapshot_observations(
        journal, batch, processed_at=NOW + timedelta(seconds=1)
    )

    assert [result.status for result in results] == [
        "RECORDED",
        "RECORDED",
        "MISSING",
    ]
    assert results[0].sequence is not None and results[1].sequence is not None
    first = journal.read_event(results[0].sequence)
    second = journal.read_event(results[1].sequence)
    assert first is not None and second is not None
    first_source_time = first[0].source_event_at
    second_source_time = second[0].source_event_at
    assert first_source_time is not None and second_source_time is not None
    assert first_source_time.isoformat() == "2026-10-13T15:59:59.100000+00:00"
    assert second_source_time.isoformat() == "2026-10-13T15:59:58.900000+00:00"
    assert first[0].system_first_seen_at == NOW
    assert first[0].available_at == NOW + timedelta(seconds=1)
    payload = json.loads(first[1])
    assert payload["source_response_sequence"] == batch.sequence
    assert payload["coverage_label"] == "NOT_SIP_EQUIVALENT"
    assert payload["full_market_coverage"] is False
    assert payload["ranking_basis"] == "NONE_PER_SYMBOL"
    assert payload["strategy_eligibility"] == "BLOCKED_RESEARCH"
    early = replay_at(
        [first[0], second[0]],
        ReplayMode.PROSPECTIVE,
        NOW + timedelta(milliseconds=500),
    )
    available = replay_at(
        [first[0], second[0]], ReplayMode.PROSPECTIVE, NOW + timedelta(seconds=1)
    )
    assert early.observations == ()
    assert [item.event.sequence for item in available.observations] == [
        first[0].sequence,
        second[0].sequence,
    ]
    available.require_recorded_timing()

    duplicate = journal_iex_snapshot_observations(
        journal, batch, processed_at=NOW + timedelta(seconds=2)
    )
    assert [result.status for result in duplicate] == [
        "DUPLICATE",
        "DUPLICATE",
        "MISSING",
    ]
    assert journal.read_event(5) is None
    journal.close()


def test_snapshot_projection_rejects_batch_not_matching_raw_response(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    client = AlpacaScannerData(
        PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
        journal,
        timeout_seconds=2,
        fetch=lambda *_: json.dumps(
            {"AAA": _snapshot("2026-10-13T15:59:59Z")}
        ).encode(),
        clock=lambda: NOW,
    )
    batch = client.collect_iex_snapshot_batch(("AAA",))
    altered = IexSnapshotBatch(
        sequence=batch.sequence,
        retrieved_at=batch.retrieved_at,
        requested_symbols=batch.requested_symbols,
        snapshots={"AAA": {"latestTrade": {"t": "2026-10-13T15:59:00Z"}}},
        missing_symbols=batch.missing_symbols,
    )

    with pytest.raises(SnapshotObservationError, match="do not match"):
        journal_iex_snapshot_observations(
            journal, altered, processed_at=NOW + timedelta(seconds=1)
        )
    assert journal.read_event(3) is None
    journal.close()


def test_missing_or_malformed_trade_timestamp_is_preserved_but_not_trusted(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    client = AlpacaScannerData(
        PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
        journal,
        timeout_seconds=2,
        fetch=lambda *_: json.dumps(
            {"AAA": {"latestTrade": {"t": "not-a-time", "p": 12.5}}}
        ).encode(),
        clock=lambda: NOW,
    )
    batch = client.collect_iex_snapshot_batch(("AAA",))

    (result,) = journal_iex_snapshot_observations(
        journal, batch, processed_at=NOW + timedelta(seconds=1)
    )

    assert result.status == "RECORDED"
    assert result.source_timestamp_status == "MISSING_OR_MALFORMED"
    assert result.sequence is not None
    event = journal.read_event(result.sequence)
    assert event is not None and event[0].source_event_at is None
    assert json.loads(event[1])["snapshot"]["latestTrade"]["t"] == "not-a-time"
    journal.close()


def test_future_trade_timestamp_is_retained_as_clock_conflict(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    client = AlpacaScannerData(
        PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
        journal,
        timeout_seconds=2,
        fetch=lambda *_: json.dumps(
            {"AAA": _snapshot("2026-10-13T16:00:02Z")}
        ).encode(),
        clock=lambda: NOW,
    )
    batch = client.collect_iex_snapshot_batch(("AAA",))

    (result,) = journal_iex_snapshot_observations(
        journal, batch, processed_at=NOW + timedelta(seconds=1)
    )

    assert result.source_timestamp_status == ("AFTER_LOCAL_FIRST_SEEN_CLOCK_CONFLICT")
    assert result.sequence is not None
    event = journal.read_event(result.sequence)
    assert event is not None and event[0].external_clock_conflict
    assert json.loads(event[1])["strategy_eligibility"] == "BLOCKED_RESEARCH"
    journal.close()
