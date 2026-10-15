"""Synthetic account-wide recovery scenarios; never contact a broker."""

import base64
import json
import sqlite3
import stat
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError, OrderHttpObservation
from alpaca_ross_bot.order_reconciliation import (
    OrderReconciler,
    ReconciliationPolicy,
    RestBatch,
)
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import PaperHttpResponse

NOW = datetime(2026, 10, 15, 13, tzinfo=UTC)
ACCOUNT = {
    "id": "11111111-1111-4111-8111-111111111111",
    "status": "ACTIVE",
    "currency": "USD",
    "trading_blocked": False,
    "account_blocked": False,
    "trade_suspended_by_user": False,
}
ACCOUNT_RAW = json.dumps(ACCOUNT).encode()
ACCOUNT_REF = paper_account_ref(ACCOUNT_RAW)
BROKER_ID = "22222222-2222-4222-8222-222222222222"
OTHER_BROKER_ID = "33333333-3333-4333-8333-333333333333"
CID = "owned-1"
POLICY = ReconciliationPolicy(timedelta(seconds=30), timedelta(seconds=5), 4)


def _at(seconds: int = 0) -> datetime:
    return NOW + timedelta(seconds=seconds)


def _request(
    cid: str = CID, *, symbol: str = "AAPL", side: str = "buy"
) -> dict[str, object]:
    return {
        "client_order_id": cid,
        "symbol": symbol,
        "qty": "10",
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }


def _reserve(
    journal: Journal, cid: str = CID, *, side: str = "buy", sent: bool = True
) -> None:
    journal.reserve_order_intent(
        cid,
        {
            "paper_endpoint": "https://paper-api.alpaca.markets",
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": json.dumps(
                _request(cid, side=side), sort_keys=True, separators=(",", ":")
            ),
        },
    )
    if sent:
        journal.mark_order_send_attempt(cid, NOW)


def _order(
    status: str = "partially_filled",
    filled: str = "2",
    seconds: int = 7,
    *,
    cid: str = CID,
    broker_id: str = BROKER_ID,
    symbol: str = "AAPL",
    side: str = "buy",
) -> dict[str, object]:
    return _request(cid, symbol=symbol, side=side) | {
        "id": broker_id,
        "status": status,
        "filled_qty": filled,
        "asset_class": "us_equity",
        "order_class": "simple",
        "extended_hours": False,
        "updated_at": _at(seconds).isoformat(),
    }


def _frame(
    order: dict[str, object],
    event: str,
    execution: str | None = None,
    *,
    position_qty: str | None = None,
) -> bytes:
    data: dict[str, object] = {
        "event": event,
        "timestamp": order["updated_at"],
        "order": order,
    }
    if execution is not None:
        data["execution_id"] = execution
    if position_qty is not None:
        data["position_qty"] = position_qty
    return json.dumps({"stream": "trade_updates", "data": data}).encode()


def _activity(
    quantity: str,
    identity: str = "activity-1",
    *,
    broker_id: str = BROKER_ID,
    side: str = "buy",
) -> dict[str, object]:
    return {
        "id": identity,
        "activity_type": "FILL",
        "order_id": broker_id,
        "symbol": "AAPL",
        "side": side,
        "qty": quantity,
        "price": "1.00",
        "transaction_time": _at(6).isoformat(),
    }


def _position(quantity: str, symbol: str = "AAPL") -> dict[str, object]:
    return {
        "symbol": symbol,
        "qty": quantity,
        "side": "long",
        "asset_class": "us_equity",
    }


def _batch(reconciler: OrderReconciler, order: dict[str, object]) -> RestBatch:
    filled = str(order["filled_qty"])
    terminal = order["status"] in {"filled", "canceled", "expired", "rejected"}
    return RestBatch(
        account_ref=ACCOUNT_REF,
        started_at=_at(8),
        completed_at=_at(9),
        local_revision=int(cast(int, reconciler.view()["watermark"])),
        open_orders=() if terminal else (order,),
        positions=(_position(filled),) if Decimal(filled) else (),
        lookups={CID: PaperHttpResponse(200, json.dumps(order).encode())},
        activities={str(order["id"]): (_activity(filled),) if Decimal(filled) else ()},
        complete=True,
        stable=True,
    )


def _connect(reconciler: OrderReconciler, session: str = "session-1") -> None:
    for status in ("CONNECTING", "AUTHENTICATED", "SUBSCRIBED"):
        reconciler.stream_health(status, NOW, session)


def _projection(reconciler: OrderReconciler, cid: str = CID) -> dict[str, object]:
    return cast(dict[str, dict[str, object]], reconciler.view()["orders"])[cid]


