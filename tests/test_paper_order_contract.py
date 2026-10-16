import json
from decimal import Decimal, localcontext

import pytest

from alpaca_ross_bot.paper_order_contract import (
    PaperContractError,
    paper_account_ref,
    require_submission_eligible_account,
    validate_order_snapshot,
    validate_saved_request,
)

ACCOUNT = {
    "id": "11111111-1111-4111-8111-111111111111",
    "status": "ACTIVE",
    "currency": "USD",
    "trading_blocked": False,
    "account_blocked": False,
    "trade_suspended_by_user": False,
}
REQUEST = {
    "client_order_id": "test-1",
    "symbol": "AAPL",
    "side": "buy",
    "type": "limit",
    "time_in_force": "day",
    "qty": "1",
    "limit_price": "1.00",
}
ORDER = REQUEST | {
    "id": "22222222-2222-4222-8222-222222222222",
    "filled_qty": "0",
    "status": "new",
    "asset_class": "us_equity",
    "order_class": "simple",
    "extended_hours": False,
}
STOP_REQUEST = {
    key: value for key, value in REQUEST.items() if key != "limit_price"
} | {
    "client_order_id": "stop-1",
    "side": "sell",
    "type": "stop",
    "stop_price": "1.00",
}
STOP_ORDER = {
    key: value for key, value in ORDER.items() if key != "limit_price"
} | STOP_REQUEST


def test_account_flags_must_be_explicit_and_identity_is_opaque() -> None:
    reference = paper_account_ref(json.dumps(ACCOUNT).encode())
    assert reference.startswith("sha256:")
    assert str(ACCOUNT["id"]) not in reference
    for field in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        for value in (None, True, "false", 0):
            with pytest.raises(PaperContractError):
                require_submission_eligible_account(
                    json.dumps(ACCOUNT | {field: value}).encode()
                )


@pytest.mark.parametrize(
    "change",
    [
        {"client_order_id": "other"},
        {"symbol": "MSFT"},
        {"side": "sell"},
        {"qty": "2"},
        {"filled_qty": "1.1"},
        {"filled_qty": "NaN"},
        {"status": "new_future_status"},
        {"status": "filled", "filled_qty": "0"},
        {"status": "partially_filled", "filled_qty": "1"},
        {"limit_price": "2"},
        {"order_class": "bracket"},
        {"asset_class": "crypto"},
        {"extended_hours": True},
        {"replaced_by": "22222222-2222-4222-8222-222222222222"},
    ],
)
def test_mismatched_or_unsupported_order_evidence_is_rejected(
    change: dict[str, str],
) -> None:
    with pytest.raises(PaperContractError):
        validate_order_snapshot(
            json.dumps(REQUEST), json.dumps(ORDER | change).encode()
        )


def test_matching_order_snapshot_preserves_partial_fill() -> None:
    snapshot = validate_order_snapshot(
        json.dumps(REQUEST),
        json.dumps(
            ORDER | {"filled_qty": "0.5", "status": "partially_filled"}
        ).encode(),
    )
    assert str(snapshot.filled_quantity) == "0.5"
    assert snapshot.client_order_id == "test-1"


def test_duplicate_broker_fields_fail_closed() -> None:
    with pytest.raises(PaperContractError):
        paper_account_ref(b'{"id":"one","id":"two"}')


@pytest.mark.parametrize(
    "change",
    [
        {"qty": "0"},
        {"qty": "NaN"},
        {"qty": "-1"},
        {"notional": "5"},
        {"time_in_force": "gtc"},
        {"extended_hours": True},
        {"order_class": "bracket"},
    ],
)
def test_unsupported_engineering_requests_fail_closed(
    change: dict[str, object],
) -> None:
    with pytest.raises(PaperContractError):
        validate_saved_request(json.dumps(REQUEST | change))


@pytest.mark.parametrize(
    "price", ["1", "1.01", "1.0100", "123.45", "0.9999", "0.0001", "0.10000"]
)
def test_sell_stop_day_request_and_matching_response_on_numeric_grid(
    price: str,
) -> None:
    request = STOP_REQUEST | {"stop_price": price}
    assert validate_saved_request(json.dumps(request)) == request
    snapshot = validate_order_snapshot(
        json.dumps(request), json.dumps(STOP_ORDER | {"stop_price": price}).encode()
    )
    assert snapshot.client_order_id == "stop-1"
    assert snapshot.quantity == Decimal("1")
    assert snapshot.filled_quantity == 0


