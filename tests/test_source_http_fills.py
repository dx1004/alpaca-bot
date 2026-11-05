"""Authenticated HTTP FILL attribution and exact account-row corroboration."""

import base64
import json
from decimal import Decimal
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop_fills import derive_source_http_close_fills
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import _json
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.order_reconciliation import RestBatch
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.source_close_http import SourceCloseHttp
from alpaca_ross_bot.source_close_http_capture import SourceCloseHttpCaptures
from test_account_risk_owner import RECON_POLICY
from test_source_close_http import ORDER_ID, submit
from test_source_close_http import source_http as source_http_fixture
from test_source_close_http_capture import MemoryHttpGets, capture, fill
from test_source_close_http_capture import http_capture as http_capture_fixture

source_http = source_http_fixture
http_capture = http_capture_fixture


def partial_http(case: Any, *, two: bool = False) -> dict[str, Any]:
    gets, now = case[2], case[3][7][0]
    gets.order.update(
        status="partially_filled",
        filled_qty="0.5" if two else "0.25",
        filled_at=now.isoformat(),
    )
    rows = [fill(case, provider_optional_numeric=1.25)]
    if two:
        rows.append(
            fill(
                case,
                id="source-http-second-fill",
                cum_qty="0.5",
                leaves_qty=str(Decimal(gets.order["qty"]) - Decimal("0.5")),
            )
        )
    gets.pages = [_json(rows), b"[]"]
    return capture(case)


def derive_http(case: Any, *packets: dict[str, Any], **changes: Any) -> dict[str, Any]:
    fixture = case[3]
    return derive_source_http_close_fills(
        fixture[0].journal,
        fixture[6][2].initial,
        **(
            dict(
                client_order_id=fixture[2]["client_order_id"],
                capture_sequences=tuple(
                    packet["capture_ref"]["sequence"] for packet in packets
                ),
                through_sequence=fixture[0].journal.read_evidence_sequence(),
                decision_at=fixture[7][0],
            )
            | changes
        ),
    )


def rest_rows(
    case: Any, rows: list[dict[str, Any]], *, extra_lookup: bool = False
) -> None:
    fixture = case[3]
    source, _, _, _, at, _, _ = fixture[6]
    gets = case[2]
    lookups = {
        cid: PaperHttpResponse(200, _json(order))
        for cid, order in source.orders.items()
    }
    lookups[fixture[2]["client_order_id"]] = PaperHttpResponse(200, _json(gets.order))
    if extra_lookup:
        cid = "other-msft-identity"
        request = {
            "client_order_id": cid,
            "symbol": "MSFT",
            "qty": "1",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }
        source.journal.reserve_order_intent(
            cid,
            {
                "paper_endpoint": "https://paper-api.alpaca.markets",
                "paper_account_ref": source.journal.read_paper_account_ref(),
                "purpose": "ENGINEERING_TEST",
                "request_body": _json(request).decode(),
            },
        )
        other = source.order(cid, at, "new", 0) | {"id": gets.order["id"]}
        lookups[cid] = PaperHttpResponse(200, _json(other))
    position = sum(
        (
            Decimal(str(order["filled_qty"]))
            for order in source.orders.values()
            if order["symbol"] == gets.order["symbol"] and order["side"] == "buy"
        ),
        Decimal(0),
    ) - Decimal(gets.order["filled_qty"])
    source.reconciler.apply_rest_batch(
        RestBatch(
            source.journal.read_paper_account_ref(),
            at,
            at,
            source.reconciler.view()["watermark"],
            (dict(gets.order),),
            (
                {
                    "symbol": gets.order["symbol"],
                    "qty": str(position),
                    "side": "long",
                    "asset_class": "us_equity",
                },
            ),
            lookups,
            {gets.order["id"]: tuple(rows)},
            True,
            True,
        ),
        RECON_POLICY,
        at=at,
    )


