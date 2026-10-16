from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction as F

import pytest

from alpaca_ross_bot.strategy_features import (
    NY,
    Bar,
    FeatureUnavailable,
    Session,
    Slot,
    aggregate_minutes,
    build_trend,
    ema,
    expected_history,
    latest_entry_bar,
    observed_bar,
    series,
    session_slots,
)


def session(
    day: date = date(2026, 10, 16), hours: int = 6, minutes: int = 30
) -> Session:
    opened = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY).astimezone(UTC)
    return Session(
        day,
        opened,
        opened + timedelta(hours=hours, minutes=minutes),
        opened - timedelta(days=200),
        "synthetic-audited-calendar",
    )


def bar(slot: Slot, close: int = 10, revision: int = 0) -> Bar:
    return Bar(
        "ABC",
        slot,
        F(close),
        F(close + 1),
        F(close - 1),
        F(close),
        7,
        slot.end,
        revision,
        f"bar:{slot.start}:{revision}",
    )


def test_rth_anchor_and_truncated_hour_not_wall_clock_or_weekday() -> None:
    # Explicit Saturday and unusual early close are honored, never weekday-derived.
    day = session(date(2026, 10, 17), 3, 17)
    slots = session_slots(day, 60, day.open_at)
    assert [slot.end - slot.start for slot in slots] == [timedelta(hours=1)] * 3 + [
        timedelta(minutes=17)
    ]
    assert slots[0].start == day.open_at
    assert slots[-1].end == day.close_at


def test_dst_uses_supplied_exchange_calendar() -> None:
    before, after = session(date(2026, 10, 30)), session(date(2026, 11, 2))
    assert before.open_at.hour == 13
    assert after.open_at.hour == 14


def test_every_minute_required_and_exact_aggregation() -> None:
    day = session()
    minutes = tuple(
        bar(slot, 10 + i)
        for i, slot in enumerate(session_slots(day, 1, day.open_at)[:5])
    )
    slot = session_slots(day, 5, day.open_at)[0]
    result = aggregate_minutes("ABC", slot, minutes, slot.end)
    assert (result.open, result.high, result.low, result.close, result.volume) == (
        F(10),
        F(15),
        F(9),
        F(14),
        35,
    )
    with pytest.raises(FeatureUnavailable, match="missing"):
        aggregate_minutes("ABC", slot, minutes[:2] + minutes[3:], slot.end)
    with pytest.raises(FeatureUnavailable, match="not completed|missing"):
        aggregate_minutes("ABC", slot, minutes, slot.end - timedelta(microseconds=1))


def test_revision_availability_does_not_rewrite_previous_view() -> None:
    slot = session_slots(session(), 1, session().open_at)[0]
    original = bar(slot)
    revised = replace(bar(slot, 20, 1), available_at=slot.end + timedelta(seconds=20))
    assert observed_bar("ABC", slot, (original, revised), slot.end) == original
    assert (
        observed_bar("ABC", slot, (original, revised), revised.available_at) == revised
    )
    with pytest.raises(FeatureUnavailable, match="conflicting"):
        observed_bar("ABC", slot, (original, replace(original, high=F(99))), slot.end)
    with pytest.raises(FeatureUnavailable, match="revision clock"):
        observed_bar(
            "ABC",
            slot,
            (
                revised,
                replace(
                    original, available_at=revised.available_at + timedelta(seconds=1)
                ),
            ),
            revised.available_at + timedelta(seconds=1),
        )


@pytest.mark.parametrize("period", [9, 21, 50])
def test_exact_ema_seed_and_recurrence(period: int) -> None:
    closes = (F(1),) * period + (F(2),) * (100 - period)
    decay = F(period - 1, period + 1)
    assert ema(closes, period) == 2 - decay ** (100 - period)


@pytest.mark.parametrize("count", [0, 9, 99, 101])
def test_ema_rejects_not_exactly100(count: int) -> None:
    with pytest.raises(FeatureUnavailable, match="100"):
        ema((F(1),) * count, 9)


