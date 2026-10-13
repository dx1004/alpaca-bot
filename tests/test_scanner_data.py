import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.http_transport import HttpTransportError, bounded_alpaca_get
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.scanner_data import AlpacaScannerData, ScannerDataError

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)


class FakeFetcher:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.requests: list[tuple[str, Mapping[str, str], float, int]] = []

    def __call__(
        self, url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        self.requests.append((url, headers, timeout, max_bytes))
        return self.response


def _credentials() -> PaperCredentials:
    return PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret"))


def _client(
    tmp_path: Path,
    fetch: FakeFetcher,
    *,
    max_bytes: int = 1024 * 1024,
) -> tuple[AlpacaScannerData, Journal]:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    client = AlpacaScannerData(
        _credentials(),
        journal,
        timeout_seconds=2,
        max_response_bytes=max_bytes,
        fetch=fetch,
        clock=lambda: NOW,
    )
    return client, journal


def test_asset_directory_is_scoped_to_current_active_equity_catalog(
    tmp_path: Path,
) -> None:
    fetch = FakeFetcher(
        json.dumps(
            [
                {
                    "id": "asset-1",
                    "symbol": "AAPL",
                    "class": "us_equity",
                    "status": "active",
                },
                {
                    "id": "asset-2",
                    "symbol": "XYZ",
                    "class": "us_equity",
                    "status": "active",
                },
            ]
        ).encode()
    )
    client, journal = _client(tmp_path, fetch)

    result = client.collect_active_equity_assets()

    assert result.symbols == ("AAPL", "XYZ")
    assert result.catalog_scope == "CURRENT_ALPACA_ASSET_MASTER_NOT_HISTORICAL_UNIVERSE"
    url, headers, timeout, max_bytes = fetch.requests[0]
    assert url.startswith("https://paper-api.alpaca.markets/v2/assets?")
    assert parse_qs(urlsplit(url).query) == {
        "status": ["active"],
        "asset_class": ["us_equity"],
    }
    assert headers == {
        "APCA-API-KEY-ID": "paper-key",
        "APCA-API-SECRET-KEY": "paper-secret",
    }
    assert timeout == 2
    assert max_bytes == 1024 * 1024
    raw_event = journal.read_event(1)
    parsed_event = journal.read_event(2)
    assert (
        raw_event is not None
        and raw_event[0].event_kind == "ASSET_DIRECTORY_RAW_RESPONSE"
    )
    assert (
        parsed_event is not None
        and parsed_event[0].event_kind == "ASSET_DIRECTORY_PARSED"
    )
    raw_payload = json.loads(raw_event[1])
    assert base64.b64decode(raw_payload["raw_response_base64"]) == fetch.response
    assert raw_payload["continuity_proven"] is False
    assert json.loads(parsed_event[1])["source_response_sequence"] == 1
    journal.close()


def test_snapshot_batch_uses_explicit_iex_symbols_and_reports_missing_data(
    tmp_path: Path,
) -> None:
    fetch = FakeFetcher(
        json.dumps(
            {
                "AAPL": {
                    "latestTrade": {
                        "t": "2026-10-13T15:59:59Z",
                        "p": 250.25,
                    },
                    "prevDailyBar": {"t": "2026-10-12T04:00:00Z", "c": 249.5},
                }
            }
        ).encode()
    )
    client, journal = _client(tmp_path, fetch)

    result = client.collect_iex_snapshot_batch(("AAPL", "MISSING"))

    assert tuple(result.snapshots) == ("AAPL",)
    assert result.missing_symbols == ("MISSING",)
    assert result.coverage_label == "NOT_SIP_EQUIVALENT"
    assert result.full_market_coverage is False
    url, _, _, _ = fetch.requests[0]
    assert url.startswith("https://data.alpaca.markets/v2/stocks/snapshots?")
    assert parse_qs(urlsplit(url).query) == {
        "symbols": ["AAPL,MISSING"],
        "feed": ["iex"],
    }
    raw_event = journal.read_event(1)
    parsed_event = journal.read_event(2)
    assert raw_event is not None and raw_event[0].feed == "iex"
    raw_payload = json.loads(raw_event[1])
    assert raw_payload["full_market_coverage"] is False
    assert parsed_event is not None
    assert json.loads(parsed_event[1])["missing_symbols"] == ["MISSING"]
    assert json.loads(parsed_event[1])["strategy_eligibility"] == "BLOCKED_RESEARCH"
    journal.close()


def test_invalid_asset_response_is_preserved_and_rejected(tmp_path: Path) -> None:
    fetch = FakeFetcher(
        json.dumps(
            [
                {
                    "id": "asset-1",
                    "symbol": "AAPL",
                    "class": "crypto",
                    "status": "active",
                }
            ]
        ).encode()
    )
    client, journal = _client(tmp_path, fetch)

    with pytest.raises(ScannerDataError, match="requested filters"):
        client.collect_active_equity_assets()

    raw_event = journal.read_event(1)
    rejected = journal.read_event(2)
    assert raw_event is not None and raw_event[0].feed == "asset_master"
    assert rejected is not None and rejected[0].feed == "asset_master"
    assert json.loads(rejected[1])["source_response_sequence"] == 1
    journal.close()


def test_explicit_symbol_validation_and_unrequested_response_fail_closed(
    tmp_path: Path,
) -> None:
    fetch = FakeFetcher(b'{"OTHER": {}}')
    client, journal = _client(tmp_path, fetch)
    with pytest.raises(ScannerDataError, match="unique"):
        client.collect_iex_snapshot_batch(("AAPL", "AAPL"))
    assert fetch.requests == []
    assert journal.read_event(1) is None

    with pytest.raises(ScannerDataError, match="unrequested symbol"):
        client.collect_iex_snapshot_batch(("AAPL",))
    assert journal.read_event(1) is not None
    rejected = journal.read_event(2)
    assert rejected is not None
    assert rejected[0].event_kind == "SCANNER_DATA_RESPONSE_REJECTED"
    journal.close()


def test_request_failure_is_audited_without_exception_text_or_retry(
    tmp_path: Path,
) -> None:
    class FailedFetcher:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(
            self,
            url: str,
            headers: Mapping[str, str],
            timeout: float,
            max_bytes: int,
        ) -> bytes:
            self.calls += 1
            raise TimeoutError("do not persist this detail")

    fetch = FailedFetcher()
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    client = AlpacaScannerData(
        _credentials(), journal, timeout_seconds=1, fetch=fetch, clock=lambda: NOW
    )

    with pytest.raises(ScannerDataError, match="request failed"):
        client.collect_iex_snapshot_batch(("AAPL",))

    assert fetch.calls == 1
    event = journal.read_event(1)
    assert event is not None and event[0].event_kind == "SCANNER_DATA_REQUEST_FAILED"
    payload = json.loads(event[1])
    assert payload["error_type"] == "TimeoutError"
    assert "do not persist" not in event[1].decode()
    journal.close()


def test_http_transport_rejects_live_or_arbitrary_hosts_before_network() -> None:
    with pytest.raises(HttpTransportError, match="read-only hosts"):
        bounded_alpaca_get(
            "https://api.alpaca.markets/v2/orders",
            {},
            timeout_seconds=1,
            max_bytes=1024,
        )
