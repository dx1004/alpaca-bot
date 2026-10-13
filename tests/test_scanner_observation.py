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
    profiles = rows[0]["source_scoped_screening_measurements"]
    assert isinstance(profiles, list) and len(profiles) == 3
    general, stock_selection, worksheet = profiles
    assert general["profile_id"] == "ROSS_TOOLKIT_GENERAL_2025_SCREEN"
    assert general["source_ids"] == [
        "ROSS_TOOLKIT_GENERAL_2025",
    ]
    assert stock_selection["profile_id"] == "ROSS_TOOLKIT_STOCK_SELECTION_2025_SCREEN"
    assert stock_selection["source_ids"] == ["ROSS_TOOLKIT_RVOL_2025"]
    assert worksheet["profile_id"] == "ROSS_TOOLKIT_WORKSHEET_2025_SCREEN"
    assert worksheet["source_ids"] == ["ROSS_TOOLKIT_WORKSHEET_2025"]
    for profile in profiles:
        assert profile["assessment_status"] == "BLOCKED_RESEARCH"
        assert profile["strategy_eligibility"] == "BLOCKED_RESEARCH"
    general_measurements = general["measurements"]
    stock_selection_measurements = stock_selection["measurements"]
    worksheet_measurements = worksheet["measurements"]
    assert isinstance(general_measurements, dict)
    assert isinstance(stock_selection_measurements, dict)
    assert isinstance(worksheet_measurements, dict)
    assert stock_selection_measurements["rvol_30d_at_least_5x"]["result"] == (
        "MEASURED_THRESHOLD_MET"
    )
    assert stock_selection_measurements["daily_gain_at_least_10pct"]["result"] == (
        "MEASURED_THRESHOLD_MET"
    )
    assert stock_selection_measurements["continuation_exception"] == (
        "UNAVAILABLE_PRIOR_DAY_MOVE_AND_HOLDING_PRICE_UNDEFINED"
    )
    assert stock_selection_measurements["catalyst_review"] == (
        "UNAVAILABLE_CAUSAL_HEADLINE_REVIEW_NOT_AUTOMATED"
    )
    assert general_measurements["news_preference"] == (
        "UNAVAILABLE_HUMAN_REVIEW_NOT_A_HARD_FILTER"
    )
    assert worksheet_measurements["reported_price_range_5_to_10_usd"]["result"] == (
        "MEASURED_OUTSIDE_REPORTED_RANGE"
    )
    assert worksheet_measurements["positive_catalyst"] == (
        "UNAVAILABLE_NEWS_SEMANTICS_BLOCKED"
    )
    assert worksheet_measurements["top_five_premarket_gapper"] == (
        "UNAVAILABLE_PREMARKET_RANK_MISSING"
    )
    assert worksheet_measurements["hot_cold_market_state"] == (
        "UNAVAILABLE_REGIME_NOT_CLASSIFIED"
    )
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


def test_unmet_daily_gain_keeps_continuation_exception_unresolved(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    snapshot = capture_scanner_observations(
        journal,
        (_observation("ABC", price="10.9"),),
        first_seen_at=AS_OF,
        processed_at=AS_OF + timedelta(seconds=1),
    )

    rows = snapshot["rows"]
    assert isinstance(rows, list)
    row = rows[0]
    assert isinstance(row, dict)
    profiles = row["source_scoped_screening_measurements"]
    assert isinstance(profiles, list)
    stock_selection = profiles[1]
    measurements = stock_selection["measurements"]
    assert isinstance(measurements, dict)
    assert measurements["daily_gain_at_least_10pct"]["result"] == (
        "MEASURED_THRESHOLD_NOT_MET"
    )
    assert measurements["continuation_exception"] == (
        "UNAVAILABLE_PRIOR_DAY_MOVE_AND_HOLDING_PRICE_UNDEFINED"
    )
    assert stock_selection["strategy_eligibility"] == "BLOCKED_RESEARCH"
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
    profiles = row["source_scoped_screening_measurements"]
    assert isinstance(profiles, list)
    general = profiles[0]
    general_measurements = general["measurements"]
    assert isinstance(general_measurements, dict)
    float_result = general_measurements["float_under_20m"]
    assert isinstance(float_result, dict)
    assert float_result["result"] == "MEASUREMENT_ONLY_SOURCE_UNVERIFIED"
    assert float_result["numeric_comparison"] == "MEASURED_THRESHOLD_MET"

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
