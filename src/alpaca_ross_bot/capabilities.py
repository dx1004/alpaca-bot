"""Read-only, redacted Alpaca REST capability checks.

Endpoint definitions come from https://docs.alpaca.markets/us/docs/authentication
and the linked Alpaca Trading and Market Data API references.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from websockets.exceptions import InvalidStatus, WebSocketException
from websockets.sync.client import connect as ws_connect

from alpaca_ross_bot.config import (
    PAPER_TRADING_URL,
    ConfigError,
    PaperConfig,
    PaperCredentials,
)

_MAX_BODY = 65_536


class ProbeStatus(StrEnum):
    BLOCKED_CREDENTIALS = "BLOCKED_CREDENTIALS"
    NOT_RUN = "NOT_RUN"
    REACHABLE = "REACHABLE"
    REACHABLE_SCHEMA_UNVERIFIED = "REACHABLE_SCHEMA_UNVERIFIED"
    FORBIDDEN = "FORBIDDEN"
    INSUFFICIENT_SUBSCRIPTION = "INSUFFICIENT_SUBSCRIPTION"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    SYMBOL_LIMIT = "SYMBOL_LIMIT"
    CONNECTION_LIMIT = "CONNECTION_LIMIT"
    UNAVAILABLE = "UNAVAILABLE"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: ProbeStatus
    required_fields_present: bool | None


@dataclass(frozen=True)
class CapabilityReport:
    as_of: date
    checks: tuple[ProbeResult, ...]
    stream_checks: tuple[ProbeResult, ...] = ()
    paper_trading_endpoint_validated: bool = False
    sip_equivalence_verified: bool = False
    websocket_access_verified: bool = False
    subscription_limits_verified: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "paper_trading_endpoint_validated": self.paper_trading_endpoint_validated,
            "sip_equivalence_verified": self.sip_equivalence_verified,
            "websocket_access_verified": self.websocket_access_verified,
            "subscription_limits_verified": self.subscription_limits_verified,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "required_fields_present": check.required_fields_present,
                }
                for check in self.checks
            ],
            "stream_checks": [
                {"name": check.name, "status": check.status.value}
                for check in self.stream_checks
            ],
        }


type Fetch = Callable[[str, Mapping[str, str]], tuple[int | None, bytes]]
type StreamExchange = Callable[[str, Mapping[str, str], str], ProbeStatus]


def _stream_exchange(url: str, headers: Mapping[str, str], channel: str) -> ProbeStatus:
    """Authenticate and confirm one symbol subscription; discard all data frames."""
    try:
        with ws_connect(
            url,
            additional_headers=headers,
            open_timeout=5,
            close_timeout=2,
            max_size=_MAX_BODY,
        ) as stream:
            for expected in ("connected", "authenticated"):
                response = _stream_response(stream.recv(timeout=5), "success", expected)
                if response is not True:
                    return (
                        response
                        if isinstance(response, ProbeStatus)
                        else ProbeStatus.MALFORMED
                    )
            stream.send(json.dumps({"action": "subscribe", channel: ["AAPL"]}))
            for _ in range(5):
                response = _stream_response(
                    stream.recv(timeout=5), "subscription", channel
                )
                if response is True:
                    return ProbeStatus.REACHABLE
                if isinstance(response, ProbeStatus):
                    return response
            return ProbeStatus.UNAVAILABLE
    except InvalidStatus as exc:
        if exc.response.status_code == 401:
            return ProbeStatus.UNAUTHORIZED
        if exc.response.status_code == 403:
            return ProbeStatus.FORBIDDEN
        if exc.response.status_code == 429:
            return ProbeStatus.RATE_LIMITED
        return ProbeStatus.UNAVAILABLE
    except (OSError, TimeoutError, WebSocketException, ValueError, UnicodeError):
        return ProbeStatus.UNAVAILABLE


def _stream_response(
    raw: str | bytes, expected_type: str, expected_value: str
) -> bool | ProbeStatus | None:
    try:
        messages = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict):
            return None
        if message.get("T") == "error":
            code = message.get("code")
            if type(code) is not int:
                return ProbeStatus.UNAVAILABLE
            return {
                401: ProbeStatus.UNAUTHORIZED,
                403: ProbeStatus.FORBIDDEN,
                405: ProbeStatus.SYMBOL_LIMIT,
                406: ProbeStatus.CONNECTION_LIMIT,
                409: ProbeStatus.INSUFFICIENT_SUBSCRIPTION,
                429: ProbeStatus.RATE_LIMITED,
            }.get(code, ProbeStatus.UNAVAILABLE)
        if message.get("T") == expected_type:
            value = (
                message.get("msg")
                if expected_type == "success"
                else message.get(expected_value)
            )
            if value == expected_value or value == ["AAPL"]:
                return True
    return None


def probe_streams(
    config: PaperConfig,
    environment: Mapping[str, str],
    *,
    connect: bool = False,
    exchange: StreamExchange = _stream_exchange,
) -> tuple[ProbeResult, ...]:
    """Test IEX, SIP, and symbol-scoped news access without retaining content."""
    endpoints = (
        ("market_stream_iex", "wss://stream.data.alpaca.markets/v2/iex", "trades"),
        ("market_stream_sip", "wss://stream.data.alpaca.markets/v2/sip", "trades"),
        ("news_stream", "wss://stream.data.alpaca.markets/v1beta1/news", "news"),
    )
    try:
        credentials = config.resolve_credentials(environment)
    except ConfigError:
        return tuple(
            ProbeResult(name, ProbeStatus.BLOCKED_CREDENTIALS, None)
            for name, _, _ in endpoints
        )
    if not connect:
        return tuple(
            ProbeResult(name, ProbeStatus.NOT_RUN, None) for name, _, _ in endpoints
        )
    headers = credentials.auth_headers()
    return tuple(
        ProbeResult(name, exchange(url, headers, channel), None)
        for name, url, channel in endpoints
    )


def _fetch(url: str, headers: Mapping[str, str]) -> tuple[int | None, bytes]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read(_MAX_BODY + 1)
    except HTTPError as exc:
        return exc.code, b""
    except (URLError, TimeoutError):
        return None, b""


def _checks(
    config: PaperConfig, as_of: date
) -> tuple[tuple[str, str, frozenset[str]], ...]:
    trading = config.trading_base_url
    data = config.market_data_base_url
    day = as_of.isoformat()
    return (
        (
            "paper_account",
            f"{trading}/v2/account",
            frozenset(
                {"status", "currency", "cash", "equity", "buying_power", "multiplier"}
            ),
        ),
        (
            "asset_sample_AAPL",
            f"{trading}/v2/assets/AAPL",
            frozenset({"symbol", "status", "tradable"}),
        ),
        (
            "market_clock",
            f"{trading}/v2/clock",
            frozenset({"timestamp", "is_open", "next_open", "next_close"}),
        ),
        (
            "market_calendar",
            f"{trading}/v2/calendar?start={day}&end={day}",
            frozenset(),
        ),
        (
            "latest_trade_iex",
            f"{data}/v2/stocks/trades/latest?symbols=AAPL&feed=iex",
            frozenset({"trades"}),
        ),
        (
            "latest_trade_sip",
            f"{data}/v2/stocks/trades/latest?symbols=AAPL&feed=sip",
            frozenset({"trades"}),
        ),
        (
            "latest_quote_iex",
            f"{data}/v2/stocks/quotes/latest?symbols=AAPL&feed=iex",
            frozenset({"quotes"}),
        ),
        (
            "latest_quote_sip",
            f"{data}/v2/stocks/quotes/latest?symbols=AAPL&feed=sip",
            frozenset({"quotes"}),
        ),
        (
            "news_rest",
            f"{data}/v1beta1/news?symbols=AAPL&limit=1",
            frozenset({"news"}),
        ),
        (
            "screener_most_actives_sip",
            f"{data}/v1beta1/screener/stocks/most-actives?by=volume&top=1",
            frozenset(),
        ),
        (
            "screener_movers_sip",
            f"{data}/v1beta1/screener/stocks/movers?top=1",
            frozenset(),
        ),
    )


def probe_rest(
    config: PaperConfig,
    environment: Mapping[str, str],
    as_of: date,
    *,
    connect: bool = False,
    fetch: Fetch = _fetch,
    only_checks: frozenset[str] | None = None,
) -> CapabilityReport:
    """Return only capability status; never expose account values or credentials."""
    all_checks = _checks(config, as_of)
    known_names = {name for name, _, _ in all_checks}
    if only_checks is not None and not only_checks <= known_names:
        raise ValueError("requested capability check name is unknown")
    plan = tuple(
        check for check in all_checks if only_checks is None or check[0] in only_checks
    )
    paper_endpoint_validated = config.trading_base_url == PAPER_TRADING_URL
    try:
        credentials: PaperCredentials = config.resolve_credentials(environment)
    except ConfigError:
        return CapabilityReport(
            as_of,
            tuple(
                ProbeResult(name, ProbeStatus.BLOCKED_CREDENTIALS, None)
                for name, _, _ in plan
            ),
            paper_trading_endpoint_validated=paper_endpoint_validated,
        )
    if not connect:
        return CapabilityReport(
            as_of,
            tuple(ProbeResult(name, ProbeStatus.NOT_RUN, None) for name, _, _ in plan),
            paper_trading_endpoint_validated=paper_endpoint_validated,
        )
    headers = credentials.auth_headers()
    checks = []
    for name, url, required_fields in plan:
        status_code, body = fetch(url, headers)
        if status_code == 401:
            result = ProbeResult(name, ProbeStatus.UNAUTHORIZED, None)
        elif status_code == 403:
            result = ProbeResult(name, ProbeStatus.FORBIDDEN, None)
        elif status_code == 429:
            result = ProbeResult(name, ProbeStatus.RATE_LIMITED, None)
        elif status_code == 400:
            result = ProbeResult(name, ProbeStatus.MALFORMED, False)
        elif status_code != 200:
            result = ProbeResult(name, ProbeStatus.UNAVAILABLE, None)
        elif len(body) > _MAX_BODY:
            result = ProbeResult(name, ProbeStatus.MALFORMED, False)
        else:
            try:
                decoded = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                result = ProbeResult(name, ProbeStatus.MALFORMED, False)
            else:
                if name.startswith("screener_"):
                    result = ProbeResult(
                        name, ProbeStatus.REACHABLE_SCHEMA_UNVERIFIED, None
                    )
                else:
                    shape_ok = (
                        isinstance(decoded, list)
                        if name == "market_calendar"
                        else isinstance(decoded, dict)
                        and required_fields <= decoded.keys()
                    )
                    result = ProbeResult(
                        name,
                        ProbeStatus.REACHABLE if shape_ok else ProbeStatus.MALFORMED,
                        shape_ok,
                    )
        checks.append(result)
    return CapabilityReport(
        as_of,
        tuple(checks),
        paper_trading_endpoint_validated=paper_endpoint_validated,
    )
