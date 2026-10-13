"""Read-only Alpaca IEX universe and explicit-symbol snapshot collection."""

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from urllib.parse import urlencode

from alpaca_ross_bot.config import PaperCredentials
from alpaca_ross_bot.http_transport import bounded_alpaca_get
from alpaca_ross_bot.journal import Journal

_ASSET_URL = "https://paper-api.alpaca.markets/v2/assets"
_SNAPSHOT_URL = "https://data.alpaca.markets/v2/stocks/snapshots"
_MAX_DEFAULT_BYTES = 16 * 1024 * 1024


class ScannerDataError(ValueError):
    """An Alpaca scanner data request or response failed validation."""


@dataclass(frozen=True)
class AssetDirectorySnapshot:
    sequence: int
    retrieved_at: datetime
    symbols: tuple[str, ...]
    catalog_scope: str = "CURRENT_ALPACA_ASSET_MASTER_NOT_HISTORICAL_UNIVERSE"


@dataclass(frozen=True)
class IexSnapshotBatch:
    sequence: int
    retrieved_at: datetime
    requested_symbols: tuple[str, ...]
    snapshots: Mapping[str, Mapping[str, object]]
    missing_symbols: tuple[str, ...]
    coverage_label: str = "NOT_SIP_EQUIVALENT"
    full_market_coverage: bool = False


type ScannerFetcher = Callable[[str, Mapping[str, str], float, int], bytes]
type Clock = Callable[[], datetime]


