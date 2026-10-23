"""Synthetic finite native GET capture; no orders or ownership qualification."""

import base64
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_capture import capture_native_oco_group
from alpaca_ross_bot.native_oco_evidence import inspect_native_oco_capture
from alpaca_ross_bot.paper_order_http import PaperHttpResponse, PaperOrderHttp
from test_paper_order_http import ACCOUNT_BODY, ACCOUNT_REF, CONFIG, CREDENTIALS

NOW = datetime(2026, 10, 20, 15, tzinfo=UTC)
TARGET = "11111111-1111-4111-8111-111111111111"
STOP = "22222222-2222-4222-8222-222222222222"
GROUP = "33333333-3333-4333-8333-333333333333"


def _order() -> bytes:
    common: dict[str, object] = {
        "group_id": GROUP,
        "symbol": "AAPL",
        "side": "sell",
        "qty": "4",
        "filled_qty": "0",
        "filled_avg_price": None,
        "status": "new",
        "time_in_force": "day",
        "notional": None,
        "replaces": None,
        "replaced_by": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "submitted_at": NOW.isoformat(),
    }
    stop = common | {
        "id": STOP,
        "client_order_id": "different-stop-client-id",
        "group_role": "stop_loss",
        "order_class": "simple",
        "type": "stop",
        "stop_price": "9.50",
        "limit_price": None,
        "legs": None,
    }
    target = common | {
        "id": TARGET,
        "client_order_id": "native-target-client-id",
        "group_role": "take_profit",
        "order_class": "oco",
        "type": "limit",
        "limit_price": "12.00",
        "stop_price": None,
        "legs": [stop],
    }
    return json.dumps(target).encode()


class Exchange:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.account = PaperHttpResponse(200, ACCOUNT_BODY)
        self.order: PaperHttpResponse | Exception = PaperHttpResponse(200, _order())
        self.activities: dict[str, list[dict[str, object]]] = {}
        self.repeat_pages = False

    def __call__(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        assert method == "GET" and body is None
        assert set(headers) >= {"APCA-API-KEY-ID", "APCA-API-SECRET-KEY"}
        self.calls.append((method, path))
        if path == "/v2/account":
            return self.account
        if path == f"/v2/orders/{TARGET}?nested=true":
            if isinstance(self.order, Exception):
                raise self.order
            return self.order
        parsed = urlsplit(path)
        assert parsed.path == "/v2/account/activities"
        query = parse_qs(parsed.query)
        assert query["activity_types"] == ["FILL"]
        assert query["direction"] == ["asc"] and query["page_size"] == ["100"]
        rows = self.activities.get(query["order_id"][0], [])
        if "page_token" in query and not self.repeat_pages:
            rows = []
        return PaperHttpResponse(200, json.dumps(rows).encode())


def _api(exchange: Exchange) -> PaperOrderHttp:
    return PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        ACCOUNT_REF,
        timeout_seconds=2,
        max_request_bytes=1024,
        max_response_bytes=65_536,
        exchange=exchange,
    )


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    root = tmp_path / "private"
    store = Journal.open_private(root / "journal.sqlite3", root)
    store.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_BODY, NOW)
    return store


def _capture(
    store: Journal, exchange: Exchange, *, pages: int = 2
) -> tuple[int, dict[str, object]]:
    sequence = capture_native_oco_group(
        _api(exchange),
        store,
        account_ref=ACCOUNT_REF,
        broker_order_id=TARGET,
        clock=lambda: NOW,
        max_fill_pages=pages,
    )
    record = store.read_event(sequence)
    assert record is not None
    return sequence, json.loads(record[1])


def test_nested_group_capture_uses_only_get_and_keeps_private_source(
    journal: Journal,
) -> None:
    exchange = Exchange()
    before = journal.read_order_intent_ids()
    sequence, raw = _capture(journal, exchange)
    assert raw["schema"] == 2
    assert raw["transport_origin"] == "INJECTED_EXCHANGE"
    assert len(exchange.calls) == 4
    assert raw["requested_order_id"] == TARGET
    assert journal.read_order_intent_ids() == before == ()
    assert journal.read_paper_account_ref() == ACCOUNT_REF
    observed = inspect_native_oco_capture(
        journal,
        sequence,
        account_ref=ACCOUNT_REF,
        decision_at=NOW,
        max_age=timedelta(seconds=1),
    )
    assert observed.topology_observed and observed.accounting_complete
    assert len(observed.orders) == 2 and observed.fills == ()
    authority = observed.public_summary()["authority"]
    assert isinstance(authority, dict) and all(v is False for v in authority.values())
    assert all(method == "GET" for method, _ in exchange.calls)
    journal.close()