def test_series_uses_all_expected_not_latest100_available() -> None:
    day = session()
    expected = session_slots(day, 1, day.open_at)[:100]
    observations = tuple(bar(slot) for slot in expected)
    assert len(series("ABC", expected, observations, day.close_at)) == 100
    with pytest.raises(FeatureUnavailable, match="missing"):
        series("ABC", expected, observations[:-1], day.close_at)
    with pytest.raises(FeatureUnavailable, match="ordered"):
        series("ABC", tuple(reversed(expected)), observations, day.close_at)
    with pytest.raises(FeatureUnavailable, match="missing"):
        series("OTHER", expected, observations, day.close_at)


def test_build_trend_derives100_expected_calendar_slots_and_freezes_at_open() -> None:
    current = session()
    history = tuple(session(current.day - timedelta(days=i)) for i in range(100, 0, -1))
    daily_slots = expected_history(history, None, current.open_at)
    hourly_slots = expected_history(history, 60, current.open_at)
    daily = tuple(bar(slot, 100 + i) for i, slot in enumerate(daily_slots))
    minutes = tuple(
        bar(
            Slot(
                slot.start + timedelta(minutes=i), slot.start + timedelta(minutes=i + 1)
            ),
            100 + n,
        )
        for n, slot in enumerate(hourly_slots)
        for i in range(int((slot.end - slot.start).total_seconds() // 60))
    )
    trend = build_trend("ABC", current, history, daily, minutes)
    assert trend.prior_close == 199
    assert trend.prior_close > trend.daily21 > trend.daily50
    assert trend.hourly9 > trend.hourly21
    assert len(trend.evidence_refs) == 200
    revised = replace(
        daily[-1],
        close=F(500),
        high=F(500),
        revision=1,
        evidence_ref="late-revision",
        available_at=current.open_at + timedelta(seconds=1),
    )
    assert build_trend("ABC", current, history, daily + (revised,), minutes) == trend
    with pytest.raises(FeatureUnavailable, match="missing"):
        build_trend("ABC", current, history, daily, minutes[:-1])


@pytest.mark.parametrize(
    "bad", ["naive", "future_calendar", "bad_price", "bad_feed", "negative_volume"]
)
def test_malformed_data_never_becomes_a_feature(bad: str) -> None:
    day = session()
    slot = session_slots(day, 1, day.open_at)[0]
    with pytest.raises(FeatureUnavailable):
        if bad == "naive":
            replace(day, open_at=day.open_at.replace(tzinfo=None))
        elif bad == "future_calendar":
            session_slots(
                replace(day, available_at=day.open_at + timedelta(seconds=1)),
                1,
                day.open_at,
            )
        elif bad == "bad_price":
            replace(bar(slot), close=F(0))
        elif bad == "bad_feed":
            replace(bar(slot), feed="sip")
        else:
            replace(bar(slot), volume=-1)


def test_latest_entry_bar_requires_all_minutes_and_obeys_decision_switch() -> None:
    day = session()
    minutes = tuple(bar(slot) for slot in session_slots(day, 1, day.open_at)[:15])
    before = day.open_at + timedelta(minutes=14, seconds=59)
    assert (
        latest_entry_bar("ABC", day, minutes, before, before).slot == minutes[13].slot
    )
    switch = day.open_at + timedelta(minutes=15)
    assert latest_entry_bar("ABC", day, minutes, switch, switch).slot == Slot(
        day.open_at + timedelta(minutes=10), switch
    )
    # Late pre-switch event cannot anticipate the10:00–10:05 bucket boundary.
    assert latest_entry_bar("ABC", day, minutes, before, switch).slot == Slot(
        day.open_at + timedelta(minutes=5), day.open_at + timedelta(minutes=10)
    )
    with pytest.raises(FeatureUnavailable, match="missing"):
        latest_entry_bar("ABC", day, minutes[:12] + minutes[13:], switch, switch)
    with pytest.raises(FeatureUnavailable, match="no completed"):
        latest_entry_bar("ABC", day, minutes, day.open_at, day.open_at)
