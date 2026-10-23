"""Prepared requests through real collector code with synthetic GET exchanges."""

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.native_oco_capture import capture_native_oco_group
from alpaca_ross_bot.native_oco_correlation import NativeOcoCorrelations
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.paper_order_http import PaperHttpResponse, PaperOrderHttp
from test_account_risk_owner import SourceAccount
from test_native_oco_requests import prepared as prepared
from test_paper_order_http import CONFIG, CREDENTIALS
from test_risk_ledger_audit import ACCOUNT

ROOT = "aaaaaaaa-1111-4111-8111-111111111111"
STOP = "bbbbbbbb-2222-4222-8222-222222222222"
GROUP = "cccccccc-3333-4333-8333-333333333333"


class ReportedExchange:
    def __init__(self, body: str, when: datetime) -> None:
        request = json.loads(body)
        shared = {
            "group_id": GROUP,
            "symbol": request["symbol"],
            "side": "sell",
            "qty": request["qty"],
            "filled_qty": "0",
            "status": "new",
            "time_in_force": "day",
            "created_at": when.isoformat(),
            "updated_at": when.isoformat(),
            "submitted_at": when.isoformat(),
            "replaces": None,
            "replaced_by": None,
        }
        self.order: dict[str, Any] = shared | {
            "id": ROOT,
            "client_order_id": request["client_order_id"],
            "group_role": "take_profit",
            "order_class": "oco",
            "type": "limit",
            "limit_price": request["take_profit"]["limit_price"],
            "stop_price": None,
            "legs": [
                shared
                | {
                    "id": STOP,
                    "client_order_id": "independent-stop-id",
                    "group_role": "stop_loss",
                    "order_class": "simple",
                    "type": "stop",
                    "stop_price": request["stop_loss"]["stop_price"],
                    "limit_price": None,
                    "legs": None,
                }
            ],
        }
        self.fills: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        assert method == "GET" and body is None
        self.calls.append((method, path))
        if path == "/v2/account":
            value: object = ACCOUNT
        elif path == f"/v2/orders/{self.order['id']}?nested=true":
            value = self.order
        else:
            parsed = urlsplit(path)
            assert parsed.path == "/v2/account/activities"
            query = parse_qs(parsed.query)
            value = (
                []
                if "page_token" in query
                else self.fills.get(query["order_id"][0], [])
            )
        return PaperHttpResponse(200, json.dumps(value).encode())


def collect(source: SourceAccount, exchange: ReportedExchange, when: datetime) -> int:
    account_ref = source.journal.read_paper_account_ref()
    assert isinstance(account_ref, str)
    api = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        timeout_seconds=1,
        max_request_bytes=100_000,
        max_response_bytes=100_000,
        exchange=exchange,
    )
    return capture_native_oco_group(
        api,
        source.journal,
        account_ref=account_ref,
        broker_order_id=exchange.order["id"],
        clock=lambda: when,
        max_fill_pages=3,
    )


