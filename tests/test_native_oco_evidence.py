"""Immutable synthetic native OCO observations, never broker orders."""

import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_evidence import (
    NativeOcoEvidence,
    inspect_native_oco_capture,
    native_oco_order_ids,
)
from alpaca_ross_bot.paper_order_contract import paper_account_ref

NOW = datetime(2026, 10, 20, 15, tzinfo=UTC)
TP = "aaaaaaaa-1111-4111-8111-111111111111"
STOP = "bbbbbbbb-2222-4222-8222-222222222222"
GROUP = "cccccccc-3333-4333-8333-333333333333"
ACCOUNT = json.dumps({"id": "dddddddd-4444-4444-8444-444444444444"}).encode()
REF = paper_account_ref(ACCOUNT)


def _order() -> dict[str, Any]:
    shared: dict[str, Any] = {
        "group_id": GROUP,
        "symbol": "AAPL",
        "side": "sell",
        "qty": "4",
        "filled_qty": "0",
        "status": "new",
        "replaces": None,
        "time_in_force": "day",
        "replaced_by": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "submitted_at": NOW.isoformat(),
    }
    return shared | {
        "id": TP,
        "client_order_id": "target-cid",
        "group_role": "take_profit",
        "order_class": "oco",
        "type": "limit",
        "limit_price": "12",
        "stop_price": None,
        "legs": [
            shared
            | {
                "id": STOP,
                "client_order_id": "independent-stop-cid",
                "group_role": "stop_loss",
                "order_class": "simple",
                "type": "stop",
                "stop_price": "9.5",
                "limit_price": None,
                "legs": None,
            }
        ],
    }


def _fill(identity: str = "fill-a", order_id: str = TP) -> dict[str, Any]:
    return {
        "id": identity,
        "activity_type": "FILL",
        "order_id": order_id,
        "symbol": "AAPL",
        "side": "sell",
        "qty": "1.25",
        "price": "12",
        "cum_qty": "99999",
        "transaction_time": NOW.isoformat(),
    }


def _path(identity: str, cursor: str | None = None) -> str:
    query = [
        ("activity_types", "FILL"),
        ("order_id", identity),
        ("direction", "asc"),
        ("page_size", "100"),
    ]
    if cursor is not None:
        query.append(("page_token", cursor))
    return "/v2/account/activities?" + urlencode(query)


def _request(path: str, body: object, status: int = 200) -> dict[str, Any]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return {
        "method": "GET",
        "path": path,
        "status": status,
        "body": base64.b64encode(raw).decode(),
        "error": None,
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
    }


