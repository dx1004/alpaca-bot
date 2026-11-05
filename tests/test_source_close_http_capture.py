"""Canonical memory HTTP GETs only; no source ownership or order replay."""

import base64
import json
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot import paper_order_http
from alpaca_ross_bot.crossed_stop_capture import SourceCloseCaptures
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import _json
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.source_close_http_capture import (
    AUTHORITY,
    KIND,
    ORIGIN,
    SOURCE,
    SourceCloseHttpCaptures,
    read_source_close_http_captures_at,
)
from test_risk_ledger_audit import ACCOUNT
from test_source_close_http import source_http as source_http_fixture
from test_source_close_http import submit

source_http = source_http_fixture


class MemoryHttpGets:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.order = snapshot
        self.lookup: dict[str, Any] | None = None
        self.account = PaperHttpResponse(200, _json(ACCOUNT))
        self.pages = [b"[]"]
        self.repeat = False
        self.calls: list[str] = []
        self.failure: Exception | PaperHttpResponse | None = None

    def __call__(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_bytes: int,
    ) -> PaperHttpResponse:
        assert method == "GET" and body is None
        self.calls.append(path)
        if path == "/v2/account":
            return self.account
        if self.failure is not None:
            if isinstance(self.failure, Exception):
                raise self.failure
            return self.failure
        if path.startswith("/v2/orders:by_client_order_id?"):
            assert parse_qs(urlsplit(path).query) == {
                "client_order_id": [self.order["client_order_id"]]
            }
            return PaperHttpResponse(200, _json(self.lookup or self.order))
        if path == f"/v2/orders/{self.order['id']}?nested=true":
            return PaperHttpResponse(200, _json(self.order))
        query = parse_qs(urlsplit(path).query)
        assert urlsplit(path).path == "/v2/account/activities"
        assert query["activity_types"] == ["FILL"]
        assert query["order_id"] == [self.order["id"]]
        assert query["direction"] == ["asc"] and query["page_size"] == ["100"]
        raw = self.pages[0]
        if not self.repeat and len(self.pages) > 1:
            self.pages.pop(0)
        return PaperHttpResponse(200, raw)


@pytest.fixture
def http_capture(source_http: Any) -> Any:
    owner, _, _, _, api, _, ready, now = source_http
    row = submit(source_http)
    assert row["outcome"]["result_kind"] == "HTTP_RESPONSE"
    snapshot = json.loads(base64.b64decode(row["outcome"]["body_base64"]))
    gets = MemoryHttpGets(snapshot)
    api._exchange = gets
    return SourceCloseHttpCaptures(owner), row, gets, source_http


def capture(case: Any, **changes: Any) -> dict[str, Any]:
    captures, row, _, fixture = case
    return cast(
        dict[str, Any],
        captures.capture(
            **(
                dict(
                    attempt_ref=row["attempt_ref"],
                    decision_at=fixture[7][0],
                    api=fixture[4],
                    clock=lambda: fixture[7][0],
                    max_body_bytes=10_000,
                    max_fill_pages=3,
                    page_size=100,
                )
                | changes
            )
        ),
    )


def fill(case: Any, **changes: Any) -> dict[str, Any]:
    _, _, gets, fixture = case
    return {
        "id": "source-http-capture-fill",
        "order_id": gets.order["id"],
        "activity_type": "FILL",
        "type": "fill",
        "symbol": gets.order["symbol"],
        "side": "sell",
        "qty": "0.25",
        "cum_qty": "0.25",
        "leaves_qty": str(Decimal(gets.order["qty"]) - Decimal("0.25")),
        "price": "9.25",
        "transaction_time": fixture[7][0].isoformat(),
    } | changes