def _with_baseline(
    journal: Journal, positions: tuple[dict[str, object], ...] = ()
) -> OrderReconciler:
    item = OrderReconciler(journal, ACCOUNT_REF, clock=lambda: NOW)
    batch = RestBatch(
        account_ref=ACCOUNT_REF,
        started_at=NOW,
        completed_at=NOW,
        local_revision=cast(int, item.view()["watermark"]),
        open_orders=(),
        positions=positions,
        lookups={},
        activities={},
        complete=True,
        stable=True,
    )
    report = item.apply_rest_batch(batch, POLICY, at=NOW)
    item.register_position_baseline(report.watermark, POLICY)
    return item


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[Journal]:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    path = root / "journal.sqlite3"
    item = Journal(path, root)
    path.chmod(0o600)
    item.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_RAW, NOW)
    yield item
    item.close()


@pytest.fixture
def reconciler(journal: Journal) -> OrderReconciler:
    item = _with_baseline(journal)
    _reserve(journal)
    _connect(item)
    return item


@pytest.mark.parametrize(
    ("terminal", "filled", "state"),
    [
        ("canceled", "5", "CANCELED"),
        ("expired", "5", "EXPIRED"),
        ("filled", "10", "FILLED"),
    ],
)
def test_restart_rest_recovery_and_cancel_fill_race(
    journal: Journal,
    reconciler: OrderReconciler,
    tmp_path: Path,
    terminal: str,
    filled: str,
    state: str,
) -> None:
    reconciler.ingest_frame(_frame(_order("new", "0", 1), "new"), _at(1), "session-1")
    partial = _frame(_order(filled="2", seconds=2), "partial_fill", "ws-fill-1")
    reconciler.ingest_frame(partial, _at(2), "session-1")
    before_duplicate = reconciler.view()["watermark"]
    reconciler.ingest_frame(partial, _at(3), "session-1")
    assert reconciler.view()["watermark"] == before_duplicate
    reconciler.stream_health("DISCONNECTED", _at(4), "session-1")
    journal.close()

    root = tmp_path / "private"
    reopened = Journal(root / "journal.sqlite3", root)
    try:
        recovered = OrderReconciler(reopened, ACCOUNT_REF, clock=lambda: _at(5))
        assert recovered.view()["stream_gap"] is True
        assert recovered.view()["last_report"] is None
        assert _projection(recovered)["filled_quantity"] == "2"
        _connect(recovered, "session-2")
        pending = _order("pending_cancel", "5", 7)
        recovery = replace(
            _batch(recovered, pending),
            activities={BROKER_ID: (_activity("2"), _activity("3", "activity-2"))},
        )
        report = recovered.apply_rest_batch(recovery, POLICY, at=_at(10))
        assert report.status == "MATCHED"
        assert report.expected_positions == report.observed_positions == {"AAPL": "5"}
        assert _projection(recovered)["state"] == "CANCEL_PENDING"
        assert recovered.view()["stream_gap"] is False

        recovered.ingest_frame(partial, _at(10), "session-2")
        recovered.ingest_frame(
            _frame(_order("new", "0", 1), "accepted"), _at(10), "session-2"
        )
        assert _projection(recovered)["filled_quantity"] == "5"
        event = "fill" if terminal == "filled" else terminal
        final_order = _order(terminal, filled, 11)
        recovered.ingest_frame(
            _frame(final_order, event, "ws-final"), _at(11), "session-2"
        )
        assert recovered.view()["last_report"] is None
        assert _projection(recovered)["state"] == state
        activities = [_activity("2"), _activity("3", "activity-2")]
        if terminal == "filled":
            activities.append(_activity("5", "activity-3"))
        final_batch = replace(
            _batch(recovered, final_order),
            started_at=_at(12),
            completed_at=_at(13),
            activities={BROKER_ID: tuple(activities)},
        )
        final_report = recovered.apply_rest_batch(final_batch, POLICY, at=_at(14))
        assert final_report.status == "MATCHED"
        assert final_report.expected_positions == {"AAPL": filled}
        assert final_report.observed_positions == {"AAPL": filled}
        assert final_report.snapshots_atomic is False
        assert final_report.trading_authorized is False
        assert reopened.has_order_send_attempt(CID)
        assert recovered.verify_rebuild()
        reopened.close()
        reopened = Journal(root / "journal.sqlite3", root)
        restarted = OrderReconciler(reopened, ACCOUNT_REF, clock=lambda: _at(15))
        assert _projection(restarted)["state"] == state
        assert _projection(restarted)["filled_quantity"] == filled
        assert restarted.view()["stream_gap"] is True
        assert restarted.view()["last_report"] is None
        assert restarted.verify_rebuild()
    finally:
        reopened.close()


