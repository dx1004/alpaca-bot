"""Source-shaped account risk owner integration; no broker or injected resolver."""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import pytest

from alpaca_ross_bot import risk_baseline
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner, EntryRiskPlan
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.incident_risk import IncidentRiskRequest
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.order_reconciliation import (
    OrderReconciler,
    ReconciliationPolicy,
    RestBatch,
)
from alpaca_ross_bot.owned_equity_ledger import audit_owned_equity
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, KIND, SCHEMA, SOURCE
from test_risk_ledger_audit import ACCOUNT, BEFORE, NOW, POLICY, REF, calendar, request
from test_risk_marks import QUOTE, URL, put

PLAN = EntryRiskPlan("risk-entry-1", "AAPL", "10", "9.50")
RECON_POLICY = ReconciliationPolicy(timedelta(seconds=30), timedelta(seconds=5), 4)


class SourceAccount:
    """Retain raw account bytes plus the actual lifecycle evidence they describe."""

    def __init__(self, journal: Journal, reconciler: OrderReconciler) -> None:
        self.journal, self.reconciler = journal, reconciler
        self.orders: dict[str, dict[str, object]] = {}
        self.fills: list[dict[str, object]] = []

    def intent(
        self,
        cid: str,
        at: datetime,
        *,
        side: str = "buy",
        order_type: str = "limit",
        qty: int = 4,
        price: str = "10",
        sent: bool = True,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "client_order_id": cid,
            "symbol": "AAPL",
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": "day",
        }
        if order_type in {"limit", "stop"}:
            body["limit_price" if order_type == "limit" else "stop_price"] = price
        self.journal.reserve_order_intent(
            cid,
            {
                "paper_endpoint": ENDPOINT,
                "paper_account_ref": REF,
                "purpose": "ENGINEERING_TEST",
                "request_body": json.dumps(body, sort_keys=True, separators=(",", ":")),
            },
        )
        if sent:
            self.journal.mark_order_send_attempt(cid, at)
        return body

    def order(
        self, cid: str, at: datetime, status: str, filled: int | Decimal
    ) -> dict[str, object]:
        intent = self.journal.read_order_intent(cid)
        assert isinstance(intent, dict)
        body = json.loads(str(intent["request_body"]))
        order = body | {
            "id": str(uuid5(NAMESPACE_URL, cid)),
            "status": status,
            "filled_qty": str(filled),
            "asset_class": "us_equity",
            "order_class": "simple",
            "extended_hours": False,
            "updated_at": at.isoformat(),
        }
        self.orders[cid] = order
        return cast(dict[str, object], order)

    def fill(self, cid: str, at: datetime, qty: int | Decimal, price: str) -> None:
        self.fills.append(
            {
                "id": "execution-" + str(len(self.fills)),
                "activity_type": "FILL",
                "order_id": self.orders[cid]["id"],
                "symbol": "AAPL",
                "side": self.orders[cid]["side"],
                "qty": str(qty),
                "price": price,
                "transaction_time": at.isoformat(),
            }
        )

    def selection(
        self,
        at: datetime,
        *,
        cash: str = "500",
        power: str | None = None,
        position: int | Decimal = 0,
        bid: str = "10",
    ) -> IncidentRiskRequest:
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
        open_orders = tuple(
            row
            for row in self.orders.values()
            if row["status"] not in {"filled", "canceled", "expired", "rejected"}
        )
        grouped = {
            str(order["id"]): tuple(
                row for row in self.fills if row["order_id"] == order["id"]
            )
            for order in self.orders.values()
        }
        report = self.reconciler.apply_rest_batch(
            RestBatch(
                account_ref=REF,
                started_at=at,
                completed_at=at,
                local_revision=cast(int, self.reconciler.view()["watermark"]),
                open_orders=open_orders,
                positions=positions,
                lookups={
                    cid: PaperHttpResponse(200, json.dumps(row).encode())
                    for cid, row in self.orders.items()
                },
                activities=grouped,
                complete=True,
                stable=True,
            ),
            RECON_POLICY,
            at=at,
        )
        assert report.status == "MATCHED", report.differences
        rows = [
            request(
                "/v2/account",
                ACCOUNT
                | {
                    "cash": cash,
                    "equity": cash,
                    "buying_power": cash if power is None else power,
                    "non_marginable_buying_power": cash if power is None else power,
                    "multiplier": "1",
                },
                at,
            ),
            request("/v2/positions", positions, at),
            request("/v2/orders?status=open&limit=500&nested=false", open_orders, at),
            request(
                "/v2/account/activities?direction=desc&page_size=100", self.fills, at
            ),
        ]
        if self.fills:
            rows.append(
                request(
                    "/v2/account/activities?direction=desc&page_size=100&page_token="
                    + str(self.fills[-1]["id"]),
                    [],
                    at,
                )
            )
        seq = self.journal.append_next(
            source_id=SOURCE,
            feed="paper",
            event_kind=KIND,
            payload=json.dumps(
                {
                    "schema": SCHEMA,
                    "account_ref": REF,
                    "endpoint": ENDPOINT,
                    "requests": rows,
                }
            ).encode(),
            system_first_seen_at=at,
            processed_at=at,
        ).sequence
        cal = calendar(self.journal, at)
        marks: tuple[tuple[str, int, int, int], ...] = ()
        if position:
            qseq = put(
                self.journal,
                [
                    QUOTE
                    | {"bp": bid, "ap": bid, "t": at.isoformat().replace("+00:00", "Z")}
                ],
                at=at,
            )
            mseq = put(
                self.journal,
                {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
                metadata=True,
                at=at,
            )
            marks = (("AAPL", qseq, 0, mseq),)
        return IncidentRiskRequest(
            seq, cal, timedelta(seconds=30), timedelta(days=1), marks
        )


@contextmanager
def account_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, freeze: bool = True
) -> Iterator[tuple[SourceAccount, AccountRiskOwner]]:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), BEFORE
        )
        reconciler = OrderReconciler(journal, REF, clock=lambda: BEFORE)
        first = RestBatch(
            REF,
            BEFORE,
            BEFORE,
            cast(int, reconciler.view()["watermark"]),
            (),
            (),
            {},
            {},
            True,
            True,
        )
        report = reconciler.apply_rest_batch(first, RECON_POLICY, at=BEFORE)
        reconciler.register_position_baseline(report.watermark, RECON_POLICY)
        for status in ("CONNECTING", "AUTHENTICATED", "SUBSCRIBED"):
            reconciler.stream_health(status, BEFORE, "source-fixture")
        source = SourceAccount(journal, reconciler)
        old = source.selection(BEFORE)
        if freeze:
            monkeypatch.setattr(risk_baseline, "_now", lambda: BEFORE)
            risk_baseline.freeze_baseline(
                journal,
                account_ref=REF,
                account_sequence=old.capture_sequence,
                calendar_sequence=old.calendar_sequence,
                session_day=BEFORE.date(),
                max_age=timedelta(seconds=30),
            )
        yield source, AccountRiskOwner(journal, REF, POLICY)
    finally:
        journal.close()