def _capture(
    order: dict[str, Any] | None = None,
    target_pages: list[list[dict[str, Any]]] | None = None,
    stop_pages: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    requests = [
        _request("/v2/account", ACCOUNT),
        _request(f"/v2/orders/{TP}?nested=true", _order() if order is None else order),
    ]
    for identity, pages in ((TP, target_pages), (STOP, stop_pages)):
        cursor = None
        for rows in [[]] if pages is None else pages:
            requests.append(_request(_path(identity, cursor), rows))
            if rows:
                cursor = rows[-1]["id"]
    return {
        "schema": 1,
        "paper_endpoint": "https://paper-api.alpaca.markets",
        "account_ref": REF,
        "requested_order_id": TP,
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "requests": requests,
    }


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    root = tmp_path / "private"
    store = Journal.open_private(root / "journal.sqlite3", root)
    store.record_paper_account_verification(REF, ACCOUNT, NOW)
    return store


def _append(journal: Journal, capture: dict[str, Any]) -> int:
    return journal.append_next(
        source_id="ALPACA_NATIVE_OCO",
        feed="paper",
        event_kind="NATIVE_OCO_CAPTURE",
        payload=json.dumps(capture).encode(),
        system_first_seen_at=NOW,
        processed_at=datetime.fromisoformat(capture["completed_at"]),
    ).sequence


def _inspect(journal: Journal, capture: dict[str, Any]) -> NativeOcoEvidence:
    sequence = _append(journal, capture)
    return inspect_native_oco_capture(
        journal,
        sequence,
        account_ref=REF,
        decision_at=NOW,
        max_age=timedelta(seconds=5),
    )


@pytest.mark.parametrize("child_class", ["simple", "oco"])
def test_reported_group_preserves_distinct_cids_and_child_class(
    journal: Journal,
    child_class: str,
) -> None:
    order = _order()
    order["legs"][0]["order_class"] = child_class
    assert native_oco_order_ids(json.dumps(order).encode(), TP.upper()) == (TP, STOP)
    before = journal.read_evidence_sequence()
    result = _inspect(journal, _capture(order))
    horizon = journal.read_evidence_sequence()
    assert result.topology_observed and result.accounting_complete
    assert result.orders[0].client_order_id != result.orders[1].client_order_id
    assert result.orders[1].observed_order_class == child_class
    assert result.exhausted_order_ids == (TP, STOP)
    assert result.fills == ()
    assert not any(result.to_dict()["authority"].values())
    assert result.to_dict()["authority"]["dispatch_authorized"] is False
    summary = json.dumps(result.public_summary())
    assert TP not in summary and REF not in summary and "target-cid" not in summary
    assert journal.read_order_intent_ids() == ()
    assert horizon == before + 1
    inspect_native_oco_capture(
        journal,
        horizon,
        account_ref=REF,
        decision_at=NOW,
        max_age=timedelta(seconds=5),
        through_sequence=horizon,
    )
    assert journal.read_evidence_sequence() == horizon


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "group_id",
        "group_role",
        "created_at",
        "updated_at",
        "qty",
        "filled_qty",
        "client_order_id",
        "time_in_force",
    ],
)
def test_missing_core_field_is_local_unqualified_not_empty_group(
    journal: Journal,
    field: str,
) -> None:
    order = _order()
    del order[field]
    with pytest.raises(ValueError, match="locally unqualified"):
        native_oco_order_ids(json.dumps(order).encode(), TP)
    result = _inspect(journal, _capture(order, target_pages=[], stop_pages=[]))
    assert "LOCAL_OCO_ROOT_UNQUALIFIED" in result.issues
    assert not result.accounting_complete
    assert result.evidence_ref.startswith("sha256:")


@pytest.mark.parametrize(
    "change", ["role", "group", "symbol", "side", "same_id", "nested"]
)
def test_bad_stop_topology_cannot_become_group(journal: Journal, change: str) -> None:
    order = _order()
    stop = order["legs"][0]
    field, value = {
        "role": ("group_role", "entry"),
        "group": ("group_id", TP),
        "symbol": ("symbol", "MSFT"),
        "side": ("side", "buy"),
        "same_id": ("id", TP),
        "nested": ("legs", [{}]),
    }[change]
    stop[field] = value
    with pytest.raises(ValueError):
        native_oco_order_ids(json.dumps(order).encode(), TP)
    assert not _inspect(journal, _capture(order)).accounting_complete


def test_fractional_fill_dedupe_ignores_cumulative_quantity_economics(
    journal: Journal,
) -> None:
    order = _order()
    order["filled_qty"] = "1.25"
    order["legs"][0]["qty"] = "2.75"  # Broker-reported child qty need not equal root.
    fill = _fill()
    result = _inspect(journal, _capture(order, [[fill, copy.deepcopy(fill)], []]))
    assert result.accounting_complete
    assert len(result.fills) == 1
    assert result.fills[0].quantity == Fraction(5, 4)
    assert result.to_dict()["fill_census"][0]["known_fill_quantity"] == "5/4"
    assert result.orders[1].quantity == Fraction(11, 4)


@pytest.mark.parametrize("field,value", [("price", "13"), ("cum_qty", "1.25")])
def test_changed_duplicate_activity_conflicts_even_non_economic_fields(
    journal: Journal,
    field: str,
    value: str,
) -> None:
    order = _order()
    order["filled_qty"] = "1.25"
    fill = _fill()
    result = _inspect(journal, _capture(order, [[fill, fill | {field: value}], []]))
    assert "FILL_IDENTITY_CONFLICT" in result.issues
    assert len(result.fills) == 1 and result.fills[0].price == 12
    assert not result.accounting_complete


