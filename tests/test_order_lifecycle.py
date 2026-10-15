import json
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from alpaca_ross_bot.order_lifecycle import (
    LifecycleState,
    OrderObservation,
    OrderProjection,
    parse_order_observation,
    reduce_order,
    signed_positions,
    timestamp_ns,
)
from alpaca_ross_bot.paper_order_contract import PaperContractError

REQUEST = {
    "client_order_id": "client-1",
    "symbol": "AAPL",
    "side": "buy",
    "type": "limit",
    "time_in_force": "day",
    "qty": "1",
    "limit_price": "1.00",
}
ORDER = REQUEST | {
    "id": "22222222-2222-4222-8222-222222222222",
    "status": "new",
    "filled_qty": "0",
    "asset_class": "us_equity",
    "order_class": "simple",
    "extended_hours": False,
    "updated_at": "2026-10-15T13:30:00.123456789Z",
}


def observation(
    status: str = "new", filled: str = "0", tick: int = 0, source: str = "WS"
) -> OrderObservation:
    stamp = f"2026-10-15T13:30:00.{123456789 + tick:09d}Z"
    return parse_order_observation(
        json.dumps(REQUEST),
        json.dumps(ORDER | {"status": status, "filled_qty": filled}).encode(),
        "account-1",
        source,
        "evidence-" + str(tick),
        stamp,
    )


@pytest.mark.parametrize(
    ("status", "filled", "expected"),
    [
        ("accepted", "0", LifecycleState.ACCEPTED),
        ("new", "0", LifecycleState.ACCEPTED),
        ("rejected", "0", LifecycleState.REJECTED),
        ("partially_filled", "0.125", LifecycleState.PARTIAL),
        ("filled", "1", LifecycleState.FILLED),
        ("pending_cancel", "0.125", LifecycleState.CANCEL_PENDING),
        ("canceled", "0.125", LifecycleState.CANCELED),
        ("expired", "0.125", LifecycleState.EXPIRED),
        ("done_for_day", "0.125", LifecycleState.UNKNOWN),
        ("pending_new", "0", LifecycleState.UNKNOWN),
        ("replaced", "0", LifecycleState.UNKNOWN),
        ("calculated", "0", LifecycleState.UNKNOWN),
    ],
)
def test_documented_normalization_is_narrow(
    status: str, filled: str, expected: LifecycleState
) -> None:
    projected = reduce_order(None, observation(status, filled))
    assert projected.state == expected
    assert projected.filled_quantity == Decimal(filled)
    assert bool(projected.issues) == (expected == LifecycleState.UNKNOWN)


def test_partial_cancel_retains_cumulative_fills_and_does_not_sum_snapshots() -> None:
    first = reduce_order(None, observation("partially_filled", "0.1"))
    second = reduce_order(first, observation("partially_filled", "0.3", 1))
    canceled = reduce_order(second, observation("canceled", "0.3", 2))
    assert canceled.state == LifecycleState.CANCELED
    assert canceled.filled_quantity == Decimal("0.3")
    assert signed_positions([canceled]) == {"AAPL": Decimal("0.3")}


def test_duplicate_and_stale_updates_are_idempotent() -> None:
    older = observation("partially_filled", "0.1")
    latest = observation("partially_filled", "0.3", 2)
    projected = reduce_order(reduce_order(None, older), latest)
    assert reduce_order(projected, latest) == projected
    assert reduce_order(projected, older) == projected


@pytest.mark.parametrize("reverse", [False, True])
def test_fill_cancel_race_is_explicit_without_a_terminal_winner(reverse: bool) -> None:
    canceled = observation("canceled", "0.3", 1)
    filled = observation("filled", "1", 2)
    first, second = (filled, canceled) if reverse else (canceled, filled)
    result = reduce_order(reduce_order(None, first), second)
    assert result.state == LifecycleState.UNKNOWN
    assert result.filled_quantity == Decimal("1")
    assert "terminal_chronology_conflict" in result.issues
    assert result.raw_status == "filled"


