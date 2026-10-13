import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.feed_health import FeedHealth, FeedKind
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.news_evidence import NewsEvidenceError, capture_news_frame

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)


def _health(*, connected: bool = True) -> FeedHealth:
    return FeedHealth(
        kind=FeedKind.NEWS,
        feed="alpaca_news",
        connection_epoch=1,
        subscribed_epoch=1,
        connected=connected,
        gap_unresolved=False,
        last_heartbeat_at=NOW - timedelta(seconds=1),
        last_data_at=None,
    )


def _article(**changes: object) -> dict[str, object]:
    article: dict[str, object] = {
        "T": "n",
        "id": 42,
        "headline": "Company reports results",
        "summary": "Provider summary",
        "author": "Reporter",
        "created_at": "2026-10-13T15:59:00Z",
        "updated_at": "2026-10-13T15:59:00Z",
        "content": "Provider content",
        "url": "https://example.test/article",
        "symbols": ["ABC"],
        "source": "Provider",
    }
    article.update(changes)
    return article


def _capture(
    journal: Journal,
    article: dict[str, object],
    *,
    first_seen: datetime = NOW,
    health: FeedHealth | None = None,
) -> tuple[dict[str, object], ...]:
    return capture_news_frame(
        journal,
        json.dumps([article]),
        first_seen_at=first_seen,
        processed_at=first_seen,
        health=health or _health(),
        health_now=first_seen,
        max_heartbeat_age_seconds=30,
    )


def test_news_capture_is_journaled_but_never_strategy_eligible(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    record = _capture(journal, _article())[0]

    assert record["article_first_seen_at"] == NOW.isoformat()
    assert record["provider_created_lag_seconds"] == 60
    assert record["provider_created_age_band"] == "UNDER_2H"
    assert (
        record["age_band_boundary_convention"] == "exact_source_boundaries_unresolved"
    )
    assert record["delay_assessment"] == "UNASSESSED_NO_APPROVED_PATH_THRESHOLD"
    assert record["news_eligibility"] == "BLOCKED_RESEARCH"
    assert record["market_reaction"] == "NOT_EVALUATED"
    assert record["ticker_mapping"] == "PROVIDER_SYMBOLS_ONLY"
    event = journal.read_event(1)
    assert event is not None and event[0].event_kind == "NEWS_ARTICLE_VERSION"
    assert event[0].feed == "alpaca_news"
    journal.close()


def test_duplicate_does_not_refresh_first_seen_and_revision_is_unverified(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    first = _capture(journal, _article())[0]
    later = NOW + timedelta(minutes=5)
    duplicate = _capture(journal, _article(), first_seen=later)[0]
    revision = _capture(
        journal,
        _article(headline="Company reports amended results"),
        first_seen=later + timedelta(seconds=1),
    )[0]

    assert first["content_relation"] == "INITIAL_VERSION"
    assert duplicate["content_relation"] == "EXACT_CONTENT_REPEAT"
    assert duplicate["article_first_seen_at"] == NOW.isoformat()
    assert revision["content_relation"] == "REVISION_UNVERIFIED"
    assert revision["news_eligibility"] == "BLOCKED_RESEARCH"
    journal.close()


def test_timestamp_conflict_and_unhealthy_feed_are_vetoed(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    conflict = _capture(
        journal,
        _article(updated_at="2026-10-13T15:58:00Z"),
    )[0]
    unhealthy = _capture(
        journal,
        _article(id=43),
        health=_health(connected=False),
    )[0]

    assert conflict["timestamp_conflict"] is True
    assert conflict["status"] == "CONFLICTING"
    assert unhealthy["status"] == "FEED_UNHEALTHY"
    assert unhealthy["news_eligibility"] == "BLOCKED_RESEARCH"
    journal.close()


def test_future_provider_timestamp_is_a_conflict(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    record = _capture(
        journal,
        _article(
            created_at="2026-10-13T16:01:00Z",
            updated_at="2026-10-13T16:01:00Z",
        ),
    )[0]

    assert record["timestamp_conflict"] is True
    assert record["status"] == "CONFLICTING"
    assert record["news_eligibility"] == "BLOCKED_RESEARCH"
    journal.close()


def test_old_news_age_bands_are_display_metadata_not_eligibility(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    two_hours = _capture(
        journal,
        _article(
            id=43,
            created_at="2026-10-13T14:00:00Z",
            updated_at="2026-10-13T14:00:00Z",
        ),
    )[0]
    old = _capture(
        journal,
        _article(
            id=44,
            created_at="2026-10-12T15:00:00Z",
            updated_at="2026-10-12T15:00:00Z",
        ),
    )[0]

    assert two_hours["provider_created_age_band"] == "BOUNDARY_UNRESOLVED"
    assert old["provider_created_age_band"] == "OVER_24H"
    assert two_hours["news_eligibility"] == "BLOCKED_RESEARCH"
    assert old["news_eligibility"] == "BLOCKED_RESEARCH"
    age_basis = two_hours["age_band_basis"]
    assert isinstance(age_basis, str)
    assert age_basis.endswith("display_approximation")
    journal.close()


def test_news_age_band_avoids_inventing_exact_boundary_semantics(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    for article_id, created_at in (
        (45, "2026-10-13T14:00:00Z"),
        (46, "2026-10-13T04:00:00Z"),
        (47, "2026-10-12T16:00:00Z"),
    ):
        record = _capture(
            journal,
            _article(
                id=article_id,
                created_at=created_at,
                updated_at=created_at,
            ),
        )[0]
        assert record["provider_created_age_band"] == "BOUNDARY_UNRESOLVED"
        assert (
            record["age_band_boundary_convention"]
            == "exact_source_boundaries_unresolved"
        )
    journal.close()


def test_malformed_article_is_journaled_as_rejected(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    result = _capture(journal, _article(id="42"))

    assert result == ({"status": "MALFORMED", "eligibility": "BLOCKED"},)
    event = journal.read_event(1)
    assert event is not None and event[0].event_kind == "NEWS_REJECTED"
    assert json.loads(event[1])["status"] == "MALFORMED"
    journal.close()


def test_local_timestamps_must_be_utc(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    with pytest.raises(NewsEvidenceError, match="UTC"):
        capture_news_frame(
            journal,
            json.dumps([_article()]),
            first_seen_at=NOW.replace(tzinfo=None),
            processed_at=NOW,
            health=_health(),
            health_now=NOW,
            max_heartbeat_age_seconds=30,
        )
    journal.close()