def test_get_association_never_creates_ack_send_or_risk_ownership(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    when = at + timedelta(seconds=1)
    exchange = ReportedExchange(original["receipt"]["request_body"], when)
    capture = collect(source, exchange, when)
    risk = source.journal.read_account_risk_events()
    orders = source.journal.read_order_events()
    intents = source.journal.read_order_intent_ids()
    oco = source.journal.read_oco_research_events()
    associations = NativeOcoCorrelations(requests)
    result = associations.observe(
        "obs-a",
        "group-a",
        capture,
        decision_at=when,
        max_age=timedelta(seconds=5),
        received_at=when,
    )
    assert result["receipt"]["status"] == "MATCHED_REPORTED_FIELDS"
    assert result["receipt"]["prepared_ref"] == original["request_ref"]
    assert result["send_proven"] is False and result["ack_proven"] is False
    assert not any(result["authority"].values())
    assert result["receipt"]["source_observation"]["accounting_complete"] is True
    assert source.journal.read_account_risk_events() == risk
    assert source.journal.read_order_events() == orders
    assert source.journal.read_order_intent_ids() == intents
    assert source.journal.read_oco_research_events() == oco
    assert source.journal.read_order_send_started_at("native-cid-a") is None
    assert owner.view().model.groups[0].acknowledgement == "UNKNOWN"
    assert all(method == "GET" for method, _ in exchange.calls)
    assert associations.verify_rebuild()


@pytest.mark.parametrize("adjusted", [True, False])
def test_partial_target_quantity_is_observed_arithmetic_not_protection(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    adjusted: bool,
) -> None:
    source, _, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    when = at + timedelta(seconds=1)
    exchange = ReportedExchange(original["receipt"]["request_body"], when)
    exchange.order["filled_qty"] = "0.25"
    exchange.order["status"] = "partially_filled"
    exchange.order["legs"][0]["qty"] = "0.75" if adjusted else "1"
    exchange.fills[ROOT] = [
        {
            "id": "partial-target-fill",
            "activity_type": "FILL",
            "order_id": ROOT,
            "symbol": "AAPL",
            "side": "sell",
            "qty": "0.25",
            "price": "11.03",
            "transaction_time": when.isoformat(),
        }
    ]
    capture = collect(source, exchange, when)
    associations = NativeOcoCorrelations(requests)
    result = associations.observe(
        "partial",
        "group-a",
        capture,
        decision_at=when,
        max_age=timedelta(seconds=5),
        received_at=when,
    )
    receipt = result["receipt"]
    assert receipt["quantity_relation"] == (
        "OBSERVED_RESIDUAL_AFTER_TARGET_FILLS"
        if adjusted
        else "QUANTITY_RELATION_UNQUALIFIED"
    )
    assert receipt["status"] == ("MATCHED_REPORTED_FIELDS" if adjusted else "BLOCKED")
    assert receipt["source_observation"]["fills"][0]["quantity"] == "1/4"
    assert not any(result["authority"].values())
    assert result["send_proven"] is False and result["ack_proven"] is False


def test_old_association_keeps_original_request_before_later_preparation_conflict(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    when = at + timedelta(seconds=1)
    capture = collect(
        source, ReportedExchange(original["receipt"]["request_body"], when), when
    )
    associations = NativeOcoCorrelations(requests)
    first = associations.observe(
        "original",
        "group-a",
        capture,
        decision_at=when,
        max_age=timedelta(seconds=5),
        received_at=when,
    )
    later = when + timedelta(seconds=1)
    requests.prepare("group-a", "different-cid", later, later)
    assert associations.verify_rebuild()
    retry = associations.observe(
        "original",
        "group-a",
        capture,
        decision_at=when,
        max_age=timedelta(seconds=5),
        received_at=later,
    )
    assert retry["receipt"] == first["receipt"]
    assert retry["association_ref"] == first["association_ref"]
    assert not any(retry["authority"].values())


def test_wrong_client_observation_is_retained_without_adopting_identity(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    when = at + timedelta(seconds=1)
    exchange = ReportedExchange(original["receipt"]["request_body"], when)
    exchange.order["client_order_id"] = "another-client"
    capture = collect(source, exchange, when)
    associations = NativeOcoCorrelations(requests)
    result = associations.observe(
        "wrong-client",
        "group-a",
        capture,
        decision_at=when,
        max_age=timedelta(seconds=5),
        received_at=when,
    )
    assert result["receipt"]["status"] == "BLOCKED"
    assert "ROOT_CLIENT_ID_MISMATCH" in result["receipt"]["issues"]
    assert (
        result["receipt"]["source_observation"]["orders"][0]["client_order_id"]
        == "another-client"
    )
    current = associations.view()
    assert current is not None and not current["assignments"]
    assert not any(result["authority"].values())


def test_changed_broker_identity_latches_conflict_and_preserves_first_assignment(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    when = at + timedelta(seconds=1)
    exchange = ReportedExchange(original["receipt"]["request_body"], when)
    capture = collect(source, exchange, when)
    associations = NativeOcoCorrelations(requests)
    associations.observe(
        "first",
        "group-a",
        capture,
        decision_at=when,
        max_age=timedelta(seconds=5),
        received_at=when,
    )
    before = associations.view()
    assert before is not None
    later = when + timedelta(seconds=1)
    changed = ReportedExchange(original["receipt"]["request_body"], later)
    changed.order["id"] = "dddddddd-4444-4444-8444-444444444444"
    capture = collect(source, changed, later)
    result = associations.observe(
        "changed",
        "group-a",
        capture,
        decision_at=later,
        max_age=timedelta(seconds=5),
        received_at=later,
    )
    assert result["receipt"]["status"] == "IDENTITY_CONFLICT"
    current = associations.view()
    assert current is not None
    assert current["assignments"] == before["assignments"]
    assert result["issues"]
    assert associations.verify_rebuild()


def test_balanced_exits_on_both_roles_remain_facts_without_owned_executions(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    when = at + timedelta(seconds=1)
    exchange = ReportedExchange(original["receipt"]["request_body"], when)
    exchange.order["filled_qty"] = "0.25"
    exchange.order["status"] = "partially_filled"
    exchange.order["legs"][0].update(
        qty="0.75", filled_qty="0.25", status="partially_filled"
    )
    for identity, price in ((ROOT, "11.03"), (STOP, "9.50")):
        exchange.fills[identity] = [
            {
                "id": "observed-" + identity,
                "activity_type": "FILL",
                "order_id": identity,
                "symbol": "AAPL",
                "side": "sell",
                "qty": "0.25",
                "price": price,
                "transaction_time": when.isoformat(),
            }
        ]
    capture = collect(source, exchange, when)
    before = owner.view()
    result = NativeOcoCorrelations(requests).observe(
        "both-roles",
        "group-a",
        capture,
        decision_at=when,
        max_age=timedelta(seconds=5),
        received_at=when,
    )
    assert result["receipt"]["status"] == "MATCHED_REPORTED_FIELDS"
    assert len(result["receipt"]["source_observation"]["fills"]) == 2
    assert owner.view() == before
    assert not any(result["authority"].values())
