"""Strict account and simple-equity response contracts, not trading authority."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID


class PaperContractError(ValueError):
    """Broker evidence cannot establish the expected identity or shape."""


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PaperContractError("duplicate broker JSON field")
        result[key] = value
    return result


def decode_broker_json(raw: bytes | str) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_object, parse_float=Decimal)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PaperContractError("broker JSON is invalid") from exc


def paper_account_ref(raw: bytes) -> str:
    """Validate account identity independently of permission to open exposure."""
    account = decode_broker_json(raw)
    if not isinstance(account, dict):
        raise PaperContractError("paper account response is invalid")
    identity = account.get("id")
    try:
        if not isinstance(identity, str):
            raise ValueError
        normalized = str(UUID(identity))
    except ValueError as exc:
        raise PaperContractError("paper account UUID is invalid") from exc
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()


def require_submission_eligible_account(raw: bytes) -> None:
    """Block submission on missing/blocked account flags; keep reads available."""
    account = decode_broker_json(raw)
    if not isinstance(account, dict):
        raise PaperContractError("paper account response is invalid")
    if account.get("status") != "ACTIVE" or account.get("currency") != "USD":
        raise PaperContractError("paper account is not active in USD")
    for field in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        if account.get(field) is not False:
            raise PaperContractError("paper account trading flags are not clear")


@dataclass(frozen=True, repr=False)
class OrderSnapshot:
    broker_order_id: str
    client_order_id: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal


_STATUSES = frozenset(
    {
        "new",
        "partially_filled",
        "filled",
        "done_for_day",
        "canceled",
        "expired",
        "replaced",
        "pending_cancel",
        "pending_replace",
        "pending_review",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "stopped",
        "rejected",
        "suspended",
        "calculated",
        "held",
    }
)


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise PaperContractError("order numeric evidence is invalid")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise PaperContractError("order numeric evidence is invalid") from exc
    if not number.is_finite():
        raise PaperContractError("order numeric evidence is not finite")
    return number


def validate_saved_request(raw: str) -> dict[str, object]:
    """Accept only the explicitly supported simple-equity engineering shape."""
    intent = decode_broker_json(raw)
    required = {"client_order_id", "symbol", "qty", "side", "type", "time_in_force"}
    optional = {"limit_price", "stop_price", "order_class", "extended_hours"}
    if (
        not isinstance(intent, dict)
        or not required <= set(intent)
        or set(intent) - required - optional
    ):
        raise PaperContractError("engineering order request shape is unsupported")
    if (
        any(
            not isinstance(intent[field], str) or not str(intent[field]).strip()
            for field in ("client_order_id", "symbol")
        )
        or intent["side"] not in ("buy", "sell")
        or intent["type"] not in ("market", "limit", "stop")
        or (intent["type"] == "stop" and intent["side"] != "sell")
        or intent["time_in_force"] != "day"
        or intent.get("order_class", "simple") != "simple"
        or intent.get("extended_hours", False) is not False
        or _number(intent["qty"]) <= 0
    ):
        raise PaperContractError("engineering order request values are unsupported")
    if intent["type"] == "limit":
        if _number(intent.get("limit_price")) <= 0:
            raise PaperContractError("engineering limit price must be positive")
    elif "limit_price" in intent:
        raise PaperContractError(f"{intent['type']} request cannot specify limit price")
    if intent["type"] == "stop":
        price = _number(intent.get("stop_price"))
        if price <= 0:
            raise PaperContractError("engineering stop price must be positive")
        price_tuple = price.as_tuple()
        permitted_places = 2 if price >= 1 else 4
        excess_places = -int(price_tuple.exponent) - permitted_places
        if excess_places > 0 and any(price_tuple.digits[-excess_places:]):
            raise PaperContractError("engineering stop price is outside its tick grid")
    elif "stop_price" in intent:
        raise PaperContractError("non-stop request cannot specify stop price")
    return intent


def validate_order_snapshot(request_body: str, raw: bytes) -> OrderSnapshot:
    """Match a simple qty-based equity response to its immutable request.

    Complex/notional/replacement paths remain unsupported. A matched snapshot
    never establishes position reconciliation, cancellation finality or replay.
    """
    intent = validate_saved_request(request_body)
    order = decode_broker_json(raw)
    if not isinstance(intent, dict) or not isinstance(order, dict):
        raise PaperContractError("order evidence must be an object")
    for field in ("client_order_id", "symbol", "side", "type", "time_in_force"):
        value = intent.get(field)
        if not isinstance(value, str) or not value or order.get(field) != value:
            raise PaperContractError("order response does not match saved request")
    if (
        intent.get("notional") is not None
        or order.get("notional") is not None
        or intent.get("order_class", "simple") != "simple"
        or order.get("order_class") != "simple"
        or order.get("asset_class") != "us_equity"
        or order.get("extended_hours") is not False
        or order.get("legs") not in (None, [])
        or order.get("replaces") is not None
        or order.get("replaced_by") is not None
    ):
        raise PaperContractError("order response needs unsupported reconciliation")
    for field in ("limit_price", "stop_price"):
        if intent.get(field) is None:
            if order.get(field) is not None:
                raise PaperContractError("order price does not match saved request")
        elif _number(intent[field]) != _number(order.get(field)):
            raise PaperContractError("order price does not match saved request")
    quantity = _number(order.get("qty"))
    filled = _number(order.get("filled_qty"))
    if (
        quantity <= 0
        or quantity != _number(intent.get("qty"))
        or not 0 <= filled <= quantity
    ):
        raise PaperContractError("order quantity does not match saved request")
    status = order.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        raise PaperContractError("order status is unsupported")
    if (status == "filled" and filled != quantity) or (
        status == "partially_filled" and not 0 < filled < quantity
    ):
        raise PaperContractError("order status and filled quantity conflict")
    identity = order.get("id")
    try:
        if not isinstance(identity, str):
            raise ValueError
        identity = str(UUID(identity))
    except ValueError as exc:
        raise PaperContractError("broker order UUID is invalid") from exc
    return OrderSnapshot(
        identity, str(intent["client_order_id"]), status, quantity, filled
    )
