from pathlib import Path

import pytest

from alpaca_ross_bot.config import ConfigError, PaperConfig
from alpaca_ross_bot.feed_clients import (
    make_data_streams,
    make_observable_stream,
    make_rest_recovery,
)
from alpaca_ross_bot.feed_health import FeedKind
from alpaca_ross_bot.journal import Journal

PAPER_VALUES: dict[str, object] = {
    "mode": "paper",
    "trading_base_url": "https://paper-api.alpaca.markets",
    "market_data_base_url": "https://data.alpaca.markets",
    "api_key_id_env": "ALPACA_PAPER_KEY_ID",
    "api_secret_key_env": "ALPACA_PAPER_SECRET_KEY",
}


def test_sdk_streams_are_iex_and_news_without_connecting() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    streams = make_data_streams(
        config,
        {"ALPACA_PAPER_KEY_ID": "test-key", "ALPACA_PAPER_SECRET_KEY": "test-secret"},
    )
    assert streams.stock._endpoint.endswith("/v2/iex")
    assert streams.news._endpoint.endswith("/v1beta1/news")
    assert streams.stock._raw_data is True
    assert streams.news._raw_data is True
    assert streams.coverage_label == "NOT_SIP_EQUIVALENT"


def test_stream_construction_requires_paper_credentials() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    with pytest.raises(ConfigError):
        make_data_streams(config, {})


def test_observable_stream_constructor_does_not_connect(tmp_path: Path) -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    stream = make_observable_stream(
        config,
        {"ALPACA_PAPER_KEY_ID": "test-key", "ALPACA_PAPER_SECRET_KEY": "test-secret"},
        FeedKind.EQUITIES,
        timeout_seconds=2,
        heartbeat_interval_seconds=10,
        heartbeat_timeout_seconds=30,
        journal=journal,
    )
    assert stream.identity.endpoint == "wss://stream.data.alpaca.markets/v2/iex"
    assert stream.identity.coverage_label == "NOT_SIP_EQUIVALENT"
    assert not stream.subscribed
    journal.close()


def test_observable_news_stream_has_separate_identity(tmp_path: Path) -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    stream = make_observable_stream(
        config,
        {"ALPACA_PAPER_KEY_ID": "test-key", "ALPACA_PAPER_SECRET_KEY": "test-secret"},
        FeedKind.NEWS,
        timeout_seconds=2,
        heartbeat_interval_seconds=10,
        heartbeat_timeout_seconds=30,
        journal=journal,
    )
    assert stream.identity.endpoint.endswith("/v1beta1/news")
    assert stream.identity.coverage_label == "SEPARATE_ALPACA_NEWS_STREAM"
    journal.close()


def test_rest_recovery_construction_is_paper_credential_gated_and_read_only(
    tmp_path: Path,
) -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    recovery = make_rest_recovery(
        config,
        {"ALPACA_PAPER_KEY_ID": "test-key", "ALPACA_PAPER_SECRET_KEY": "test-secret"},
        timeout_seconds=2,
        max_pages=3,
        max_bytes=1024,
        journal=journal,
    )
    assert recovery is not None
    with pytest.raises(ConfigError):
        make_rest_recovery(
            config,
            {},
            timeout_seconds=2,
            max_pages=3,
            max_bytes=1024,
            journal=journal,
        )
    journal.close()
