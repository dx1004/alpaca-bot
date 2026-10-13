from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from alpaca_ross_bot.events import EventEnvelope, EventError, validate_journal_order

BASE = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


def event(**changes: object) -> EventEnvelope:
    fields = {
        "sequence": 1,
        "source_id": "ALPACA_IEX",
        "feed": "iex",
        "event_kind": "trade",
        "payload_ref": "sha256:example",
        "source_event_at": BASE,
        "provider_received_at": BASE + timedelta(milliseconds=1),
        "system_first_seen_at": BASE + timedelta(milliseconds=2),
        "processed_at": BASE + timedelta(milliseconds=3),
        "available_at": BASE + timedelta(milliseconds=4),
    }
    fields.update(changes)
    return EventEnvelope(**fields)  # type: ignore[arg-type]


def test_decision_cannot_see_event_before_availability() -> None:
    observed = event()
    assert not observed.visible_at(BASE + timedelta(milliseconds=3))
    assert observed.visible_at(BASE + timedelta(milliseconds=4))


@pytest.mark.parametrize(
    "changes",
    [
        {"system_first_seen_at": BASE + timedelta(milliseconds=5)},
        {"processed_at": BASE + timedelta(milliseconds=5)},
        {"available_at": BASE + timedelta(milliseconds=1)},
        {"sequence": 0},
        {"sequence": True},
        {"feed": " "},
        {"source_event_at": datetime(2026, 10, 5, 13, 0)},
        {"available_at": BASE.astimezone(timezone(timedelta(hours=-7)))},
    ],
)
def test_invalid_local_envelope_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(EventError):
        event(**changes)


def test_external_clock_conflict_is_retained_and_flagged() -> None:
    observed = event(provider_received_at=BASE + timedelta(seconds=1))
    assert observed.external_clock_conflict
    assert observed.provider_received_at == BASE + timedelta(seconds=1)


def test_journal_sequence_does_not_sort_by_source_event_time() -> None:
    first = event(source_event_at=BASE + timedelta(seconds=1))
    late = replace(first, sequence=2, source_event_at=BASE - timedelta(seconds=1))
    validate_journal_order([first, late])
    with pytest.raises(EventError, match="contiguous"):
        validate_journal_order([late, first])
    with pytest.raises(EventError, match="contiguous"):
        validate_journal_order([first, replace(late, sequence=3)])