def test_lookup_404_does_not_resolve_unknown_send(
    journal: Journal, reconciler: OrderReconciler
) -> None:
    batch = replace(
        _batch(reconciler, _order("new", "0")),
        open_orders=(),
        lookups={CID: PaperHttpResponse(404, b"not found")},
    )
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    assert report.status != "MATCHED"
    assert report.differences
    assert reconciler.view()["stream_gap"] is True
    with pytest.raises(JournalError, match="already attempted"):
        journal.mark_order_send_attempt(CID, _at(11))


def test_known_open_order_with_missing_lookup_is_reported_without_crashing(
    reconciler: OrderReconciler,
) -> None:
    order = _order()
    reconciler.ingest_frame(_frame(order, "partial_fill"), _at(7), "session-1")
    batch = replace(
        _batch(reconciler, order), lookups={CID: PaperHttpResponse(404, b"not found")}
    )
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    codes = {issue["code"] for issue in report.differences}
    assert {"LOOKUP_UNKNOWN", "OPEN_ORDER_LOOKUP_UNVERIFIED"} <= codes
    assert report.status != "MATCHED"
    assert _projection(reconciler)["filled_quantity"] == "2"
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.verify_rebuild()


def test_matching_rejected_order_is_terminal_without_a_position(
    journal: Journal,
    reconciler: OrderReconciler,
) -> None:
    order = _order("rejected", "0")
    reconciler.ingest_frame(_frame(order, "rejected"), _at(7), "session-1")
    report = reconciler.apply_rest_batch(_batch(reconciler, order), POLICY, at=_at(10))
    assert report.status == "MATCHED"
    assert _projection(reconciler)["state"] == "REJECTED"
    assert Decimal(report.expected_positions.get("AAPL", "0")) == 0
    assert report.observed_positions == {}
    restarted = OrderReconciler(journal, ACCOUNT_REF, clock=lambda: NOW)
    assert restarted.view()["last_report"] is None
    assert restarted.view()["stream_gap"] is True
    assert _projection(restarted)["state"] == "REJECTED"


def test_subscription_and_current_session_are_required(journal: Journal) -> None:
    item = _with_baseline(journal)
    _reserve(journal)
    report = item.apply_rest_batch(_batch(item, _order()), POLICY, at=_at(10))
    assert report.status != "MATCHED"
    assert item.view()["stream_gap"] is True
    _connect(item)
    item.ingest_frame(_frame(_order(), "partial_fill"), _at(7), "obsolete-session")
    assert item.view()["stream_gap"] is True
    assert item.view()["last_report"] is None


def test_missing_account_position_baseline_is_explicit(journal: Journal) -> None:
    item = OrderReconciler(journal, ACCOUNT_REF, clock=lambda: NOW)
    _reserve(journal)
    _connect(item)
    report = item.apply_rest_batch(_batch(item, _order()), POLICY, at=_at(10))
    assert report.status != "MATCHED"
    assert "BASELINE_NOT_REGISTERED" in {issue["code"] for issue in report.differences}
    assert item.view()["stream_gap"] is True


