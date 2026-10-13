import base64
import json
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.rest_recovery import AlpacaRestRecovery, RecoveryError


class Pages:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = deque(responses)
        self.requests: list[tuple[str, float, int]] = []

    def __call__(
        self, url: str, headers: object, timeout: float, max_bytes: int
    ) -> bytes:
        self.requests.append((url, timeout, max_bytes))
        return json.dumps(self.responses.popleft()).encode()


def _recovery(
    pages: Pages, journal: Journal, *, max_pages: int = 5
) -> AlpacaRestRecovery:
    return AlpacaRestRecovery(
        PaperCredentials(SecretValue("paper-key"), SecretValue("paper-secret")),
        timeout_seconds=3,
        max_pages=max_pages,
        max_bytes=1_000_000,
        journal=journal,
        fetch_page=pages,
    )


def test_trades_page_tokens_are_followed_even_after_short_page(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    pages = Pages(
        [
            {"trades": {"AAPL": [{"t": "one"}]}, "next_page_token": "cursor-2"},
            {"trades": {"AAPL": [{"t": "two"}]}, "next_page_token": None},
        ]
    )
    result = _recovery(pages, journal).collect(
        "trades",
        symbols=("AAPL",),
        start="2026-10-09T12:00:00Z",
        end="2026-10-09T12:01:00Z",
        page_size=1,
    )

    assert result.pagination_exhausted
    assert result.continuity_proven is False
    assert result.coverage_label == "NOT_SIP_EQUIVALENT"
    first_query = parse_qs(urlsplit(pages.requests[0][0]).query)
    next_query = parse_qs(urlsplit(pages.requests[1][0]).query)
    assert first_query["feed"] == ["iex"]
    assert "page_token" not in first_query
    assert next_query["page_token"] == ["cursor-2"]
    assert result.pages[0].next_page_token == "cursor-2"
    first_page = journal.read_event(1)
    assert first_page is not None
    metadata = json.loads(first_page[1])
    assert metadata["page_token"] is None
    assert metadata["next_page_token"] == "cursor-2"
    assert metadata["continuity_proven"] is False
    raw_response = json.loads(base64.b64decode(metadata["raw_body_base64"]))
    assert raw_response["next_page_token"] == "cursor-2"
    assert journal.read_event(2) is not None
    journal.close()


def test_news_is_separate_and_does_not_claim_iex_or_sip_coverage(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    pages = Pages([{"news": [{"id": "n1"}]}])
    result = _recovery(pages, journal).collect(
        "news",
        symbols=("AAPL",),
        start="2026-10-09T12:00:00Z",
        end="2026-10-09T12:01:00Z",
        page_size=50,
    )
    query = parse_qs(urlsplit(pages.requests[0][0]).query)
    assert result.coverage_label == "ALPACA_NEWS"
    assert "feed" not in query
    news_page = journal.read_event(1)
    assert news_page is not None and news_page[0].feed == "alpaca_news"
    journal.close()


def test_repeated_or_unexhausted_tokens_fail_closed(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    repeated = Pages(
        [
            {"quotes": {"AAPL": []}, "next_page_token": "same"},
            {"quotes": {"AAPL": []}, "next_page_token": "same"},
        ]
    )
    with pytest.raises(RecoveryError, match="repeated"):
        _recovery(repeated, journal).collect(
            "quotes",
            symbols=("AAPL",),
            start="2026-10-09T12:00:00Z",
            end="2026-10-09T12:01:00Z",
            page_size=100,
        )

    unfinished = Pages([{"quotes": {"AAPL": []}, "next_page_token": "more"}])
    with pytest.raises(RecoveryError, match="page budget"):
        _recovery(unfinished, journal, max_pages=1).collect(
            "quotes",
            symbols=("AAPL",),
            start="2026-10-09T12:00:00Z",
            end="2026-10-09T12:01:00Z",
            page_size=100,
        )
    journal.close()


def test_invalid_query_or_response_fails_closed(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    malformed = Pages([{"quotes": [], "next_page_token": 7}])
    recovery = _recovery(malformed, journal)
    with pytest.raises(ValueError, match="unique"):
        recovery.collect(
            "quotes",
            symbols=("AAPL", "AAPL"),
            start="2026-10-09T12:00:00Z",
            end="2026-10-09T12:01:00Z",
            page_size=100,
        )
    with pytest.raises(RecoveryError, match="malformed"):
        recovery.collect(
            "quotes",
            symbols=("AAPL",),
            start="2026-10-09T12:00:00Z",
            end="2026-10-09T12:01:00Z",
            page_size=100,
        )
    malformed_event = journal.read_event(1)
    assert malformed_event is not None
    metadata = json.loads(malformed_event[1])
    assert metadata["pagination_exhausted"] is False
    assert metadata["continuity_proven"] is False
    assert metadata["raw_body_base64"]
    journal.close()
