import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.replay import ReplayMode, replay_at
from alpaca_ross_bot.scanner_data import AlpacaScannerData, ScannerDataError
from alpaca_ross_bot.scanner_rankings import (
    SCREENER_FEED,
    RankingObservationError,
    journal_sip_ranking_observation,
)

NOW = datetime(2026, 10, 14, 16, tzinfo=UTC)


def _active() -> dict[str, Any]:
    return {
        "last_updated": "2026-10-14T15:59:59.123456789Z",
        "most_actives": [
            {"symbol": "BBB", "volume": 1200.5, "trade_count": 90},
            {"symbol": "AAA", "volume": 1000, "trade_count": 80},
        ],
    }


def _movers() -> dict[str, Any]:
    return {
        "last_updated": "2026-10-14T15:59:59Z",
        "market_type": "stocks",
        "gainers": [
            {"symbol": "AAA", "percent_change": 10.25, "change": 1.025, "price": 11.025}
        ],
        "losers": [{"symbol": "BBB", "percent_change": -20, "change": -2, "price": 8}],
    }


class Fetcher:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.urls: list[str] = []

    def __call__(
        self, url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        self.urls.append(url)
        return self.raw


def _client(tmp_path: Path, raw: bytes) -> tuple[AlpacaScannerData, Journal, Fetcher]:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    fetch = Fetcher(raw)
    client = AlpacaScannerData(
        PaperCredentials(SecretValue("key"), SecretValue("secret")),
        journal,
        timeout_seconds=1,
        fetch=fetch,
        clock=lambda: NOW,
    )
    return client, journal, fetch


def test_active_list_raw_provenance_order_and_limited_scope(tmp_path: Path) -> None:
    raw = json.dumps(_active()).encode()
    client, journal, fetch = _client(tmp_path, raw)
    result = client.collect_sip_rankings("most_actives", top=2, by="volume")
    assert result.sequence == 2 and result.symbol_count == 2
    assert parse_qs(urlsplit(fetch.urls[0]).query) == {"top": ["2"], "by": ["volume"]}
    parent = journal.read_event(1)
    child = journal.read_event(2)
    assert parent is not None and child is not None
    metadata = json.loads(parent[1])
    assert base64.b64decode(metadata["raw_response_base64"]) == raw
    payload = json.loads(child[1])
    assert payload["source_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert payload["source_response_sequence"] == 1
    assert [row["symbol"] for row in payload["rows"]] == ["BBB", "AAA"]
    assert [row["provider_rank"] for row in payload["rows"]] == [1, 2]
    assert payload["rows"][0]["volume"] == "1200.5"
    assert payload["provider_last_updated_raw"].endswith("123456789Z")
    assert child[0].feed == SCREENER_FEED
    assert payload["full_market_coverage"] is False
    assert payload["scope"] == "PROVIDER_TOP_LIST_ONLY"
    assert payload["premarket_gap_rank"] == "UNAVAILABLE"
    assert payload["strategy_eligibility"] == "BLOCKED_RESEARCH"
    assert payload["iex_enrichment_coverage_label"] == "NOT_SIP_EQUIVALENT"
    journal.close()


def test_movers_preserve_groups_values_and_do_not_supply_premarket_ranks(
    tmp_path: Path,
) -> None:
    client, journal, fetch = _client(tmp_path, json.dumps(_movers()).encode())
    result = client.collect_sip_rankings("movers", top=1)
    assert result.symbol_count == 2
    assert fetch.urls == [
        "https://data.alpaca.markets/v1beta1/screener/stocks/movers?top=1"
    ]
    child = journal.read_event(2)
    assert child is not None
    payload = json.loads(child[1])
    assert [row["list"] for row in payload["rows"]] == ["gainers", "losers"]
    assert [row["provider_rank"] for row in payload["rows"]] == [1, 1]
    assert payload["rows"][1]["percent_change"] == "-20"
    assert payload["session_status"] == "UNVERIFIED"
    assert payload["movers_reset_semantics"] == (
        "PREVIOUS_MARKET_DAY_BEFORE_MARKET_OPEN_RESET"
    )
    journal.close()


def test_invalid_parameters_do_not_contact_provider(tmp_path: Path) -> None:
    client, journal, fetch = _client(tmp_path, b"{}")
    for top in (0, 101, True):
        with pytest.raises(RankingObservationError, match="bounds"):
            client.collect_sip_rankings("most_actives", top=top, by="volume")
    with pytest.raises(RankingObservationError, match="bounds"):
        client.collect_sip_rankings("movers", top=51)
    with pytest.raises(RankingObservationError, match="requires"):
        client.collect_sip_rankings("most_actives", top=1)
    with pytest.raises(RankingObservationError, match="does not accept"):
        client.collect_sip_rankings("movers", top=1, by="trades")
    assert fetch.urls == [] and journal.read_event(1) is None
    journal.close()


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'{"last_updated":"2026-10-14T15:59:59Z","most_actives":[],"most_actives":[]}',
        json.dumps({**_active(), "last_updated": "2026-10-14"}).encode(),
        json.dumps({**_active(), "last_updated": "2026-13-40T15:59:59Z"}).encode(),
        json.dumps(
            {
                **_active(),
                "most_actives": [{"symbol": "AAA", "volume": True, "trade_count": 1}],
            }
        ).encode(),
        json.dumps(
            {
                **_active(),
                "most_actives": [{"symbol": "AAA", "volume": -1, "trade_count": 1}],
            }
        ).encode(),
        json.dumps(
            {**_active(), "most_actives": [_active()["most_actives"][0]] * 2}
        ).encode(),
        json.dumps(
            {
                **_active(),
                "most_actives": [
                    {"symbol": "AAA", "volume": float("nan"), "trade_count": 1}
                ],
            }
        ).encode(),
    ],
)
def test_invalid_payload_retains_raw_and_rejection_only(
    tmp_path: Path, raw: bytes
) -> None:
    client, journal, _ = _client(tmp_path, raw)
    with pytest.raises(RankingObservationError):
        client.collect_sip_rankings("most_actives", top=2, by="trades")
    parent, rejected = journal.read_event(1), journal.read_event(2)
    assert parent is not None and rejected is not None
    assert base64.b64decode(json.loads(parent[1])["raw_response_base64"]) == raw
    assert rejected[0].event_kind == "SCANNER_DATA_RESPONSE_REJECTED"
    assert journal.read_event(3) is None
    journal.close()


