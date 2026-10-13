import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca_ross_bot.feed_health import FeedHealth, FeedKind
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.news_evidence import capture_news_frame
from alpaca_ross_bot.news_market_context import (
    NewsMarketContextError,
    capture_news_market_context,
)
from alpaca_ross_bot.scanner_observation import (
    SymbolObservation,
    capture_scanner_observations,
)

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)


def _journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")


def _capture_article(
    journal: Journal,
    *,
    symbols: list[str] | None = None,
    first_seen_at: datetime = NOW,
    processed_at: datetime | None = None,
) -> None:
    health = FeedHealth(
        kind=FeedKind.NEWS,
        feed="alpaca_news",
        connection_epoch=1,
        subscribed_epoch=1,
        connected=True,
        gap_unresolved=False,
        last_heartbeat_at=NOW - timedelta(seconds=1),
        last_data_at=None,
    )
    article = {
        "T": "n",
        "id": 71,
        "headline": "ABC reports new contract",
        "summary": "ABC announced a supply agreement.",
        "author": "Reporter",
        "created_at": "2026-10-13T15:59:00Z",
        "updated_at": "2026-10-13T15:59:00Z",
        "content": "Not needed for context linkage",
        "url": "https://example.test/article",
        "symbols": symbols if symbols is not None else ["ABC"],
        "source": "Provider",
    }
    raw = json.dumps([article])
    raw_event = journal.append_next(
        source_id="alpaca_news",
        feed="alpaca_news",
        event_kind="RAW_FRAME",
        payload=raw.encode(),
        system_first_seen_at=first_seen_at,
        processed_at=first_seen_at,
    )
    capture_news_frame(
        journal,
        raw,
        first_seen_at=first_seen_at,
        processed_at=processed_at if processed_at is not None else first_seen_at,
        health=health,
        health_now=first_seen_at,
        max_heartbeat_age_seconds=30,
        raw_frame_sequence=raw_event.sequence,
    )


def _snapshot(
    journal: Journal,
    *,
    symbol: str,
    as_of: datetime,
    processed_at: datetime,
    price: str,
    volume: int,
    first_seen_at: datetime | None = None,
) -> None:
    observation = SymbolObservation(
        symbol=symbol,
        as_of=as_of,
        feed="iex",
        price_source="alpaca_iex_snapshot_latest_trade",
        latest_trade_price=Decimal(price),
        prior_close=Decimal("10"),
        prior_close_as_of=as_of - timedelta(days=1),
        session_volume=volume,
        trailing_daily_volumes=(),
        historical_feed="iex",
        history_source="not_supplied_for_context_link",
        history_available_at=as_of,
        history_adjustment="raw",
    )
    capture_scanner_observations(
        journal,
        (observation,),
        first_seen_at=first_seen_at if first_seen_at is not None else processed_at,
        processed_at=processed_at,
    )


def test_news_market_context_joins_exact_symbols_and_is_idempotent(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    _snapshot(
        journal,
        symbol="ABC",
        as_of=NOW - timedelta(minutes=1),
        processed_at=NOW - timedelta(seconds=30),
        price="10.25",
        volume=500,
    )
    _capture_article(journal)
    _snapshot(
        journal,
        symbol="ABC",
        as_of=NOW + timedelta(minutes=1),
        processed_at=NOW + timedelta(minutes=1),
        price="10.75",
        volume=620,
    )

    report = capture_news_market_context(
        journal,
        article_sequence=3,
        before_sequence=1,
        after_sequence=4,
        processed_at=NOW + timedelta(minutes=2),
    )
    rows = report["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["symbol"] == "ABC"
    assert rows[0]["price_delta"] == "0.50"
    assert rows[0]["session_volume_delta"] == 120
    assert report["coverage_label"] == "NOT_SIP_EQUIVALENT"
    assert report["causality"] == "NOT_ASSESSED_TEMPORAL_ASSOCIATION_ONLY"
    assert report["strategy_eligibility"] == "BLOCKED_RESEARCH"
    event = journal.read_event(5)
    assert event is not None
    assert event[0].event_kind == "NEWS_MARKET_CONTEXT_OBSERVATION"

    repeated = capture_news_market_context(
        journal,
        article_sequence=3,
        before_sequence=1,
        after_sequence=4,
        processed_at=NOW + timedelta(minutes=2),
    )
    assert repeated["status"] == "DUPLICATE"
    assert journal.read_event(6) is None
    journal.close()


def test_context_rejects_pre_news_data_not_yet_available(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _snapshot(
        journal,
        symbol="ABC",
        as_of=NOW - timedelta(minutes=1),
        processed_at=NOW + timedelta(seconds=1),
        first_seen_at=NOW - timedelta(seconds=1),
        price="10.25",
        volume=500,
    )
    _capture_article(journal)
    _snapshot(
        journal,
        symbol="ABC",
        as_of=NOW + timedelta(minutes=1),
        processed_at=NOW + timedelta(minutes=1),
        price="10.75",
        volume=620,
    )

    with pytest.raises(NewsMarketContextError, match="not locally available"):
        capture_news_market_context(
            journal,
            article_sequence=3,
            before_sequence=1,
            after_sequence=4,
            processed_at=NOW + timedelta(minutes=2),
        )
    journal.close()


def test_context_does_not_map_symbols_or_claim_reaction_when_unmatched(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    _snapshot(
        journal,
        symbol="ABC.A",
        as_of=NOW - timedelta(minutes=1),
        processed_at=NOW - timedelta(seconds=30),
        price="10.25",
        volume=500,
    )
    _capture_article(journal, symbols=["ABC"])
    _snapshot(
        journal,
        symbol="ABC.A",
        as_of=NOW + timedelta(minutes=1),
        processed_at=NOW + timedelta(minutes=1),
        price="10.75",
        volume=620,
    )

    report = capture_news_market_context(
        journal,
        article_sequence=3,
        before_sequence=1,
        after_sequence=4,
        processed_at=NOW + timedelta(minutes=2),
    )
    assert report["status"] == "NO_COMMON_PROVIDER_SYMBOL"
    assert report["rows"] == []
    assert report["catalyst_qualification"] == "NOT_ASSESSED"
    journal.close()


def test_context_rejects_reordered_sequences(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _snapshot(
        journal,
        symbol="ABC",
        as_of=NOW - timedelta(minutes=1),
        processed_at=NOW - timedelta(seconds=30),
        price="10.25",
        volume=500,
    )
    _capture_article(journal)
    _snapshot(
        journal,
        symbol="ABC",
        as_of=NOW + timedelta(minutes=1),
        processed_at=NOW + timedelta(minutes=1),
        price="10.75",
        volume=620,
    )

    with pytest.raises(NewsMarketContextError, match="order around"):
        capture_news_market_context(
            journal,
            article_sequence=3,
            before_sequence=4,
            after_sequence=1,
            processed_at=NOW + timedelta(minutes=2),
        )
    journal.close()