def test_http_partial_capture_reopen_preserves_raw_and_never_replays_order(
    http_capture: Any,
) -> None:
    captures, row, gets, fixture = http_capture
    source, risk, oco, emergency, _, _, _ = fixture[6]
    before = risk.view(), oco.view(), emergency.view()
    at = fixture[7][0]
    gets.order.update(
        status="partially_filled", filled_qty="0.25", filled_at=at.isoformat()
    )
    raw = b"  " + _json([fill(http_capture, optional_numeric=1.25)]) + b"\n"
    gets.pages = [raw, b"[]"]
    packet = capture(http_capture)
    assert packet["origin"] == ORIGIN == "INJECTED_EXCHANGE"
    assert packet["pin"]["attempt_origin"] == row["precall"]["origin"]
    assert packet["pin"]["outcome_ref"] == row["outcome_ref"]
    assessment = packet["assessment"]
    assert assessment["association"] == "HTTP_POST_GET_IDENTITY_CONSISTENT"
    assert assessment["fill_component_consistent"]
    assert assessment["fill_quantity"] == "1/4"
    assert base64.b64decode(packet["requests"][3]["body_base64"]) == raw
    assert packet["authority"] == assessment["authority"] == AUTHORITY
    assert all(value is False for value in AUTHORITY.values())
    assert (risk.view(), oco.view(), emergency.view()) == before
    assert not source.journal.has_order_send_attempt(gets.order["client_order_id"])
    calls = list(gets.calls)
    assert submit(fixture) == row
    assert gets.calls == calls
    assert captures.view() == [packet]
    other = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:
        assert read_source_close_http_captures_at(
            other,
            oco.initial,
            through_sequence=packet["capture_ref"]["sequence"],
            decision_at=at,
        ) == [packet]
    finally:
        other.close()


def test_http_capture_rejects_wrong_transport_account_and_mid_read_switch(
    http_capture: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captures, _, gets, fixture = http_capture
    api = fixture[4]
    with pytest.raises(JournalError, match="canonical source HTTP owner"):
        SourceCloseHttpCaptures(cast(Any, object()))
    with pytest.raises(JournalError, match="canonical source attempts"):
        SourceCloseCaptures(fixture[0])
    with pytest.raises(TypeError):
        capture(http_capture, get=gets)
    assert gets.calls == []
    original_ref = api._paper_account_ref
    api._paper_account_ref = "sha256:wrong-configured-account"
    with pytest.raises(JournalError, match="configured account"):
        capture(http_capture)
    assert gets.calls == []
    api._paper_account_ref = original_ref
    invoked: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        invoked.append("forbidden")
        raise AssertionError("builtin transport must never execute")

    monkeypatch.setattr(paper_order_http, "_exchange_paper_https", forbidden)
    api._exchange = forbidden
    with pytest.raises(JournalError, match="origin"):
        capture(http_capture)
    assert invoked == [] and gets.calls == []
    api._exchange = gets
    gets.account = PaperHttpResponse(
        200, _json(ACCOUNT | {"id": "99999999-9999-4999-8999-999999999999"})
    )
    packet = capture(http_capture)
    assert packet["assessment"]["issues"] == ["ACCOUNT_NOT_MATCHED"]
    assert gets.calls == ["/v2/account"]
    gets.account = PaperHttpResponse(200, _json(ACCOUNT))
    gets.calls.clear()

    def switch(*args: Any) -> PaperHttpResponse:
        response = gets(*args)
        api._exchange = forbidden
        return cast(PaperHttpResponse, response)

    api._exchange = switch
    with pytest.raises(JournalError, match="origin"):
        capture(http_capture)
    assert gets.calls == ["/v2/account"] and invoked == []
    assert captures.view() == [packet]


def test_http_capture_raw_failures_malformed_identity_and_page_bounds(
    http_capture: Any,
) -> None:
    captures, _, gets, fixture = http_capture
    packets = []
    gets.lookup = gets.order | {"id": "not-a-uuid"}
    packets.append(capture(http_capture))
    assert len(gets.calls) == 2
    assert packets[-1]["assessment"]["issues"] == ["CID_LOOKUP_NOT_MATCHED"]
    gets.lookup = None
    gets.calls.clear()
    gets.failure = TimeoutError("do-not-retain-secret-text")
    packets.append(capture(http_capture))
    assert packets[-1]["requests"][1]["error"] == "CALLBACK_EXCEPTION"
    assert b"do-not-retain-secret-text" not in _json(packets[-1])
    gets.failure = PaperHttpResponse(200, b"x" * 1001)
    packets.append(capture(http_capture, max_body_bytes=1000))
    assert packets[-1]["requests"][1]["error"] == "BODY_LIMIT_EXCEEDED_PREFIX_ONLY"
    assert len(base64.b64decode(packets[-1]["requests"][1]["body_base64"])) == 1000
    gets.failure = None
    gets.order.update(
        status="partially_filled",
        filled_qty="0.25",
        filled_at=fixture[7][0].isoformat(),
    )
    gets.pages, gets.repeat = [_json([fill(http_capture)])], True
    packets.append(capture(http_capture, max_fill_pages=1))
    assert "FILL_PAGE_BUDGET_TRUNCATED" in packets[-1]["assessment"]["issues"]
    packets.append(capture(http_capture))
    assert "FILL_CURSOR_INVALID_OR_LOOP" in packets[-1]["assessment"]["issues"]
    assert all(not row["assessment"]["fill_component_consistent"] for row in packets)
    assert captures.view() == packets


@pytest.mark.parametrize("field", ["pin", "origin"])
def test_http_capture_strict_reference_retag_and_prefix(
    http_capture: Any,
    field: str,
) -> None:
    captures, row, gets, fixture = http_capture
    with pytest.raises(JournalError):
        capture(http_capture, attempt_ref=row["attempt_ref"] | {"hash": "sha256:wrong"})
    assert gets.calls == []
    packet = capture(http_capture)
    forged = {key: value for key, value in packet.items() if key != "capture_ref"}
    forged = json.loads(_json(forged))
    if field == "pin":
        forged["pin"]["request_sha256"] = "sha256:wrong"
    else:
        forged["origin"] = "INJECTED_GET_CALLBACK_NOT_BROKER_PROOF"
    captures.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=_json(forged),
        system_first_seen_at=fixture[7][0],
        processed_at=fixture[7][0],
    )
    with pytest.raises(JournalError):
        captures.view()
    assert captures.read_verified_at(
        packet["capture_ref"]["sequence"], fixture[7][0]
    ) == [packet]


