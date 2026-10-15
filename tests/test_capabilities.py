from datetime import date

from alpaca_ross_bot.capabilities import (
    ProbeStatus,
    _stream_response,
    probe_rest,
    probe_streams,
)
from alpaca_ross_bot.config import PaperConfig

PAPER_VALUES: dict[str, object] = {
    "mode": "paper",
    "trading_base_url": "https://paper-api.alpaca.markets",
    "market_data_base_url": "https://data.alpaca.markets",
    "api_key_id_env": "ALPACA_PAPER_KEY_ID",
    "api_secret_key_env": "ALPACA_PAPER_SECRET_KEY",
}


def test_absent_credentials_block_all_requests() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)

    def unexpected_fetch(url: str, headers: object) -> tuple[int, bytes]:
        raise AssertionError(f"network used unexpectedly for {url}")

    report = probe_rest(
        config, {}, date(2026, 9, 28), connect=True, fetch=unexpected_fetch
    )
    assert all(
        check.status is ProbeStatus.BLOCKED_CREDENTIALS for check in report.checks
    )
    assert report.as_dict()["paper_trading_endpoint_validated"] is True
    assert "paper_trading_only" not in report.as_dict()
    assert not report.websocket_access_verified
    assert not report.subscription_limits_verified


def test_screener_only_probe_skips_other_capabilities_without_credentials() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    report = probe_rest(
        config,
        {},
        date(2026, 9, 28),
        connect=True,
        only_checks=frozenset({"screener_most_actives_sip", "screener_movers_sip"}),
    )
    assert [check.name for check in report.checks] == [
        "screener_most_actives_sip",
        "screener_movers_sip",
    ]
    assert all(
        check.status is ProbeStatus.BLOCKED_CREDENTIALS for check in report.checks
    )


def test_read_only_plan_and_report_redaction() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    visited: list[str] = []

    def fake_fetch(url: str, headers: object) -> tuple[int, bytes]:
        visited.append(url)
        if "feed=sip" in url:
            return 403, b""
        if url.endswith("/v2/account"):
            return (
                200,
                b'{"status":"ACTIVE","currency":"USD","cash":"500","equity":"500","buying_power":"500","multiplier":"1"}',
            )
        if "/v2/calendar?" in url:
            return 200, b"[]"
        if "/v2/assets/" in url:
            return 200, b'{"symbol":"AAPL","status":"active","tradable":true}'
        if "/v2/clock" in url:
            return (
                200,
                b'{"timestamp":"x","is_open":false,"next_open":"x","next_close":"x"}',
            )
        if "/news?" in url:
            return 200, b'{"news":[]}'
        if "/trades/" in url:
            return 200, b'{"trades":{}}'
        return 200, b'{"quotes":{}}'

    report = probe_rest(
        config,
        {"ALPACA_PAPER_KEY_ID": "secret-id", "ALPACA_PAPER_SECRET_KEY": "secret-key"},
        date(2026, 9, 28),
        connect=True,
        fetch=fake_fetch,
    )
    assert len(visited) == 11
    assert all(
        url.startswith((config.trading_base_url, config.market_data_base_url))
        for url in visited
    )
    assert all("/orders" not in url for url in visited)
    assert (
        next(
            check for check in report.checks if check.name == "latest_trade_sip"
        ).status
        is ProbeStatus.FORBIDDEN
    )
    assert all(
        check.status is ProbeStatus.REACHABLE_SCHEMA_UNVERIFIED
        and check.required_fields_present is None
        for check in report.checks
        if check.name.startswith("screener_")
    )
    assert not report.sip_equivalence_verified
    assert "secret" not in str(report.as_dict())
    assert "500" not in str(report.as_dict())
    assert report.as_dict()["paper_trading_endpoint_validated"] is True
    assert not report.sip_equivalence_verified


