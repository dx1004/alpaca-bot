"""Per-symbol, replayable observations derived from journaled IEX snapshots."""

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.scanner_data import IexSnapshotBatch


class SnapshotObservationError(ValueError):
    """A snapshot batch does not match its raw journal evidence."""


@dataclass(frozen=True)
class SnapshotObservationResult:
    symbol: str
    status: str
    sequence: int | None
    source_timestamp_status: str | None


def journal_iex_snapshot_observations(
    journal: Journal,
    batch: IexSnapshotBatch,
    *,
    processed_at: datetime,
) -> tuple[SnapshotObservationResult, ...]:
    """Append one replayable observation per explicit symbol in an IEX batch.

    The raw Alpaca response remains authoritative. Per-symbol source timestamps
    are retained independently; this function does not rank symbols, derive
    strategy features, or claim full-market coverage.
    """
    _require_utc(processed_at, "processed_at")
    raw_event = journal.read_event(batch.sequence)
    if raw_event is None:
        raise SnapshotObservationError("raw snapshot response is missing")
    raw_envelope, raw_metadata_bytes = raw_event
    if (
        raw_envelope.event_kind != "IEX_SNAPSHOT_RAW_RESPONSE"
        or raw_envelope.feed != "iex"
        or raw_envelope.source_id != "alpaca_scanner_data"
        or batch.retrieved_at != raw_envelope.system_first_seen_at
    ):
        raise SnapshotObservationError("snapshot response provenance is invalid")

    metadata = _decode_object(raw_metadata_bytes, "raw response metadata")
    encoded = metadata.get("raw_response_base64")
    digest = metadata.get("response_sha256")
    requested = metadata.get("requested_symbols")
    if (
        metadata.get("feed") != "iex"
        or metadata.get("coverage_label") != "NOT_SIP_EQUIVALENT"
        or metadata.get("full_market_coverage") is not False
    ):
        raise SnapshotObservationError("raw snapshot coverage provenance is invalid")
    if not isinstance(encoded, str) or not isinstance(digest, str):
        raise SnapshotObservationError("raw snapshot response bytes are unavailable")
    if requested != list(batch.requested_symbols):
        raise SnapshotObservationError("requested symbol provenance does not match")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise SnapshotObservationError(
            "raw snapshot response encoding is invalid"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SnapshotObservationError("raw snapshot response digest does not match")
    response = _decode_object(raw, "snapshot response")
    if response != dict(batch.snapshots):
        raise SnapshotObservationError("parsed snapshots do not match raw response")
    if set(response) - set(batch.requested_symbols):
        raise SnapshotObservationError("response contains an unrequested symbol")
    missing = tuple(
        symbol for symbol in batch.requested_symbols if symbol not in response
    )
    if missing != batch.missing_symbols:
        raise SnapshotObservationError("missing-symbol evidence does not match")
    if processed_at < raw_envelope.system_first_seen_at:
        raise SnapshotObservationError("processing precedes local first-seen")

    results: list[SnapshotObservationResult] = []
    for symbol in batch.requested_symbols:
        snapshot = response.get(symbol)
        if snapshot is None:
            results.append(SnapshotObservationResult(symbol, "MISSING", None, None))
            continue
        if not isinstance(snapshot, dict):
            raise SnapshotObservationError("snapshot symbol payload is malformed")
        latest_trade = snapshot.get("latestTrade")
        trade_timestamp = (
            latest_trade.get("t") if isinstance(latest_trade, dict) else None
        )
        provider_at, timestamp_status = _provider_timestamp(trade_timestamp)
        if provider_at is not None and provider_at > raw_envelope.system_first_seen_at:
            timestamp_status = "AFTER_LOCAL_FIRST_SEEN_CLOCK_CONFLICT"
        payload = {
            "record_type": "IEX_SYMBOL_SNAPSHOT_OBSERVATION",
            "symbol": symbol,
            "source_response_sequence": batch.sequence,
            "source_timestamps": _source_timestamps(snapshot),
            "provider_observation_at": (
                None if provider_at is None else provider_at.isoformat()
            ),
            "source_timestamp_status": timestamp_status,
            "snapshot": snapshot,
            "coverage_label": "NOT_SIP_EQUIVALENT",
            "scope": "EXPLICIT_CALLER_SYMBOL_ONLY",
            "ranking_basis": "NONE_PER_SYMBOL",
            "full_market_coverage": False,
            "strategy_eligibility": "BLOCKED_RESEARCH",
        }
        state_key = f"iex_snapshot_observation:{batch.sequence}:{symbol}"
        appended = journal.append_next_if_state_missing(
            state_key=state_key,
            state_value={"source_response_sequence": batch.sequence},
            source_id="alpaca_iex",
            feed="iex",
            event_kind="IEX_SYMBOL_SNAPSHOT_OBSERVATION",
            payload=_canonical_json(payload),
            system_first_seen_at=raw_envelope.system_first_seen_at,
            processed_at=processed_at,
            source_event_at=provider_at,
        )
        results.append(
            SnapshotObservationResult(
                symbol=symbol,
                status="RECORDED" if appended is not None else "DUPLICATE",
                sequence=None if appended is None else appended.sequence,
                source_timestamp_status=timestamp_status,
            )
        )
    return tuple(results)


def _decode_object(raw: bytes, name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise SnapshotObservationError(f"{name} is not strict JSON") from exc
    if not isinstance(decoded, dict):
        raise SnapshotObservationError(f"{name} must be a JSON object")
    return decoded


def _source_timestamps(snapshot: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    fields = ("latestTrade", "latestQuote", "minuteBar", "dailyBar", "prevDailyBar")
    for field in fields:
        value = snapshot.get(field)
        result[field] = value.get("t") if isinstance(value, dict) else None
    return result


def _provider_timestamp(value: object) -> tuple[datetime | None, str]:
    if not isinstance(value, str):
        return None, "MISSING_OR_MALFORMED"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "MISSING_OR_MALFORMED"
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None, "MISSING_OR_MALFORMED"
    return parsed.astimezone(UTC), "RECORDED"


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise SnapshotObservationError(f"{field} must be timezone-aware UTC")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")