@pytest.mark.parametrize("kind", ["unknown", "missing"])
def test_http_capture_unknown_and_late_outcome_keep_original_association(
    source_http: Any,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    owner, _, kwargs, _, api, _, ready, now = source_http
    pending = []
    if kind == "missing":
        original = owner._append

        def append(item: Any, clock: Any, prepare: Any) -> Any:
            if item.get("kind") == "OUTCOME":
                pending.append((item, prepare))
                raise OSError("synthetic interrupted OUTCOME append")
            return original(item, clock, prepare)

        monkeypatch.setattr(owner, "_append", append)
        with pytest.raises(OSError):
            submit(source_http)
        monkeypatch.setattr(owner, "_append", original)
    else:
        exchange = api._exchange

        def unknown(method: str, *args: Any) -> Any:
            if method == "POST":
                raise TimeoutError("unlogged private failure")
            return exchange(method, *args)

        api._exchange = unknown
        submit(source_http)
    row = owner.view()[0]
    snapshot = json.loads(row["precall"]["request_body"]) | {
        "id": "88888888-8888-4888-8888-888888888888",
        "order_class": "simple",
        "asset_class": "us_equity",
        "extended_hours": False,
        "status": "new",
        "filled_qty": "0",
        "created_at": now[0].isoformat(),
        "submitted_at": now[0].isoformat(),
        "updated_at": now[0].isoformat(),
    }
    gets = MemoryHttpGets(snapshot)
    api._exchange = gets
    case = SourceCloseHttpCaptures(owner), row, gets, source_http
    packet = capture(case)
    assert packet["assessment"]["association"] == "GET_CANDIDATE_ONLY"
    assert packet["assessment"]["post_reported_order_id"] is None
    assert packet["authority"] == AUTHORITY
    if kind == "missing":
        item, prepare = pending[0]
        now[0] += timedelta(seconds=1)
        original(item, lambda: now[0], prepare)
        assert owner.view()[0]["outcome"]["result_kind"] == "HTTP_RESPONSE"
    assert case[0].view() == [packet]
    assert case[0].read_verified_at(packet["capture_ref"]["sequence"], ready[4]) == [
        packet
    ]
    assert not owner.journal.has_order_send_attempt(kwargs["client_order_id"])