def test_http_cumulative_reopen_causal_reader_and_full_raw_identity(
    http_capture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = partial_http(http_capture)
    second = partial_http(http_capture, two=True)
    fixture = http_capture[3]
    journal, initial = fixture[0].journal, fixture[6][2].initial
    before = tuple(journal._db.iterdump())

    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("current constructor entered HTTP attribution")

    with monkeypatch.context() as context:
        context.setattr(AccountRiskOwner, "__init__", deny)
        context.setattr(OcoJournal, "__init__", deny)
        context.setattr(SourceCloseHttp, "__init__", deny)
        result = derive_http(http_capture, first, second)
        assert (
            result["classification"]
            == "INJECTED_SOURCE_HTTP_CLOSE_FILL_ATTRIBUTION_COMPONENT"
        )
        assert (
            result["origin"] == "INJECTED_EXCHANGE"
            and result["component_attributed"] is True
        )
        assert (
            result["cumulative_fill_quantity"] == "1/2"
            and len(result["executions"]) == 2
        )
        assert (
            result["original_portions"]
            == http_capture[1]["precall"]["qualification"]["handoff"]["portions"]
        )
        assert result["finality"] == "UNKNOWN" and all(
            value is False for value in result["authority"].values()
        )
        other = Journal.open_private(journal._path, journal._runtime_root)
        try:
            assert (
                derive_source_http_close_fills(
                    other,
                    initial,
                    client_order_id=fixture[2]["client_order_id"],
                    capture_sequences=(
                        first["capture_ref"]["sequence"],
                        second["capture_ref"]["sequence"],
                    ),
                    through_sequence=other.read_evidence_sequence(),
                    decision_at=fixture[7][0],
                )
                == result
            )
        finally:
            other.close()
    assert tuple(journal._db.iterdump()) == before and len(fixture[3].posts) == 1
    # Optional raw numeric corrections veto an otherwise identical execution ID.
    rows = [
        fill(http_capture, provider_optional_numeric=2.5),
        fill(
            http_capture,
            id="source-http-second-fill",
            cum_qty="0.5",
            leaves_qty=str(Decimal(http_capture[2].order["qty"]) - Decimal("0.5")),
        ),
    ]
    http_capture[2].pages = [_json(rows), b"[]"]
    changed = capture(http_capture)
    with pytest.raises(JournalError, match="contents changed"):
        derive_http(http_capture, changed)


def test_exact_account_rest_corroborates_but_other_owner_and_mismatches_veto(
    http_capture: Any,
) -> None:
    packet = partial_http(http_capture)
    raw_rows = json.loads(base64.b64decode(packet["requests"][3]["body_base64"]))
    rest_rows(http_capture, raw_rows)
    assert derive_http(http_capture, packet)["component_attributed"] is True
    source = http_capture[3][6][0]
    cid = http_capture[3][2]["client_order_id"]
    assert cid not in source.reconciler.view()["orders"]
    assert not source.journal.has_order_send_attempt(cid)
    rest_rows(http_capture, raw_rows, extra_lookup=True)
    result = derive_http(http_capture, packet)
    assert result["component_attributed"] is False
    assert "SIMPLE_REST_ORDER_UUID_REUSE" in result["issues"]
    rest_rows(
        http_capture,
        [
            raw_rows[0] | {"price": "9.30"},
            raw_rows[0] | {"id": "unindexed-source-fill"},
        ],
    )
    result = derive_http(http_capture, packet)
    assert "SIMPLE_REST_ACTIVITY_ID_REUSE" in result["issues"]
    assert "SOURCE_REST_ACTIVITY_UNBOUND" in result["issues"]
    source.reconciler.ingest_frame(
        _json(
            {
                "stream": "trade_updates",
                "data": {
                    "event": "partial_fill",
                    "timestamp": http_capture[3][7][0].isoformat(),
                    "order": dict(http_capture[2].order),
                },
            }
        ),
        http_capture[3][7][0],
        "source-fixture",
    )
    assert (
        "SOURCE_CID_ALREADY_SIMPLE_PROJECTED"
        in derive_http(http_capture, packet)["issues"]
    )


def test_unknown_authenticated_outcome_cannot_be_adopted(source_http: Any) -> None:
    source_http[3].response = TimeoutError("synthetic unknown")
    row = submit(source_http)
    assert row["outcome"]["result_kind"] == "TRANSPORT_UNKNOWN"
    body = json.loads(row["precall"]["request_body"])
    at = source_http[7][0]
    snapshot = body | {
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
    gets = MemoryHttpGets(snapshot)
    source_http[4]._exchange = gets
    case = SourceCloseHttpCaptures(source_http[0]), row, gets, source_http
    packet = capture(case)
    with pytest.raises(JournalError, match="confirmed"):
        derive_http(case, packet)
    assert len(source_http[3].posts) == 1
