"""Persist raw Alpaca stream frames with first-seen evidence."""

from datetime import datetime

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.feed_health import FeedKind
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.stream_protocol import ReceivedFrame


def append_received_frame(
    journal: Journal,
    feed: FeedKind,
    frame: ReceivedFrame,
    *,
    processed_at: datetime,
) -> EventEnvelope:
    """Append the original frame atomically; it is not a strategy-eligible event."""
    if frame.feed is not None and frame.feed is not feed:
        raise JournalError("received frame feed does not match the requested feed")
    payload = frame.raw.encode("utf-8") if isinstance(frame.raw, str) else frame.raw
    if frame.journal_sequence is not None:
        existing = journal.read_event(frame.journal_sequence)
        if existing is None:
            raise JournalError("received frame journal sequence is missing")
        envelope, recorded_payload = existing
        expected_feed = "iex" if feed is FeedKind.EQUITIES else "alpaca_news"
        if (
            envelope.event_kind != "RAW_FRAME"
            or envelope.feed != expected_feed
            or recorded_payload != payload
        ):
            raise JournalError("received frame does not match its journal event")
        return envelope
    return journal.append_next(
        source_id="alpaca_market_data",
        feed="iex" if feed is FeedKind.EQUITIES else "alpaca_news",
        event_kind="RAW_FRAME",
        payload=payload,
        system_first_seen_at=frame.first_seen_at,
        processed_at=processed_at,
    )
