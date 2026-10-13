from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.scanner_observation import (
    ScannerObservationError,
    SymbolObservation,
    capture_scanner_observations,
)

AS_OF = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)


def _observation(
    symbol: str,
    *,
    price: str = "11",
    float_shares: int | None = None,
    float_source: str | None = None,
    float_observed_at: datetime | None = None,
) -> SymbolObservation:
    return SymbolObservation(
        symbol=symbol,
        as_of=AS_OF,
        feed="iex",
        price_source="alpaca_iex_stream",
        latest_trade_price=Decimal(price),
        prior_close=Decimal("10"),
        prior_close_as_of=AS_OF - timedelta(days=1),
        session_volume=1_000,
        trailing_daily_volumes=tuple([100] * 50),
        historical_feed="iex",
        history_source="alpaca_iex_history",
        history_available_at=AS_OF - timedelta(seconds=1),
        history_adjustment="raw",
        float_shares=float_shares,
        float_source=float_source,
        float_observed_at=float_observed_at,
    )


def test_measurements_rank_only_supplied_iex_symbols(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    snapshot = capture_scanner_observations(
        journal,
        (_observation("XYZ", price="12"), _observation("ABC", price="11")),
        first_seen_at=AS_OF,
        processed_at=AS_OF + timedelta(seconds=1),
    )

    rows = snapshot["rows"]
    assert isinstance(rows, list)
    assert [row["symbol"] for row in rows] == ["XYZ", "ABC"]
    assert rows[0]["observed_rank_by_change"] == 1
    assert rows[0]["change_percent"] == "20.0"
    assert rows[0]["session_volume_over_30d_mean"] == "10"
    assert rows[0]["session_volume_over_50d_mean"] == "10"
    assert snapshot["coverage_label"] == "NOT_SIP_EQUIVALENT"
    assert snapshot["full_market_coverage"] is False
    assert snapshot["scope"] == "EXPLICIT_OBSERVED_SYMBOLS_ONLY"
    assert snapshot["strategy_eligibility"] == "BLOCKED_RESEARCH"
    assert rows[0]["float_status"] == "MISSING"
    event = journal.read_event(1)
    assert event is not None and event[0].event_kind == "SCANNER_OBSERVATION"
    assert event[0].system_first_seen_at == AS_OF
    assert event[0].processed_at == AS_OF + timedelta(seconds=1)
    journal.close()


def test_local_first_seen_cannot_precede_provider_observation(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    with pytest.raises(ScannerObservationError, match="first-seen precedes"):
        capture_scanner_observations(
            journal,
            (_observation("ABC"),),
            first_seen_at=AS_OF - timedelta(seconds=1),
            processed_at=AS_OF,
        )
    journal.close()


def test_float_provenance_and_short_rvol_history_stay_unverified(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    observation = _observation(
        "ABC",
        float_shares=1_000_000,
        float_source="unverified-source",
        float_observed_at=AS_OF - timedelta(days=1),
    )
    observation = replace(observation, trailing_daily_volumes=tuple([100] * 30))
    snapshot = capture_scanner_observations(
        journal,
        (observation,),
        first_seen_at=AS_OF,
        processed_at=AS_OF + timedelta(seconds=1),
    )
    rows = snapshot["rows"]
    assert isinstance(rows, list)
    row = rows[0]
    assert isinstance(row, dict)
    assert row["float_status"] == "SOURCE_TIMESTAMP_RECORDED_NOT_VALIDATED"
    assert row["session_volume_over_30d_mean"] == "10"
    assert row["session_volume_over_50d_mean"] is None

    incomplete = capture_scanner_observations(
        journal,
        (
            _observation(
                "DEF",
                float_shares=1_000_000,
                float_source="   ",
            ),
        ),
        first_seen_at=AS_OF,
        processed_at=AS_OF + timedelta(seconds=1),
    )
    incomplete_rows = incomplete["rows"]
    assert isinstance(incomplete_rows, list)
    assert incomplete_rows[0]["float_status"] == "PROVENANCE_INCOMPLETE"
    journal.close()


def test_incomplete_coverage_invalid_feed_and_future_float_fail_closed(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    with pytest.raises(ScannerObservationError, match="IEX"):
        capture_scanner_observations(
            journal,
            (
                SymbolObservation(
                    symbol="ABC",
                    as_of=AS_OF,
                    feed="sip",
                    price_source="alpaca",
                    latest_trade_price=Decimal("11"),
                    prior_close=Decimal("10"),
                    prior_close_as_of=AS_OF - timedelta(days=1),
                    session_volume=100,
                    trailing_daily_volumes=(),
                    historical_feed="iex",
                    history_source="alpaca_iex_history",
                    history_available_at=AS_OF - timedelta(seconds=1),
                    history_adjustment="raw",
                ),
            ),
            first_seen_at=AS_OF,
            processed_at=AS_OF + timedelta(seconds=1),
        )

    with pytest.raises(ScannerObservationError, match="future float"):
        capture_scanner_observations(
            journal,
            (
                _observation(
                    "ABC",
                    float_shares=1_000_000,
                    float_source="source",
                    float_observed_at=AS_OF + timedelta(seconds=1),
                ),
            ),
            first_seen_at=AS_OF + timedelta(seconds=2),
            processed_at=AS_OF + timedelta(seconds=2),
        )
    journal.close()


def test_sip_history_and_future_volume_records_are_not_mixed(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    with pytest.raises(ScannerObservationError, match="historical volume feed"):
        capture_scanner_observations(
            journal,
            (replace(_observation("ABC"), historical_feed="sip"),),
            first_seen_at=AS_OF + timedelta(seconds=1),
            processed_at=AS_OF + timedelta(seconds=1),
        )
    with pytest.raises(ScannerObservationError, match="future history"):
        capture_scanner_observations(
            journal,
            (
                replace(
                    _observation("ABC"),
                    history_available_at=AS_OF + timedelta(seconds=1),
                ),
            ),
            first_seen_at=AS_OF + timedelta(seconds=2),
            processed_at=AS_OF + timedelta(seconds=2),
        )
    journal.close()