def test_partial_pages_are_unknown_census_even_when_snapshot_matches(
    journal: Journal,
) -> None:
    order = _order()
    order["filled_qty"] = "1.25"
    result = _inspect(journal, _capture(order, [[_fill()]]))
    assert len(result.fills) == 1
    assert result.exhausted_order_ids == (STOP,)
    assert "FILL_CENSUS_INCOMPLETE" in result.issues
    assert not result.accounting_complete


def test_reported_overexit_and_negative_residual_are_retained(
    journal: Journal,
) -> None:
    order = _order()
    order["filled_qty"] = "5"
    order["legs"][0]["filled_qty"] = "1.25"
    result = _inspect(
        journal,
        _capture(
            order,
            [[_fill() | {"qty": "5"}], []],
            [[_fill("stop-fill", STOP)], []],
        ),
    )
    assert len(result.fills) == 2
    assert result.orders[0].to_dict()["reported_remaining_quantity"] == "-1/1"
    assert "GROUP_FILLS_EXCEED_ROOT_REPORTED_QUANTITY" in result.issues
    assert "SNAPSHOT_FILLED_EXCEEDS_REPORTED_QUANTITY" in result.issues
    assert not any(result.to_dict()["authority"].values())


def test_partial_target_then_remaining_stop_has_consistent_capture_census(
    journal: Journal,
) -> None:
    order = _order()
    order["filled_qty"] = "1"
    order["legs"][0].update(qty="3", filled_qty="3")
    result = _inspect(
        journal,
        _capture(
            order,
            [[_fill() | {"qty": "1"}], []],
            [[_fill("stop-fill", STOP) | {"qty": "3", "price": "9.4"}], []],
        ),
    )
    assert result.accounting_complete
    assert {fill.order_id for fill in result.fills} == {TP, STOP}
    assert sum((fill.quantity for fill in result.fills), Fraction()) == 4
    assert not any(result.to_dict()["authority"].values())


@pytest.mark.parametrize("body", [b"invalid-json", b"{}", b"null"])
def test_malformed_fill_page_does_not_create_empty_census(
    journal: Journal,
    body: bytes,
) -> None:
    capture = _capture()
    capture["requests"][2] = _request(_path(TP), body)
    result = _inspect(journal, capture)
    assert "FILL_PAGE_UNQUALIFIED" in result.issues
    assert TP not in result.exhausted_order_ids
    assert not result.accounting_complete


@pytest.mark.parametrize("which", ["started_at", "completed_at"])
def test_future_request_clock_is_refused(journal: Journal, which: str) -> None:
    capture = _capture()
    capture["requests"][0][which] = "2026-10-20T15:00:00.000000001Z"
    sequence = _append(journal, capture)
    with pytest.raises(JournalError, match="chronology"):
        inspect_native_oco_capture(
            journal,
            sequence,
            account_ref=REF,
            decision_at=NOW,
            max_age=timedelta(seconds=1),
        )


def test_fully_exhausted_fill_total_disagreement_blocks_accounting(
    journal: Journal,
) -> None:
    result = _inspect(journal, _capture(target_pages=[[_fill()], []]))
    assert "FILL_SNAPSHOT_QUANTITY_DISAGREEMENT" in result.issues
    assert not result.accounting_complete


@pytest.mark.parametrize("field", ["replaces", "replaced_by"])
def test_replacement_link_is_not_summed_or_claimed_complete(
    journal: Journal,
    field: str,
) -> None:
    order = _order()
    order[field] = GROUP
    result = _inspect(journal, _capture(order))
    assert result.topology_observed and len(result.orders) == 2
    assert "REPLACEMENT_HISTORY_UNRESOLVED" in result.issues
    assert not result.accounting_complete