def test_cardinality_and_market_type_fail_closed(tmp_path: Path) -> None:
    client, journal, fetch = _client(tmp_path, json.dumps(_active()).encode())
    with pytest.raises(RankingObservationError, match="exceeds"):
        client.collect_sip_rankings("most_actives", top=1, by="volume")
    fetch.raw = json.dumps({**_movers(), "market_type": "crypto"}).encode()
    with pytest.raises(RankingObservationError, match="market type"):
        client.collect_sip_rankings("movers", top=1)
    journal.close()


def test_projection_idempotency_and_delayed_replay_availability(
    tmp_path: Path,
) -> None:
    client, journal, _ = _client(tmp_path, json.dumps(_active()).encode())
    clocks = iter((NOW, NOW, NOW + timedelta(seconds=2)))
    client._clock = lambda: next(clocks)
    result = client.collect_sip_rankings("most_actives", top=2, by="volume")
    duplicate = journal_sip_ranking_observation(
        journal, source_response_sequence=1, processed_at=NOW + timedelta(seconds=3)
    )
    assert result.status == "RECORDED" and duplicate.status == "DUPLICATE"
    assert duplicate.sequence is None and journal.read_event(3) is None
    child = journal.read_event(2)
    assert child is not None
    parent = journal.read_event(1)
    assert parent is not None
    events = [parent[0], child[0]]
    before = replay_at(events, ReplayMode.PROSPECTIVE, NOW + timedelta(seconds=1))
    after = replay_at(events, ReplayMode.PROSPECTIVE, NOW + timedelta(seconds=2))
    assert [item.event.event_kind for item in before.observations] == [
        "SIP_SCREENER_RAW_RESPONSE"
    ]
    assert [item.event.event_kind for item in after.observations] == [
        "SIP_SCREENER_RAW_RESPONSE",
        "SIP_RANKING_OBSERVATION",
    ]
    with pytest.raises(RankingObservationError, match="processing"):
        journal_sip_ranking_observation(
            journal, source_response_sequence=1, processed_at=NOW - timedelta(seconds=1)
        )
    journal.close()


@pytest.mark.parametrize(
    "updated", ["2026-10-14T16:00:00.000000001Z", "2026-10-14T16:00:01Z"]
)
def test_future_computation_cannot_enter_derived_replay(
    tmp_path: Path, updated: str
) -> None:
    client, journal, _ = _client(
        tmp_path, json.dumps({**_active(), "last_updated": updated}).encode()
    )
    clocks = iter((NOW, NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2)))
    client._clock = lambda: next(clocks)
    with pytest.raises(RankingObservationError, match="future ranking"):
        client.collect_sip_rankings("most_actives", top=2, by="volume")
    parent, rejected = journal.read_event(1), journal.read_event(2)
    assert parent is not None and rejected is not None
    replay = replay_at(
        [parent[0], rejected[0]], ReplayMode.PROSPECTIVE, NOW + timedelta(seconds=3)
    )
    assert [item.event.event_kind for item in replay.observations] == [
        "SIP_SCREENER_RAW_RESPONSE",
        "SCANNER_DATA_RESPONSE_REJECTED",
    ]
    assert journal.read_event(3) is None
    journal.close()


def test_forged_raw_digest_or_request_cannot_be_projected(tmp_path: Path) -> None:
    client, journal, _ = _client(tmp_path, json.dumps(_active()).encode())
    client.collect_sip_rankings("most_actives", top=2, by="volume")
    original = journal.read_event(1)
    assert original is not None
    for field, value in (("response_sha256", "bad"), ("request_url", "https://other/")):
        payload = json.loads(original[1])
        payload[field] = value
        event = journal.append_next(
            source_id="alpaca_scanner_data",
            feed=SCREENER_FEED,
            event_kind="SIP_SCREENER_RAW_RESPONSE",
            payload=json.dumps(payload).encode(),
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        with pytest.raises(RankingObservationError):
            journal_sip_ranking_observation(
                journal, source_response_sequence=event.sequence, processed_at=NOW
            )
    journal.close()


def test_request_failure_has_one_sanitized_event_without_retry(tmp_path: Path) -> None:
    client, journal, fetch = _client(tmp_path, b"{}")

    def fail(url: str, headers: Mapping[str, str], timeout: float, size: int) -> bytes:
        fetch.urls.append(url)
        raise TimeoutError("sensitive detail")

    client._fetch = fail
    with pytest.raises(ScannerDataError, match="request failed"):
        client.collect_sip_rankings("movers", top=1)
    event = journal.read_event(1)
    assert event is not None and event[0].feed == SCREENER_FEED
    assert len(fetch.urls) == 1 and "sensitive detail" not in event[1].decode()
    journal.close()