def view(owner: AccountRiskOwner) -> dict[str, Any]:
    state = owner.view()
    assert state is not None
    return state


def money(value: Fraction) -> str:
    cents = value * 100
    assert cents.denominator == 1
    return f"{cents.numerator // 100}.{cents.numerator % 100:02d}"


def test_missing_real_e0_vetoes_without_synthesizing_target_equity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch, freeze=False) as (source, owner):
        decision = owner.reserve("first", source.selection(NOW), NOW, PLAN)
        assert decision["quantity"] == 0
        state = view(owner)
        assert "PRESESSION_E0_MISSING" in state["issues"]
        assert state["e0"] is None and state["held"] is None
        assert not state["source_snapshot_qualified"]
        assert owner.verify_rebuild()


def test_initial_reservation_is_largest_whole_fee_bounded_quantity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        decision = owner.reserve("first", source.selection(NOW), NOW, PLAN)
        assert decision["quantity"] == 4 and decision["reason"] == "FEASIBLE"
        state = view(owner)
        assert state["e0"] == "500" and state["net"] == "0"
        assert state["held"]["pending_quantity"] == 4
        assert Fraction(state["pending_open_loss_reserve"]) == Fraction(203, 100)
        assert Fraction(state["additional_local_cash_hold"]) == Fraction(4001, 100)
        assert Fraction(state["capacity"]) == Fraction(297, 100)
        assert (
            not decision["runtime_authorized"]
            and not decision["strategy_plan_qualified"]
        )
        assert source.journal.read_order_intent_ids() == ()
        assert owner.verify_rebuild()


