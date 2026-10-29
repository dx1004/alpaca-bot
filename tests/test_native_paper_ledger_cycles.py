"""Sequential canonical native/simple account cycles using injected sources only."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner, EntryRiskPlan
from alpaca_ross_bot.paper_execution_adapter import (
    inspect_native_engineering_executions,
)
from alpaca_ross_bot.paper_ledger_sources import (
    inspect_native_paper_ledger_sources,
    inspect_paper_ledger_sources,
)
from test_native_account_risk_owner import full_book
from test_native_account_risk_owner import native_risk_book as native_risk_book
from test_native_oco_correlation_integration import ROOT, STOP
from test_native_paper_ledger_sources import arguments
from test_native_paper_ledger_sources import native_completed as native_completed
from test_observation_candidate_binding import CID
from test_risk_ledger_audit import POLICY, REF


def test_native_then_simple_cycles_rebuild_once_per_canonical_episode(
    native_completed: Any,
) -> None:
    book, first_selection, first_at = native_completed
    source, risk, _, _, reported = book
    journal = source.journal
    first_args = arguments(book, first_at)
    first_ledger = inspect_native_paper_ledger_sources(
        journal, first_selection.capture_sequence, **first_args
    )
    first_executions = inspect_native_engineering_executions(
        journal, first_selection.capture_sequence, **first_args
    )
    first_risk = risk.view()
    assert first_risk is not None and set(first_risk["completed"]) == {CID}
    first_completion = first_risk["completed"][CID]
    first_events = tuple(journal.read_account_risk_events())
    first_oco_events = tuple(journal.read_oco_research_events())

    second_cid, exit_cid = "second-simple-entry", "second-simple-close"
    reserve_at = first_at + timedelta(seconds=1)
    reserved = risk.reserve(
        "second-simple-reservation",
        full_book(source, reported, reserve_at, cash="502.06", position=0),
        reserve_at,
        EntryRiskPlan(second_cid, "AAPL", "10", "9.50"),
    )
    quantity = reserved["quantity"]
    assert type(quantity) is int and quantity > 0, reserved
    bought_at = reserve_at + timedelta(seconds=1)
    source.intent(second_cid, bought_at, qty=quantity)
    entry_order = source.order(second_cid, bought_at, "filled", quantity)
    source.fill(second_cid, bought_at, quantity, "9.875")
    buy_activity = source.fills[-1].copy()
    cash = Decimal("502.06") - quantity * Decimal("9.875")
    risk.observe(
        "second-simple-bought",
        full_book(source, reported, bought_at, cash=str(cash), position=quantity),
        bought_at,
    )
    held = risk.view()
    assert held is not None and held["held"]["client_order_id"] == second_cid
    assert held["last_snapshot"]["qualified"] and held["issues"] == []

    sold_at = bought_at + timedelta(seconds=1)
    source.intent(exit_cid, sold_at, side="sell", order_type="market", qty=quantity)
    risk.bind_exit("second-simple-bound-close", sold_at, exit_cid, "CLOSE")
    exit_order = source.order(exit_cid, sold_at, "filled", quantity)
    source.fill(exit_cid, sold_at, quantity, "10.125")
    sell_activity = source.fills[-1].copy()
    cash += quantity * Decimal("10.125")
    selection = full_book(source, reported, sold_at, cash=str(cash), position=0)
    risk.observe("second-simple-flat", selection, sold_at)
    state = risk.view()
    assert state is not None and state["held"] is None
    assert state["last_snapshot"]["qualified"] and state["issues"] == []
    assert set(state["completed"]) == {CID, second_cid}
    assert state["completed"][CID] == first_completion
    assert state["completed"][second_cid]["order_ids"] == sorted((second_cid, exit_cid))
    assert "native_context" not in state["completed"][second_cid]
    assert risk.verify_rebuild()
    assert AccountRiskOwner(journal, REF, POLICY).view() == state
    assert (
        tuple(journal.read_account_risk_events())[: len(first_events)] == first_events
    )
    assert tuple(journal.read_oco_research_events()) == first_oco_events
    assert (
        len({source.orders[CID]["id"], entry_order["id"], exit_order["id"], ROOT, STOP})
        == 5
    )
    assert set(journal.read_order_intent_ids()) == {CID, second_cid, exit_cid}

    kwargs = arguments(book, sold_at)
    before = tuple(journal._db.iterdump())
    ledger = inspect_native_paper_ledger_sources(
        journal, selection.capture_sequence, **kwargs
    )
    converted = inspect_native_engineering_executions(
        journal, selection.capture_sequence, **kwargs
    )
    trips = {row.entry_client_order_id: row for row in ledger.observed_roundtrips}
    assert set(trips) == {CID, second_cid}
    assert trips[CID].native_order_ids == tuple(sorted((ROOT, STOP)))
    assert trips[CID].native_group_ids == ("group-a",)
    assert trips[CID].transport_origin == "INJECTED_EXCHANGE"
    assert trips[CID].execution_ids == first_ledger.observed_roundtrips[0].execution_ids
    assert trips[second_cid].native_order_ids == ()
    assert trips[second_cid].native_group_ids == ()
    assert trips[second_cid].transport_origin is None
    assert trips[second_cid].filled_quantity == quantity
    second_ids = {str(buy_activity["id"]), str(sell_activity["id"])}
    assert set(trips[second_cid].execution_ids) == second_ids
    assignments = [identity for row in trips.values() for identity in row.execution_ids]
    assert len(assignments) == len(set(assignments)) == 5
    assert set(assignments) == {row.activity_id for row in ledger.activities}
    assert len(converted.executions) == 5
    assert not converted.unconverted_observations
    observations = {row.activity_id: row for row in ledger.activities}
    for row in converted.executions:
        assert row.roundtrip_entry_client_order_id == (
            second_cid if row.execution_id in second_ids else CID
        )
        assert (
            row.ownership_sources
            == trips[row.roundtrip_entry_client_order_id].source_refs
        )
        assert row.activity_source == observations[row.execution_id].source
        assert row.canonical_row == observations[row.execution_id].canonical_row
        assert row.canonical_row_hash == (
            "sha256:" + hashlib.sha256(row.canonical_row).hexdigest()
        )
        assert row.transport_origin == (
            "INJECTED_EXCHANGE"
            if row.execution_id in {"native-report-a", "native-report-b"}
            else None
        )
        assert row.origin == "ENGINEERING_TEST"
        for ref in (row.activity_source, *row.ownership_sources):
            stored = journal.read_event(ref.sequence)
            assert stored is not None and stored[0].payload_ref == ref.payload_ref
            assert ref.declared_journal_scope == kwargs["journal_scope"]
    second = {
        row.execution_id: row
        for row in converted.executions
        if row.execution_id in second_ids
    }
    assert second[str(buy_activity["id"])].price == Fraction("9.875")
    assert second[str(sell_activity["id"])].price == Fraction("10.125")
    for raw, at in ((buy_activity, bought_at), (sell_activity, sold_at)):
        row = second[str(raw["id"])]
        assert row.quantity == quantity and row.at == at
        assert row.original_timestamp == raw["transaction_time"]
        assert row.submicrosecond_remainder == 0
        assert json.loads(row.canonical_row) == raw
    fractional = next(
        row for row in converted.executions if row.execution_id == "native-report-a"
    )
    assert fractional.quantity == Fraction(1, 8)
    assert fractional.price == Fraction("11.03")
    assert fractional.submicrosecond_remainder == Fraction(789, 1_000_000_000)
    for report in (ledger, converted):
        assert not any(
            (
                report.formal_sample_eligible,
                report.runtime_authorized,
                report.dispatch_authorized,
                report.source_admission_accepted,
                report.activity_finality_accepted,
                report.account_day_census_accepted,
                report.journal_scope_authenticated,
            )
        )
    assert ledger.formal_origin is None
    legacy = inspect_paper_ledger_sources(journal, selection.capture_sequence, **kwargs)
    assert not legacy.observed_roundtrips
    assert "NATIVE_GROUP_OWNERSHIP_UNSUPPORTED" in legacy.issues
    assert (
        inspect_native_paper_ledger_sources(
            journal, first_selection.capture_sequence, **first_args
        )
        == first_ledger
    )
    assert (
        inspect_native_engineering_executions(
            journal, first_selection.capture_sequence, **first_args
        )
        == first_executions
    )
    assert tuple(journal._db.iterdump()) == before