@pytest.mark.parametrize("reverse", [False, True])
def test_cumulative_regression_is_sticky_and_preserves_greatest_fills(
    reverse: bool,
) -> None:
    more = observation("partially_filled", "0.5")
    newer_less = observation("partially_filled", "0.3", 2)
    first, second = (newer_less, more) if reverse else (more, newer_less)
    projected = reduce_order(reduce_order(None, first), second)
    assert projected.state == LifecycleState.UNKNOWN
    assert projected.filled_quantity == Decimal("0.5")
    assert "cumulative_regression" in projected.issues
    fresh = observation("filled", "1", 3, "REST")
    recovered = reduce_order(projected, fresh, authoritative=True)
    assert recovered.state == LifecycleState.UNKNOWN
    assert recovered.filled_quantity == Decimal("1")
    assert recovered.issues == ("cumulative_regression",)


@pytest.mark.parametrize(
    "field, value",
    [("account_ref", "other"), ("symbol", "MSFT"), ("side", "sell")],
)
def test_observation_identity_conflict_cannot_be_cleared(
    field: str, value: str
) -> None:
    first = observation("partially_filled", "0.3")
    projected = reduce_order(None, first)
    incoming = observation("filled", "1", 1)
    if field == "account_ref":
        conflict = replace(incoming, account_ref=value)
    elif field == "symbol":
        conflict = replace(incoming, symbol=value)
    else:
        assert field == "side"
        conflict = replace(incoming, side=value)
    bad = reduce_order(projected, conflict)
    assert bad.state == LifecycleState.UNKNOWN
    assert bad.filled_quantity == Decimal("0.3")
    recovered = reduce_order(
        bad, observation("filled", "1", 2, "REST"), authoritative=True
    )
    assert recovered.state == LifecycleState.UNKNOWN
    assert "identity_conflict:" + field in recovered.issues


@pytest.mark.parametrize("field", ["client_order_id", "broker_order_id", "quantity"])
def test_snapshot_identity_conflict_remains_explicit(field: str) -> None:
    first = observation()
    new = observation(tick=1)
    if field == "quantity":
        snapshot = replace(new.snapshot, quantity=Decimal("2"))
    elif field == "client_order_id":
        snapshot = replace(new.snapshot, client_order_id="different")
    else:
        assert field == "broker_order_id"
        snapshot = replace(new.snapshot, broker_order_id="different")
    conflict = replace(new, snapshot=snapshot)
    result = reduce_order(reduce_order(None, first), conflict)
    assert result.state == LifecycleState.UNKNOWN
    assert "identity_conflict:" + field in result.issues


def test_fresh_authoritative_rest_resolves_only_recoverable_conflicts() -> None:
    canceled = reduce_order(None, observation("canceled", "0.3"))
    conflicted = reduce_order(canceled, observation("filled", "1", 2))
    with pytest.raises(ValueError, match="requires REST"):
        reduce_order(conflicted, observation("filled", "1", 3), authoritative=True)
    stale = reduce_order(
        conflicted, observation("canceled", "0.3", 1, "REST"), authoritative=True
    )
    assert stale.state == LifecycleState.UNKNOWN
    fresh = reduce_order(
        conflicted, observation("filled", "1", 3, "REST"), authoritative=True
    )
    assert fresh.state == LifecycleState.FILLED
    assert fresh.issues == ()


def test_equal_nanosecond_conflict_needs_rest_recovery() -> None:
    original = reduce_order(None, observation("partially_filled", "0.3"))
    conflict = reduce_order(original, observation("canceled", "0.3"))
    assert conflict.state == LifecycleState.UNKNOWN
    assert "same_time_conflict" in conflict.issues
    recovered = reduce_order(
        conflict, observation("canceled", "0.3", source="REST"), authoritative=True
    )
    assert recovered.state == LifecycleState.CANCELED


def test_done_for_day_can_resume_but_requires_explicit_rest_resolution() -> None:
    paused = reduce_order(None, observation("done_for_day", "0.1"))
    later = reduce_order(paused, observation("partially_filled", "0.3", 2))
    assert later.state == LifecycleState.UNKNOWN
    assert "terminal_chronology_conflict" not in later.issues
    recovered = reduce_order(
        later, observation("partially_filled", "0.3", 3, "REST"), authoritative=True
    )
    assert recovered.state == LifecycleState.PARTIAL