@pytest.mark.parametrize(
    "fault", ["stale", "foreign", "generic_snapshot", "late_intent"]
)
def test_baseline_rejects_unqualified_or_late_evidence(
    journal: Journal, fault: str
) -> None:
    item = OrderReconciler(journal, ACCOUNT_REF, clock=lambda: NOW)
    if fault == "generic_snapshot":
        evidence = journal.append_next(
            source_id="ALPACA_PAPER",
            feed="paper",
            event_kind="PAPER_POSITIONS_SNAPSHOT",
            payload=b"[]",
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        sequence = evidence.sequence
    else:
        batch = RestBatch(
            account_ref="foreign-account" if fault == "foreign" else ACCOUNT_REF,
            started_at=_at(-100) if fault == "stale" else NOW,
            completed_at=_at(-99) if fault == "stale" else NOW,
            local_revision=cast(int, item.view()["watermark"]),
            open_orders=(),
            positions=(),
            lookups={},
            activities={},
            complete=True,
            stable=True,
        )
        sequence = item.apply_rest_batch(batch, POLICY, at=NOW).watermark
    if fault == "late_intent":
        _reserve(journal)
    with pytest.raises(JournalError):
        item.register_position_baseline(sequence, POLICY)
    assert item.view()["baseline"] is None
    assert item.view()["stream_gap"] is True
    assert item.verify_rebuild()


def test_foreign_account_does_not_rebind_or_modify_owned_projection(
    journal: Journal, reconciler: OrderReconciler
) -> None:
    with pytest.raises(JournalError, match="not bound"):
        OrderReconciler(journal, "foreign-account", clock=lambda: NOW)
    before = reconciler.view()["orders"]
    report = reconciler.apply_rest_batch(
        replace(_batch(reconciler, _order()), account_ref="foreign-account"),
        POLICY,
        at=_at(10),
    )
    assert report.status != "MATCHED"
    assert report.differences
    assert reconciler.view()["orders"] == before
    assert journal.read_paper_account_ref() == ACCOUNT_REF


@pytest.mark.parametrize("conflict", ["broker_identity", "execution_identity"])
def test_conflicting_identity_cannot_produce_a_clean_reconciliation(
    journal: Journal, reconciler: OrderReconciler, conflict: str
) -> None:
    first = _order(filled="2", seconds=2)
    reconciler.ingest_frame(
        _frame(first, "partial_fill", "execution-1"), _at(2), "session-1"
    )
    changed = _order(filled="5", seconds=3)
    if conflict == "broker_identity":
        changed["id"] = OTHER_BROKER_ID
        execution = "execution-2"
    else:
        execution = "execution-1"
    raw = _frame(changed, "partial_fill", execution)
    reconciler.ingest_frame(raw, _at(3), "session-1")
    if conflict == "execution_identity":
        assert "EVENT_ID_CONFLICT" in cast(list[str], reconciler.view()["issues"])
        retained_fill = str(_projection(reconciler)["filled_quantity"])
        assert Decimal(retained_fill) <= Decimal("5")
        reconciler.ingest_frame(raw, _at(4), "session-1")
        assert _projection(reconciler)["filled_quantity"] == retained_fill
    report = reconciler.apply_rest_batch(
        _batch(reconciler, changed), POLICY, at=_at(10)
    )
    assert report.status != "MATCHED"
    assert report.differences
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.verify_rebuild()
    if conflict == "execution_identity":
        assert "EVENT_ID_CONFLICT" in {issue["code"] for issue in report.differences}
        restarted = OrderReconciler(journal, ACCOUNT_REF, clock=lambda: _at(11))
        assert "EVENT_ID_CONFLICT" in cast(list[str], restarted.view()["issues"])
        assert _projection(restarted)["filled_quantity"] == retained_fill
        assert restarted.verify_rebuild()


@pytest.mark.parametrize("has_timestamp", [True, False])
def test_saved_post_identity_precedes_first_lifecycle_projection(
    journal: Journal,
    has_timestamp: bool,
) -> None:
    initial = _with_baseline(journal)
    _reserve(journal)
    historical = _order("new", "0", 1)
    if not has_timestamp:
        historical.pop("updated_at")
    journal.record_order_http_observation(
        CID,
        OrderHttpObservation(
            "POST", "HTTP_RESPONSE", _at(2), 200, json.dumps(historical).encode()
        ),
    )
    assert initial.view()["orders"] == {}
    recovered = OrderReconciler(journal, ACCOUNT_REF, clock=lambda: _at(3))
    _connect(recovered)
    changed = _order(broker_id=OTHER_BROKER_ID)
    report = recovered.apply_rest_batch(_batch(recovered, changed), POLICY, at=_at(10))
    assert report.status != "MATCHED"
    assert "BROKER_ID_CONFLICT" in {issue["code"] for issue in report.differences}
    assert _projection(recovered)["broker_order_id"] == BROKER_ID
    assert _projection(recovered)["state"] == "UNKNOWN"
    assert recovered.view()["stream_gap"] is True
    assert recovered.verify_rebuild()


@pytest.mark.parametrize("has_timestamp", [True, False])
def test_historical_fill_survives_unknown_current_lookup(
    journal: Journal, reconciler: OrderReconciler, has_timestamp: bool
) -> None:
    historical = _order(filled="7", seconds=1)
    if not has_timestamp:
        historical.pop("updated_at")
    journal.record_order_http_observation(
        CID,
        OrderHttpObservation(
            "POST", "HTTP_RESPONSE", _at(1), 201, json.dumps(historical).encode()
        ),
    )
    batch = replace(
        _batch(reconciler, _order(filled="7")),
        lookups={CID: PaperHttpResponse(404, b"not found")},
    )
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    assert report.status != "MATCHED"
    assert "LOOKUP_UNKNOWN" in {issue["code"] for issue in report.differences}
    assert _projection(reconciler)["broker_order_id"] == BROKER_ID
    assert _projection(reconciler)["filled_quantity"] == "7"
    assert _projection(reconciler)["state"] == "UNKNOWN"
    assert report.expected_positions == report.observed_positions == {"AAPL": "7"}
    evidence = journal.read_event(report.watermark)
    assert evidence is not None
    captured = json.loads(evidence[1])["batch"]["lookups"][CID]
    assert captured["status"] == 404
    assert base64.b64decode(captured["body"]) == b"not found"
    with pytest.raises(JournalError, match="already attempted"):
        journal.mark_order_send_attempt(CID, _at(11))
    assert reconciler.verify_rebuild()


def test_historical_identity_is_not_replaced_by_open_order_on_unknown_lookup(
    journal: Journal, reconciler: OrderReconciler
) -> None:
    historical = _order(filled="7", seconds=1)
    journal.record_order_http_observation(
        CID,
        OrderHttpObservation(
            "POST", "HTTP_RESPONSE", _at(1), 201, json.dumps(historical).encode()
        ),
    )
    changed = _order(filled="7", broker_id=OTHER_BROKER_ID)
    batch = replace(
        _batch(reconciler, changed), lookups={CID: PaperHttpResponse(404, b"not found")}
    )
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    assert "BROKER_ID_CONFLICT" in {issue["code"] for issue in report.differences}
    assert report.status != "MATCHED"
    assert _projection(reconciler)["broker_order_id"] == BROKER_ID
    assert _projection(reconciler)["filled_quantity"] == "7"
    assert _projection(reconciler)["state"] == "UNKNOWN"
    assert reconciler.verify_rebuild()


def test_untimed_foreign_historical_identity_cannot_raise_known_fill(
    journal: Journal, reconciler: OrderReconciler
) -> None:
    original = _order(filled="2", seconds=1)
    foreign = _order(filled="7", broker_id=OTHER_BROKER_ID)
    foreign.pop("updated_at")
    for index, order in enumerate((original, foreign), start=1):
        journal.record_order_http_observation(
            CID,
            OrderHttpObservation(
                "POST", "HTTP_RESPONSE", _at(index), 201, json.dumps(order).encode()
            ),
        )
    batch = replace(
        _batch(reconciler, _order()),
        lookups={CID: PaperHttpResponse(404, b"not found")},
    )
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    assert report.status != "MATCHED"
    codes = {issue["code"] for issue in report.differences}
    assert {"BROKER_ID_CONFLICT", "LOOKUP_UNKNOWN"} <= codes
    assert _projection(reconciler)["broker_order_id"] == BROKER_ID
    assert _projection(reconciler)["filled_quantity"] == "2"
    assert _projection(reconciler)["state"] == "UNKNOWN"
    assert report.expected_positions == report.observed_positions == {"AAPL": "2"}
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.verify_rebuild()


def test_stream_position_difference_requires_matching_rest_recovery(
    journal: Journal,
) -> None:
    item = _with_baseline(journal, (_position("4"),))
    _reserve(journal)
    _connect(item)
    order = _order()
    item.ingest_frame(
        _frame(order, "partial_fill", "execution-1", position_qty="9"),
        _at(7),
        "session-1",
    )
    differences = cast(
        dict[str, dict[str, str]], item.view()["stream_position_differences"]
    )
    assert differences["AAPL"]["expected"] == "6"
    assert differences["AAPL"]["observed"] == "9"
    assert differences["AAPL"]["evidence_ref"]
    assert item.view()["last_report"] is None
    assert item.verify_rebuild()
    disagreeing = replace(_batch(item, order), positions=(_position("9"),))
    report = item.apply_rest_batch(disagreeing, POLICY, at=_at(10))
    assert report.status == "DIFFERENCES"
    assert item.view()["stream_position_differences"]
    matching = replace(_batch(item, order), positions=(_position("6"),))
    report = item.apply_rest_batch(matching, POLICY, at=_at(11))
    assert report.status == "MATCHED"
    assert report.expected_positions == report.observed_positions == {"AAPL": "6"}
    assert item.view()["stream_position_differences"] == {}
    assert item.view()["stream_gap"] is False
    assert item.verify_rebuild()


def test_invalid_stream_position_quantity_preserves_evidence_and_blocks(
    reconciler: OrderReconciler,
) -> None:
    raw = _frame(_order(), "partial_fill", "execution-1", position_qty="not-a-number")
    reconciler.ingest_frame(raw, _at(7), "session-1")
    assert "MALFORMED_TRADE_UPDATE" in cast(list[str], reconciler.view()["issues"])
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.view()["last_report"] is None
    assert reconciler.verify_rebuild()


def test_websocket_broker_uuid_cannot_identify_two_owned_client_ids(
    journal: Journal, reconciler: OrderReconciler
) -> None:
    _reserve(journal, "owned-2")
    reconciler.ingest_frame(
        _frame(_order(filled="2", seconds=2), "partial_fill", "execution-1"),
        _at(2),
        "session-1",
    )
    reconciler.ingest_frame(
        _frame(
            _order(filled="3", seconds=3, cid="owned-2"), "partial_fill", "execution-2"
        ),
        _at(3),
        "session-1",
    )
    assert "OWNED_BROKER_ID_REUSED" in cast(list[str], reconciler.view()["issues"])
    assert set(cast(dict[str, object], reconciler.view()["orders"])) == {CID}
    assert _projection(reconciler)["filled_quantity"] == "2"
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.verify_rebuild()


@pytest.mark.parametrize("collision", ["broker_uuid", "activity_id"])
def test_rest_order_and_activity_identities_are_account_wide(
    journal: Journal, reconciler: OrderReconciler, collision: str
) -> None:
    _reserve(journal, "owned-2")
    first = _order()
    second_broker = BROKER_ID if collision == "broker_uuid" else OTHER_BROKER_ID
    second = _order(filled="3", cid="owned-2", broker_id=second_broker)
    batch = replace(
        _batch(reconciler, first),
        open_orders=(first, second),
        positions=(_position("5"),),
        lookups={
            CID: PaperHttpResponse(200, json.dumps(first).encode()),
            "owned-2": PaperHttpResponse(200, json.dumps(second).encode()),
        },
        activities={
            BROKER_ID: (_activity("2", "shared-activity"),),
        },
    )
    if collision == "activity_id":
        batch = replace(
            batch,
            activities=batch.activities
            | {
                OTHER_BROKER_ID: (
                    _activity("3", "shared-activity", broker_id=OTHER_BROKER_ID),
                )
            },
        )
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    expected_code = (
        "OWNED_BROKER_ID_REUSED"
        if collision == "broker_uuid"
        else "FILL_ACTIVITY_ID_CONFLICT"
    )
    assert expected_code in {issue["code"] for issue in report.differences}
    assert report.status != "MATCHED"
    orders = cast(dict[str, dict[str, object]], reconciler.view()["orders"])
    assert len({order["broker_order_id"] for order in orders.values()}) == len(orders)
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.verify_rebuild()


def test_untimed_historical_fill_cannot_be_reduced_by_fresh_rest(
    journal: Journal, reconciler: OrderReconciler
) -> None:
    historical = _order(filled="5")
    historical.pop("updated_at")
    journal.record_order_http_observation(
        CID,
        OrderHttpObservation(
            "POST", "HTTP_RESPONSE", _at(1), 200, json.dumps(historical).encode()
        ),
    )
    report = reconciler.apply_rest_batch(
        _batch(reconciler, _order()), POLICY, at=_at(10)
    )
    assert "HISTORICAL_ORDER_TIME_UNVERIFIED" in {
        issue["code"] for issue in report.differences
    }
    assert report.status != "MATCHED"
    assert _projection(reconciler)["filled_quantity"] == "5"
    assert _projection(reconciler)["state"] == "UNKNOWN"
    assert report.expected_positions == {"AAPL": "5"}
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.verify_rebuild()


@pytest.mark.parametrize(
    "difference",
    ["external_order", "external_position", "missing_position", "wrong_quantity"],
)
def test_account_wide_differences_are_explicit_and_never_adopted(
    reconciler: OrderReconciler, difference: str
) -> None:
    owned = _order()
    batch = _batch(reconciler, owned)
    if difference == "external_order":
        external = _order(
            "new", "0", cid="external-1", broker_id=OTHER_BROKER_ID, symbol="MSFT"
        )
        batch = replace(batch, open_orders=(owned, external))
    elif difference == "external_position":
        batch = replace(batch, positions=(_position("2"), _position("4", "MSFT")))
    elif difference == "missing_position":
        batch = replace(batch, positions=())
    else:
        batch = replace(batch, positions=(_position("3"),))
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    assert report.status == "DIFFERENCES"
    assert report.differences
    for issue in report.differences:
        assert {
            "code",
            "subject",
            "expected",
            "observed",
            "evidence_ref",
        } <= issue.keys()
        assert issue["evidence_ref"]
    assert report.expected_positions == {"AAPL": "2"}
    assert "external-1" not in cast(dict[str, object], reconciler.view()["orders"])
    assert reconciler.view()["stream_gap"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "incomplete",
        "unstable",
        "stale",
        "future",
        "long_capture",
        "local_change",
        "missing_activities",
    ],
)
def test_incomplete_or_untrusted_rest_round_never_clears_gap(
    reconciler: OrderReconciler, fault: str
) -> None:
    batch = _batch(reconciler, _order())
    if fault == "incomplete":
        batch = replace(batch, complete=False, issues=("open_order_page_limit",))
    elif fault == "unstable":
        batch = replace(batch, stable=False)
    elif fault == "stale":
        batch = replace(batch, started_at=_at(-100), completed_at=_at(-99))
    elif fault == "future":
        batch = replace(batch, started_at=_at(11), completed_at=_at(12))
    elif fault == "long_capture":
        batch = replace(batch, started_at=NOW)
    elif fault == "local_change":
        batch = replace(batch, local_revision=batch.local_revision - 1)
    else:
        batch = replace(batch, activities={})
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    assert report.status != "MATCHED"
    assert report.differences
    assert reconciler.view()["stream_gap"] is True
    if fault == "local_change":
        assert "LOCAL_CHANGED_DURING_CAPTURE" in {
            item["code"] for item in report.differences
        }
    assert reconciler.verify_rebuild()


