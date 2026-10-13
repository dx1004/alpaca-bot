"""Availability-aware views over recorded and downloaded events."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from alpaca_ross_bot.events import EventEnvelope, _utc, validate_journal_order


class ReplayError(ValueError):
    """A replay cannot support the requested timing claim."""


class ReplayMode(StrEnum):
    PROSPECTIVE = "PROSPECTIVE"
    HISTORICAL_RESEARCH = "HISTORICAL_RESEARCH"


class TimingBasis(StrEnum):
    RECORDED_AVAILABILITY = "RECORDED_AVAILABILITY"
    ASSUMED_SOURCE_TIME = "ASSUMED_SOURCE_TIME"


@dataclass(frozen=True)
class ReplayObservation:
    event: EventEnvelope
    timing_basis: TimingBasis


@dataclass(frozen=True)
class ReplayFrame:
    mode: ReplayMode
    decision_at: datetime
    observations: tuple[ReplayObservation, ...]

    @property
    def research_only(self) -> bool:
        """Historical downloads cannot establish decision-time fidelity."""
        return self.mode is ReplayMode.HISTORICAL_RESEARCH

    @property
    def has_assumed_timing(self) -> bool:
        return any(
            item.timing_basis is TimingBasis.ASSUMED_SOURCE_TIME
            for item in self.observations
        )

    def require_recorded_timing(self) -> None:
        """Gate local timing proof; feed and strategy checks remain separate."""
        if self.research_only or self.has_assumed_timing:
            raise ReplayError("historical research cannot prove recorded timing")


def replay_at(
    events: Iterable[EventEnvelope], mode: ReplayMode, decision_at: datetime
) -> ReplayFrame:
    """Return only versions visible under the selected timing contract.

    Historical source-time assumptions are restricted to raw trade/quote events.
    Downloaded bars, news, revisions, mappings and derived features require
    recorded availability; historical mode is never a fidelity acceptance path.
    """
    _utc(decision_at, "decision_at")
    if not isinstance(mode, ReplayMode):
        raise ValueError("unsupported replay mode")
    recorded = list(events)
    validate_journal_order(recorded)
    observations = []
    for event in recorded:
        if event.external_clock_conflict:
            continue
        if event.source_event_at is not None and event.source_event_at > decision_at:
            continue
        if event.visible_at(decision_at):
            observations.append(
                ReplayObservation(event, TimingBasis.RECORDED_AVAILABILITY)
            )
        elif (
            mode is ReplayMode.HISTORICAL_RESEARCH
            and event.event_kind in {"trade", "quote"}
            and event.source_event_at is not None
            and event.source_event_at <= decision_at
        ):
            observations.append(
                ReplayObservation(event, TimingBasis.ASSUMED_SOURCE_TIME)
            )
    return ReplayFrame(mode, decision_at, tuple(observations))
