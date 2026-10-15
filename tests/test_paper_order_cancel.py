import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alpaca_ross_bot.config import PaperConfig, PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal, JournalError, OrderHttpObservation
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
NOW = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)
BROKER_ID = "22222222-2222-4222-8222-222222222222"
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


def _request(**changes: object) -> str:
    value: dict[str, object] = {
        "client_order_id": "entry-1",
        "symbol": "AAPL",
        "qty": "2",
        "side": "buy",
        "type": "limit",
        "limit_price": "1",
        "time_in_force": "day",
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _order(request: str, **changes: object) -> bytes:
    value = json.loads(request) | {
        "id": BROKER_ID,
        "asset_class": "us_equity",
        "order_class": "simple",
        "extended_hours": False,
        "status": "new",
        "filled_qty": "0",
        "updated_at": NOW.isoformat(),
    }
    value.update(changes)
    return json.dumps(value).encode()


class FakeExchange:
    def __init__(self, request: str) -> None:
        self.account = ACCOUNT_BODY
        self.lookup: PaperHttpResponse | Exception = PaperHttpResponse(
            200, _order(request)
        )
        self.cancel: PaperHttpResponse | Exception = PaperHttpResponse(204, b"")
        self.calls: list[tuple[str, str, bytes | None]] = []

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
        assert timeout == 2
        assert cap == 4096
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/v2/account"):
            return PaperHttpResponse(200, self.account)
        if (method, path) == (
            "GET",
            "/v2/orders:by_client_order_id?client_order_id=entry-1",
        ):
            result = self.lookup
        elif (method, path) == ("DELETE", "/v2/orders/" + BROKER_ID):
            result = self.cancel
        else:
            raise AssertionError("unexpected broker request")
        if isinstance(result, Exception):
            raise result
        return result


def _setup(
    tmp_path: Path, *, request: str | None = None, sent: bool = True
) -> tuple[Journal, PaperOrderHttp, FakeExchange]:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    body = request if request is not None else _request()
    journal.reserve_order_intent(
        "entry-1",
        {
            "paper_endpoint": CONFIG.trading_base_url,
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": body,
        },
    )
    if sent:
        journal.mark_order_send_attempt("entry-1", NOW)
        journal.record_order_http_observation(
            "entry-1",
            OrderHttpObservation("POST", "HTTP_RESPONSE", NOW, 200, _order(body)),
        )
    exchange = FakeExchange(body)
    http = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        ACCOUNT_REF,
        timeout_seconds=2,
        max_request_bytes=4096,
        max_response_bytes=4096,
        exchange=exchange,
    )
    return journal, http, exchange


@pytest.mark.parametrize(
    "status,filled", [("accepted", "0"), ("new", "0"), ("partially_filled", "1")]
)
def test_cancel_204_is_one_owned_request_without_terminal_authority(
    tmp_path: Path, status: str, filled: str
) -> None:
    journal, http, exchange = _setup(tmp_path)
    exchange.lookup = PaperHttpResponse(
        200, _order(_request(), status=status, filled_qty=filled)
    )
    assert http.cancel_engineering_entry_once(
        journal, "entry-1", NOW
    ) == PaperHttpResponse(204, b"")
    assert exchange.calls == [
        ("GET", "/v2/account", None),
        ("GET", "/v2/orders:by_client_order_id?client_order_id=entry-1", None),
        ("DELETE", "/v2/orders/" + BROKER_ID, None),
    ]
    assert journal.read_order_cancel_attempt("entry-1") == (BROKER_ID, NOW.isoformat())
    assert journal.has_order_send_attempt("entry-1")
    observations = journal.read_order_http_observations("entry-1")
    assert [item.request_kind for item in observations] == ["POST", "GET", "DELETE"]
    assert json.loads(observations[-2].body or b"{}")["status"] == status
    assert observations[-1].http_status == 204
    assert journal.read_order_events() == ()
    journal.close()
    root = tmp_path / "private"
    reopened = Journal.open_private(root / "journal.sqlite3", root)
    exchange.calls.clear()
    with pytest.raises(JournalError, match="already attempted"):
        http.cancel_engineering_entry_once(reopened, "entry-1", NOW)
    assert exchange.calls == []
    reopened.close()