def test_account_positions_are_baseline_plus_net_signed_owned_fills(
    journal: Journal,
) -> None:
    reconciler = _with_baseline(journal, (_position("4"),))
    _reserve(journal)
    _reserve(journal, "owned-sell", side="sell")
    _connect(reconciler)
    buy = _order("filled", "10")
    sell = _order(
        "canceled", "3", cid="owned-sell", broker_id=OTHER_BROKER_ID, side="sell"
    )
    batch = replace(
        _batch(reconciler, buy),
        positions=(_position("11"),),
        lookups={
            CID: PaperHttpResponse(200, json.dumps(buy).encode()),
            "owned-sell": PaperHttpResponse(200, json.dumps(sell).encode()),
        },
        activities={
            BROKER_ID: (_activity("10"),),
            OTHER_BROKER_ID: (
                _activity("3", "sell-activity", broker_id=OTHER_BROKER_ID, side="sell"),
            ),
        },
    )
    report = reconciler.apply_rest_batch(batch, POLICY, at=_at(10))
    assert report.status == "MATCHED"
    assert report.expected_positions == report.observed_positions == {"AAPL": "11"}
    assert reconciler.verify_rebuild()


def test_projection_and_evidence_commit_roll_back_together(
    journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = journal._append_locked

    def fail_after_write(
        event: EventEnvelope,
        payload: bytes,
        state_updates: Mapping[str, object] | None,
    ) -> None:
        append(event, payload, state_updates)
        raise sqlite3.OperationalError("synthetic disk failure")

    def apply_once() -> EventEnvelope | None:
        return journal.apply_order_event(
            account_ref=ACCOUNT_REF,
            state_key="order_lifecycle:atomic",
            dedupe_key="fill-1",
            event_kind="TEST",
            payload=b"fill",
            received_at=NOW,
            reduce=lambda *_: {"filled": "2"},
        )

    monkeypatch.setattr(journal, "_append_locked", fail_after_write)
    with pytest.raises(sqlite3.OperationalError, match="disk failure"):
        apply_once()
    assert journal.read_state("order_lifecycle:atomic") is None
    assert journal.read_state("order_evidence:" + ACCOUNT_REF + ":fill-1") is None
    assert journal.read_order_events(through_sequence=1000) == ()
    monkeypatch.setattr(journal, "_append_locked", append)
    assert apply_once() is not None
    assert journal.read_state("order_lifecycle:atomic") == {"filled": "2"}


@pytest.mark.parametrize("changed_input", ["intent", "send", "http_observation"])
def test_reconciliation_captures_changed_local_inputs_inside_transaction(
    journal: Journal,
    reconciler: OrderReconciler,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
) -> None:
    if changed_input == "send":
        _reserve(journal, "late-intent", sent=False)
    snapshot = reconciler._intents
    mutated = False

    def snapshot_then_mutate() -> dict[str, object]:
        nonlocal mutated
        captured = snapshot()
        if mutated:
            return captured
        mutated = True
        root = tmp_path / "private"
        concurrent = Journal(root / "journal.sqlite3", root)
        try:
            if changed_input == "intent":
                _reserve(concurrent, "late-intent", sent=False)
            elif changed_input == "send":
                concurrent.mark_order_send_attempt("late-intent", _at(1))
            else:
                concurrent.record_order_http_observation(
                    CID,
                    OrderHttpObservation(
                        "POST",
                        "HTTP_RESPONSE",
                        _at(1),
                        200,
                        json.dumps(_order("new", "0", 1)).encode(),
                    ),
                )
        finally:
            concurrent.close()
        return captured

    monkeypatch.setattr(reconciler, "_intents", snapshot_then_mutate)
    report = reconciler.apply_rest_batch(
        _batch(reconciler, _order()), POLICY, at=_at(10)
    )
    assert "LOCAL_INPUTS_CHANGED_DURING_CAPTURE" in {
        issue["code"] for issue in report.differences
    }
    assert report.status != "MATCHED"
    evidence = journal.read_event(report.watermark)
    assert evidence is not None
    persisted = json.loads(evidence[1])
    assert persisted["batch"]["local_inputs_changed"] is True
    captured = persisted["intents"]
    if changed_input == "intent":
        assert captured["late-intent"]["sent"] is False
    elif changed_input == "send":
        assert captured["late-intent"]["sent"] is True
    else:
        assert len(captured[CID]["http_history"]) == 1
    assert reconciler.view()["stream_gap"] is True
    assert reconciler.verify_rebuild()


@pytest.mark.parametrize("duplicate", [True, False])
def test_concurrent_connections_deduplicate_without_losing_distinct_fills(
    journal: Journal, tmp_path: Path, duplicate: bool
) -> None:
    barrier = Barrier(2)
    root = tmp_path / "private"

    def apply(index: int) -> bool:
        connection = Journal(root / "journal.sqlite3", root)
        try:
            barrier.wait(timeout=5)

            def project(old: object, _conflict: bool, _sequence: int) -> object:
                prior = cast(dict[str, int], old) if old is not None else {"fills": 0}
                return {"fills": prior["fills"] + 1}

            event = connection.apply_order_event(
                account_ref=ACCOUNT_REF,
                state_key="order_lifecycle:concurrency",
                dedupe_key="fill" if duplicate else f"fill-{index}",
                event_kind="TEST",
                payload=b"one share",
                received_at=NOW,
                reduce=project,
            )
            return event is not None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(apply, (1, 2)))
    expected = 1 if duplicate else 2
    assert sum(results) == expected
    assert journal.read_state("order_lifecycle:concurrency") == {"fills": expected}
    assert len(journal.read_order_events(through_sequence=1000)) == expected


