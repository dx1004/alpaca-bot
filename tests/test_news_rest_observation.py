import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.news_rest_observation import (
    RestNewsObservationError,
    capture_rest_news_page,
)
from alpaca_ross_bot.replay import ReplayMode, replay_at
from alpaca_ross_bot.rest_recovery import AlpacaRestRecovery


def test_processing_cannot_precede_parent_availability(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    at = datetime(2026, 10, 13, 16, tzinfo=UTC)
    journal = Journal(tmp_path / "j.sqlite3", tmp_path)
    page = journal.append_next(
        source_id="alpaca_news",
        feed="alpaca_news",
        event_kind="REST_RECOVERY_PAGE",
        payload=json.dumps(
            {
                "kind": "news",
                "raw_body_base64": base64.b64encode(b'{"news":[{"id":42}]}').decode(),
            }
        ).encode(),
        system_first_seen_at=at,
        processed_at=at + timedelta(seconds=2),
    )
    with pytest.raises(RestNewsObservationError, match="availability"):
        capture_rest_news_page(
            journal, page.sequence, processed_at=at + timedelta(seconds=1)
        )
    assert journal.read_event(2) is None
    journal.close()


class _Page:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def __call__(
        self, url: str, headers: object, timeout: float, max_bytes: int
    ) -> bytes:
        return json.dumps(self.response).encode()


def _collect_news(journal: Journal, records: list[object]) -> None:
    collector = AlpacaRestRecovery(
        PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
        timeout_seconds=3,
        max_pages=2,
        max_bytes=100_000,
        journal=journal,
        fetch_page=_Page({"news": records}),
    )
    collector.collect(
        "news",
        symbols=("ABC",),
        start="2026-10-13T15:00:00Z",
        end="2026-10-13T15:01:00Z",
        page_size=50,
    )


def test_rest_articles_are_visible_only_after_local_projection(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    _collect_news(
        journal,
        [
            {
                "id": 42,
                "created_at": "2026-10-13T14:00:00Z",
                "updated_at": "2026-10-13T14:05:00Z",
            }
        ],
    )
    page = journal.read_event(1)
    assert page is not None
    processed_at = page[0].system_first_seen_at + timedelta(seconds=1)

    projected = capture_rest_news_page(journal, 1, processed_at=processed_at)

    assert len(projected) == 1
    article = journal.read_event(projected[0].sequence)
    assert article is not None
    event, payload = article
    evidence = json.loads(payload)
    assert event.event_kind == "NEWS_REST_ARTICLE_OBSERVATION"
    assert event.source_event_at is None
    assert event.provider_received_at is None
    assert event.available_at == processed_at
    assert evidence["parent_page_sequence"] == 1
    assert evidence["record_index"] == 0
    assert evidence["timing_basis"] == "LOCAL_RETRIEVAL_AND_PROCESSING_ONLY"
    assert evidence["news_eligibility"] == "BLOCKED_RESEARCH"

    prior = replay_at([page[0], event], ReplayMode.PROSPECTIVE, page[0].available_at)
    assert [item.event.event_kind for item in prior.observations] == [
        "REST_RECOVERY_PAGE"
    ]
    available = replay_at([page[0], event], ReplayMode.PROSPECTIVE, processed_at)
    assert [item.event.event_kind for item in available.observations] == [
        "REST_RECOVERY_PAGE",
        "NEWS_REST_ARTICLE_OBSERVATION",
    ]
    assert capture_rest_news_page(journal, 1, processed_at=processed_at) == ()
    assert journal.read_event(3) is None
    journal.close()


def test_rest_news_projection_rejects_wrong_parent_and_bad_records(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    _collect_news(journal, [{"id": 1}])
    page = journal.read_event(1)
    assert page is not None
    at = page[0].system_first_seen_at + timedelta(seconds=1)
    with pytest.raises(RestNewsObservationError, match="processing time"):
        capture_rest_news_page(
            journal,
            1,
            processed_at=page[0].system_first_seen_at - timedelta(seconds=1),
        )
    wrong_parent = journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="REST_RECOVERY_PAGE",
        payload=b"{}",
        system_first_seen_at=at,
        processed_at=at,
    )
    with pytest.raises(RestNewsObservationError, match="not an Alpaca news REST page"):
        capture_rest_news_page(journal, wrong_parent.sequence, processed_at=at)

    bad_journal = Journal(tmp_path / "var" / "bad.sqlite3", tmp_path / "var")
    _collect_news(bad_journal, ["not-an-object"])
    bad_page = bad_journal.read_event(1)
    assert bad_page is not None
    with pytest.raises(RestNewsObservationError, match="must be an object"):
        capture_rest_news_page(
            bad_journal,
            1,
            processed_at=bad_page[0].system_first_seen_at + timedelta(seconds=1),
        )
    assert bad_journal.read_event(2) is None
    bad_journal.close()
    journal.close()
