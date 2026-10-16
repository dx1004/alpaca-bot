import base64
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.feed_health import FeedHealth, FeedKind
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.news_evidence import capture_news_frame
from alpaca_ross_bot.news_preference import capture_optional_news_view
from alpaca_ross_bot.news_rest_observation import capture_rest_news_page

NOW = datetime(2026, 10, 13, 16, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def article(journal: Journal, at: datetime = NOW, headline: str = "Results") -> int:
    message = {
        "T": "n",
        "id": 42,
        "headline": headline,
        "summary": "Summary",
        "author": "Reporter",
        "source": "Provider",
        "symbols": ["ABC"],
        "created_at": "2026-10-13T15:00:00Z",
        "updated_at": "2026-10-13T15:00:00Z",
    }
    raw = json.dumps([message]).encode()
    parent = journal.append_next(
        source_id="alpaca_news",
        feed="alpaca_news",
        event_kind="RAW_FRAME",
        payload=raw,
        system_first_seen_at=at,
        processed_at=at,
    )
    capture_news_frame(
        journal,
        raw,
        first_seen_at=at,
        processed_at=at,
        health=FeedHealth(
            kind=FeedKind.NEWS,
            feed="alpaca_news",
            connection_epoch=1,
            subscribed_epoch=1,
            connected=False,
            gap_unresolved=True,
            last_heartbeat_at=None,
            last_data_at=None,
        ),
        health_now=at,
        max_heartbeat_age_seconds=30,
        raw_frame_sequence=parent.sequence,
    )
    return parent.sequence + 1


def view(
    journal: Journal,
    refs: tuple[int, ...],
    at: datetime = NOW,
    symbol: str = "ABC",
    root: Path = ROOT,
) -> dict[str, object]:
    event = capture_optional_news_view(
        journal,
        repo_root=root,
        symbol=symbol,
        input_refs=refs,
        decision_at=at,
        processed_at=at,
    )
    row = journal.read_event(event.sequence)
    assert row is not None
    return json.loads(row[1])  # type: ignore[no-any-return]


def test_revision_visibility_duplicate_and_nonveto(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.sqlite3", tmp_path)
    first = article(journal)
    later = NOW + timedelta(seconds=1)
    duplicate = article(journal, later)
    revision = article(journal, later, "Revised")
    early = view(journal, (first, duplicate, revision))
    assert len(early["records"]) == 1  # type: ignore[arg-type]
    assert early["input_refs"] == [first]
    final = view(journal, (first, duplicate, revision), later)
    records = final["records"]
    assert isinstance(records, list) and len(records) == 3
    assert records[1]["article_first_seen_at"] == NOW.isoformat()
    assert records[2]["content_relation"] == "REVISION_UNVERIFIED"
    assert final["affects_scanner_qualification"] is False
    assert final["preference_status"] == "UNVERIFIED"
    assert view(journal, (), later)["records"] == []
    assert view(journal, (first,), later, "abc")["records"] == []
    assert view(journal, (first,), later, "ABC.A")["records"] == []
    journal.close()


def test_forged_raw_binding_and_clock_rejected(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.sqlite3", tmp_path)
    first = article(journal)
    row = journal.read_event(first)
    assert row is not None
    data = json.loads(row[1])
    data["headline"] = "Forged"
    fake = journal.append_next(
        source_id="alpaca_news",
        feed="alpaca_news",
        event_kind="NEWS_ARTICLE_VERSION",
        payload=json.dumps(data).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    with pytest.raises(ValueError, match="differs from raw"):
        view(journal, (fake.sequence,))
    with pytest.raises(ValueError, match="precedes decision"):
        capture_optional_news_view(
            journal,
            repo_root=ROOT,
            symbol="ABC",
            input_refs=(),
            decision_at=NOW,
            processed_at=NOW - timedelta(seconds=1),
        )
    journal.close()


def test_research_tampering_prevents_append(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    for relative in (
        "evidence/general-iex-daily30-screen-v1.json",
        "evidence/hybrid-500-iex-intraday-research-v1.json",
        "docs/no-leverage-pullback-research.md",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (root / "evidence/hybrid-500-iex-intraday-research-v1.json").write_text("{}")
    journal = Journal(tmp_path / "j.sqlite3", tmp_path)
    with pytest.raises(ValueError, match="digest"):
        view(journal, (), root=root)
    assert journal.read_event(1) is None
    journal.close()


def test_rest_record_hash_retrieval_timing_and_wrong_source(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "j.sqlite3", tmp_path)
    body = {
        "news": [
            {
                "id": 42,
                "symbols": ["ABC"],
                "headline": "Old story",
                "created_at": "2020-01-01T00:00:00Z",
            }
        ]
    }
    page = journal.append_next(
        source_id="alpaca_news",
        feed="alpaca_news",
        event_kind="REST_RECOVERY_PAGE",
        payload=json.dumps(
            {
                "kind": "news",
                "raw_body_base64": base64.b64encode(json.dumps(body).encode()).decode(),
            }
        ).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    later = NOW + timedelta(seconds=1)
    projected = capture_rest_news_page(journal, page.sequence, processed_at=later)[0]
    assert view(journal, (projected.sequence,))["records"] == []
    result = view(journal, (projected.sequence,), later)
    records = result["records"]
    assert isinstance(records, list)
    assert records[0]["available_at"] == later.isoformat()
    assert records[0]["local_receipt_at"] == NOW.isoformat()
    assert records[0]["provider_created_at"] == "2020-01-01T00:00:00Z"
    original = journal.read_event(projected.sequence)
    assert original is not None
    data = json.loads(original[1])
    data["record_sha256"] = "0" * 64
    forged = journal.append_next(
        source_id="alpaca_news",
        feed="alpaca_news",
        event_kind="NEWS_REST_ARTICLE_OBSERVATION",
        payload=json.dumps(data).encode(),
        system_first_seen_at=NOW,
        processed_at=later,
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        view(journal, (forged.sequence,), later)
    wrong = journal.append_next(
        source_id="other",
        feed="alpaca_news",
        event_kind="NEWS_ARTICLE_VERSION",
        payload=b"{}",
        system_first_seen_at=NOW,
        processed_at=later,
    )
    with pytest.raises(ValueError, match="source/feed"):
        view(journal, (wrong.sequence,), later)
    journal.close()