@pytest.mark.parametrize("status", [401, 403, 500])
def test_account_failure_is_retained_without_order_lookup(
    journal: Journal, status: int
) -> None:
    exchange = Exchange()
    exchange.account = PaperHttpResponse(status, b"account response")
    _, raw = _capture(journal, exchange)
    assert exchange.calls == [("GET", "/v2/account")]
    requests = raw["requests"]
    assert isinstance(requests, list) and len(requests) == 1
    assert requests[0]["status"] == status
    assert base64.b64decode(requests[0]["body"]) == b"account response"
    journal.close()


def test_foreign_account_is_not_adopted_or_followed(journal: Journal) -> None:
    exchange = Exchange()
    body = json.loads(ACCOUNT_BODY)
    body["id"] = STOP
    exchange.account = PaperHttpResponse(200, json.dumps(body).encode())
    _capture(journal, exchange)
    assert exchange.calls == [("GET", "/v2/account")]
    assert journal.read_paper_account_ref() == ACCOUNT_REF
    journal.close()


@pytest.mark.parametrize(
    "response",
    [PaperHttpResponse(404, b"missing"), PaperHttpResponse(200, b"invalid JSON")],
)
def test_failed_or_malformed_order_is_raw_evidence_not_an_empty_group(
    journal: Journal, response: PaperHttpResponse
) -> None:
    exchange = Exchange()
    exchange.order = response
    _, raw = _capture(journal, exchange)
    assert len(exchange.calls) == 2
    requests = raw["requests"]
    assert isinstance(requests, list)
    assert base64.b64decode(requests[1]["body"]) == response.body
    journal.close()


def test_timeout_is_not_rejection_and_exception_message_is_not_stored(
    journal: Journal,
) -> None:
    exchange = Exchange()
    exchange.order = TimeoutError("sensitive transport details")
    _, raw = _capture(journal, exchange)
    assert len(exchange.calls) == 2
    requests = raw["requests"]
    assert isinstance(requests, list)
    assert requests[1]["error"] == "PaperOrderHttpError"
    assert requests[1]["status"] is None and requests[1]["body"] is None
    assert "sensitive" not in json.dumps(raw)
    journal.close()


def test_activity_capture_follows_each_order_cursor_until_empty(
    journal: Journal,
) -> None:
    exchange = Exchange()
    exchange.activities[TARGET] = [
        {
            "id": "fill-first",
            "activity_type": "FILL",
            "order_id": TARGET,
            "symbol": "AAPL",
            "side": "sell",
            "qty": "1.25",
            "price": "12.00",
            "cum_qty": "999",
            "transaction_time": NOW.isoformat(),
        }
    ]
    _, raw = _capture(journal, exchange)
    assert len(exchange.calls) == 5
    assert "page_token=fill-first" in exchange.calls[3][1]
    assert STOP in exchange.calls[4][1]
    requests = raw["requests"]
    assert isinstance(requests, list) and len(requests) == 5
    journal.close()


@pytest.mark.parametrize("repeat", [False, True])
def test_page_budget_or_repeated_cursor_cannot_loop_forever(
    journal: Journal, repeat: bool
) -> None:
    exchange = Exchange()
    exchange.activities[TARGET] = [{"id": "same-cursor"}]
    exchange.repeat_pages = repeat
    _capture(journal, exchange, pages=2 if repeat else 1)
    assert len(exchange.calls) <= 5
    journal.close()


@pytest.mark.parametrize("pages", [0, -1, True])
def test_invalid_budget_fails_before_network(journal: Journal, pages: int) -> None:
    exchange = Exchange()
    with pytest.raises(ValueError, match="budget"):
        _capture(journal, exchange, pages=pages)
    assert exchange.calls == []
    journal.close()


def test_invalid_identity_fails_before_network(journal: Journal) -> None:
    exchange = Exchange()
    with pytest.raises(ValueError, match="UUID"):
        capture_native_oco_group(
            _api(exchange),
            journal,
            account_ref=ACCOUNT_REF,
            broker_order_id="bad/../../order",
            clock=lambda: NOW,
            max_fill_pages=2,
        )
    assert exchange.calls == []
    journal.close()