@pytest.mark.parametrize(
    "result",
    [
        TimeoutError("lost DELETE reply"),
        PaperHttpResponse(422, b'{"message":"not cancelable"}'),
    ],
)
def test_uncertain_or_refused_cancel_preserves_marker_and_raw_evidence(
    tmp_path: Path, result: PaperHttpResponse | Exception
) -> None:
    journal, http, exchange = _setup(tmp_path)
    exchange.cancel = result
    if isinstance(result, Exception):
        with pytest.raises(PaperOrderHttpError, match="outcome is unknown"):
            http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    else:
        assert http.cancel_engineering_entry_once(journal, "entry-1", NOW) == result
    assert journal.has_order_cancel_attempt("entry-1")
    observation = journal.read_order_http_observations("entry-1")[-1]
    assert observation.request_kind == "DELETE"
    if isinstance(result, Exception):
        assert observation.result_kind == "TRANSPORT_UNKNOWN"
    else:
        assert (observation.http_status, observation.body) == (
            result.status,
            result.body,
        )
    calls_before = len(exchange.calls)
    with pytest.raises(JournalError, match="already attempted"):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert len(exchange.calls) == calls_before
    journal.close()


@pytest.mark.parametrize(
    "status",
    [
        "filled",
        "canceled",
        "expired",
        "rejected",
        "pending_cancel",
        "pending_new",
        "done_for_day",
    ],
)
def test_nonworking_lookup_never_requests_cancel(tmp_path: Path, status: str) -> None:
    journal, http, exchange = _setup(tmp_path)
    filled = "2" if status == "filled" else "0"
    exchange.lookup = PaperHttpResponse(
        200, _order(_request(), status=status, filled_qty=filled)
    )
    with pytest.raises(PaperOrderHttpError, match="requires a working"):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert not journal.has_order_cancel_attempt("entry-1")
    assert all(method == "GET" for method, _, _ in exchange.calls)
    journal.close()


@pytest.mark.parametrize(
    "response",
    [
        PaperHttpResponse(404, b"not found"),
        PaperHttpResponse(200, b"bad JSON"),
        PaperHttpResponse(200, _order(_request(), symbol="MSFT")),
        PaperHttpResponse(
            200, _order(_request(), id="33333333-3333-4333-8333-333333333333")
        ),
        PaperHttpResponse(200, _order(_request(), status="unrecognized")),
        TimeoutError("lookup timed out"),
    ],
)
def test_unknown_or_conflicting_lookup_never_requests_cancel(
    tmp_path: Path, response: PaperHttpResponse | Exception
) -> None:
    journal, http, exchange = _setup(tmp_path)
    exchange.lookup = response
    with pytest.raises(PaperOrderHttpError):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert not journal.has_order_cancel_attempt("entry-1")
    assert all(method == "GET" for method, _, _ in exchange.calls)
    journal.close()


def test_wrong_authenticated_account_blocks_cancel(tmp_path: Path) -> None:
    journal, http, exchange = _setup(tmp_path)
    exchange.account = json.dumps(
        json.loads(ACCOUNT_BODY) | {"id": "33333333-3333-4333-8333-333333333333"}
    ).encode()
    with pytest.raises(PaperOrderHttpError, match="identity differs"):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert exchange.calls == [("GET", "/v2/account", None)]
    assert not journal.has_order_cancel_attempt("entry-1")
    journal.close()


def test_blocked_same_account_retains_entry_cancellation_access(tmp_path: Path) -> None:
    journal, http, exchange = _setup(tmp_path)
    exchange.account = json.dumps(
        json.loads(ACCOUNT_BODY) | {"trading_blocked": True}
    ).encode()
    assert http.cancel_engineering_entry_once(journal, "entry-1", NOW).status == 204
    assert journal.has_order_cancel_attempt("entry-1")
    journal.close()


@pytest.mark.parametrize(
    "body,sent",
    [
        (_request(side="sell"), True),
        (_request(order_class="bracket"), True),
        (_request(type="stop", stop_price="1"), True),
        (_request() + " ", True),
        (_request(client_order_id="other"), True),
        (_request(), False),
    ],
)
def test_unsupported_or_unsent_entry_is_rejected_before_http(
    tmp_path: Path, body: str, sent: bool
) -> None:
    journal, http, exchange = _setup(tmp_path, request=body, sent=sent)
    with pytest.raises(PaperOrderHttpError):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert exchange.calls == []
    assert not journal.has_order_cancel_attempt("entry-1")
    journal.close()