def test_two_private_connections_serialize_one_risk_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        barrier = Barrier(2)
        root = tmp_path / "private"

        def reserve(cid: str) -> dict[str, object]:
            journal = Journal.open_private(root / "journal.sqlite3", root)
            try:
                contender = AccountRiskOwner(journal, REF, POLICY)
                barrier.wait(timeout=10)
                return contender.reserve(
                    cid, selection, NOW, EntryRiskPlan(cid, "AAPL", "10", "9.50")
                )
            finally:
                journal.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ("left", "right")))
        assert sorted(cast(int, row["quantity"]) for row in results) == [0, 4]
        assert {row["reason"] for row in results} == {"FEASIBLE", "SLOT_OCCUPIED"}
        assert view(owner)["held"]["quantity"] == 4
        assert len(view(owner)["used_plan_ids"]) == 1
        assert owner.verify_rebuild()


def test_exact_duplicate_is_idempotent_but_changed_event_identity_latches_veto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        first = owner.reserve("stable", selection, NOW, PLAN)
        state = view(owner)
        assert owner.reserve("stable", selection, NOW, PLAN) == first
        assert view(owner) == state
        assert len(source.journal.read_account_risk_events()) == 1
        conflict = owner.reserve(
            "stable", selection, NOW, EntryRiskPlan("changed", "AAPL", "10", "9.50")
        )
        assert conflict["quantity"] == 0 and conflict["reason"] == "EVENT_ID_CONFLICT"
        assert view(owner)["veto"] and view(owner)["capacity"] == "0"
        assert owner.verify_rebuild()


def test_later_intent_and_send_do_not_rewrite_reservation_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        owner.reserve("first", selection, NOW, PLAN)
        original = view(owner)
        original_event = source.journal.read_account_risk_events()[0]
        source.intent(PLAN.client_order_id, NOW + timedelta(seconds=1), sent=False)
        source.journal.mark_order_send_attempt(
            PLAN.client_order_id, NOW + timedelta(seconds=2)
        )
        restarted = AccountRiskOwner(source.journal, REF, POLICY)
        assert view(restarted) == original
        assert source.journal.read_account_risk_events()[0] == original_event
        assert restarted.verify_rebuild()


def test_exact_retry_after_later_intent_send_keeps_original_event_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        expected = owner.reserve("first", selection, NOW, PLAN)
        original = view(owner)
        source.intent(PLAN.client_order_id, NOW + timedelta(seconds=1))
        assert owner.reserve("first", selection, NOW, PLAN) == expected
        assert view(owner) == original
        assert len(source.journal.read_account_risk_events()) == 1
        assert owner.verify_rebuild()


def test_unsent_plan_needs_explicit_abandonment_and_old_identity_stays_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        owner.reserve("first", selection, NOW, PLAN)
        owner.observe("refresh", selection, NOW)
        assert view(owner)["held"] is not None
        owner.abandon_unsent("abandon", selection, NOW)
        assert view(owner)["held"] is None
        assert view(owner)["pending_open_loss_reserve"] == "0"
        duplicate = owner.reserve("reuse", selection, NOW, PLAN)
        assert duplicate["reason"] == "PLAN_ID_REUSED" and duplicate["quantity"] == 0
        assert owner.verify_rebuild()


