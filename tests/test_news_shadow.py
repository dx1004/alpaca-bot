import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alpaca_ross_bot.feed_health import FeedHealth, FeedKind
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.news_evidence import capture_news_frame
from alpaca_ross_bot.news_shadow import ModelCall, evaluate_news_shadow

NOW = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)


def _article() -> dict[str, object]:
    return {
        "T": "n",
        "id": 42,
        "headline": "ABC reports new contract",
        "summary": "ABC announced a supply agreement.",
        "author": "Reporter",
        "created_at": "2026-10-13T15:59:00Z",
        "updated_at": "2026-10-13T15:59:00Z",
        "content": "Long provider content is excluded from the compact prompt.",
        "url": "https://example.test/article",
        "symbols": ["ABC"],
        "source": "Provider",
    }


def _journal(tmp_path: Path) -> Journal:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    health = FeedHealth(
        kind=FeedKind.NEWS,
        feed="alpaca_news",
        connection_epoch=1,
        subscribed_epoch=1,
        connected=True,
        gap_unresolved=False,
        last_heartbeat_at=NOW - timedelta(seconds=1),
        last_data_at=None,
    )
    raw = json.dumps([_article()])
    raw_event = journal.append_next(
        source_id="alpaca_news",
        feed="alpaca_news",
        event_kind="RAW_FRAME",
        payload=raw.encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    capture_news_frame(
        journal,
        raw,
        first_seen_at=NOW,
        processed_at=NOW,
        health=health,
        health_now=NOW,
        max_heartbeat_age_seconds=30,
        raw_frame_sequence=raw_event.sequence,
    )
    return journal


def _output(**extra: object) -> str:
    result: dict[str, object] = {
        "event_type": "contract",
        "summary": "ABC announced a supply agreement.",
        "evidence_spans": ["ABC announced a supply agreement."],
        "symbols": ["ABC"],
        "uncertainties": [],
    }
    result.update(extra)
    return json.dumps(result)


def _evaluate(journal: Journal, model_call: ModelCall) -> dict[str, object]:
    return asyncio.run(
        evaluate_news_shadow(
            journal,
            evidence_sequence=2,
            evaluated_at=NOW + timedelta(seconds=1),
            model_id="test-model",
            prompt_version="test-prompt-v1",
            timeout_seconds=1,
            model_call=model_call,
        )
    )


def test_shadow_event_is_separate_and_uses_compact_input(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    captured: list[str] = []

    async def model_call(prompt: str) -> str:
        captured.append(prompt)
        return _output()

    report = _evaluate(journal, model_call)

    assert report["status"] == "COMPLETED"
    assert report["shadow_only"] is True
    assert report["formal_eligibility"] == "UNCHANGED"
    assert "content" not in captured[0]
    assert "Long provider content" not in captured[0]
    assert "ABC reports new contract" in captured[0]
    shadow = journal.read_event(3)
    assert shadow is not None
    assert shadow[0].event_kind == "NEWS_AI_SHADOW"
    assert shadow[0].feed == "alpaca_news"
    saved = json.loads(shadow[1])
    assert saved["status"] == "COMPLETED"
    assert "headline" not in saved
    assert saved["formal_eligibility"] == "UNCHANGED"
    journal.close()


def test_timeout_and_invalid_output_write_only_shadow_errors(tmp_path: Path) -> None:
    timeout_journal = _journal(tmp_path / "timeout")

    async def timeout_call(prompt: str) -> str:
        await asyncio.sleep(0.05)
        return _output()

    timeout_report = asyncio.run(
        evaluate_news_shadow(
            timeout_journal,
            evidence_sequence=2,
            evaluated_at=NOW + timedelta(seconds=1),
            model_id="test-model",
            prompt_version="test-prompt-v1",
            timeout_seconds=0.001,
            model_call=timeout_call,
        )
    )
    assert timeout_report["status"] == "TIMEOUT"
    assert timeout_report["error_type"] == "TimeoutError"
    timeout_event = timeout_journal.read_event(3)
    assert timeout_event is not None
    assert timeout_event[0].event_kind == "NEWS_AI_SHADOW"
    timeout_journal.close()

    invalid_journal = _journal(tmp_path / "invalid")

    async def invalid_call(prompt: str) -> str:
        return _output(symbols=["XYZ"])

    invalid_report = _evaluate(invalid_journal, invalid_call)
    assert invalid_report["status"] == "INVALID_OUTPUT"
    assert "analysis" not in invalid_report
    invalid_event = invalid_journal.read_event(3)
    assert invalid_event is not None
    assert invalid_event[0].event_kind == "NEWS_AI_SHADOW"
    invalid_journal.close()


def test_shadow_result_is_idempotent_and_cannot_add_authority(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    calls = 0

    async def model_call(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return _output(formal_eligibility="APPROVED")

    first = _evaluate(journal, model_call)
    second = _evaluate(journal, model_call)

    assert first["status"] == "INVALID_OUTPUT"
    assert second["status"] == "DUPLICATE"
    assert calls == 1
    shadow_event = journal.read_event(3)
    assert shadow_event is not None
    assert shadow_event[0].event_kind == "NEWS_AI_SHADOW"
    assert journal.read_event(4) is None
    journal.close()