def test_nanoseconds_are_exact_and_offset_equivalent() -> None:
    zero = timestamp_ns("1970-01-01T00:00:00Z")
    assert zero == 0
    assert timestamp_ns("1970-01-01T00:00:00.000000001Z") == 1
    assert timestamp_ns("1969-12-31T23:59:59.999999999Z") == -1
    assert timestamp_ns("1970-01-01T01:00:00.000000001+01:00") == 1
    assert timestamp_ns("1970-01-01T00:00:00.1Z") == 100_000_000
    early = observation("partially_filled", "0.1")
    later = observation("partially_filled", "0.2", 1)
    assert later.broker_time_ns - early.broker_time_ns == 1
    assert (
        reduce_order(reduce_order(None, early), later).state == LifecycleState.PARTIAL
    )


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-10-15T13:30:00",
        "2026-10-15T13:30:00.1234567890Z",
        "2026-02-30T13:30:00Z",
        "2026-10-15T13:30:60Z",
        "2026-10-15T13:30:00+24:00",
        "2026-10-15T13:30:00+01:60",
        "garbage",
    ],
)
def test_invalid_or_naive_timestamps_fail_closed(stamp: str) -> None:
    with pytest.raises(PaperContractError, match="timestamp"):
        timestamp_ns(stamp)


def test_parser_fallback_and_saved_identity_validation() -> None:
    parsed = parse_order_observation(
        json.dumps(REQUEST), json.dumps(ORDER).encode(), "a", "REST", "e"
    )
    assert parsed.broker_time_ns == timestamp_ns(str(ORDER["updated_at"]))
    for changes in ({"symbol": "MSFT"}, {"filled_qty": "NaN"}, {"updated_at": None}):
        with pytest.raises(PaperContractError):
            parse_order_observation(
                json.dumps(REQUEST),
                json.dumps(ORDER | changes).encode(),
                "a",
                "REST",
                "e",
            )


def test_json_roundtrip_preserves_decimals_nanoseconds_issues_and_restart() -> None:
    first = observation("partially_filled", "0.100000000000000001")
    second = observation("partially_filled", "0.200000000000000002", 2)
    before = reduce_order(None, first)
    restored = OrderProjection.from_dict(json.loads(json.dumps(before.to_dict())))
    assert restored == before
    assert reduce_order(restored, second) == reduce_order(before, second)
    conflicted = reduce_order(restored, observation("rejected", "0", 3))
    assert OrderProjection.from_dict(conflicted.to_dict()) == conflicted


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": 1.0},
        {"filled_quantity": "NaN"},
        {"filled_quantity": "2"},
        {"last_time_ns": True},
        {"side": "unknown"},
        {"state": "future"},
        {"issues": ["conflict"]},
        {"issues": ["x", "x"]},
        {"state": "FILLED"},
        {"state": "PARTIAL"},
        {"state": "UNKNOWN"},
        {"raw_status": "filled"},
    ],
)
def test_cached_projection_rejects_invalid_values(change: dict[str, object]) -> None:
    data = reduce_order(None, observation()).to_dict()
    with pytest.raises(PaperContractError):
        OrderProjection.from_dict(data | change)


def test_signed_positions_is_exact_and_does_not_claim_account_reconciliation() -> None:
    buy = reduce_order(None, observation("canceled", "0.3"))
    sell = replace(
        buy, client_order_id="client-2", side="sell", filled_quantity=Decimal("0.1")
    )
    unknown = replace(
        buy, client_order_id="client-3", state=LifecycleState.UNKNOWN, issues=("gap",)
    )
    assert signed_positions([buy, sell, unknown]) == {"AAPL": Decimal("0.5")}
    with pytest.raises(PaperContractError):
        signed_positions([buy, buy])
    with pytest.raises(PaperContractError):
        signed_positions([buy, replace(sell, account_ref="other")])


def test_signed_positions_ignores_ambient_decimal_rounding() -> None:
    buy = reduce_order(
        None, observation("partially_filled", "0.123456789123456789123456789")
    )
    sell = replace(
        buy,
        client_order_id="client-2",
        side="sell",
        filled_quantity=Decimal("0.000000000000000000000000001"),
    )
    with localcontext() as context:
        context.prec = 3
        result = signed_positions([buy, sell])
    assert result == {"AAPL": Decimal("0.123456789123456789123456788")}
