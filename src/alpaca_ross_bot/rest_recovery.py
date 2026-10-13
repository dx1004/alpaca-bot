"""Bounded, token-visible Alpaca REST page collection for gap investigation.

Pagination exhaustion is recorded separately from WebSocket continuity proof.
"""

import base64
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal
from urllib.parse import urlencode

from alpaca_ross_bot.config import PaperCredentials
from alpaca_ross_bot.http_transport import HttpTransportError, bounded_alpaca_get
from alpaca_ross_bot.journal import Journal

RecoveryKind = Literal["trades", "quotes", "news"]
_ROUTES = {
    "trades": ("/v2/stocks/trades", "trades", 10_000),
    "quotes": ("/v2/stocks/quotes", "quotes", 10_000),
    "news": ("/v1beta1/news", "news", 50),
}
_MAX_PAGE_BYTES = 16 * 1024 * 1024


class RecoveryError(ValueError):
    """A bounded REST query failed validation or was not exhausted."""


type PageFetcher = Callable[[str, Mapping[str, str], float, int], bytes]


@dataclass(frozen=True)
class RawRecoveryPage:
    page_token: str | None
    next_page_token: str | None
    raw_body: bytes


@dataclass(frozen=True)
class RecoveryQueryResult:
    kind: RecoveryKind
    pages: tuple[RawRecoveryPage, ...]
    pagination_exhausted: bool
    coverage_label: str
    continuity_proven: Literal[False] = False


