from datetime import UTC, datetime, timedelta
from pathlib import Path

from alpaca_ross_bot.feed_capture import append_received_frame
from alpaca_ross_bot.feed_health import FeedKind
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.stream_protocol import ReceivedFrame

NOW = datetime(2026, 10, 9, 13, 0, tzinfo=UTC)


def test_raw_frames_append_in_journal_order_with_first_seen_times(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    raw_iex = '[{"T":"t","S":"AAPL","p":1}]'
    first = append_received_frame(
        journal,
        FeedKind.EQUITIES,
        ReceivedFrame(raw_iex, NOW),
        processed_at=NOW + timedelta(milliseconds=2),
    )
    raw_news = '[{"T":"n","symbol":"AAPL"}]'
    second = append_received_frame(
        journal,
        FeedKind.NEWS,
        ReceivedFrame(raw_news.encode(), NOW + timedelta(milliseconds=3)),
        processed_at=NOW + timedelta(milliseconds=4),
    )

    assert first.sequence == 1
    assert first.feed == "iex"
    assert first.event_kind == "RAW_FRAME"
    assert first.system_first_seen_at == NOW
    assert first.available_at == NOW + timedelta(milliseconds=2)
    assert journal.read_event(1) == (first, raw_iex.encode())
    assert second.sequence == 2
    assert second.feed == "alpaca_news"
    assert journal.read_event(2) == (second, raw_news.encode())
    journal.close()


def test_durably_received_frame_is_returned_without_duplicate_append(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    raw = '[{"T":"t","S":"AAPL","p":1}]'
    existing = journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="RAW_FRAME",
        payload=raw.encode(),
        system_first_seen_at=NOW,
        processed_at=NOW + timedelta(milliseconds=1),
    )
    received = ReceivedFrame(
        raw,
        NOW,
        feed=FeedKind.EQUITIES,
        journal_sequence=existing.sequence,
    )

    returned = append_received_frame(
        journal,
        FeedKind.EQUITIES,
        received,
        processed_at=NOW + timedelta(milliseconds=2),
    )

    assert returned == existing
    assert journal.read_event(2) is None
    journal.close()