class AlpacaScannerData:
    """Collect current asset metadata and explicit IEX snapshot batches.

    Every method is a fixed-host GET. The asset list is a current broker
    catalog, not a historical or complete market universe; snapshots cover
    only caller-supplied symbols. No method assigns strategy eligibility.
    """

    def __init__(
        self,
        credentials: PaperCredentials,
        journal: Journal,
        *,
        timeout_seconds: float,
        max_response_bytes: int = _MAX_DEFAULT_BYTES,
        fetch: ScannerFetcher | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout must be positive and finite")
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ValueError("response byte limit must be positive")
        self._credentials = credentials
        self._journal = journal
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._fetch = fetch or bounded_alpaca_get
        self._clock = clock or (lambda: datetime.now(UTC))

    def collect_active_equity_assets(self) -> AssetDirectorySnapshot:
        """Capture Alpaca's current active equity asset catalog as evidence."""
        query = urlencode({"status": "active", "asset_class": "us_equity"})
        url = f"{_ASSET_URL}?{query}"
        raw, received_at, sequence = self._request_and_journal(
            url,
            feed="asset_master",
            event_kind="ASSET_DIRECTORY_RAW_RESPONSE",
            metadata={
                "query": {"status": "active", "asset_class": "us_equity"},
                "catalog_scope": "CURRENT_ALPACA_ASSET_MASTER_NOT_HISTORICAL_UNIVERSE",
            },
        )
        decoded = self._decode_response(raw, sequence, feed="asset_master")
        if not isinstance(decoded, list):
            self._reject_response(
                sequence,
                received_at,
                "asset response must be an array",
                feed="asset_master",
            )
            raise ScannerDataError("asset response must be an array")
        symbols: list[str] = []
        for record in decoded:
            if not isinstance(record, dict):
                self._reject_response(
                    sequence,
                    received_at,
                    "asset record is malformed",
                    feed="asset_master",
                )
                raise ScannerDataError("asset record is malformed")
            symbol = record.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                self._reject_response(
                    sequence,
                    received_at,
                    "asset symbol is malformed",
                    feed="asset_master",
                )
                raise ScannerDataError("asset symbol is malformed")
            if record.get("class") != "us_equity" or record.get("status") != "active":
                self._reject_response(
                    sequence,
                    received_at,
                    "asset record does not match requested filters",
                    feed="asset_master",
                )
                raise ScannerDataError("asset record does not match requested filters")
            symbols.append(symbol)
        if len(set(symbols)) != len(symbols):
            self._reject_response(
                sequence,
                received_at,
                "asset symbols are duplicated",
                feed="asset_master",
            )
            raise ScannerDataError("asset symbols are duplicated")
        self._journal_parse_result(
            source_id="alpaca_assets",
            feed="asset_master",
            event_kind="ASSET_DIRECTORY_PARSED",
            source_sequence=sequence,
            retrieved_at=received_at,
            processed_at=self._now_after(received_at),
            payload={"symbols": symbols, "symbol_count": len(symbols)},
        )
        return AssetDirectorySnapshot(sequence, received_at, tuple(symbols))

    def collect_iex_snapshot_batch(self, symbols: tuple[str, ...]) -> IexSnapshotBatch:
        """Fetch one explicit symbol batch from the IEX snapshot endpoint."""
        _validate_symbols(symbols)
        query = urlencode({"symbols": ",".join(symbols), "feed": "iex"})
        url = f"{_SNAPSHOT_URL}?{query}"
        raw, received_at, sequence = self._request_and_journal(
            url,
            feed="iex",
            event_kind="IEX_SNAPSHOT_RAW_RESPONSE",
            metadata={
                "requested_symbols": list(symbols),
                "feed": "iex",
                "coverage_label": "NOT_SIP_EQUIVALENT",
                "full_market_coverage": False,
                "universe_scope": "EXPLICIT_CALLER_SYMBOLS_ONLY",
            },
        )
        decoded = self._decode_response(raw, sequence, feed="iex")
        if not isinstance(decoded, dict):
            self._reject_response(
                sequence, received_at, "snapshot response must be an object", feed="iex"
            )
            raise ScannerDataError("snapshot response must be an object")
        snapshots: dict[str, Mapping[str, object]] = {}
        for symbol, snapshot in decoded.items():
            if symbol not in symbols:
                self._reject_response(
                    sequence,
                    received_at,
                    "snapshot response contains an unrequested symbol",
                    feed="iex",
                )
                raise ScannerDataError(
                    "snapshot response contains an unrequested symbol"
                )
            if not isinstance(snapshot, dict):
                self._reject_response(
                    sequence, received_at, "snapshot value is malformed", feed="iex"
                )
                raise ScannerDataError("snapshot value is malformed")
            snapshots[symbol] = snapshot
        missing = tuple(symbol for symbol in symbols if symbol not in snapshots)
        self._journal_parse_result(
            source_id="alpaca_iex",
            feed="iex",
            event_kind="IEX_SNAPSHOT_PARSED",
            source_sequence=sequence,
            retrieved_at=received_at,
            processed_at=self._now_after(received_at),
            payload={
                "requested_symbols": list(symbols),
                "returned_symbols": sorted(snapshots),
                "missing_symbols": list(missing),
                "coverage_label": "NOT_SIP_EQUIVALENT",
                "full_market_coverage": False,
                "strategy_eligibility": "BLOCKED_RESEARCH",
            },
        )
        return IexSnapshotBatch(
            sequence,
            received_at,
            symbols,
            snapshots,
            missing,
        )

    def _request_and_journal(
        self,
        url: str,
        *,
        feed: str,
        event_kind: str,
        metadata: Mapping[str, object],
    ) -> tuple[bytes, datetime, int]:
        requested_at = self._now()
        try:
            raw = self._fetch(
                url,
                self._credentials.auth_headers(),
                self._timeout,
                self._max_response_bytes,
            )
        except Exception as exc:
            failed_at = self._now_after(requested_at)
            self._journal_parse_result(
                source_id="alpaca_scanner_data",
                feed=feed,
                event_kind="SCANNER_DATA_REQUEST_FAILED",
                source_sequence=None,
                retrieved_at=failed_at,
                processed_at=failed_at,
                payload={
                    "request_url": url,
                    "requested_at": requested_at.isoformat(),
                    "failed_at": failed_at.isoformat(),
                    "error_type": type(exc).__name__,
                    "strategy_eligibility": "BLOCKED_RESEARCH",
                },
            )
            raise ScannerDataError("Alpaca scanner data request failed") from exc
        received_at = self._now_after(requested_at)
        if len(raw) > self._max_response_bytes:
            self._journal_parse_result(
                source_id="alpaca_scanner_data",
                feed=feed,
                event_kind="SCANNER_DATA_RESPONSE_REJECTED",
                source_sequence=None,
                retrieved_at=received_at,
                processed_at=received_at,
                payload={
                    "request_url": url,
                    "requested_at": requested_at.isoformat(),
                    "received_at": received_at.isoformat(),
                    "reason": "response_byte_budget_exceeded",
                    "response_bytes": len(raw),
                },
            )
            raise ScannerDataError("REST response byte budget exceeded")
        payload = {
            **metadata,
            "request_method": "GET",
            "request_url": url,
            "requested_at": requested_at.isoformat(),
            "received_at": received_at.isoformat(),
            "response_bytes": len(raw),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_response_base64": base64.b64encode(raw).decode("ascii"),
            "continuity_proven": False,
        }
        event = self._journal.append_next(
            source_id="alpaca_scanner_data",
            feed=feed,
            event_kind=event_kind,
            payload=_canonical_json(payload),
            system_first_seen_at=received_at,
            processed_at=received_at,
        )
        return raw, received_at, event.sequence

    def _decode_response(self, raw: bytes, sequence: int, *, feed: str) -> object:
        try:
            return json.loads(raw, parse_constant=_reject_constant)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            event = self._journal.read_event(sequence)
            if event is not None:
                self._reject_response(
                    sequence,
                    event[0].system_first_seen_at,
                    "REST response is not strict JSON",
                    feed=feed,
                )
            raise ScannerDataError("REST response is not strict JSON") from exc

    def _reject_response(
        self, sequence: int, retrieved_at: datetime, reason: str, *, feed: str
    ) -> None:
        self._journal_parse_result(
            source_id="alpaca_scanner_data",
            feed=feed,
            event_kind="SCANNER_DATA_RESPONSE_REJECTED",
            source_sequence=sequence,
            retrieved_at=retrieved_at,
            processed_at=self._now_after(retrieved_at),
            payload={"reason": reason, "strategy_eligibility": "BLOCKED_RESEARCH"},
        )

    def _journal_parse_result(
        self,
        *,
        source_id: str,
        feed: str,
        event_kind: str,
        source_sequence: int | None,
        retrieved_at: datetime,
        processed_at: datetime,
        payload: Mapping[str, object],
    ) -> None:
        result = dict(payload)
        if source_sequence is not None:
            result["source_response_sequence"] = source_sequence
        self._journal.append_next(
            source_id=source_id,
            feed=feed,
            event_kind=event_kind,
            payload=_canonical_json(result),
            system_first_seen_at=retrieved_at,
            processed_at=processed_at,
        )

    def _now(self) -> datetime:
        value = self._clock()
        _require_utc(value)
        return value

    def _now_after(self, previous: datetime) -> datetime:
        value = self._now()
        if value < previous:
            raise ScannerDataError("local clock moved backward during REST request")
        return value


def _validate_symbols(symbols: tuple[str, ...]) -> None:
    if not symbols or any(
        not isinstance(symbol, str)
        or not symbol.strip()
        or symbol.strip() != symbol
        or "," in symbol
        for symbol in symbols
    ):
        raise ScannerDataError("explicit symbols must be nonempty and unambiguous")
    if len(set(symbols)) != len(symbols):
        raise ScannerDataError("explicit symbols must be unique")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_utc(value: datetime) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ScannerDataError("local request clocks must be timezone-aware UTC")


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")