def test_persistence_failure_cannot_return_a_source_receipt(
    journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange = Exchange()
    before = journal.read_evidence_sequence()

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic native capture failure")

    monkeypatch.setattr(journal, "append_next", fail)
    with pytest.raises(OSError, match="capture failure"):
        _capture(journal, exchange)
    assert journal.read_evidence_sequence() == before
    journal.close()


def test_clock_rewind_fails_without_backdating_raw_evidence(journal: Journal) -> None:
    exchange = Exchange()
    stamps = iter((NOW, NOW - timedelta(microseconds=1)))
    with pytest.raises(JournalError, match="rewound"):
        capture_native_oco_group(
            _api(exchange),
            journal,
            account_ref=ACCOUNT_REF,
            broker_order_id=TARGET,
            clock=lambda: next(stamps),
            max_fill_pages=2,
        )
    assert exchange.calls == []
    journal.close()


def _append_capture(journal: Journal, payload: dict[str, object]) -> int:
    return journal.append_next(
        source_id="ALPACA_NATIVE_OCO",
        feed="paper",
        event_kind="NATIVE_OCO_CAPTURE",
        payload=json.dumps(payload).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    ).sequence


def test_legacy_capture_remains_observation_without_origin(journal: Journal) -> None:
    try:
        current_seq, payload = _capture(journal, Exchange())
        payload.pop("transport_origin")
        payload["schema"] = 1
        legacy_seq = _append_capture(journal, payload)
        current = inspect_native_oco_capture(
            journal,
            current_seq,
            account_ref=ACCOUNT_REF,
            decision_at=NOW,
            max_age=timedelta(seconds=1),
        )
        legacy = inspect_native_oco_capture(
            journal,
            legacy_seq,
            account_ref=ACCOUNT_REF,
            decision_at=NOW,
            max_age=timedelta(seconds=1),
        )
        assert current.orders == legacy.orders
        assert current.fills == legacy.fills
        assert current.accounting_complete is legacy.accounting_complete is True
        # Fact serialization stays compatible with existing schema-1 receipts;
        # an ownership binder must separately require the raw schema-2 origin.
        assert legacy.to_dict()["schema"] == 1
        assert "transport_origin" not in legacy.to_dict()
        assert not any(legacy.to_dict()["authority"].values())
    finally:
        journal.close()


@pytest.mark.parametrize("origin", [None, ["INJECTED_EXCHANGE"], "UNKNOWN"])
def test_unknown_capture_origin_is_not_qualified(
    journal: Journal, origin: object
) -> None:
    try:
        _, payload = _capture(journal, Exchange())
        payload["transport_origin"] = origin
        sequence = _append_capture(journal, payload)
        with pytest.raises(JournalError, match="transport origin"):
            inspect_native_oco_capture(
                journal,
                sequence,
                account_ref=ACCOUNT_REF,
                decision_at=NOW,
                max_age=timedelta(seconds=1),
            )
    finally:
        journal.close()


def test_default_https_capture_origin_with_patched_exchange_only(
    journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpaca_ross_bot import paper_order_http

    exchange = Exchange()
    monkeypatch.setattr(paper_order_http, "_exchange_paper_https", exchange)
    api = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        ACCOUNT_REF,
        timeout_seconds=2,
        max_request_bytes=1024,
        max_response_bytes=65_536,
    )
    try:
        sequence = capture_native_oco_group(
            api,
            journal,
            account_ref=ACCOUNT_REF,
            broker_order_id=TARGET,
            clock=lambda: NOW,
            max_fill_pages=2,
        )
        record = journal.read_event(sequence)
        assert record is not None
        payload = json.loads(record[1])
        assert payload["schema"] == 2
        assert payload["transport_origin"] == "BUILTIN_PAPER_HTTPS"
        assert len(exchange.calls) == 4
        assert all(method == "GET" for method, _ in exchange.calls)
    finally:
        journal.close()


def test_capture_hash_mismatch_blocks_interpretation(
    journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        sequence, _ = _capture(journal, Exchange())
        stored = journal.read_event(sequence)
        assert stored is not None
        envelope, raw = stored
        monkeypatch.setattr(
            journal,
            "read_event",
            lambda _sequence: (
                replace(envelope, payload_ref="sha256:" + "0" * 64),
                raw,
            ),
        )
        with pytest.raises(JournalError, match="payload hash differs"):
            inspect_native_oco_capture(
                journal,
                sequence,
                account_ref=ACCOUNT_REF,
                decision_at=NOW,
                max_age=timedelta(seconds=1),
            )
    finally:
        journal.close()