def test_partial_fill_retains_pending_risk_without_double_counting_broker_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        owner.reserve("first", source.selection(NOW), NOW, PLAN)
        at = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, at)
        source.order(PLAN.client_order_id, at, "partially_filled", 2)
        source.fill(PLAN.client_order_id, at, 2, "10")
        selection = source.selection(at, cash="480", power="460", position=2)
        audit = audit_owned_equity(
            source.journal,
            selection.capture_sequence,
            selection.calendar_sequence,
            account_ref=REF,
            decision_at=at,
            max_age=selection.max_age,
            fee_policy=POLICY,
            mark_refs=selection.mark_refs,
            max_metadata_age=selection.max_metadata_age,
        )
        assert audit.reported_conservation_verified, audit.issues
        owner.observe("partial", selection, at)
        state = view(owner)
        assert state["source_snapshot_qualified"] and state["issues"] == [], state[
            "last_snapshot"
        ]
        assert state["held"]["pending_quantity"] == state["held"]["open_quantity"] == 2
        assert state["held"]["broker_hold_represented"]
        assert Fraction(state["pending_open_loss_reserve"]) == Fraction(205, 100)
        assert Fraction(state["additional_local_cash_hold"]) == Fraction(1, 100)
        assert Fraction(state["last_snapshot"]["funds"]) == 460
        assert owner.verify_rebuild()


@pytest.mark.parametrize("later_whole", [False, True])
def test_fractional_partial_observation_is_durable_and_latches_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, later_whole: bool
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        owner.reserve("first", source.selection(NOW), NOW, PLAN)
        before = view(owner)
        at = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, at)
        source.order(PLAN.client_order_id, at, "partially_filled", Decimal("0.5"))
        source.fill(PLAN.client_order_id, at, Decimal("0.5"), "10")
        selection = source.selection(
            at, cash="495", power="460", position=Decimal("0.5")
        )
        owner.observe("fractional", selection, at)
        state = view(owner)
        assert state["watermark"] > before["watermark"]
        assert not state["source_snapshot_qualified"]
        assert "REPORTED_INPUTS_UNQUALIFIED" in state["issues"]
        assert state["capacity"] == "0"
        assert {
            key: value
            for key, value in state["held"].items()
            if key != "quantity_incident"
        } == before["held"]
        assert not state["held"]["quantity_incident"]["source_snapshot_qualified"]
        assert state["last_snapshot"]["positions"] == {"AAPL": "1/2"}
        event, raw = source.journal.read_account_risk_events()[-1]
        legacy = json.loads(raw)
        assert legacy.pop("quantity_incident_policy") == "LATCH_UNSUPPORTED_FRACTION_V1"
        historical = owner._reduce(before, legacy, False, event.sequence)
        assert "quantity_incident" not in historical["held"]
        assert historical["held"] == before["held"]
        assert owner.verify_rebuild()
        # Even after the fractional shares exit and account becomes flat,
        # unsupported entry quantity must not discard the risk observation.
        later = at + timedelta(seconds=1)
        final_qty = 4 if later_whole else Decimal("0.5")
        final_cash = "498" if later_whole else "499.75"
        source.order(
            PLAN.client_order_id,
            later,
            "filled" if later_whole else "canceled",
            final_qty,
        )
        if later_whole:
            source.fill(PLAN.client_order_id, later, Decimal("3.5"), "10")
        source.intent(
            "stop-fraction", later, side="sell", order_type="stop", price="9.50"
        )
        owner.bind_exit("bind-fraction-stop", later, "stop-fraction", "STOP")
        source.order(
            "stop-fraction", later, "filled" if later_whole else "canceled", final_qty
        )
        source.fill("stop-fraction", later, final_qty, "9.50")
        owner.observe(
            "fractional-flat", source.selection(later, cash=final_cash), later
        )
        state = view(owner)
        assert state["source_snapshot_qualified"]
        assert "UNSUPPORTED_FRACTIONAL_RISK_QUANTITY" in state["issues"]
        assert state["held"]["quantity_incident"]["filled_quantity"] == "1/2"
        assert state["held"]["quantity_incident"]["open_quantity"] == "1/2"
        assert state["capacity"] == "0" and state["completed"] == {}
        incident = state["held"]["quantity_incident"]
        frozen_held = state["held"]
        for seconds in (1, 60):
            owner.observe(
                "after-fraction-" + str(seconds),
                selection
                if seconds == 60
                else source.selection(later, cash=final_cash),
                later + timedelta(seconds=seconds),
            )
            state = view(owner)
            assert state["capacity"] == "0" and state["completed"] == {}
            assert state["held"] == frozen_held
            assert state["held"]["quantity_incident"] == incident
            assert "UNSUPPORTED_FRACTIONAL_RISK_QUANTITY" in state["issues"]
        restarted = AccountRiskOwner(source.journal, REF, POLICY)
        assert restarted.view() == state and restarted.verify_rebuild()
        assert owner.verify_rebuild()


