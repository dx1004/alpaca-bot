import json
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alpaca_ross_bot import paper_order_http
from alpaca_ross_bot.config import (
    ConfigError,
    PaperConfig,
    PaperCredentials,
    SecretValue,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import (
    PaperHttpResponse,
    PaperOrderHttp,
    PaperOrderHttpError,
)

CONFIG = PaperConfig(
    trading_base_url="https://paper-api.alpaca.markets",
    market_data_base_url="https://data.alpaca.markets",
    api_key_id_env="PAPER_KEY",
    api_secret_key_env="PAPER_SECRET",
)
CREDENTIALS = PaperCredentials(SecretValue("test-key"), SecretValue("test-secret"))
NOW = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
ACCOUNT_BODY = json.dumps(
    {
        "id": "11111111-1111-4111-8111-111111111111",
        "status": "ACTIVE",
        "currency": "USD",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }
).encode()
ACCOUNT_REF = paper_account_ref(ACCOUNT_BODY)


def _request_body(client_id: str) -> str:
    return json.dumps(
        {
            "client_order_id": client_id,
            "symbol": "AAPL",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class FakeExchange:
    def __init__(self, response: PaperHttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, str, bytes | None, float, int]] = []
        self.account_calls = 0
        self.account_body = ACCOUNT_BODY

    def __call__(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        assert headers["APCA-API-KEY-ID"] == "test-key"
        assert headers["APCA-API-SECRET-KEY"] == "test-secret"
        if path == "/v2/account":
            self.account_calls += 1
            return PaperHttpResponse(200, self.account_body)
        self.calls.append((method, path, body, timeout, cap))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _http(exchange: FakeExchange) -> PaperOrderHttp:
    return PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        ACCOUNT_REF,
        timeout_seconds=2,
        max_request_bytes=256,
        max_response_bytes=512,
        exchange=exchange,
    )


def _journal(tmp_path: Path) -> Journal:
    root = tmp_path / "var"
    path = root / "journal.sqlite3"
    journal = Journal(path, root)
    root.chmod(0o700)
    path.chmod(0o600)
    return journal


def _reserve(journal: Journal, client_id: str = "cid-1") -> None:
    journal.reserve_order_intent(
        client_id,
        {
            "paper_endpoint": CONFIG.trading_base_url,
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": _request_body(client_id),
        },
    )


def test_exact_paper_endpoint_and_positive_bounds_required() -> None:
    exchange = FakeExchange(PaperHttpResponse(200, b"{}"))
    with pytest.raises(ConfigError, match="paper endpoint"):
        PaperOrderHttp(
            replace(CONFIG, trading_base_url="https://api.alpaca.markets"),
            CREDENTIALS,
            "test-account",
            timeout_seconds=2,
            max_request_bytes=256,
            max_response_bytes=512,
            exchange=exchange,
        )
    with pytest.raises(ValueError, match="timeout"):
        PaperOrderHttp(
            CONFIG,
            CREDENTIALS,
            "test-account",
            timeout_seconds=0,
            max_request_bytes=256,
            max_response_bytes=512,
            exchange=exchange,
        )
    assert exchange.calls == []


def test_post_and_lookup_are_exactly_one_request_each(tmp_path: Path) -> None:
    exchange = FakeExchange(PaperHttpResponse(200, b'{"id":"fake"}'))
    http = _http(exchange)
    journal = _journal(tmp_path)
    _reserve(journal)
    assert http.post_reserved_order_once(journal, "cid-1", NOW).status == 200
    assert journal.has_order_send_attempt("cid-1")
    assert http.get_order_by_client_id(journal, "cid-1").status == 200
    assert exchange.calls == [
        ("POST", "/v2/orders", _request_body("cid-1").encode(), 2, 512),
        ("GET", "/v2/orders:by_client_order_id?client_order_id=cid-1", None, 2, 512),
    ]
    observations = journal.read_order_http_observations("cid-1")
    assert [
        (item.request_kind, item.result_kind, item.http_status, item.body)
        for item in observations
    ] == [
        ("POST", "HTTP_RESPONSE", 200, b'{"id":"fake"}'),
        ("GET", "HTTP_RESPONSE", 200, b'{"id":"fake"}'),
    ]
    journal.close()


def test_uncertain_status_and_transport_failure_do_not_retry(tmp_path: Path) -> None:
    response = FakeExchange(PaperHttpResponse(504, b"gateway timeout"))
    journal = _journal(tmp_path)
    _reserve(journal)
    assert _http(response).post_reserved_order_once(
        journal, "cid-1", NOW
    ) == PaperHttpResponse(504, b"gateway timeout")
    assert len(response.calls) == 1
    with pytest.raises(JournalError, match="already attempted"):
        _http(response).post_reserved_order_once(journal, "cid-1", NOW)
    assert len(response.calls) == 1
    assert journal.read_order_http_observations("cid-1")[0].http_status == 504

    failure = FakeExchange(TimeoutError("test timed out"))
    _reserve(journal, "cid-2")
    with pytest.raises(PaperOrderHttpError, match="outcome is unknown"):
        _http(failure).post_reserved_order_once(journal, "cid-2", NOW)
    assert len(failure.calls) == 1
    assert journal.has_order_send_attempt("cid-2")
    assert (
        journal.read_order_http_observations("cid-2")[0].result_kind
        == "TRANSPORT_UNKNOWN"
    )
    journal.close()


def test_lookup_404_is_evidence_without_resubmit_permission(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    post = FakeExchange(PaperHttpResponse(504, b"gateway timeout"))
    _http(post).post_reserved_order_once(journal, "cid-1", NOW)
    journal.close()
    journal = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    lookup = FakeExchange(PaperHttpResponse(404, b"not found"))
    assert _http(lookup).get_order_by_client_id(journal, "cid-1").status == 404
    assert [
        item.http_status for item in journal.read_order_http_observations("cid-1")
    ] == [504, 404]
    with pytest.raises(JournalError, match="already attempted"):
        _http(post).post_reserved_order_once(journal, "cid-1", NOW)
    assert len(post.calls) == 1
    journal.close()


def test_lookup_requires_matching_reserved_attempt(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    exchange = FakeExchange(PaperHttpResponse(200, b"{}"))
    with pytest.raises(PaperOrderHttpError, match="identity"):
        _http(exchange).get_order_by_client_id(journal, "cid-1")
    journal.mark_order_send_attempt("cid-1", NOW)
    other_account = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        "another-account",
        timeout_seconds=2,
        max_request_bytes=256,
        max_response_bytes=512,
        exchange=exchange,
    )
    with pytest.raises(PaperOrderHttpError, match="identity"):
        other_account.get_order_by_client_id(journal, "cid-1")
    assert exchange.calls == []
    journal.close()


def test_authenticated_account_change_blocks_post_before_send(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    exchange = FakeExchange(PaperHttpResponse(200, b"{}"))
    http = _http(exchange)
    http.verify_account(journal)
    changed = json.loads(ACCOUNT_BODY)
    changed["id"] = "33333333-3333-4333-8333-333333333333"
    exchange.account_body = json.dumps(changed).encode()
    with pytest.raises(PaperOrderHttpError, match="identity"):
        http.post_reserved_order_once(journal, "cid-1", NOW)
    assert not journal.has_order_send_attempt("cid-1")
    assert exchange.calls == []
    journal.close()


@pytest.mark.parametrize("post", [True, False])
def test_wrong_first_account_cannot_poison_reserved_journal(
    tmp_path: Path, post: bool
) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    exchange = FakeExchange(PaperHttpResponse(200, b"[]"))
    account = json.loads(ACCOUNT_BODY)
    account["id"] = "33333333-3333-4333-8333-333333333333"
    exchange.account_body = json.dumps(account).encode()
    http = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        timeout_seconds=2,
        max_request_bytes=256,
        max_response_bytes=512,
        exchange=exchange,
    )
    with pytest.raises((PaperOrderHttpError, JournalError)):
        if post:
            http.post_reserved_order_once(journal, "cid-1", NOW)
        else:
            http.audit_readonly_state(journal)
    assert journal.read_paper_account_ref() is None
    assert not journal.has_order_send_attempt("cid-1")
    exchange.account_body = ACCOUNT_BODY
    assert http.verify_account(journal) == ACCOUNT_REF
    journal.close()


def test_blocked_account_prevents_new_send_but_preserves_lookup(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    _reserve(journal, "existing-unknown")
    journal.mark_order_send_attempt("existing-unknown", NOW)
    exchange = FakeExchange(PaperHttpResponse(404, b"not found"))
    account = json.loads(ACCOUNT_BODY)
    account["trading_blocked"] = True
    exchange.account_body = json.dumps(account).encode()
    http = _http(exchange)
    with pytest.raises(PaperOrderHttpError, match="submission is blocked"):
        http.post_reserved_order_once(journal, "cid-1", NOW)
    assert not journal.has_order_send_attempt("cid-1")
    assert http.get_order_by_client_id(journal, "existing-unknown").status == 404
    assert journal.has_order_send_attempt("existing-unknown")
    assert all(call[0] == "GET" for call in exchange.calls)
    journal.close()


def test_wrong_account_reservation_cannot_block_bound_account_lookup(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    exchange = FakeExchange(PaperHttpResponse(404, b"not found"))
    http = _http(exchange)
    http.verify_account(journal)
    _reserve(journal)
    journal.mark_order_send_attempt("cid-1", NOW)
    with pytest.raises(JournalError, match="bound paper account"):
        journal.reserve_order_intent("other", {"paper_account_ref": "another-account"})
    assert journal.read_order_intent("other") is None
    assert http.get_order_by_client_id(journal, "cid-1").status == 404
    journal.close()


def test_empty_readonly_snapshots_do_not_clear_local_send(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    journal.mark_order_send_attempt("cid-1", NOW)
    exchange = FakeExchange(PaperHttpResponse(200, b"[]"))
    report = _http(exchange).audit_readonly_state(journal)
    assert report["paper_account_verified"] is True
    assert report["open_orders_snapshot_empty"] is True
    assert report["positions_snapshot_empty"] is True
    assert report["local_send_attempts_present"] is True
    assert report["snapshots_atomic"] is False
    assert report["trading_authorized"] is False
    assert all(call[0] == "GET" for call in exchange.calls)
    assert journal.has_order_send_attempt("cid-1")
    journal.close()


@pytest.mark.parametrize("matching", [True, False])
def test_lookup_snapshot_requires_saved_order_identity(
    tmp_path: Path, matching: bool
) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    journal.mark_order_send_attempt("cid-1", NOW)
    order = json.loads(_request_body("cid-1")) | {
        "id": "22222222-2222-4222-8222-222222222222",
        "asset_class": "us_equity",
        "order_class": "simple",
        "status": "partially_filled",
        "filled_qty": "0.5",
        "extended_hours": False,
    }
    if not matching:
        order["symbol"] = "MSFT"
    exchange = FakeExchange(PaperHttpResponse(200, json.dumps(order).encode()))
    snapshot = _http(exchange).reconcile_order_snapshot(journal, "cid-1")
    assert (snapshot is not None) is matching
    assert journal.has_order_send_attempt("cid-1")
    assert all(call[0] == "GET" for call in exchange.calls)
    journal.close()


def test_lookup_cannot_rebind_previously_acknowledged_broker_uuid(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    order = json.loads(_request_body("cid-1")) | {
        "id": "22222222-2222-4222-8222-222222222222",
        "asset_class": "us_equity",
        "order_class": "simple",
        "status": "new",
        "filled_qty": "0",
        "extended_hours": False,
    }
    exchange = FakeExchange(PaperHttpResponse(200, json.dumps(order).encode()))
    http = _http(exchange)
    http.post_reserved_order_once(journal, "cid-1", NOW)
    changed = order | {"id": "33333333-3333-4333-8333-333333333333"}
    exchange.response = PaperHttpResponse(200, json.dumps(changed).encode())
    assert http.reconcile_order_snapshot(journal, "cid-1") is None
    assert journal.has_order_send_attempt("cid-1")
    journal.close()


def test_result_persistence_failure_keeps_send_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = _journal(tmp_path)
    _reserve(journal)
    exchange = FakeExchange(PaperHttpResponse(200, b'{"id":"fake"}'))

    def fail_record(*_args: object) -> None:
        raise sqlite3.OperationalError("simulated disk failure")

    monkeypatch.setattr(journal, "record_order_http_observation", fail_record)
    with pytest.raises(sqlite3.OperationalError, match="disk failure"):
        _http(exchange).post_reserved_order_once(journal, "cid-1", NOW)
    assert len(exchange.calls) == 1
    assert journal.has_order_send_attempt("cid-1")
    journal.close()

    reopened = Journal(tmp_path / "var" / "journal.sqlite3", tmp_path / "var")
    with pytest.raises(JournalError, match="already attempted"):
        _http(exchange).post_reserved_order_once(reopened, "cid-1", NOW)
    assert len(exchange.calls) == 1
    reopened.close()


def test_request_and_response_caps_fail_closed(tmp_path: Path) -> None:
    exchange = FakeExchange(PaperHttpResponse(200, b"x" * 513))
    http = _http(exchange)
    journal = _journal(tmp_path)
    _reserve(journal)
    journal.reserve_order_intent(
        "too-large",
        {
            "paper_endpoint": CONFIG.trading_base_url,
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": "x" * 257,
        },
    )
    with pytest.raises(PaperOrderHttpError, match="cap"):
        http.post_reserved_order_once(journal, "too-large", NOW)
    assert exchange.calls == []
    with pytest.raises(PaperOrderHttpError, match="cap"):
        http.post_reserved_order_once(journal, "cid-1", NOW)
    assert len(exchange.calls) == 1
    assert journal.has_order_send_attempt("cid-1")
    assert not journal.has_order_send_attempt("too-large")
    assert (
        journal.read_order_http_observations("cid-1")[0].result_kind
        == "TRANSPORT_UNKNOWN"
    )
    journal.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("paper_endpoint", "https://api.alpaca.markets"),
        ("paper_account_ref", "another-account"),
        ("purpose", "HYBRID_STRATEGY"),
    ],
)
def test_wrong_intent_identity_never_marks_or_sends(
    tmp_path: Path, field: str, value: str
) -> None:
    exchange = FakeExchange(PaperHttpResponse(200, b"{}"))
    journal = _journal(tmp_path)
    intent = {
        "paper_endpoint": CONFIG.trading_base_url,
        "paper_account_ref": ACCOUNT_REF,
        "purpose": "ENGINEERING_TEST",
        "request_body": '{"client_order_id":"cid-1"}',
    }
    journal.reserve_order_intent("cid-1", intent | {field: value})
    with pytest.raises(PaperOrderHttpError, match="identity"):
        _http(exchange).post_reserved_order_once(journal, "cid-1", NOW)
    assert not journal.has_order_send_attempt("cid-1")
    assert exchange.calls == []
    journal.close()


def test_duplicate_json_key_cannot_change_client_id_after_validation(
    tmp_path: Path,
) -> None:
    exchange = FakeExchange(PaperHttpResponse(200, b"{}"))
    journal = _journal(tmp_path)
    journal.reserve_order_intent(
        "cid-1",
        {
            "paper_endpoint": CONFIG.trading_base_url,
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": '{"client_order_id":"cid-1","client_order_id":"cid-2"}',
        },
    )
    with pytest.raises(PaperOrderHttpError, match="canonical"):
        _http(exchange).post_reserved_order_once(journal, "cid-1", NOW)
    assert not journal.has_order_send_attempt("cid-1")
    assert exchange.calls == []
    journal.close()


def test_default_exchange_uses_only_paper_host(monkeypatch: pytest.MonkeyPatch) -> None:
    connections: list[object] = []

    class Socket:
        def settimeout(self, value: float) -> None:
            assert 0 < value <= 2

    class Response:
        status = 200

        def __init__(self) -> None:
            self.reads = 0

        def getheader(self, name: str) -> None:
            assert name == "Content-Length"
            return None

        def read1(self, size: int) -> bytes:
            self.reads += 1
            return b"{}" if self.reads == 1 else b""

    class Connection:
        def __init__(self, host: str, timeout: float) -> None:
            assert host == "paper-api.alpaca.markets"
            assert timeout == 2
            self.sock: Socket | None = None
            self.requests: list[tuple[str, str, bytes | None]] = []
            self.closed = False
            connections.append(self)

        def connect(self) -> None:
            self.sock = Socket()

        def request(
            self, method: str, path: str, body: bytes | None, headers: dict[str, str]
        ) -> None:
            assert headers["APCA-API-KEY-ID"] == "test-key"
            self.requests.append((method, path, body))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(paper_order_http, "HTTPSConnection", Connection)
    result = paper_order_http._exchange_paper_https(
        "POST", "/v2/orders", CREDENTIALS.auth_headers(), b"{}", 2, 512
    )
    assert result == PaperHttpResponse(200, b"{}")
    connection = connections[0]
    assert isinstance(connection, Connection)
    assert connection.requests == [("POST", "/v2/orders", b"{}")]
    assert connection.closed
