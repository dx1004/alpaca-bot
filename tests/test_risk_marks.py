import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.risk_marks import BidMark, inspect_bid_mark

NOW = datetime(2026, 10, 16, 14, tzinfo=UTC)
QUOTE = {
    "T": "q",
    "S": "AAPL",
    "bp": "100.001",
    "ap": "101",
    "bs": 1,
    "as": 1,
    "bx": "V",
    "ax": "V",
    "z": "C",
    "c": ["R"],
    "t": "2026-10-16T14:00:00Z",
}
URL = "https://data.alpaca.markets/v2/stocks/meta/conditions/quote?tape=C"


def put(
    journal: Journal,
    payload: object,
    *,
    metadata: bool = False,
    at: datetime = NOW,
    feed: str = "iex",
    source_event_at: datetime | None = None,
) -> int:
    return journal.append_next(
        source_id="ALPACA_MARKET_METADATA" if metadata else "alpaca_market_data",
        feed=feed,
        event_kind="CONDITION_CODE_METADATA" if metadata else "RAW_FRAME",
        payload=json.dumps(payload).encode(),
        system_first_seen_at=at,
        processed_at=at,
        source_event_at=source_event_at,
    ).sequence


def meta(journal: Journal, **changes: object) -> int:
    return put(
        journal,
        {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"}), **changes},
        metadata=True,
    )


def inspect(journal: Journal, sequence: int, metadata: int, index: int = 0) -> BidMark:
    return inspect_bid_mark(
        journal,
        symbol="AAPL",
        sequence=sequence,
        record_index=index,
        metadata_sequence=metadata,
        decision_at=NOW,
        max_metadata_age=timedelta(days=1),
    )


def test_exact_mark_never_grants_runtime(tmp_path: Path) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence, metadata = put(journal, [QUOTE]), meta(journal)
        with localcontext() as ctx:
            ctx.prec = 1
            mark = inspect(journal, sequence, metadata)
            assert mark.long_value(3) == Fraction(300003, 1000)
        assert mark.observed_valid and not mark.runtime_authorized
        assert mark.source_refs == (sequence, metadata)
        assert "100.001" not in repr(mark)
        for qty in (0, -1, True, Fraction(1, 2)):
            with pytest.raises(ValueError):
                mark.long_value(qty)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bp", "NaN"),
        ("bp", "102"),
        ("bs", 0),
        ("T", "t"),
        ("T", "c"),
        ("S", "SPY"),
        ("c", ["R", "R"]),
        ("c", ["X"]),
        ("z", "D"),
        ("t", "2026-10-16T13:59:58.999999999Z"),
        ("t", "2026-10-16T14:00:00.000000001Z"),
    ],
)
def test_invalid_quotes(tmp_path: Path, field: str, value: object) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence = put(journal, [{**QUOTE, field: value}])
        with pytest.raises(ValueError):
            inspect(journal, sequence, meta(journal))


@pytest.mark.parametrize(
    "case",
    [
        "same_frame",
        "new_frame",
        "foreign",
        "metadata_future",
        "metadata_stale",
        "mapping",
        "cross_tape",
        "new_mapping",
    ],
)
def test_source_and_latest_binding(tmp_path: Path, case: str) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence = put(
            journal,
            [QUOTE, QUOTE] if case == "same_frame" else [QUOTE],
            feed="sip" if case == "foreign" else "iex",
        )
        metadata = meta(journal)
        if case == "new_frame":
            put(journal, [QUOTE])
        if case == "metadata_future":
            metadata = put(
                journal,
                {"url": URL, "body": "{}"},
                metadata=True,
                at=NOW + timedelta(microseconds=1),
            )
        if case == "metadata_stale":
            metadata = put(
                journal,
                {"url": URL, "body": "{}"},
                metadata=True,
                at=NOW - timedelta(days=2),
            )
        if case == "mapping":
            metadata = meta(journal, body='{"R":"changed"}')
        if case == "cross_tape":
            metadata = meta(journal, url=URL[:-1] + "A")
        if case == "new_mapping":
            meta(journal, body='{"R":"changed"}')
        with pytest.raises(ValueError):
            inspect(journal, sequence, metadata)


def test_future_and_other_symbols_do_not_supersede(tmp_path: Path) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence, metadata = put(journal, [QUOTE]), meta(journal)
        put(journal, [{**QUOTE, "S": "SPY"}])
        put(journal, [QUOTE], at=NOW + timedelta(seconds=1))
        assert inspect(journal, sequence, metadata).observed_valid


@pytest.mark.parametrize(
    ("stamp", "valid"),
    [
        ("2026-10-16T13:59:59Z", True),
        ("2026-10-16T13:59:58.999999999Z", False),
    ],
)
def test_exact_one_second_event_bound(tmp_path: Path, stamp: str, valid: bool) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence = put(journal, [{**QUOTE, "t": stamp}])
        metadata = meta(journal)
        if valid:
            assert inspect(journal, sequence, metadata).observed_valid
        else:
            with pytest.raises(ValueError, match="stale"):
                inspect(journal, sequence, metadata)


def test_latest_same_frame_quote_can_be_selected(tmp_path: Path) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence = put(journal, [QUOTE, {**QUOTE, "bp": "100.002"}])
        assert inspect(journal, sequence, meta(journal), 1).bid == Fraction(50001, 500)


def test_duplicate_metadata_keys_rejected(tmp_path: Path) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence = put(journal, [QUOTE])
        metadata = meta(journal, body='{"R":"bad","R":"Regular Market Maker Open"}')
        with pytest.raises(ValueError, match="duplicate"):
            inspect(journal, sequence, metadata)


def test_foreign_metadata_source_rejected(tmp_path: Path) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence = put(journal, [QUOTE])
        metadata = put(journal, {"url": URL, "body": "{}"}, feed="sip")
        with pytest.raises(ValueError, match="metadata identity"):
            inspect(journal, sequence, metadata)


@pytest.mark.parametrize("target", ["quote", "metadata"])
def test_changed_persisted_payload_rejected(tmp_path: Path, target: str) -> None:
    path = tmp_path / "j.sqlite3"
    with closing(Journal.open_private(path, tmp_path)) as journal:
        sequence, metadata = put(journal, [QUOTE]), meta(journal)
        with closing(sqlite3.connect(path)) as connection:
            # Simulate out-of-band corruption beyond the append-only SQL guard.
            connection.execute("DROP TRIGGER events_no_update")
            connection.execute(
                "UPDATE events SET payload=? WHERE sequence=?",
                (b"[]", sequence if target == "quote" else metadata),
            )
            connection.commit()
        with pytest.raises(JournalError, match="integrity"):
            inspect(journal, sequence, metadata)


@pytest.mark.parametrize("target", ["quote", "metadata"])
def test_external_clock_conflict_rejected(tmp_path: Path, target: str) -> None:
    with closing(Journal.open_private(tmp_path / "j.sqlite3", tmp_path)) as journal:
        sequence = put(
            journal,
            [QUOTE],
            source_event_at=NOW + timedelta(microseconds=1)
            if target == "quote"
            else None,
        )
        metadata = put(
            journal,
            {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
            metadata=True,
            source_event_at=NOW + timedelta(microseconds=1)
            if target == "metadata"
            else None,
        )
        with pytest.raises(ValueError, match="clock|metadata identity"):
            inspect(journal, sequence, metadata)
