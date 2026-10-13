"""Fail-closed evidence gate for the selected Alpaca data feeds.

This is collection health, not scanner or strategy qualification.  Callers
must supply observed connection, subscription and recovery evidence; an SDK
reconnect or a cached payload cannot manufacture those facts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from alpaca_ross_bot.events import _utc


class FeedKind(StrEnum):
    EQUITIES = "EQUITIES"
    NEWS = "NEWS"


class HealthReason(StrEnum):
    WRONG_FEED = "WRONG_FEED"
    DISCONNECTED = "DISCONNECTED"
    SUBSCRIPTION_UNVERIFIED = "SUBSCRIPTION_UNVERIFIED"
    GAP_UNRESOLVED = "GAP_UNRESOLVED"
    HEARTBEAT_STALE = "HEARTBEAT_STALE"
    DATA_STALE = "DATA_STALE"
    CLOCK_CONFLICT = "CLOCK_CONFLICT"


@dataclass(frozen=True)
class FeedHealth:
    kind: FeedKind
    feed: str
    connection_epoch: int
    subscribed_epoch: int | None
    connected: bool
    gap_unresolved: bool
    last_heartbeat_at: datetime | None
    last_data_at: datetime | None

    def reasons(
        self,
        *,
        now: datetime,
        max_heartbeat_age: timedelta,
        max_data_age: timedelta | None,
    ) -> tuple[HealthReason, ...]:
        """Return explicit vetoes; news may be quiet, equities require data.

        Ages are engineering controls supplied by a separately reviewed
        source configuration.  This method chooses no strategy threshold.
        """
        _utc(now, "now")
        if max_heartbeat_age <= timedelta(0) or (
            max_data_age is not None and max_data_age <= timedelta(0)
        ):
            raise ValueError("health age limits must be positive")
        if self.kind is FeedKind.EQUITIES and max_data_age is None:
            raise ValueError("equity feed requires a data age limit")
        if not isinstance(self.kind, FeedKind):
            raise ValueError("unsupported feed kind")
        if self.connection_epoch < 1:
            raise ValueError("connection epoch must be positive")
        for name in ("last_heartbeat_at", "last_data_at"):
            value = getattr(self, name)
            if value is not None:
                _utc(value, name)

        reasons = []
        expected = "iex" if self.kind is FeedKind.EQUITIES else "alpaca_news"
        if self.feed != expected:
            reasons.append(HealthReason.WRONG_FEED)
        if not self.connected:
            reasons.append(HealthReason.DISCONNECTED)
        if self.subscribed_epoch != self.connection_epoch:
            reasons.append(HealthReason.SUBSCRIPTION_UNVERIFIED)
        if self.gap_unresolved:
            reasons.append(HealthReason.GAP_UNRESOLVED)
        for observed in (self.last_heartbeat_at, self.last_data_at):
            if observed is not None and observed > now:
                reasons.append(HealthReason.CLOCK_CONFLICT)
                break
        if self.last_heartbeat_at is None or (
            now - self.last_heartbeat_at > max_heartbeat_age
        ):
            reasons.append(HealthReason.HEARTBEAT_STALE)
        if max_data_age is not None and (
            self.last_data_at is None or now - self.last_data_at > max_data_age
        ):
            reasons.append(HealthReason.DATA_STALE)
        return tuple(reasons)

    @property
    def coverage_label(self) -> str:
        """The current live equity source never claims SIP coverage."""
        return "NOT_SIP_EQUIVALENT"
