from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.replay import ReplayError, ReplayMode, TimingBasis, replay_at

T0 = datetime(2021, 7, 13, 13, 0, tzinfo=UTC)
DOWNLOAD = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


def downloaded(sequence: int, kind: str, source_at: datetime) -> EventEnvelope:
    return EventEnvelope(
        sequence=sequence,
        source_id="HISTORICAL_DOWNLOAD",
        feed="sip_historical_unverified",
        event_kind=kind,
        payload_ref=f"sha256:sample-{sequence}",
        source_event_at=source_at,
        provider_received_at=None,
        system_first_seen_at=DOWNLOAD,
        processed_at=DOWNLOAD,
        available_at=DOWNLOAD,
    )


def test_prospective_hides_future_news_versions_and_mappings() -> None:
    raw = downloaded(1, "news", T0)
    first = replace(
        raw,
        system_first_seen_at=T0 + timedelta(seconds=1),
        processed_at=T0 + timedelta(seconds=2),
        available_at=T0 + timedelta(seconds=3),
    )
    revision = replace(
        first,
        sequence=2,
        event_kind="news_revision",
        available_at=T0 + timedelta(minutes=2),
    )
    mapping = replace(
        first,
        sequence=3,
        event_kind="ticker_mapping",
        available_at=T0 + timedelta(minutes=3),
    )
    feature = replace(
        first,
        sequence=4,
        event_kind="derived_feature",
        available_at=T0 + timedelta(minutes=4),
    )
    events = [first, revision, mapping, feature]

    before = replay_at(events, ReplayMode.PROSPECTIVE, T0 + timedelta(seconds=2))
    early = replay_at(events, ReplayMode.PROSPECTIVE, T0 + timedelta(minutes=1))
    late = replay_at(events, ReplayMode.PROSPECTIVE, T0 + timedelta(minutes=4))
    assert before.observations == ()
    assert [item.event.sequence for item in early.observations] == [1]
    assert [item.event.sequence for item in late.observations] == [1, 2, 3, 4]
    assert not early.has_assumed_timing


def test_historical_research_marks_raw_market_timing_as_assumed() -> None:
    events = [
        downloaded(1, "trade", T0),
        downloaded(2, "quote", T0),
        downloaded(3, "bar", T0),
        downloaded(4, "news", T0),
        downloaded(5, "news_revision", T0),
        downloaded(6, "ticker_mapping", T0),
        downloaded(7, "derived_feature", T0),
        downloaded(8, "trade", T0 + timedelta(minutes=2)),
    ]
    decision = T0 + timedelta(minutes=1)
    prospective = replay_at(events, ReplayMode.PROSPECTIVE, decision)
    research = replay_at(events, ReplayMode.HISTORICAL_RESEARCH, decision)
    assert prospective.observations == ()
    assert [item.event.sequence for item in research.observations] == [1, 2]
    assert all(
        item.timing_basis is TimingBasis.ASSUMED_SOURCE_TIME
        for item in research.observations
    )
    assert research.has_assumed_timing
    assert research.research_only
    assert not prospective.research_only
    with pytest.raises(ReplayError, match="historical research"):
        research.require_recorded_timing()
    prospective.require_recorded_timing()


def test_out_of_order_source_times_do_not_reorder_ingestion_sequence() -> None:
    events = [
        downloaded(1, "trade", T0 + timedelta(seconds=2)),
        downloaded(2, "trade", T0 + timedelta(seconds=1)),
    ]
    frame = replay_at(events, ReplayMode.HISTORICAL_RESEARCH, T0 + timedelta(seconds=3))
    assert [item.event.sequence for item in frame.observations] == [1, 2]


def test_clock_conflict_and_future_source_timestamp_do_not_enter_view() -> None:
    first = downloaded(1, "trade", T0)
    clock_conflict = replace(
        first,
        sequence=2,
        provider_received_at=DOWNLOAD + timedelta(seconds=1),
    )
    future_source = replace(
        first, sequence=3, source_event_at=DOWNLOAD + timedelta(days=1)
    )
    frame = replay_at(
        [first, clock_conflict, future_source],
        ReplayMode.PROSPECTIVE,
        DOWNLOAD + timedelta(seconds=1),
    )
    assert [item.event.sequence for item in frame.observations] == [1]
