"""IEX-scoped measurements for research, never Ross trade qualification."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.screening_profiles import measure_ross_screening_profiles


class ScannerObservationError(ValueError):
    """A scanner measurement is incomplete or has invalid provenance."""


@dataclass(frozen=True)
class SymbolObservation:
    symbol: str
    as_of: datetime
    feed: str
    price_source: str
    latest_trade_price: Decimal
    prior_close: Decimal
    prior_close_as_of: datetime
    session_volume: int
    trailing_daily_volumes: tuple[int, ...]
    historical_feed: str
    history_source: str
    history_available_at: datetime
    history_adjustment: str
    float_shares: int | None = None
    float_source: str | None = None
    float_observed_at: datetime | None = None


def capture_scanner_observations(
    journal: Journal,
    observations: tuple[SymbolObservation, ...],
    *,
    first_seen_at: datetime,
    processed_at: datetime,
) -> dict[str, object]:
    """Journal transparent IEX measurements ranked only within observed input.

    Values and RVOL-style ratios are descriptive measurements. This function
    does not discover the market universe, apply Ross screening criteria, or
    emit a trade-eligible candidate.
    """
    _require_utc(first_seen_at, "first_seen_at")
    _require_utc(processed_at, "processed_at")
    if not observations:
        raise ScannerObservationError("at least one symbol observation is required")
    as_of = observations[0].as_of
    _require_utc(as_of, "as_of")
    if first_seen_at < as_of:
        raise ScannerObservationError("local first-seen precedes source observation")
    if processed_at < first_seen_at:
        raise ScannerObservationError("processing precedes local first-seen")
    symbols: set[str] = set()
    measured: list[dict[str, object]] = []
    for observation in observations:
        row = _measure(observation)
        rvol_measurement = row["session_volume_over_30d_mean"]
        row["source_scoped_screening_measurements"] = measure_ross_screening_profiles(
            price=observation.latest_trade_price,
            rvol_30d=(
                None if rvol_measurement is None else Decimal(str(rvol_measurement))
            ),
            float_shares=observation.float_shares,
            float_status=str(row["float_status"]),
        )
        if observation.as_of != as_of:
            raise ScannerObservationError(
                "snapshot observations must share one as-of time"
            )
        if first_seen_at < observation.as_of:
            raise ScannerObservationError(
                "local first-seen precedes a source observation"
            )
        if observation.symbol in symbols:
            raise ScannerObservationError("snapshot symbols must be unique")
        symbols.add(observation.symbol)
        measured.append(row)
    measured.sort(
        key=lambda row: (
            -Decimal(str(row["change_percent"])),
            str(row["symbol"]),
        )
    )
    for rank, row in enumerate(measured, start=1):
        row["observed_rank_by_change"] = rank

    payload: dict[str, object] = {
        "record_type": "IEX_SCANNER_OBSERVATION",
        "as_of": as_of.isoformat(),
        "first_seen_at": first_seen_at.isoformat(),
        "processed_at": processed_at.isoformat(),
        "feed": "iex",
        "coverage_label": "NOT_SIP_EQUIVALENT",
        "scope": "EXPLICIT_OBSERVED_SYMBOLS_ONLY",
        "full_market_coverage": False,
        "strategy_eligibility": "BLOCKED_RESEARCH",
        "corporate_action_status": "UNVERIFIED",
        "ranking_basis": "change_percent_within_supplied_observations_only",
        "rows": measured,
    }
    journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="SCANNER_OBSERVATION",
        payload=json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"),
        system_first_seen_at=first_seen_at,
        processed_at=processed_at,
    )
    return payload


def _measure(observation: SymbolObservation) -> dict[str, object]:
    _require_utc(observation.as_of, "as_of")
    if observation.feed != "iex":
        raise ScannerObservationError("current scanner observations require IEX")
    if not observation.symbol or not observation.symbol.strip():
        raise ScannerObservationError("symbol must be nonempty")
    if (
        not observation.latest_trade_price.is_finite()
        or not observation.prior_close.is_finite()
        or observation.latest_trade_price <= 0
        or observation.prior_close <= 0
    ):
        raise ScannerObservationError("prices must be finite and positive")
    if type(observation.session_volume) is not int or observation.session_volume < 0:
        raise ScannerObservationError("session volume must be a nonnegative integer")
    if any(
        type(volume) is not int or volume < 0
        for volume in observation.trailing_daily_volumes
    ):
        raise ScannerObservationError("trailing volumes must be nonnegative integers")
    _require_utc(observation.prior_close_as_of, "prior_close_as_of")
    _require_utc(observation.history_available_at, "history_available_at")
    if observation.prior_close_as_of >= observation.as_of:
        raise ScannerObservationError("prior close timestamp must precede the snapshot")
    if observation.history_available_at > observation.as_of:
        raise ScannerObservationError(
            "future history cannot enter a point-in-time snapshot"
        )
    if observation.historical_feed != "iex":
        raise ScannerObservationError("historical volume feed must be explicitly IEX")
    if not observation.price_source.strip() or not observation.history_source.strip():
        raise ScannerObservationError(
            "price and historical source identifiers are required"
        )
    if not observation.history_adjustment.strip():
        raise ScannerObservationError("historical adjustment mode must be recorded")
    if observation.float_shares is None:
        if (
            observation.float_source is not None
            or observation.float_observed_at is not None
        ):
            raise ScannerObservationError(
                "float provenance cannot exist without a value"
            )
        float_status = "MISSING"
    else:
        if type(observation.float_shares) is not int or observation.float_shares <= 0:
            raise ScannerObservationError("float shares must be a positive integer")
        if (
            not observation.float_source
            or not observation.float_source.strip()
            or observation.float_observed_at is None
        ):
            float_status = "PROVENANCE_INCOMPLETE"
        else:
            _require_utc(observation.float_observed_at, "float_observed_at")
            if observation.float_observed_at > observation.as_of:
                raise ScannerObservationError(
                    "future float cannot enter a point-in-time snapshot"
                )
            float_status = "SOURCE_TIMESTAMP_RECORDED_NOT_VALIDATED"

    change_percent = (
        (observation.latest_trade_price / observation.prior_close) - Decimal(1)
    ) * Decimal(100)
    rvol_measurements: dict[str, str | None] = {}
    for days in (30, 50):
        history = observation.trailing_daily_volumes[-days:]
        if len(history) < days:
            rvol_measurements[f"session_volume_over_{days}d_mean"] = None
            continue
        mean_daily = Decimal(sum(history)) / Decimal(days)
        rvol_measurements[f"session_volume_over_{days}d_mean"] = (
            None
            if mean_daily == 0
            else str(Decimal(observation.session_volume) / mean_daily)
        )

    return {
        "symbol": observation.symbol,
        "feed": "iex",
        "price_source": observation.price_source,
        "latest_trade_price": str(observation.latest_trade_price),
        "prior_close": str(observation.prior_close),
        "prior_close_as_of": observation.prior_close_as_of.isoformat(),
        "change_percent": str(change_percent),
        "session_volume": observation.session_volume,
        "historical_feed": "iex",
        "history_source": observation.history_source,
        "history_available_at": observation.history_available_at.isoformat(),
        "history_adjustment": observation.history_adjustment,
        "rvol_measurement_basis": (
            "session_volume_over_full_day_mean_not_time_normalized"
        ),
        **rvol_measurements,
        "rvol_strategy_semantics": "UNRESOLVED_NOT_ROSS_ELIGIBILITY",
        "float_shares": observation.float_shares,
        "float_source": observation.float_source,
        "float_observed_at": (
            None
            if observation.float_observed_at is None
            else observation.float_observed_at.isoformat()
        ),
        "float_status": float_status,
        "strategy_eligibility": "BLOCKED_RESEARCH",
    }


def _require_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset != timedelta(0):
        raise ScannerObservationError(f"{field} must be timezone-aware UTC")
