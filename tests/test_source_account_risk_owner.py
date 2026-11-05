"""Real memory-HTTP source facts join the existing account owner, never release."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any, cast

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop_attribution import SourceHttpCloseAttributions
from alpaca_ross_bot.incident_risk import IncidentRiskRequest
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.order_reconciliation import RestBatch
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, KIND, SCHEMA_V2, SOURCE
from alpaca_ross_bot.source_risk_registry import derive_source_registry
from test_account_risk_owner import RECON_POLICY
from test_native_account_risk_owner import native_risk_book as native_risk_book_fixture
from test_native_crossed_stop_preparation import native_ready as native_ready_fixture
from test_risk_ledger_audit import ACCOUNT, REF, calendar, request
from test_risk_marks import QUOTE, URL, put
from test_source_close_http_capture import capture, fill
from test_source_close_http_capture import http_capture as http_capture_fixture
from test_source_close_http_capture import source_http as source_http_fixture

native_risk_book = native_risk_book_fixture
native_ready = native_ready_fixture
source_http = source_http_fixture
http_capture = http_capture_fixture


def account_book(
    case: Any,
    at: datetime,
    *,
    cash: str,
    position: str,
    source_fills: list[dict[str, Any]],
    origin: str = "INJECTED_EXCHANGE",
    extra_batch: list[dict[str, Any]] | None = None,
    native_open: list[dict[str, Any]] | None = None,
    native_fills: list[dict[str, Any]] | None = None,
) -> IncidentRiskRequest:
    captures, _attempt, gets, fixture = case
    source = fixture[6][0]
    opened = (
        [gets.order]
        if gets.order["status"] not in {"filled", "canceled", "expired", "rejected"}
        else []
    )
    opened += native_open or []
    positions = (
        [
            {
                "symbol": "AAPL",
                "qty": position,
                "side": "long",
                "asset_class": "us_equity",
                "market_value": "9999",
            }
        ]
        if Fraction(position)
        else []
    )
    activities = source.fills + source_fills + (native_fills or [])
    grouped: dict[str, tuple[dict[str, Any], ...]] = {
        str(order["id"]): tuple(
            row for row in source.fills if row["order_id"] == order["id"]
        )
        for order in source.orders.values()
    }
    grouped[gets.order["id"]] = tuple(source_fills + (extra_batch or []))
    source.reconciler.apply_rest_batch(
        RestBatch(
            REF,
            at,
            at,
            cast(int, source.reconciler.view()["watermark"]),
            tuple(opened),
            cast(tuple[dict[str, object], ...], tuple(positions)),
            {
                cid: PaperHttpResponse(200, json.dumps(row).encode())
                for cid, row in source.orders.items()
            }
            | {
                gets.order["client_order_id"]: PaperHttpResponse(
                    200, json.dumps(gets.order).encode()
                )
            },
            grouped,
            True,
            True,
        ),
        RECON_POLICY,
        at=at,
    )
    rows = [
        request(
            "/v2/account",
            ACCOUNT
            | {
                "cash": cash,
                "equity": cash,
                "buying_power": cash,
                "non_marginable_buying_power": cash,
                "multiplier": "1",
            },
            at,
        ),
        request("/v2/positions", positions, at),
        request("/v2/orders?status=open&limit=500&nested=false", opened, at),
        request("/v2/account/activities?direction=desc&page_size=100", activities, at),
        request(
            "/v2/account/activities?direction=desc&page_size=100&page_token="
            + str(activities[-1]["id"]),
            [],
            at,
        ),
    ]
    seq = captures.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(
            {
                "schema": SCHEMA_V2,
                "transport_origin": origin,
                "account_ref": REF,
                "endpoint": ENDPOINT,
                "requests": rows,
            }
        ).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence
    marks: tuple[tuple[str, int, int, int], ...] = ()
    if Fraction(position):
        qseq = put(
            source.journal,
            [
                QUOTE
                | {
                    "bp": "9.49",
                    "ap": "9.50",
                    "t": at.isoformat().replace("+00:00", "Z"),
                }
            ],
            at=at,
        )
        mseq = put(
            source.journal,
            {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
            metadata=True,
            at=at,
        )
        marks = (("AAPL", qseq, 0, mseq),)
    return IncidentRiskRequest(
        seq,
        calendar(source.journal, at),
        timedelta(seconds=30),
        timedelta(days=1),
        marks,
    )


def report(
    case: Any, quantity: str, *, terminal: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _captures, _attempt, gets, fixture = case
    at = fixture[7][0]
    gets.order.update(
        status="filled" if terminal else "partially_filled",
        filled_qty=quantity,
        filled_at=at.isoformat(),
        updated_at=at.isoformat(),
    )
    rows = [
        fill(
            case,
            qty=quantity,
            price="9.49",
            cum_qty=quantity,
            leaves_qty=str(Decimal(gets.order["qty"]) - Decimal(quantity)),
        )
    ]
    gets.pages = [json.dumps(rows).encode(), b"[]"]
    packet = capture(case)
    return packet, rows


def test_source_http_account_economics_partial_terminal_and_faults(
    http_capture: Any,
) -> None:
    case = http_capture
    captures, _attempt, gets, fixture = case
    source, risk, owner, emergency, at, _, _ = fixture[6]
    original_risk = risk.view()
    original_oco = owner.view()
    original_emergency = emergency.view()
    packet, rows = report(case, "1")
    index = SourceHttpCloseAttributions(source.journal, owner.initial)
    index.observe(
        "source-partial",
        at,
        client_order_id=gets.order["client_order_id"],
        capture_sequences=(packet["capture_ref"]["sequence"],),
    )
    selection = account_book(case, at, cash="489.49", position="1", source_fills=rows)
    risk.observe_source("source-account-partial", selection, at)
    partial = risk.view()
    assert partial["last_snapshot"]["qualified"], partial["last_snapshot"]
    assert not partial["last_snapshot"]["flat"]
    assert partial["held"]["source_account_observation"]["source_exit_quantity"] == "1"
    assert partial["held"]["open_quantity"] == 1
    assert (
        partial["held"]["source_close_bindings"][gets.order["client_order_id"]][
            "source_fill_ownership_accepted"
        ]
        is False
    )
    assert (
        partial["pending_open_loss_reserve"]
        == original_risk["pending_open_loss_reserve"]
    )
    assert (
        partial["additional_local_cash_hold"]
        == original_risk["additional_local_cash_hold"]
    )
    assert (
        partial["capacity"] == "0"
        and "SOURCE_CLOSE_OWNERSHIP_PENDING" in partial["issues"]
    )
    assert (
        source.journal.read_order_send_started_at(gets.order["client_order_id"]) is None
    )
    assert owner.view() == original_oco and emergency.view() == original_emergency
    assert risk.verify_rebuild()
    horizon = source.journal.read_evidence_sequence()
    before_fault = AccountRiskOwner._read_native_causal_evidence_at(
        source.journal, REF, at, risk.fee_policy, through_sequence=horizon
    )
    # Capture a genuine later cumulative FILL.
    later = at + timedelta(seconds=1)
    fixture[7][0] = later
    gets.order.update(status="filled", filled_qty="2", updated_at=later.isoformat())
    final_rows = rows + [
        rows[0]
        | {
            "id": "source-final-fill",
            "transaction_time": later.isoformat(),
            "cum_qty": "2",
            "leaves_qty": "0",
        }
    ]
    gets.pages = [json.dumps(final_rows).encode(), b"[]"]
    final_packet = capture(case)
    terminal = account_book(
        case, later, cash="498.98", position="0", source_fills=final_rows
    )
    risk.observe("source-stale-index", terminal, later)
    assert not risk.view()["last_snapshot"]["qualified"]
    index.observe(
        "source-terminal",
        later,
        client_order_id=gets.order["client_order_id"],
        capture_sequences=(
            packet["capture_ref"]["sequence"],
            final_packet["capture_ref"]["sequence"],
        ),
    )
    risk.observe("source-account-terminal", terminal, later)
    final = risk.view()
    assert final["last_snapshot"]["qualified"], final["last_snapshot"]
    assert final["last_snapshot"]["flat"]
    assert (
        final["held"] is not None and final["completed"] == original_risk["completed"]
    )
    assert final["held"]["source_account_observation"]["source_exit_quantity"] == "2"
    assert final["held"]["open_quantity"] == 0
    assert (
        final["pending_open_loss_reserve"] == original_risk["pending_open_loss_reserve"]
    )
    assert (
        final["additional_local_cash_hold"]
        == original_risk["additional_local_cash_hold"]
    )
    assert (
        final["capacity"] == "0" and "SOURCE_CLOSE_OWNERSHIP_PENDING" in final["issues"]
    )
    assert owner.view() == original_oco and emergency.view() == original_emergency
    assert AccountRiskOwner(source.journal, REF, risk.fee_policy).view() == final
    assert risk.verify_rebuild()
    reopened = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:
        assert AccountRiskOwner(reopened, REF, risk.fee_policy).view() == final
    finally:
        reopened.close()
    stale_at = later + timedelta(seconds=31)
    context = risk._source_context(final, source.journal.read_evidence_sequence())
    with pytest.raises(JournalError, match="stale"):
        derive_source_registry(
            source.journal,
            context,
            capture_sequence=terminal.capture_sequence,
            decision_at=stale_at,
            max_age=timedelta(seconds=30),
            through_sequence=source.journal.read_evidence_sequence(),
        )
    # Contradictory raw account rows remain a permanent veto in this prefix.
    for name, bad_rows in (
        ("price", [final_rows[0] | {"price": "9.48"}, final_rows[1]]),
        ("extra", final_rows + [final_rows[0] | {"id": "unbound-source-fill"}]),
    ):
        bad = account_book(
            case, later, cash="498.98", position="0", source_fills=bad_rows
        )
        risk.observe("source-account-" + name, bad, later)
        assert not risk.view()["last_snapshot"]["qualified"]
        assert risk.view()["held"] is not None
    wrong_origin = account_book(
        case,
        later,
        cash="498.98",
        position="0",
        source_fills=final_rows,
        origin="BUILTIN_PAPER_HTTPS",
    )
    risk.observe("source-origin-mismatch", wrong_origin, later)
    assert not risk.view()["last_snapshot"]["qualified"]
    assert (
        AccountRiskOwner._read_native_causal_evidence_at(
            source.journal, REF, at, risk.fee_policy, through_sequence=horizon
        )
        == before_fault
    )


def test_source_native_http_exit_union_preserves_residual_risk(
    native_ready: Any,
) -> None:
    import base64

    from alpaca_ross_bot.crossed_stop_preparation import preparation_request
    from alpaca_ross_bot.native_risk_registry import POLICY as NATIVE_POLICY
    from alpaca_ross_bot.source_close_http import SourceCloseHttp
    from alpaca_ross_bot.source_close_http_capture import SourceCloseHttpCaptures
    from test_source_close_http import Exchange, api, submit
    from test_source_close_http_capture import MemoryHttpGets

    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, native_fixture, reported = book
    emergency.prepare_native_source(
        "http-native-residual",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
        for_handoff=True,
    )
    handoff = emergency.bind_source_intent("http-native-residual", at)
    cid = handoff["closes"]["http-native-residual"]["request"]["client_order_id"]
    risk.bind_exit("http-native-role", at, cid, "CLOSE")
    before = risk.view()
    oco_before, emergency_before = owner.view(), emergency.view()
    now = [at]
    exchange = Exchange(ACCOUNT, now)
    transport = api(exchange)
    fixture = (
        SourceCloseHttp(owner),
        native_fixture[1],
        dict(
            client_order_id=cid,
            attempt_id="http-native-attempt",
            preparation=preparation_request(
                assets, statuses, 0, timedelta(seconds=1), timedelta(seconds=1)
            )
            | {"ownership_policy": NATIVE_POLICY},
            decision_at=at,
            started_at=at,
            clock=lambda: now[0],
        ),
        exchange,
        transport,
        native_fixture[4],
        (source, risk, owner, emergency, at, assets, statuses),
        now,
    )
    attempt = submit(fixture)
    gets = MemoryHttpGets(
        json.loads(base64.b64decode(attempt["outcome"]["body_base64"]))
    )
    transport._exchange = gets
    case = SourceCloseHttpCaptures(fixture[0]), attempt, gets, fixture
    packet, rows = report(case, "1", terminal=True)
    index = SourceHttpCloseAttributions(source.journal, owner.initial)
    index.observe(
        "source-native-fill",
        at,
        client_order_id=cid,
        capture_sequences=(packet["capture_ref"]["sequence"],),
    )
    # The native group has already sold one share, its sibling protects one,
    # and the independent source residual sells the final unallocated share.
    native_open = [reported.order | {"legs": None}, reported.order["legs"][0]]
    native_rows = [row for values in reported.fills.values() for row in values]
    selection = account_book(
        case,
        at,
        cash="491.12",
        position="1",
        source_fills=rows,
        native_open=native_open,
        native_fills=native_rows,
    )
    risk.observe_source_native("source-native-account", selection, at)
    after = risk.view()
    assert after["last_snapshot"]["qualified"], after["last_snapshot"]
    observation = after["held"]["source_account_observation"]
    assert observation["filled_quantity"] == "3"
    assert observation["native_exit_quantity"] == "1"
    assert observation["source_exit_quantity"] == "1"
    assert observation["open_quantity"] == "1"
    assert after["held"]["open_quantity"] == 1
    assert after["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert after["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    assert (
        after["capacity"] == "0" and "SOURCE_CLOSE_OWNERSHIP_PENDING" in after["issues"]
    )
    assert after["completed"] == before["completed"]
    assert source.journal.read_order_send_started_at(cid) is None
    assert owner.view() == oco_before and emergency.view() == emergency_before
    assert AccountRiskOwner(source.journal, REF, risk.fee_policy).view() == after
    assert risk.verify_rebuild()


def test_unknown_source_post_cannot_qualify_reported_account(source_http: Any) -> None:
    from alpaca_ross_bot.source_close_http_capture import SourceCloseHttpCaptures
    from test_source_close_http import ORDER_ID, submit
    from test_source_close_http_capture import MemoryHttpGets

    http, _harness, _kwargs, exchange, transport, _formal, ready, now = source_http
    source, risk, owner, emergency, at, _, _ = ready
    before = risk.view()
    exchange.response = TimeoutError("synthetic transport unknown")
    attempt = submit(source_http)
    assert attempt["outcome"]["result_kind"] == "TRANSPORT_UNKNOWN"
    raw = json.loads(attempt["precall"]["request_body"]) | {
        "id": ORDER_ID,
        "status": "new",
        "filled_qty": "0",
        "asset_class": "us_equity",
        "order_class": "simple",
        "extended_hours": False,
        "created_at": at.isoformat(),
        "submitted_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }
    gets = MemoryHttpGets(raw)
    transport._exchange = gets
    case = SourceCloseHttpCaptures(http), attempt, gets, source_http
    packet, rows = report(case, "2", terminal=True)
    assert packet["assessment"]["association"] != "HTTP_POST_GET_IDENTITY_CONSISTENT"
    index = SourceHttpCloseAttributions(source.journal, owner.initial)
    with pytest.raises(JournalError):
        index.observe(
            "unknown-cannot-claim",
            at,
            client_order_id=gets.order["client_order_id"],
            capture_sequences=(packet["capture_ref"]["sequence"],),
        )
    selection = account_book(case, at, cash="498.98", position="0", source_fills=rows)
    risk.observe_source("unknown-account", selection, at)
    after = risk.view()
    assert not after["last_snapshot"]["qualified"]
    assert not after["last_snapshot"]["flat"]
    assert after["held"] is not None and after["completed"] == before["completed"]
    assert after["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert after["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    assert after["capacity"] == "0"
    assert (
        source.journal.read_order_send_started_at(gets.order["client_order_id"]) is None
    )
    assert len(exchange.posts) == 1
    assert emergency.view()["closes"]["residual"]["filled"] == "0"


def test_fresh_fill_page_does_not_refresh_old_order_get() -> None:
    from alpaca_ross_bot.source_risk_registry import _require_fresh_capture
    from test_observation_candidate_binding import AT

    recent = AT.isoformat()
    packet: dict[str, Any] = {
        "completed_at": recent,
        "requests": [
            {
                "path": "/v2/orders/synthetic?nested=true",
                "started_at": recent,
                "completed_at": recent,
            },
            {
                "path": "/v2/account/activities?activity_types=FILL",
                "started_at": recent,
                "completed_at": recent,
            },
        ],
    }
    _require_fresh_capture(packet, AT, timedelta(seconds=30))
    packet["requests"][0]["started_at"] = (AT - timedelta(seconds=31)).isoformat()
    with pytest.raises(JournalError, match="stale"):
        _require_fresh_capture(packet, AT, timedelta(seconds=30))
    packet["requests"][0]["started_at"] = recent
    packet["requests"][0]["completed_at"] = (AT - timedelta(seconds=31)).isoformat()
    with pytest.raises(JournalError, match="stale"):
        _require_fresh_capture(packet, AT, timedelta(seconds=30))


def test_source_account_activity_fold_rejects_changed_first_row() -> None:
    from alpaca_ross_bot.source_risk_registry import _account_activities

    exact = {
        "id": "source-activity",
        "activity_type": "FILL",
        "qty": "1",
        "price": "9.49",
        "optional": 1,
    }
    packet = {
        "requests": [
            {
                "path": "/v2/account/activities?direction=desc&page_size=100",
                "body": json.dumps([exact]),
            },
            {
                "path": (
                    "/v2/account/activities?direction=desc&page_size=100"
                    "&page_token=source-activity"
                ),
                "body": json.dumps([exact]),
            },
        ]
    }
    assert _account_activities(packet) == {"source-activity": exact}
    # The exact final row cannot hide an earlier different execution price.
    packet["requests"][0]["body"] = json.dumps([exact | {"price": "9.48"}])
    with pytest.raises(JournalError, match="contents changed"):
        _account_activities(packet)
    # Optional numeric/text fields also preserve their source JSON type.
    packet["requests"][0]["body"] = json.dumps([exact | {"optional": "1"}])
    with pytest.raises(JournalError, match="contents changed"):
        _account_activities(packet)
