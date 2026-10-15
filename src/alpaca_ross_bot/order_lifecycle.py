"""Pure lifecycle projections for the supported simple-equity paper contract.

Observations are cumulative snapshots, never incremental executions. This module
owns no transport, persistence, retry authority, or trading decisions.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum

from alpaca_ross_bot.paper_order_contract import (
    OrderSnapshot,
    PaperContractError,
    decode_broker_json,
    validate_order_snapshot,
)


class LifecycleState(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


_STATES = {
    "accepted": LifecycleState.ACCEPTED,
    "new": LifecycleState.ACCEPTED,
    "rejected": LifecycleState.REJECTED,
    "partially_filled": LifecycleState.PARTIAL,
    "filled": LifecycleState.FILLED,
    "pending_cancel": LifecycleState.CANCEL_PENDING,
    "canceled": LifecycleState.CANCELED,
    "expired": LifecycleState.EXPIRED,
}
_TERMINAL = frozenset({"rejected", "filled", "canceled", "expired"})
_TIMESTAMP = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def timestamp_ns(value: str) -> int:
    """Parse an offset-aware broker timestamp without losing nanoseconds."""
    match = _TIMESTAMP.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise PaperContractError("broker timestamp must include a valid UTC offset")
    offset = match[3]
    if offset != "Z" and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
        raise PaperContractError("broker timestamp offset is invalid")
    try:
        base = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
        elapsed = base.astimezone(UTC) - _EPOCH
    except ValueError as exc:
        raise PaperContractError("broker timestamp is invalid") from exc
    seconds = elapsed.days * 86_400 + elapsed.seconds
    return seconds * 1_000_000_000 + int((match[2] or "0").ljust(9, "0"))


@dataclass(frozen=True)
class OrderObservation:
    account_ref: str
    snapshot: OrderSnapshot
    symbol: str
    side: str
    broker_time_ns: int
    source: str
    evidence_ref: str


def parse_order_observation(
    request_body: str,
    raw_order: bytes,
    account_ref: str,
    source: str,
    evidence_ref: str,
    broker_timestamp: str | None = None,
) -> OrderObservation:
    """Validate saved-request identity before normalizing broker evidence."""
    snapshot = validate_order_snapshot(request_body, raw_order)
    order = decode_broker_json(raw_order)
    assert isinstance(order, dict)  # Established by validate_order_snapshot.
    stamp = order.get("updated_at") if broker_timestamp is None else broker_timestamp
    if not isinstance(stamp, str):
        raise PaperContractError("order observation is missing a broker timestamp")
    return OrderObservation(
        _text(account_ref),
        snapshot,
        _text(order["symbol"]),
        _text(order["side"]),
        timestamp_ns(stamp),
        _text(source),
        _text(evidence_ref),
    )


@dataclass(frozen=True)
class OrderProjection:
    account_ref: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    filled_quantity: Decimal
    state: LifecycleState
    raw_status: str
    last_time_ns: int
    evidence_ref: str
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return only JSON-safe values; quantities retain Decimal precision."""
        return {
            "account_ref": self.account_ref,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "filled_quantity": str(self.filled_quantity),
            "state": self.state.value,
            "raw_status": self.raw_status,
            "last_time_ns": self.last_time_ns,
            "evidence_ref": self.evidence_ref,
            "issues": list(self.issues),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "OrderProjection":
        """Reject malformed cached state rather than inventing missing fields."""
        required = {
            "account_ref",
            "client_order_id",
            "broker_order_id",
            "symbol",
            "side",
            "quantity",
            "filled_quantity",
            "state",
            "raw_status",
            "last_time_ns",
            "evidence_ref",
            "issues",
        }
        if set(value) != required:
            raise PaperContractError("lifecycle projection fields are invalid")
        issues = value["issues"]
        if (
            not isinstance(issues, list)
            or any(not isinstance(item, str) or not item.strip() for item in issues)
            or len(set(issues)) != len(issues)
        ):
            raise PaperContractError("lifecycle projection issues are invalid")
        try:
            state = LifecycleState(_text(value["state"]))
        except ValueError as exc:
            raise PaperContractError("lifecycle projection state is invalid") from exc
        quantity = _decimal(value["quantity"])
        filled = _decimal(value["filled_quantity"])
        if quantity <= 0 or not 0 <= filled <= quantity:
            raise PaperContractError("lifecycle projection quantities are invalid")
        side = _text(value["side"])
        stamp = value["last_time_ns"]
        if side not in ("buy", "sell") or type(stamp) is not int:
            raise PaperContractError(
                "lifecycle projection side or timestamp is invalid"
            )
        if issues and state != LifecycleState.UNKNOWN:
            raise PaperContractError("lifecycle projection hides unresolved issues")
        raw_status = _text(value["raw_status"])
        if not issues and (
            state == LifecycleState.UNKNOWN
            or state != _STATES.get(raw_status)
            or (state == LifecycleState.FILLED and filled != quantity)
            or (state == LifecycleState.PARTIAL and not 0 < filled < quantity)
        ):
            raise PaperContractError("lifecycle projection status is incoherent")
        return cls(
            account_ref=_text(value["account_ref"]),
            client_order_id=_text(value["client_order_id"]),
            broker_order_id=_text(value["broker_order_id"]),
            symbol=_text(value["symbol"]),
            side=side,
            quantity=quantity,
            filled_quantity=filled,
            state=state,
            raw_status=raw_status,
            last_time_ns=stamp,
            evidence_ref=_text(value["evidence_ref"]),
            issues=tuple(str(item) for item in issues),
        )


def reduce_order(
    previous: OrderProjection | None,
    observation: OrderObservation,
    *,
    authoritative: bool = False,
) -> OrderProjection:
    """Project cumulative evidence; only validated REST may resolve status gaps.

    The caller must establish REST completeness and freshness before setting
    authoritative. Identity conflicts and cumulative regressions remain sticky.
    """
    if authoritative and observation.source != "REST":
        raise ValueError("authoritative lifecycle recovery requires REST evidence")
    incoming = _project(observation)
    if previous is None:
        return incoming
    identity = (
        "account_ref",
        "client_order_id",
        "broker_order_id",
        "symbol",
        "side",
        "quantity",
    )
    conflicts = tuple(
        "identity_conflict:" + name
        for name in identity
        if getattr(previous, name) != getattr(incoming, name)
    )
    if conflicts:
        return replace(
            previous,
            state=LifecycleState.UNKNOWN,
            evidence_ref=incoming.evidence_ref,
            issues=tuple(sorted(set(previous.issues + conflicts))),
        )
    issues = set(previous.issues + incoming.issues)
    newer = incoming.last_time_ns > previous.last_time_ns
    older = incoming.last_time_ns < previous.last_time_ns
    regression = (newer and incoming.filled_quantity < previous.filled_quantity) or (
        older and incoming.filled_quantity > previous.filled_quantity
    )
    if regression:
        issues.add("cumulative_regression")
    can_recover = (
        authoritative
        and not older
        and incoming.filled_quantity >= previous.filled_quantity
        and incoming.state != LifecycleState.UNKNOWN
    )
    if can_recover:
        issues = {item for item in issues if _sticky(item)}
    else:
        earlier, later = (incoming, previous) if older else (previous, incoming)
        if earlier.raw_status in _TERMINAL and earlier.raw_status != later.raw_status:
            issues.add("terminal_chronology_conflict")
        if (
            not newer
            and not older
            and (
                previous.raw_status != incoming.raw_status
                or previous.filled_quantity != incoming.filled_quantity
            )
        ):
            issues.add("same_time_conflict")
    selected = incoming if newer or can_recover else previous
    return replace(
        selected,
        filled_quantity=max(previous.filled_quantity, incoming.filled_quantity),
        state=LifecycleState.UNKNOWN if issues else selected.state,
        issues=tuple(sorted(issues)),
    )


def signed_positions(projections: Iterable[OrderProjection]) -> dict[str, Decimal]:
    """Return net known fills, not proof of account positions or a zero baseline."""
    positions: dict[str, Decimal] = {}
    seen: set[str] = set()
    accounts: set[str] = set()
    for order in projections:
        accounts.add(order.account_ref)
        if len(accounts) != 1 or order.client_order_id in seen:
            raise PaperContractError("position projection needs one account and order")
        seen.add(order.client_order_id)
        signed = (
            order.filled_quantity
            if order.side == "buy"
            else order.filled_quantity.copy_negate()
        )
        positions[order.symbol] = _add_exact(
            positions.get(order.symbol, Decimal(0)), signed
        )
    return positions


def _project(observation: OrderObservation) -> OrderProjection:
    snapshot = observation.snapshot
    state = _STATES.get(snapshot.status, LifecycleState.UNKNOWN)
    return OrderProjection(
        account_ref=observation.account_ref,
        client_order_id=snapshot.client_order_id,
        broker_order_id=snapshot.broker_order_id,
        symbol=observation.symbol,
        side=observation.side,
        quantity=snapshot.quantity,
        filled_quantity=snapshot.filled_quantity,
        state=state,
        raw_status=snapshot.status,
        last_time_ns=observation.broker_time_ns,
        evidence_ref=observation.evidence_ref,
        issues=("unsupported_status:" + snapshot.status,)
        if state == LifecycleState.UNKNOWN
        else (),
    )


def _sticky(issue: str) -> bool:
    return issue == "cumulative_regression" or issue.startswith("identity_conflict:")


def _add_exact(left: Decimal, right: Decimal) -> Decimal:
    if not left:
        return right
    if not right:
        return left
    exponent = min(int(left.as_tuple().exponent), int(right.as_tuple().exponent))
    precision = max(left.adjusted(), right.adjusted()) - exponent + 2
    with localcontext(Context(prec=precision, Emax=MAX_EMAX, Emin=MIN_EMIN)):
        return left + right


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperContractError("lifecycle text evidence is invalid")
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise PaperContractError("lifecycle quantity must be an exact decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise PaperContractError("lifecycle quantity is invalid") from exc
    if not number.is_finite():
        raise PaperContractError("lifecycle quantity must be finite")
    return number