def test_pending_cancel_never_releases_unfilled_reserve_or_allows_abandonment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        owner.reserve("first", source.selection(NOW), NOW, PLAN)
        at = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, at)
        source.order(PLAN.client_order_id, at, "pending_cancel", 0)
        selection = source.selection(at, power="460")
        owner.observe("cancel-accepted-only", selection, at)
        state = view(owner)
        assert state["held"]["pending_quantity"] == 4
        assert Fraction(state["pending_open_loss_reserve"]) == Fraction(203, 100)
        with pytest.raises(JournalError, match="unsent flat"):
            owner.abandon_unsent("cannot-abandon-sent", selection, at)
        assert view(owner) == state and owner.verify_rebuild()


def test_terminal_flat_bound_exit_completes_one_exact_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        owner.reserve("first", source.selection(NOW), NOW, PLAN)
        bought = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, bought)
        source.order(PLAN.client_order_id, bought, "filled", 4)
        source.fill(PLAN.client_order_id, bought, 4, "10")
        owner.observe(
            "entry-filled", source.selection(bought, cash="460", position=4), bought
        )
        stopped = NOW + timedelta(seconds=2)
        source.intent("stop-1", stopped, side="sell", order_type="stop", price="9.50")
        owner.bind_exit("bind-stop", stopped, "stop-1", "STOP")
        source.order("stop-1", stopped, "filled", 4)
        source.fill("stop-1", stopped, 4, "9.50")
        selection = source.selection(stopped, cash="498")
        owner.observe("stop-filled", selection, stopped)
        state = view(owner)
        assert state["held"] is None and state["source_snapshot_qualified"]
        assert state["completed"][PLAN.client_order_id]["net"] == state["net"]
        assert Fraction(state["net"]) == Fraction(-101, 50)
        assert state["losing_streak"] == 1 and not state["daily_stop_latched"]
        owner.observe("same-final-state", selection, stopped)
        assert view(owner)["losing_streak"] == 1
        assert len(view(owner)["completed"]) == 1
        assert owner.verify_rebuild()


def test_unbound_owned_roundtrip_cannot_bypass_declared_risk_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        bought = NOW + timedelta(seconds=1)
        source.intent("unbound-buy", bought)
        source.order("unbound-buy", bought, "filled", 4)
        source.fill("unbound-buy", bought, 4, "10")
        sold = NOW + timedelta(seconds=2)
        source.intent("unbound-sell", sold, side="sell")
        source.order("unbound-sell", sold, "filled", 4)
        source.fill("unbound-sell", sold, 4, "10")
        decision = owner.reserve("unbound-veto", source.selection(sold), sold, PLAN)
        state = view(owner)
        assert state["source_snapshot_qualified"]
        assert decision["quantity"] == 0 and decision["reason"] == "RISK_VETO"
        assert "UNBOUND_EXECUTION" in state["issues"]
        assert state["held"] is None and state["losing_streak"] == 0
        assert owner.verify_rebuild()


