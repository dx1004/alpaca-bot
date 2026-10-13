"""Persist raw Alpaca stream frames with first-seen evidence."""

from datetime import datetime

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.feed_health import FeedKind
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.stream_protocol import ReceivedFrame


def append_received_frame(
    journal: Journal,
    feed: FeedKind,
    frame: ReceivedFrame,
    *,
    processed_at: datetime,
) -> EventEnvelope:
    """Append the original frame atomically; it is not a strategy-eligible event."""
    return journal.append_next(
        source_id="alpaca_market_data",
        feed="iex" if feed is FeedKind.EQUITIES else "alpaca_news",
        event_kind="RAW_FRAME",
        payload=frame.raw.encode("utf-8") if isinstance(frame.raw, str) else frame.raw,
        system_first_seen_at=frame.first_seen_at,
        processed_at=processed_at,
    )
