"""Full-book native accounting through source-shaped injected evidence only."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.incident_risk import IncidentRiskRequest
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.native_oco_http import NativeOcoHttp
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.native_risk_registry import derive_native_registry
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from alpaca_ross_bot.order_reconciliation import RestBatch
from alpaca_ross_bot.owned_equity_ledger import audit_owned_equity
from alpaca_ross_bot.paper_engineering import EngineeringHarness, EngineeringScope
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, KIND, SCHEMA_V2, SOURCE
from test_account_risk_owner import RECON_POLICY, SourceAccount, account_sources
from test_native_oco_correlation_integration import ROOT, ReportedExchange
from test_native_oco_http import Exchange, api
from test_observation_candidate_binding import AT, CID, new_decision
from test_oco_entry_evidence import _initial, _prepare
from test_oco_exit_evidence import capture
from test_risk_ledger_audit import ACCOUNT, BEFORE, POLICY, REF, calendar, request
from test_risk_marks import QUOTE, URL, put


@pytest.fixture
def native_risk_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[
    tuple[SourceAccount, AccountRiskOwner, OcoJournal, Any, ReportedExchange]
]:
    from alpaca_ross_bot import paper_order_http

    now = [BEFORE]

    class Clock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now[0]

    monkeypatch.setattr(paper_order_http, "datetime", Clock)
    original_record = OcoJournal._record

    def timed_record(
        self: OcoJournal, kind: str, operation: dict[str, object], at: datetime
    ) -> Any:
        return original_record(self, kind, operation, AT if kind == "BIND" else at)

    monkeypatch.setattr(OcoJournal, "_record", timed_record)
    formal = ACCOUNT | {"id": "22222222-2222-4222-8222-222222222222"}
    root = tmp_path / "private"
    harness = EngineeringHarness(
        root,
        EngineeringScope(
            "native-risk-book",
            REF,
            paper_account_ref(json.dumps(formal).encode()),
            tmp_path / "formal",
        ),
        clock=lambda: now[0],
    )
    open_private = Journal.open_private
    claimed = False

    def shared(cls: type[Journal], path: Path, runtime_root: Path) -> Journal:
        nonlocal claimed
        if path == root / "journal.sqlite3" and not claimed:
            claimed = True
            return harness.journal
        return open_private(path, runtime_root)

    monkeypatch.setattr(Journal, "open_private", classmethod(shared))
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        partial_at = _prepare(source, risk, new_decision())
        at = partial_at + timedelta(seconds=1)
        source.order(CID, at, "canceled", 2)
        risk.observe("terminal-entry", source.selection(at, cash="480", position=2), at)
        owner = OcoJournal(source.journal, _initial())
        bound = owner.bind_entry_census("terminal-entry", at, POLICY, at)
        owner.allocate_group(
            "group-a",
            (LotPortion(bound.model.executions[0].execution_id, Fraction(2)),),
            at,
        )
        requests = NativeOcoRequests(owner)
        prepared = requests.prepare("group-a", "native-cid-a", at, at)
        reported = ReportedExchange(prepared["receipt"]["request_body"], at)
        exchange = Exchange(
            ACCOUNT, PaperHttpResponse(201, json.dumps(reported.order).encode())
        )
        now[0] = at
        fixture = (
            NativeOcoHttp(requests),
            harness,
            at,
            exchange,
            api(Exchange(formal)),
        )
        api(exchange).post_native_oco_once(
            fixture[0],
            harness,
            formal_api=fixture[4],
            group_id="group-a",
            decision_at=at,
            started_at=at,
            clock=lambda: at,
        )
        yield source, risk, owner, fixture, reported


def full_book(
    source: SourceAccount,
    reported: ReportedExchange,
    at: datetime,
    *,
    cash: str,
    position: int | Decimal,
) -> IncidentRiskRequest:
    """Retain genuine lifecycle differences; never invent native simple intents."""
    native = [reported.order | {"legs": None}, reported.order["legs"][0]]
    opened = tuple(
        row
        for row in native
        if row["status"] not in {"filled", "canceled", "expired", "rejected"}
    )
    positions: tuple[dict[str, object], ...] = (
        (
            {
                "symbol": "AAPL",
                "qty": str(position),
                "side": "long",
                "asset_class": "us_equity",
                "market_value": "9999",
            },
        )
        if position
        else ()
    )
    grouped = {
        str(order["id"]): tuple(
            row for row in source.fills if row["order_id"] == order["id"]
        )
        for order in source.orders.values()
    }
    source.reconciler.apply_rest_batch(
        RestBatch(
            REF,
            at,
            at,
            cast(int, source.reconciler.view()["watermark"]),
            opened,
            positions,
            {
                cid: PaperHttpResponse(200, json.dumps(row).encode())
                for cid, row in source.orders.items()
            },
            grouped,
            True,
            True,
        ),
        RECON_POLICY,
        at=at,
    )
    activities = source.fills + [
        row for rows in reported.fills.values() for row in rows
    ]
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
    seq = source.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(
            {
                "schema": SCHEMA_V2,
                "transport_origin": "INJECTED_EXCHANGE",
                "account_ref": REF,
                "endpoint": ENDPOINT,
                "requests": rows,
            }
        ).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence
    marks: tuple[tuple[str, int, int, int], ...] = ()
    if position:
        qseq = put(
            source.journal,
            [
                QUOTE
                | {"bp": "10", "ap": "10", "t": at.isoformat().replace("+00:00", "Z")}
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


def test_native_full_book_partial_then_terminal_roundtrip_and_replay(
    native_risk_book: Any,
) -> None:
    source, risk, owner, fixture, reported = native_risk_book
    before = risk.view()
    assert before is not None
    reserve = Fraction(before["pending_open_loss_reserve"])
    at = fixture[2] + timedelta(seconds=1)
    reported.order.update(
        status="partially_filled", filled_qty="1", updated_at=at.isoformat()
    )
    reported.order["legs"][0].update(updated_at=at.isoformat())
    reported.fills[ROOT] = [
        {
            "id": "native-target-1",
            "activity_type": "FILL",
            "order_id": ROOT,
            "symbol": "AAPL",
            "side": "sell",
            "qty": "1",
            "price": "11.03",
            "transaction_time": at.isoformat(),
        }
    ]
    owner.bind_native_exit_census(
        "native-partial",
        "group-a",
        capture(fixture, reported, at),
        at,
        timedelta(seconds=30),
        at,
    )
    first_selection = full_book(source, reported, at, cash="491.03", position=1)
    horizon = source.journal.read_evidence_sequence()
    registry = derive_native_registry(
        source.journal,
        risk._native_context(before, horizon),
        capture_sequence=first_selection.capture_sequence,
        decision_at=at,
        max_age=first_selection.max_age,
        through_sequence=horizon,
    )
    audit = audit_owned_equity(
        source.journal,
        first_selection.capture_sequence,
        first_selection.calendar_sequence,
        account_ref=REF,
        decision_at=at,
        max_age=first_selection.max_age,
        fee_policy=POLICY,
        mark_refs=first_selection.mark_refs,
        max_metadata_age=first_selection.max_metadata_age,
        _intent_ids=source.journal.read_order_intent_ids(),
        _source_horizon=horizon,
        _native_registry=registry,
    )
    assert audit.reported_conservation_verified, audit.issues
    risk.observe_native("native-risk-partial", first_selection, at)
    partial = risk.view()
    assert partial is not None
    assert partial["last_snapshot"]["qualified"], partial["last_snapshot"]["issues"]
    assert partial["held"] is not None
    assert partial["held"]["native_ownership"]["exit_quantity"] == "1"
    assert Fraction(partial["pending_open_loss_reserve"]) >= reserve
    assert source.journal.read_order_intent_ids() == (CID,)
    later = at + timedelta(seconds=1)
    reported.order.update(status="filled", filled_qty="2", updated_at=later.isoformat())
    reported.order["legs"][0].update(status="canceled", updated_at=later.isoformat())
    reported.fills[ROOT].append(
        reported.fills[ROOT][0]
        | {"id": "native-target-2", "transaction_time": later.isoformat()}
    )
    owner.bind_native_exit_census(
        "native-terminal",
        "group-a",
        capture(fixture, reported, later),
        later,
        timedelta(seconds=30),
        later,
    )
    final_selection = full_book(source, reported, later, cash="502.06", position=0)
    risk.observe("native-risk-terminal", final_selection, later)
    final = risk.view()
    assert final is not None
    assert final["last_snapshot"]["qualified"], final["last_snapshot"]["issues"]
    assert final["held"] is None
    assert final["completed"][CID]["native_exit_quantity"] == "2"
    assert final["runtime_authorized"] is False
    rows = source.journal.read_account_risk_events()
    reopened = AccountRiskOwner(source.journal, REF, POLICY)
    assert reopened.view() == final
    reopened.observe_native("native-risk-partial", first_selection, at)
    reopened.observe("native-risk-terminal", final_selection, later)
    assert reopened.view() == final
    assert source.journal.read_account_risk_events() == rows
    assert reopened.verify_rebuild()
    next_at = later + timedelta(seconds=1)
    refreshed = full_book(source, reported, next_at, cash="502.06", position=0)
    reopened.observe("native-after-completion", refreshed, next_at)
    after = reopened.view()
    assert after is not None and after["last_snapshot"]["qualified"], after
    assert after["completed"] == final["completed"]
    assert after["held"] is None
    assert AccountRiskOwner(source.journal, REF, POLICY).view() == after


def first_native_fill(
    book: Any,
    *,
    quantity: str = "1",
    price: str = "11.03",
    position: int | Decimal = 1,
    cash: str = "491.03",
) -> tuple[datetime, IncidentRiskRequest]:
    source, _risk, owner, fixture, reported = book
    at = fixture[2] + timedelta(seconds=1)
    reported.order.update(
        status="partially_filled", filled_qty=quantity, updated_at=at.isoformat()
    )
    reported.order["legs"][0].update(updated_at=at.isoformat())
    reported.fills[ROOT] = [
        {
            "id": "native-target-1",
            "activity_type": "FILL",
            "order_id": ROOT,
            "symbol": "AAPL",
            "side": "sell",
            "qty": quantity,
            "price": price,
            "transaction_time": at.isoformat(),
        }
    ]
    owner.bind_native_exit_census(
        "native-partial",
        "group-a",
        capture(fixture, reported, at),
        at,
        timedelta(seconds=30),
        at,
    )
    return at, full_book(source, reported, at, cash=cash, position=position)


def test_unbound_account_native_fill_blocks_release_and_preserves_prior_risk(
    native_risk_book: Any,
) -> None:
    source, risk, _owner, _fixture, reported = native_risk_book
    at, selection = first_native_fill(native_risk_book)
    risk.observe_native("native-partial", selection, at)
    partial = risk.view()
    assert partial is not None and partial["last_snapshot"]["qualified"]
    later = at + timedelta(seconds=1)
    reported.fills[ROOT].append(
        reported.fills[ROOT][0]
        | {"id": "unbound-later-native-fill", "transaction_time": later.isoformat()}
    )
    # The account sees another fill, but no matching updated native GET census
    # has been bound. Flat account bytes alone cannot discharge the reservation.
    current = full_book(source, reported, later, cash="502.06", position=0)
    risk.observe("native-unbound", current, later)
    blocked = risk.view()
    assert blocked is not None and not blocked["last_snapshot"]["qualified"]
    assert {
        key: value
        for key, value in blocked["held"].items()
        if key != "native_source_observation"
    } == {
        key: value
        for key, value in partial["held"].items()
        if key != "native_source_observation"
    }
    diagnostic = blocked["held"]["native_source_observation"]
    assert diagnostic["exit_quantity"] == "1"
    assert diagnostic["account_economics_qualified"] is False
    assert diagnostic["runtime_authorized"] is False
    assert blocked["held"]["native_ownership"]["open_quantity"] == "1"
    assert blocked["completed"] == {}
    assert blocked["capacity"] == "0"
    assert Fraction(blocked["pending_open_loss_reserve"]) >= Fraction(
        partial["pending_open_loss_reserve"]
    )
    assert AccountRiskOwner(source.journal, REF, POLICY).view() == blocked


def test_fractional_native_economics_are_exact_while_risk_remains_frozen(
    native_risk_book: Any,
) -> None:
    source, risk, _owner, _fixture, _reported = native_risk_book
    before = risk.view()
    assert before is not None
    at, selection = first_native_fill(
        native_risk_book,
        quantity="0.5",
        price="11.04",
        position=Decimal("1.5"),
        cash="485.52",
    )
    risk.observe_native("native-fractional", selection, at)
    state = risk.view()
    assert state is not None
    assert state["last_snapshot"]["qualified"], state["last_snapshot"]["issues"]
    assert state["held"]["native_ownership"]["exit_quantity"] == "1/2"
    assert state["held"]["native_ownership"]["open_quantity"] == "3/2"
    assert state["held"]["quantity_incident"]["reservation_frozen"] is True
    assert "UNSUPPORTED_FRACTIONAL_RISK_QUANTITY" in state["issues"]
    assert state["capacity"] == "0" and state["completed"] == {}
    assert Fraction(state["pending_open_loss_reserve"]) >= Fraction(
        before["pending_open_loss_reserve"]
    )
    assert state["runtime_authorized"] is False
    assert AccountRiskOwner(source.journal, REF, POLICY).view() == state