def test_observed_loss_threshold_latches_through_mark_recovery_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        owner.reserve("first", source.selection(NOW), NOW, PLAN)
        bought = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, bought)
        source.order(PLAN.client_order_id, bought, "filled", 4)
        source.fill(PLAN.client_order_id, bought, 4, "10")
        bad = source.selection(bought, cash="460", position=4, bid="8.50")
        owner.observe("drawdown", bad, bought)
        state = view(owner)
        assert state["source_snapshot_qualified"]
        assert Fraction(state["net"]) == Fraction(-601, 100)
        assert state["daily_stop_latched"] and state["capacity"] == "0"
        recovered_at = bought + timedelta(seconds=1)
        recovered = source.selection(recovered_at, cash="460", position=4)
        owner.observe("mark-recovered", recovered, recovered_at)
        restarted = AccountRiskOwner(source.journal, REF, POLICY)
        assert Fraction(view(restarted)["net"]) == Fraction(-1, 100)
        assert view(restarted)["daily_stop_latched"]
        assert "DAILY_STOP_LATCHED" in view(restarted)["issues"]
        assert view(restarted)["capacity"] == "0" and restarted.verify_rebuild()


def test_three_bound_losing_roundtrips_latch_before_daily_loss_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        cash = Fraction(500)
        quantities = []
        for index in range(3):
            at = NOW + timedelta(seconds=index * 3)
            cid = "round-" + str(index)
            plan = EntryRiskPlan(cid, "AAPL", "10", "9.50")
            amount = money(cash)
            decision = owner.reserve(
                "reserve-" + cid, source.selection(at, cash=amount), at, plan
            )
            qty = cast(int, decision["quantity"])
            assert qty > 0
            quantities.append(qty)
            bought = at + timedelta(seconds=1)
            source.intent(cid, bought, qty=qty)
            source.order(cid, bought, "filled", qty)
            source.fill(cid, bought, qty, "10")
            exit_id = "close-" + str(index)
            source.intent(exit_id, bought, side="sell", order_type="market", qty=qty)
            owner.bind_exit("bind-" + cid, bought, exit_id, "CLOSE")
            sold = at + timedelta(seconds=2)
            source.order(exit_id, sold, "filled", qty)
            source.fill(exit_id, sold, qty, "9.60")
            cash -= qty * Fraction(2, 5)
            amount = money(cash)
            selection = source.selection(sold, cash=amount)
            owner.observe("closed-" + cid, selection, sold)
            state = view(owner)
            assert state["held"] is None and state["source_snapshot_qualified"]
            assert state["losing_streak"] == index + 1
        assert quantities == [4, 4, 3]
        assert Fraction(state["net"]) == Fraction(-221, 50)
        assert Fraction(state["net"]) > -5
        assert state["daily_stop_latched"] and state["capacity"] == "0"
        next_at = NOW + timedelta(seconds=9)
        blocked = owner.reserve(
            "fourth",
            source.selection(next_at, cash=amount),
            next_at,
            EntryRiskPlan("fourth", "AAPL", "10", "9.50"),
        )
        assert blocked["quantity"] == 0 and blocked["reason"] == "RISK_VETO"
        assert view(owner)["losing_streak"] == 3 and owner.verify_rebuild()


@pytest.mark.parametrize("cache", ["projection", "dedupe"])
def test_tampered_risk_cache_blocks_reopen_and_any_new_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: str
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        owner.reserve("first", selection, NOW, PLAN)
        events = source.journal.read_account_risk_events()
        with sqlite3.connect(tmp_path / "private/journal.sqlite3") as db:
            if cache == "projection":
                altered = view(owner)
                altered["held"]["quantity"] = 999
                db.execute(
                    "UPDATE state SET value_json=? WHERE key=?",
                    (json.dumps(altered), owner.state_key),
                )
            else:
                key, value = db.execute(
                    "SELECT key,value_json FROM state "
                    "WHERE key LIKE 'account_risk_evidence:%'"
                ).fetchone()
                changed = json.loads(value)
                changed["hash"] = "sha256:" + "0" * 64
                db.execute(
                    "UPDATE state SET value_json=? WHERE key=?",
                    (json.dumps(changed), key),
                )
        with pytest.raises(JournalError, match="cache|deduplication"):
            AccountRiskOwner(source.journal, REF, POLICY)
        with pytest.raises(JournalError, match="cache|deduplication"):
            owner.reserve(
                "cannot-use-tampered-state",
                selection,
                NOW,
                EntryRiskPlan("second", "AAPL", "10", "9.50"),
            )
        assert source.journal.read_account_risk_events() == events