class AlpacaRestRecovery:
    """Collect all pages for fixed query bounds, under explicit resource bounds."""

    def __init__(
        self,
        credentials: PaperCredentials,
        *,
        timeout_seconds: float,
        max_pages: int,
        max_bytes: int,
        journal: Journal,
        fetch_page: PageFetcher | None = None,
    ) -> None:
        if (
            not isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or type(max_pages) is not int
            or max_pages < 1
            or type(max_bytes) is not int
            or max_bytes < 1
        ):
            raise ValueError("REST bounds must be positive")
        self._credentials = credentials
        self._timeout = timeout_seconds
        self._max_pages = max_pages
        self._max_bytes = min(max_bytes, _MAX_PAGE_BYTES * max_pages)
        self._journal = journal
        self._fetch_page = fetch_page or _fetch_https_page

    def collect(
        self,
        kind: RecoveryKind,
        *,
        symbols: tuple[str, ...],
        start: str,
        end: str,
        page_size: int,
    ) -> RecoveryQueryResult:
        if kind not in _ROUTES:
            raise ValueError("unsupported REST recovery kind")
        route, response_key, page_limit = _ROUTES[kind]
        _validate_query(symbols, start, end, page_size, page_limit)
        deadline = time.monotonic() + self._timeout
        pages: list[RawRecoveryPage] = []
        tokens: set[str] = set()
        token: str | None = None
        total_bytes = 0
        headers = self._credentials.auth_headers()
        for _ in range(self._max_pages):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecoveryError("REST query deadline expired")
            params = {
                "symbols": ",".join(symbols),
                "start": start,
                "end": end,
                "limit": str(page_size),
                "sort": "asc",
            }
            if kind != "news":
                params["feed"] = "iex"
            if token is not None:
                params["page_token"] = token
            url = "https://data.alpaca.markets" + route + "?" + urlencode(params)
            requested_at = datetime.now(UTC)
            body = self._fetch_page(
                url,
                headers,
                remaining,
                min(_MAX_PAGE_BYTES, self._max_bytes - total_bytes),
            )
            received_at = datetime.now(UTC)
            if time.monotonic() >= deadline:
                raise RecoveryError("REST query deadline expired")
            total_bytes += len(body)
            if total_bytes > self._max_bytes:
                raise RecoveryError("REST response byte budget exceeded")
            try:
                next_token = _next_token(body, response_key)
            except RecoveryError as exc:
                self._journal_page(
                    kind,
                    symbols,
                    start,
                    end,
                    page_size,
                    token,
                    None,
                    requested_at,
                    received_at,
                    body,
                    pagination_exhausted=False,
                    parse_error=str(exc),
                )
                raise
            pages.append(RawRecoveryPage(token, next_token, body))
            self._journal_page(
                kind,
                symbols,
                start,
                end,
                page_size,
                token,
                next_token,
                requested_at,
                received_at,
                body,
                pagination_exhausted=next_token is None,
            )
            if next_token is None:
                return RecoveryQueryResult(
                    kind=kind,
                    pages=tuple(pages),
                    pagination_exhausted=True,
                    coverage_label=(
                        "NOT_SIP_EQUIVALENT" if kind != "news" else "ALPACA_NEWS"
                    ),
                )
            if next_token in tokens:
                raise RecoveryError("REST pagination token repeated")
            tokens.add(next_token)
            token = next_token
        raise RecoveryError("REST page budget ended before pagination exhausted")

    def _journal_page(
        self,
        kind: RecoveryKind,
        symbols: tuple[str, ...],
        start: str,
        end: str,
        page_size: int,
        page_token: str | None,
        next_page_token: str | None,
        requested_at: datetime,
        received_at: datetime,
        body: bytes,
        *,
        pagination_exhausted: bool,
        parse_error: str | None = None,
    ) -> None:
        metadata: dict[str, object] = {
            "kind": kind,
            "symbols": list(symbols),
            "start": start,
            "end": end,
            "page_size": page_size,
            "page_token": page_token,
            "next_page_token": next_page_token,
            "requested_at": requested_at.isoformat(),
            "received_at": received_at.isoformat(),
            "raw_body_base64": base64.b64encode(body).decode("ascii"),
            "pagination_exhausted": pagination_exhausted,
            "continuity_proven": False,
        }
        if parse_error is not None:
            metadata["parse_error"] = parse_error
        self._journal.append_next(
            source_id="alpaca_news" if kind == "news" else "alpaca_market_data",
            feed="iex" if kind != "news" else "alpaca_news",
            event_kind="REST_RECOVERY_PAGE",
            payload=json.dumps(
                metadata,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            system_first_seen_at=received_at,
            processed_at=datetime.now(UTC),
        )


def _validate_query(
    symbols: tuple[str, ...], start: str, end: str, page_size: int, page_limit: int
) -> None:
    if not symbols or any(
        not isinstance(symbol, str)
        or not symbol.strip()
        or symbol.strip() != symbol
        or "," in symbol
        for symbol in symbols
    ):
        raise ValueError("symbols must be nonempty")
    if len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be unique")
    if not start.strip() or not end.strip():
        raise ValueError("fixed UTC start and end bounds are required")
    start_at = _parse_bound(start)
    end_at = _parse_bound(end)
    if start_at >= end_at:
        raise ValueError("start must be earlier than end")
    if type(page_size) is not int or not 1 <= page_size <= page_limit:
        raise ValueError("page size is outside the Alpaca endpoint limit")


def _parse_bound(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("bounds must be RFC-3339 UTC timestamps") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("bounds must be RFC-3339 UTC timestamps")
    return parsed


def _next_token(raw: bytes, response_key: str) -> str | None:
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecoveryError("REST response is not valid JSON") from exc
    if not isinstance(body, dict) or not isinstance(
        body.get(response_key), (dict, list)
    ):
        raise RecoveryError("REST response is missing its data field")
    token = body.get("next_page_token")
    if token is None:
        return None
    if not isinstance(token, str) or not token:
        raise RecoveryError("REST pagination token is malformed")
    return token


def _fetch_https_page(
    url: str, headers: Mapping[str, str], timeout_seconds: float, max_bytes: int
) -> bytes:
    """Keep gap-recovery failures inside its documented error contract."""
    try:
        if not url.startswith("https://data.alpaca.markets/"):
            raise RecoveryError("REST host is not allowed")
        return bounded_alpaca_get(url, headers, timeout_seconds, max_bytes)
    except HttpTransportError as exc:
        raise RecoveryError(str(exc)) from exc
