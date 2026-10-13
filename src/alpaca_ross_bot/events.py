"""Point-in-time event envelope for collection and replay."""

from dataclasses import dataclass
from datetime import datetime


class EventError(ValueError):
    """An event cannot be ordered or made available as declared."""


def _utc(value: datetime, name: str) -> None:
    offset = value.utcoffset()
    if offset is None:
        raise EventError(f"{name} must be timezone-aware UTC")
    if offset.total_seconds() != 0:
        raise EventError(f"{name} must be UTC")


@dataclass(frozen=True)
class EventEnvelope:
    """One immutable observation; external clocks are recorded, not trusted.

    ``available_at`` is the earliest time this version may enter a replayed
    decision. It is not proof that the source/feed is strategy-eligible.
    """

    sequence: int
    source_id: str
    feed: str
    event_kind: str
    payload_ref: str
    source_event_at: datetime | None
    provider_received_at: datetime | None
    system_first_seen_at: datetime
    processed_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise EventError("sequence must be a positive integer")
        for name in ("source_id", "feed", "event_kind", "payload_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise EventError(f"{name} must be nonempty text")
        for name in (
            "source_event_at",
            "provider_received_at",
            "system_first_seen_at",
            "processed_at",
            "available_at",
        ):
            value = getattr(self, name)
            if value is not None:
                _utc(value, name)
        if not (self.system_first_seen_at <= self.processed_at <= self.available_at):
            raise EventError("local first-seen, processing and availability order")

    def visible_at(self, decision_at: datetime) -> bool:
        """Whether this version existed locally by the decision timestamp."""
        _utc(decision_at, "decision_at")
        return self.available_at <= decision_at

    @property
    def external_clock_conflict(self) -> bool:
        """Flag impossible-looking external order without deleting evidence."""
        if (
            self.source_event_at is not None
            and self.source_event_at > self.available_at
        ):
            return True
        return (
            self.provider_received_at is not None
            and self.provider_received_at > self.system_first_seen_at
        )


def validate_journal_order(events: list[EventEnvelope]) -> None:
    """Require an ingestion-ordered contiguous slice without sorting events."""
    for previous, current in zip(events, events[1:], strict=False):
        if current.sequence != previous.sequence + 1:
            raise EventError("journal sequence must be contiguous and ordered")