@pytest.mark.parametrize("fault", ["stale", "foreign"])
def test_invalid_source_observation_vetoes_without_releasing_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        owner.reserve("first", selection, NOW, PLAN)
        held = view(owner)["held"]
        risk = view(owner)["pending_open_loss_reserve"]
        at = NOW + timedelta(seconds=31 if fault == "stale" else 1)
        if fault == "foreign":
            original = source.journal.read_event(selection.capture_sequence)
            assert original is not None
            body = json.loads(original[1]) | {"account_ref": "foreign-account"}
            bad_seq = source.journal.append_next(
                source_id=SOURCE,
                feed="paper",
                event_kind=KIND,
                payload=json.dumps(body).encode(),
                system_first_seen_at=at,
                processed_at=at,
            ).sequence
            selection = replace(selection, capture_sequence=bad_seq)
        owner.observe("bad-source", selection, at)
        state = view(owner)
        assert state["held"] == held
        assert state["pending_open_loss_reserve"] == risk
        assert state["capacity"] == "0" and not state["source_snapshot_qualified"]
        assert "SOURCE_INPUT_INVALID_OR_UNAVAILABLE" in state["issues"]
        assert owner.verify_rebuild()


def test_failure_before_commit_rolls_back_event_projection_and_dedupe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        original = Journal._append_locked

        def append_then_fail(
            self: Journal,
            event: EventEnvelope,
            payload: bytes,
            state_updates: Mapping[str, object] | None,
        ) -> None:
            original(self, event, payload, state_updates)
            if event.source_id == "ALPACA_ACCOUNT_RISK":
                raise OSError("injected failure before commit")

        with monkeypatch.context() as patch:
            patch.setattr(Journal, "_append_locked", append_then_fail)
            with pytest.raises(OSError, match="before commit"):
                owner.reserve("first", selection, NOW, PLAN)
        assert owner.view() is None
        assert source.journal.read_account_risk_events() == ()
        assert source.journal.read_account_risk_evidence_cache(REF) == {}
        assert owner.verify_rebuild()
        assert owner.reserve("first", selection, NOW, PLAN)["quantity"] == 4
        assert len(source.journal.read_account_risk_events()) == 1
        assert owner.verify_rebuild()


def test_send_without_ack_cannot_release_plan_by_unsent_abandonment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        owner.reserve("first", selection, NOW, PLAN)
        original = view(owner)
        source.intent(PLAN.client_order_id, NOW + timedelta(seconds=1))
        owner.abandon_unsent(
            "unknown-send-abandon", selection, NOW + timedelta(seconds=2)
        )
        state = view(owner)
        assert state["held"] == original["held"]
        assert (
            state["pending_open_loss_reserve"] == original["pending_open_loss_reserve"]
        )
        assert state["capacity"] == "0" and not state["source_snapshot_qualified"]
        assert source.journal.has_order_send_attempt(PLAN.client_order_id)
        assert owner.verify_rebuild()


def test_retry_old_positive_decision_after_abandonment_cannot_regrant_quantity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(NOW)
        assert owner.reserve("first", selection, NOW, PLAN)["quantity"] == 4
        owner.abandon_unsent("abandon", selection, NOW)
        original = view(owner)
        events = source.journal.read_account_risk_events()
        repeated = owner.reserve("first", selection, NOW, PLAN)
        assert repeated["quantity"] == 0 and not repeated["runtime_authorized"]
        assert view(owner) == original and view(owner)["held"] is None
        assert source.journal.read_account_risk_events() == events
        assert owner.verify_rebuild()
