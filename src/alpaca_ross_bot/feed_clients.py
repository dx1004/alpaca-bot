"""Construct Alpaca data clients for the configured paper data scope.

Constructors perform no network I/O. SDK streams remain available for the
read-only probe; strategy-facing transport requires observable protocol state.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from alpaca.data.enums import DataFeed
from alpaca.data.live import NewsDataStream, StockDataStream

from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.feed_health import FeedKind
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.rest_recovery import AlpacaRestRecovery, PageFetcher
from alpaca_ross_bot.stream_protocol import AlpacaJsonStream, Connector


@dataclass(frozen=True)
class DataStreams:
    stock: StockDataStream
    news: NewsDataStream
    coverage_label: str = "NOT_SIP_EQUIVALENT"


def make_data_streams(
    config: PaperConfig, environment: Mapping[str, str]
) -> DataStreams:
    """Construct SDK IEX/news streams for probe comparison; do not imply health."""
    credentials = config.resolve_credentials(environment)
    key_id = credentials.key_id.reveal()
    secret_key = credentials.secret_key.reveal()
    return DataStreams(
        stock=StockDataStream(key_id, secret_key, feed=DataFeed.IEX, raw_data=True),
        news=NewsDataStream(key_id, secret_key, raw_data=True),
    )


def make_observable_stream(
    config: PaperConfig,
    environment: Mapping[str, str],
    feed: FeedKind,
    *,
    timeout_seconds: float,
    heartbeat_interval_seconds: float,
    heartbeat_timeout_seconds: float,
    journal: Journal,
    connector: Connector | None = None,
) -> AlpacaJsonStream:
    """Create a control-frame-visible stream without opening a connection."""
    credentials = config.resolve_credentials(environment)
    if connector is None:
        return AlpacaJsonStream(
            feed,
            credentials,
            timeout_seconds=timeout_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            journal=journal,
        )
    return AlpacaJsonStream(
        feed,
        credentials,
        timeout_seconds=timeout_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        journal=journal,
        connector=connector,
    )


def make_rest_recovery(
    config: PaperConfig,
    environment: Mapping[str, str],
    *,
    timeout_seconds: float,
    max_pages: int,
    max_bytes: int,
    journal: Journal,
    fetch_page: PageFetcher | None = None,
) -> AlpacaRestRecovery:
    """Construct bounded data recovery with the configured paper credentials."""
    credentials = config.resolve_credentials(environment)
    if fetch_page is None:
        return AlpacaRestRecovery(
            credentials,
            timeout_seconds=timeout_seconds,
            max_pages=max_pages,
            max_bytes=max_bytes,
            journal=journal,
        )
    return AlpacaRestRecovery(
        credentials,
        timeout_seconds=timeout_seconds,
        max_pages=max_pages,
        max_bytes=max_bytes,
        journal=journal,
        fetch_page=fetch_page,
    )