def test_tampered_cached_projection_is_detected_before_restart(
    reconciler: OrderReconciler, journal: Journal, tmp_path: Path
) -> None:
    cached = reconciler.view() | {"stream_gap": False}
    with sqlite3.connect(tmp_path / "private" / "journal.sqlite3") as connection:
        connection.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (json.dumps(cached), "order_lifecycle:" + ACCOUNT_REF),
        )
    assert reconciler.verify_rebuild() is False
    with pytest.raises(JournalError, match="durable evidence"):
        OrderReconciler(journal, ACCOUNT_REF, clock=lambda: _at(1))


def test_coherent_older_cache_cannot_hide_latest_durable_order_event(
    journal: Journal, reconciler: OrderReconciler, tmp_path: Path
) -> None:
    older = reconciler.view()
    reconciler.ingest_frame(
        _frame(_order(), "partial_fill", "execution-1"), _at(7), "session-1"
    )
    assert reconciler.verify_rebuild()
    assert older["watermark"] != reconciler.view()["watermark"]
    with sqlite3.connect(tmp_path / "private" / "journal.sqlite3") as connection:
        connection.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (json.dumps(older), "order_lifecycle:" + ACCOUNT_REF),
        )
    assert reconciler.verify_rebuild() is False
    with pytest.raises(JournalError, match="durable evidence"):
        OrderReconciler(journal, ACCOUNT_REF, clock=lambda: _at(8))


def test_open_private_creates_owner_only_account_state(tmp_path: Path) -> None:
    root = tmp_path / "new-private"
    path = root / "journal.sqlite3"
    item = Journal.open_private(path, root)
    try:
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        item.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_RAW, NOW)
        assert item.read_paper_account_ref() == ACCOUNT_REF
        item.require_private_order_state()
    finally:
        item.close()


@pytest.mark.parametrize("broad_path", ["directory", "database"])
def test_open_private_refuses_broad_existing_path_without_mutation(
    tmp_path: Path, broad_path: str
) -> None:
    root = tmp_path / "existing"
    root.mkdir(mode=0o700)
    path = root / "journal.sqlite3"
    if broad_path == "directory":
        root.chmod(0o755)
        with pytest.raises(JournalError):
            Journal.open_private(path, root)
        assert stat.S_IMODE(root.stat().st_mode) == 0o755
        assert not path.exists()
    else:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE preserved (value TEXT)")
            connection.execute("INSERT INTO preserved VALUES ('original')")
        path.chmod(0o644)
        before = path.read_bytes()
        with pytest.raises(JournalError):
            Journal.open_private(path, root)
        assert path.read_bytes() == before
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