@pytest.mark.parametrize(
    "field,value",
    [
        ("order_id", STOP),
        ("symbol", "MSFT"),
        ("side", "buy"),
        ("qty", 1.25),
        ("price", "NaN"),
        ("transaction_time", "2026-10-20T15:00:00.000000001Z"),
    ],
)
def test_unqualified_fill_never_counts_as_zero_census(
    journal: Journal,
    field: str,
    value: object,
) -> None:
    result = _inspect(journal, _capture(target_pages=[[_fill() | {field: value}], []]))
    assert "FILL_FACT_UNQUALIFIED" in result.issues
    assert not result.accounting_complete


@pytest.mark.parametrize("which", ["account", "order", "fill"])
def test_non200_retains_capture_without_complete_accounting(
    journal: Journal,
    which: str,
) -> None:
    capture = _capture()
    index = {"account": 0, "order": 1, "fill": 2}[which]
    capture["requests"][index]["status"] = 500
    capture["requests"][index]["body"] = base64.b64encode(b"error").decode()
    result = _inspect(journal, capture)
    assert not result.accounting_complete
    assert any("UNAVAILABLE" in issue for issue in result.issues)


def test_timeout_is_unknown_not_rejection(journal: Journal) -> None:
    capture = _capture()
    capture["requests"][2].update(status=None, body=None, error="TimeoutError")
    result = _inspect(journal, capture)
    assert "FILL_PAGE_UNAVAILABLE" in result.issues
    assert TP not in result.exhausted_order_ids


def test_each_request_age_checked_not_only_capture_end(journal: Journal) -> None:
    capture = _capture()
    old = (NOW - timedelta(seconds=6)).isoformat()
    capture["started_at"] = old
    capture["requests"][0].update(started_at=old, completed_at=old)
    result = _inspect(journal, capture)
    assert "CAPTURE_REQUEST_STALE" in result.issues
    assert not result.accounting_complete


@pytest.mark.parametrize(
    "kind",
    [
        "foreign_binding",
        "foreign_body",
        "badbase64",
        "extra_field",
        "future",
        "horizon",
        "future_horizon",
        "bool_horizon",
        "hash",
    ],
)
def test_unrepresentable_or_integrity_faults_refuse_without_mutation(
    journal: Journal,
    kind: str,
) -> None:
    capture = _capture()
    if kind == "foreign_body":
        capture["requests"][0] = _request("/v2/account", {"id": TP})
    elif kind == "badbase64":
        capture["requests"][1]["body"] = "!!"
    elif kind == "extra_field":
        capture["requests"][1]["headers"] = {}
    elif kind == "future":
        capture["completed_at"] = (NOW + timedelta(microseconds=1)).isoformat()
    sequence = _append(journal, capture)
    before = journal.read_evidence_sequence()
    if kind == "hash":
        journal._db.execute("DROP TRIGGER events_no_update")
        journal._db.execute(
            "UPDATE events SET payload=? WHERE sequence=?", (b"{}", sequence)
        )
    kwargs: dict[str, Any] = {
        "account_ref": REF,
        "decision_at": NOW,
        "max_age": timedelta(seconds=5),
    }
    if kind == "foreign_binding":
        kwargs["account_ref"] = "sha256:foreign"
    if kind == "horizon":
        kwargs["through_sequence"] = sequence - 1
    if kind == "future_horizon":
        kwargs["through_sequence"] = sequence + 1
    if kind == "bool_horizon":
        kwargs["through_sequence"] = True
    with pytest.raises(ValueError):
        inspect_native_oco_capture(journal, sequence, **kwargs)
    assert journal.read_evidence_sequence() == before
    assert journal.read_order_intent_ids() == ()


def test_future_source_order_clock_blocks_accounting(journal: Journal) -> None:
    order = _order()
    order["updated_at"] = "2026-10-20T15:00:00.000000001Z"
    result = _inspect(journal, _capture(order))
    assert "ORDER_SOURCE_CLOCK_FUTURE" in result.issues
    assert not result.accounting_complete