def test_cancel_marker_write_failure_prevents_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, http, exchange = _setup(tmp_path)

    def fail_marker(*_args: object) -> None:
        raise OSError("cancel marker storage failed")

    monkeypatch.setattr(journal, "mark_order_cancel_attempt", fail_marker)
    with pytest.raises(OSError, match="marker storage"):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert all(method == "GET" for method, _, _ in exchange.calls)
    assert not journal.has_order_cancel_attempt("entry-1")
    journal.close()


def test_cancel_result_write_failure_keeps_marker_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, http, exchange = _setup(tmp_path)
    record = journal.record_order_http_observation

    def fail_result(client_id: str, observation: OrderHttpObservation) -> None:
        if observation.request_kind == "DELETE":
            raise OSError("cancel result storage failed")
        record(client_id, observation)

    monkeypatch.setattr(journal, "record_order_http_observation", fail_result)
    with pytest.raises(OSError, match="result storage"):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert [method for method, _, _ in exchange.calls].count("DELETE") == 1
    assert journal.has_order_cancel_attempt("entry-1")
    journal.close()
    root = tmp_path / "private"
    reopened = Journal.open_private(root / "journal.sqlite3", root)
    exchange.calls.clear()
    with pytest.raises(JournalError, match="already attempted"):
        http.cancel_engineering_entry_once(reopened, "entry-1", NOW)
    assert exchange.calls == []
    reopened.close()


def test_private_storage_gate_failure_prevents_all_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, http, exchange = _setup(tmp_path)

    def fail_private() -> None:
        raise JournalError("private state required")

    monkeypatch.setattr(journal, "require_private_order_state", fail_private)
    with pytest.raises(JournalError, match="private state"):
        http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    assert exchange.calls == []
    journal.close()


def test_cancel_marker_rejects_saved_request_for_another_client(tmp_path: Path) -> None:
    journal, _, exchange = _setup(tmp_path, request=_request(client_order_id="other"))
    journal.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_BODY, NOW)
    with pytest.raises(JournalError, match="client identity differs"):
        journal.mark_order_cancel_attempt("entry-1", BROKER_ID, NOW)
    assert not journal.has_order_cancel_attempt("entry-1")
    assert exchange.calls == []
    journal.close()


def test_cancel_marker_rejects_broker_uuid_reused_by_another_owned_intent(
    tmp_path: Path,
) -> None:
    journal, _, exchange = _setup(tmp_path)
    journal.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_BODY, NOW)
    other_body = _request(client_order_id="other")
    journal.reserve_order_intent(
        "other",
        {
            "paper_endpoint": CONFIG.trading_base_url,
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": other_body,
        },
    )
    journal.mark_order_send_attempt("other", NOW)
    journal.record_order_http_observation(
        "other",
        OrderHttpObservation("GET", "HTTP_RESPONSE", NOW, 200, _order(other_body)),
    )
    with pytest.raises(JournalError, match="ownership is ambiguous"):
        journal.mark_order_cancel_attempt("entry-1", BROKER_ID, NOW)
    assert not journal.has_order_cancel_attempt("entry-1")
    assert not journal.has_order_cancel_attempt("other")
    assert exchange.calls == []
    journal.close()


def test_cancel_markers_reject_database_update_and_delete(tmp_path: Path) -> None:
    journal, http, _ = _setup(tmp_path)
    http.cancel_engineering_entry_once(journal, "entry-1", NOW)
    journal.close()
    path = tmp_path / "private" / "journal.sqlite3"
    with sqlite3.connect(path) as connection:
        with pytest.raises(
            sqlite3.IntegrityError, match="cancel attempts are immutable"
        ):
            connection.execute("DELETE FROM order_cancel_attempts")
        with pytest.raises(
            sqlite3.IntegrityError, match="cancel attempts are immutable"
        ):
            connection.execute(
                "UPDATE order_cancel_attempts SET started_at='rewritten'"
            )
    root = path.parent
    reopened = Journal.open_private(path, root)
    assert reopened.read_order_cancel_attempt("entry-1") == (BROKER_ID, NOW.isoformat())
    reopened.close()


def test_delete_observation_requires_prior_cancel_marker(tmp_path: Path) -> None:
    journal, _, exchange = _setup(tmp_path)
    before = journal.read_order_http_observations("entry-1")
    with pytest.raises(JournalError, match="cancel attempt must precede DELETE"):
        journal.record_order_http_observation(
            "entry-1", OrderHttpObservation("DELETE", "HTTP_RESPONSE", NOW, 204, b"")
        )
    assert journal.read_order_http_observations("entry-1") == before
    assert exchange.calls == []
    journal.close()