def test_sip_screener_probe_reports_forbidden_without_claiming_schema() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)

    def fake_fetch(url: str, headers: object) -> tuple[int, bytes]:
        if "/screener/" in url:
            return 403, b""
        if url.endswith("/v2/account"):
            return (
                200,
                b'{"status":"ACTIVE","currency":"USD","cash":"500","equity":"500","buying_power":"500","multiplier":"1"}',
            )
        if "/v2/calendar?" in url:
            return 200, b"[]"
        if "/v2/assets/" in url:
            return 200, b'{"symbol":"AAPL","status":"active","tradable":true}'
        if "/v2/clock" in url:
            return (
                200,
                b'{"timestamp":"x","is_open":false,"next_open":"x","next_close":"x"}',
            )
        if "/news?" in url:
            return 200, b'{"news":[]}'
        if "/trades/" in url:
            return 200, b'{"trades":{}}'
        return 200, b'{"quotes":{}}'

    report = probe_rest(
        config,
        {"ALPACA_PAPER_KEY_ID": "key", "ALPACA_PAPER_SECRET_KEY": "secret"},
        date(2026, 9, 28),
        connect=True,
        fetch=fake_fetch,
    )
    screeners = [check for check in report.checks if check.name.startswith("screener_")]
    assert len(screeners) == 2
    assert all(check.status is ProbeStatus.FORBIDDEN for check in screeners)
    assert not report.sip_equivalence_verified


def test_stream_probe_blocks_without_credentials_and_does_not_connect_by_default() -> (
    None
):
    config = PaperConfig.from_mapping(PAPER_VALUES)

    def unexpected_exchange(url: str, headers: object, channel: str) -> ProbeStatus:
        raise AssertionError(f"unexpected stream call: {url} {channel}")

    absent = probe_streams(config, {}, connect=True, exchange=unexpected_exchange)
    assert len(absent) == 3
    assert all(check.status is ProbeStatus.BLOCKED_CREDENTIALS for check in absent)
    present = probe_streams(
        config,
        {"ALPACA_PAPER_KEY_ID": "key", "ALPACA_PAPER_SECRET_KEY": "secret"},
        exchange=unexpected_exchange,
    )
    assert all(check.status is ProbeStatus.NOT_RUN for check in present)


def test_stream_probe_keeps_feed_entitlements_separate_and_redacted() -> None:
    config = PaperConfig.from_mapping(PAPER_VALUES)
    visited: list[tuple[str, str]] = []

    def fake_exchange(url: str, headers: object, channel: str) -> ProbeStatus:
        visited.append((url, channel))
        return ProbeStatus.FORBIDDEN if url.endswith("/sip") else ProbeStatus.REACHABLE

    results = probe_streams(
        config,
        {"ALPACA_PAPER_KEY_ID": "key", "ALPACA_PAPER_SECRET_KEY": "secret"},
        connect=True,
        exchange=fake_exchange,
    )
    assert [check.status for check in results] == [
        ProbeStatus.REACHABLE,
        ProbeStatus.FORBIDDEN,
        ProbeStatus.REACHABLE,
    ]
    assert [channel for _, channel in visited] == ["trades", "trades", "news"]
    assert "secret" not in str(results)


def test_stream_protocol_acknowledgment_is_required() -> None:
    assert _stream_response(
        '[{"T":"success","msg":"connected"}]', "success", "connected"
    )
    assert _stream_response(
        '[{"T":"subscription","news":["AAPL"]}]', "subscription", "news"
    )
    assert (
        _stream_response(
            '[{"T":"error","code":403,"msg":"forbidden"}]', "subscription", "news"
        )
        is ProbeStatus.FORBIDDEN
    )
    assert (
        _stream_response(
            '[{"T":"error","code":405,"msg":"symbol limit exceeded"}]',
            "subscription",
            "news",
        )
        is ProbeStatus.SYMBOL_LIMIT
    )
    assert (
        _stream_response(
            '[{"T":"error","code":406,"msg":"connection limit exceeded"}]',
            "subscription",
            "news",
        )
        is ProbeStatus.CONNECTION_LIMIT
    )
    assert (
        _stream_response(
            '[{"T":"error","code":409,"msg":"insufficient subscription"}]',
            "subscription",
            "trades",
        )
        is ProbeStatus.INSUFFICIENT_SUBSCRIPTION
    )
    assert (
        _stream_response('[{"T":"n","headline":"article"}]', "subscription", "news")
        is None
    )