def test_sell_stop_grid_validation_does_not_round_under_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 1
        valid = STOP_REQUEST | {"stop_price": "12345678901234567890.01"}
        assert validate_saved_request(json.dumps(valid)) == valid
        for price in ("1.001", "0.00001", "12345678901234567890.001"):
            with pytest.raises(PaperContractError, match="tick grid"):
                validate_saved_request(json.dumps(STOP_REQUEST | {"stop_price": price}))


def test_sell_stop_requires_stop_price() -> None:
    request = {key: value for key, value in STOP_REQUEST.items() if key != "stop_price"}
    with pytest.raises(PaperContractError):
        validate_saved_request(json.dumps(request))


@pytest.mark.parametrize(
    "change",
    [
        {"side": "buy"},
        {"type": "stop_limit", "limit_price": "0.99"},
        {"type": "trailing_stop"},
        {"time_in_force": "gtc"},
        {"extended_hours": True},
        {"order_class": "bracket"},
        {"order_class": "oco"},
        {"legs": []},
        {"replaces": "22222222-2222-4222-8222-222222222222"},
        {"limit_price": "1"},
        {"stop_price": None},
        {"stop_price": True},
        {"stop_price": "0"},
        {"stop_price": "-1"},
        {"stop_price": "NaN"},
        {"stop_price": "Infinity"},
        {"stop_price": "1.001"},
        {"stop_price": "0.99999"},
        {"stop_price": "0.00001"},
        {"qty": "0"},
        {"qty": "-1"},
        {"qty": "NaN"},
        {"qty": "Infinity"},
    ],
)
def test_sell_stop_unsupported_request_shapes_and_numbers_fail_closed(
    change: dict[str, object],
) -> None:
    with pytest.raises(PaperContractError):
        validate_saved_request(json.dumps(STOP_REQUEST | change))


@pytest.mark.parametrize("kind", ["market", "limit"])
def test_stop_price_is_forbidden_on_market_and_limit_requests(kind: str) -> None:
    request = REQUEST | {"type": kind, "stop_price": "1"}
    if kind == "market":
        request.pop("limit_price")
    with pytest.raises(PaperContractError, match="non-stop"):
        validate_saved_request(json.dumps(request))


@pytest.mark.parametrize(
    "change",
    [
        {"client_order_id": "other"},
        {"symbol": "MSFT"},
        {"side": "buy"},
        {"type": "market"},
        {"type": "stop_limit"},
        {"time_in_force": "gtc"},
        {"stop_price": "0.99"},
        {"stop_price": "1.001"},
        {"stop_price": None},
        {"stop_price": "NaN"},
        {"stop_price": "Infinity"},
        {"limit_price": "1"},
        {"qty": "2"},
        {"qty": "Infinity"},
        {"qty": "NaN"},
        {"filled_qty": "1.1"},
        {"filled_qty": "NaN"},
        {"status": "filled", "filled_qty": "0.5"},
        {"order_class": "oco"},
        {"asset_class": "crypto"},
        {"extended_hours": True},
        {"legs": [{"id": "33333333-3333-4333-8333-333333333333"}]},
        {"replaces": "33333333-3333-4333-8333-333333333333"},
        {"replaced_by": "33333333-3333-4333-8333-333333333333"},
        {"id": "not-a-uuid"},
    ],
)
def test_sell_stop_response_must_match_saved_standalone_request(
    change: dict[str, object],
) -> None:
    with pytest.raises(PaperContractError):
        validate_order_snapshot(
            json.dumps(STOP_REQUEST), json.dumps(STOP_ORDER | change).encode()
        )


def test_sell_stop_response_requires_stop_price_and_preserves_partial_fills() -> None:
    missing = {key: value for key, value in STOP_ORDER.items() if key != "stop_price"}
    with pytest.raises(PaperContractError):
        validate_order_snapshot(json.dumps(STOP_REQUEST), json.dumps(missing).encode())
    snapshot = validate_order_snapshot(
        json.dumps(STOP_REQUEST),
        json.dumps(
            STOP_ORDER | {"status": "partially_filled", "filled_qty": "0.5"}
        ).encode(),
    )
    assert snapshot.filled_quantity == Decimal("0.5")
