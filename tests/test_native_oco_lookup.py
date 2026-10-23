"""Raw native CID lookup remains separate from simple-order send provenance."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.paper_order_http import PaperHttpResponse, PaperOrderHttpError
from test_paper_order_http import FakeExchange, _http


@pytest.mark.parametrize("status", [200, 401, 404, 422, 500])
def test_native_lookup_retains_raw_outcome_without_nested_or_replay(
    status: int,
) -> None:
    response = PaperHttpResponse(status, b"synthetic raw evidence")
    exchange = FakeExchange(response)
    result = _http(exchange).read_native_oco_by_client_id(
        "native/id?name=x&space value"
    )
    assert result == response
    assert len(exchange.calls) == 1
    method, path, body, _, _ = exchange.calls[0]
    parsed = urlsplit(path)
    assert method == "GET" and body is None
    assert parsed.path == "/v2/orders:by_client_order_id"
    assert parse_qs(parsed.query) == {
        "client_order_id": ["native/id?name=x&space value"]
    }
    assert exchange.account_calls == 0


@pytest.mark.parametrize(
    "identity", [None, True, 1, "", " leading", "trailing ", "x" * 129]
)
def test_invalid_client_identity_never_reaches_transport(identity: Any) -> None:
    exchange = FakeExchange(PaperHttpResponse(200, b"{}"))
    with pytest.raises(ValueError):
        _http(exchange).read_native_oco_by_client_id(identity)
    assert exchange.calls == []


def test_native_lookup_accepts_the_pinned_maximum_identity_length() -> None:
    exchange = FakeExchange(PaperHttpResponse(200, b"{}"))
    _http(exchange).read_native_oco_by_client_id("x" * 128)
    assert len(exchange.calls) == 1


def test_timeout_is_unknown_and_never_automatically_retried() -> None:
    exchange = FakeExchange(TimeoutError("synthetic timeout"))
    with pytest.raises(PaperOrderHttpError):
        _http(exchange).read_native_oco_by_client_id("native-id")
    assert len(exchange.calls) == 1
