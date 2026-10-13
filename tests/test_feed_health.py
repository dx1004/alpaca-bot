from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from alpaca_ross_bot.feed_health import FeedHealth, FeedKind, HealthReason

NOW = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)


HEALTH = FeedHealth(
    kind=FeedKind.EQUITIES,
    feed="iex",
    connection_epoch=2,
    subscribed_epoch=2,
    connected=True,
    gap_unresolved=False,
    last_heartbeat_at=NOW - timedelta(seconds=1),
    last_data_at=NOW - timedelta(seconds=2),
)


def _reasons(health: FeedHealth) -> tuple[HealthReason, ...]:
    return health.reasons(
        now=NOW,
        max_heartbeat_age=timedelta(seconds=5),
        max_data_age=timedelta(seconds=10),
    )


def test_current_iex_requires_fresh_subscription_and_data() -> None:
    assert _reasons(HEALTH) == ()
    assert HEALTH.coverage_label == "NOT_SIP_EQUIVALENT"
    assert HealthReason.WRONG_FEED in _reasons(replace(HEALTH, feed="sip"))
    assert HealthReason.SUBSCRIPTION_UNVERIFIED in _reasons(
        replace(HEALTH, connection_epoch=3)
    )
    assert HealthReason.GAP_UNRESOLVED in _reasons(replace(HEALTH, gap_unresolved=True))
    assert HealthReason.DATA_STALE in _reasons(
        replace(HEALTH, last_data_at=NOW - timedelta(minutes=1))
    )


def test_liveness_and_cache_do_not_hide_outage() -> None:
    reasons = _reasons(
        replace(
            HEALTH,
            connected=False,
            last_heartbeat_at=NOW - timedelta(minutes=1),
            last_data_at=NOW - timedelta(minutes=1),
        )
    )
    assert {HealthReason.DISCONNECTED, HealthReason.HEARTBEAT_STALE} <= set(reasons)


def test_sparse_news_does_not_require_an_item_but_requires_source_health() -> None:
    health = replace(HEALTH, kind=FeedKind.NEWS, feed="alpaca_news", last_data_at=None)
    assert (
        health.reasons(
            now=NOW, max_heartbeat_age=timedelta(seconds=5), max_data_age=None
        )
        == ()
    )
    assert HealthReason.SUBSCRIPTION_UNVERIFIED in replace(
        HEALTH,
        kind=FeedKind.NEWS,
        feed="alpaca_news",
        subscribed_epoch=1,
        last_data_at=None,
    ).reasons(now=NOW, max_heartbeat_age=timedelta(seconds=5), max_data_age=None)


def test_future_clock_and_invalid_age_fail() -> None:
    assert HealthReason.CLOCK_CONFLICT in _reasons(
        replace(HEALTH, last_data_at=NOW + timedelta(seconds=1))
    )
    with pytest.raises(ValueError):
        HEALTH.reasons(
            now=NOW,
            max_heartbeat_age=timedelta(0),
            max_data_age=timedelta(seconds=1),
        )
    with pytest.raises(ValueError):
        HEALTH.reasons(
            now=NOW,
            max_heartbeat_age=timedelta(seconds=1),
            max_data_age=None,
        )
