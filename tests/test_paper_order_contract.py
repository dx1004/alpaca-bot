import json

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