def test_repeated_page_cursor_is_not_exhaustion(journal: Journal) -> None:
    order = _order()
    order["filled_qty"] = "1.25"
    result = _inspect(journal, _capture(order, [[_fill()], [_fill()]]))
    assert "FILL_CURSOR_REPEATED" in result.issues
    assert "FILL_CENSUS_INCOMPLETE" in result.issues
    assert len(result.fills) == 1


def test_no_capture_or_wrong_source_cannot_be_interpreted(journal: Journal) -> None:
    with pytest.raises(ValueError):
        inspect_native_oco_capture(
            journal, 999, account_ref=REF, decision_at=NOW, max_age=timedelta(seconds=1)
        )
    sequence = journal.append_next(
        source_id="OTHER",
        feed="paper",
        event_kind="NATIVE_OCO_CAPTURE",
        payload=json.dumps(_capture()).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    ).sequence
    with pytest.raises(JournalError, match="source mismatch"):
        inspect_native_oco_capture(
            journal,
            sequence,
            account_ref=REF,
            decision_at=NOW,
            max_age=timedelta(seconds=1),
        )


@pytest.mark.parametrize("field", ["created_at", "updated_at", "submitted_at", "child"])
def test_later_decision_does_not_clear_order_clock_after_get(
    journal: Journal, field: str
) -> None:
    later = NOW + timedelta(seconds=1)
    order = _order()
    if field == "child":
        order["legs"][0]["updated_at"] = later.isoformat()
    else:
        order[field] = later.isoformat()
        if field == "created_at":
            order["updated_at"] = later.isoformat()
    sequence = _append(journal, _capture(order))
    original = journal.read_event(sequence)
    evidence = inspect_native_oco_capture(
        journal,
        sequence,
        account_ref=REF,
        decision_at=NOW + timedelta(seconds=2),
        max_age=timedelta(seconds=5),
    )
    assert evidence.topology_observed
    assert "ORDER_SOURCE_CLOCK_AFTER_OBSERVATION" in evidence.issues
    assert not evidence.accounting_complete
    assert len(evidence.orders) == 2
    assert journal.read_event(sequence) == original
    assert journal.read_order_intent_ids() == ()


@pytest.mark.parametrize("later_pages", [False, True])
def test_later_decision_cannot_make_fill_explain_an_earlier_snapshot(
    journal: Journal, later_pages: bool
) -> None:
    order = _order()
    order["filled_qty"] = "1.25"
    fill = _fill()
    fill["transaction_time"] = (NOW + timedelta(seconds=1)).isoformat()
    capture = _capture(order, [[fill], []])
    later = NOW + timedelta(seconds=2)
    if later_pages:
        capture["completed_at"] = later.isoformat()
        for request in capture["requests"][2:]:
            request.update(started_at=later.isoformat(), completed_at=later.isoformat())
    sequence = _append(journal, capture)
    original = journal.read_event(sequence)
    evidence = inspect_native_oco_capture(
        journal,
        sequence,
        account_ref=REF,
        decision_at=later,
        max_age=timedelta(seconds=5),
    )
    assert evidence.topology_observed and not evidence.accounting_complete
    assert "FILL_AFTER_ORDER_SNAPSHOT" in evidence.issues
    assert ("FILL_SOURCE_CLOCK_AFTER_OBSERVATION" in evidence.issues) is not later_pages
    assert len(evidence.fills) == 1
    assert evidence.fills[0].quantity == Fraction(5, 4)
    assert journal.read_event(sequence) == original
    assert journal.read_order_intent_ids() == ()


def test_later_decision_keeps_temporally_consistent_capture_complete(
    journal: Journal,
) -> None:
    sequence = _append(journal, _capture())
    evidence = inspect_native_oco_capture(
        journal,
        sequence,
        account_ref=REF,
        decision_at=NOW + timedelta(seconds=2),
        max_age=timedelta(seconds=5),
    )
    assert evidence.accounting_complete and evidence.issues == ()
