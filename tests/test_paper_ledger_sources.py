"""Actual immutable source readers; no injected ownership/qualification flags."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner, EntryRiskPlan
from alpaca_ross_bot.incident_risk import IncidentRiskRequest
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.paper_ledger_sources import (
    PaperLedgerSources,
    inspect_paper_ledger_sources,
)
from alpaca_ross_bot.paper_order_contract import decode_broker_json
from test_account_risk_owner import SourceAccount, account_sources, money, view
from test_risk_ledger_audit import NOW, POLICY, REF


def inspect(
    source: SourceAccount,
    selection: IncidentRiskRequest,
    at: datetime,
    **changes: Any,
) -> PaperLedgerSources:
    values: dict[str, Any] = {
        "journal_scope": "synthetic-engineering-ledger",
        "account_ref": REF,
        "decision_at": at,
        "through_sequence": source.journal.read_evidence_sequence(),
        "max_age": timedelta(seconds=30),
        "fee_policy": POLICY,
    } | changes
    return inspect_paper_ledger_sources(
        source.journal, selection.capture_sequence, **values
    )


def complete(
    source: SourceAccount,
    owner: AccountRiskOwner,
    *,
    index: int = 0,
    cash: Fraction = Fraction(500),
    price: str = "9.60",
    nanos: bool = False,
) -> tuple[IncidentRiskRequest, datetime, Fraction]:
    at = NOW + timedelta(seconds=index * 3)
    cid, exit_id = "entry-" + str(index), "close-" + str(index)
    decision = owner.reserve(
        "reserve-" + cid,
        source.selection(at, cash=money(cash)),
        at,
        EntryRiskPlan(cid, "AAPL", "10", "9.50"),
    )
    qty = cast(int, decision["quantity"])
    assert qty > 0
    bought, sold = at + timedelta(seconds=1), at + timedelta(seconds=2)
    source.intent(cid, bought, qty=qty)
    source.order(cid, bought, "filled", qty)
    source.fill(cid, bought, 1 if nanos else qty, "10.000")
    if nanos:
        source.fills[-1]["transaction_time"] = at.isoformat().replace(
            "+00:00", ".123456789+00:00"
        )
        source.fills[-1]["cum_qty"] = "1"
        source.fills[-1]["provider_extra"] = {"keep": "source field"}
        source.fill(cid, bought, qty - 1, "10.000")
        source.fills[-1]["cum_qty"] = str(qty)
    source.intent(exit_id, bought, side="sell", order_type="market", qty=qty)
    owner.bind_exit("bind-" + cid, bought, exit_id, "CLOSE")
    source.order(exit_id, sold, "filled", qty)
    source.fill(exit_id, sold, qty, price)
    cash += qty * (Fraction(price) - 10)
    selection = source.selection(sold, cash=money(cash))
    owner.observe("closed-" + cid, selection, sold)
    return selection, sold, cash


def test_completed_scope_retains_nanos_raw_fields_and_exact_execution_quantities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection, at, _ = complete(source, owner, nanos=True)
        before = tuple(source.journal._db.iterdump())
        result = inspect(source, selection, at)
        assert tuple(source.journal._db.iterdump()) == before
        assert len(result.observed_roundtrips) == 1
        trip = result.observed_roundtrips[0]
        assert trip.entry_client_order_id == "entry-0"
        assert trip.filled_quantity == 4 and trip.origin == "ENGINEERING_TEST"
        assert trip.execution_ids == ("execution-0", "execution-1", "execution-2")
        row = result.activities[0]
        assert row.quantity == 1 and row.price == 10
        assert row.transaction_time is not None and ".123456789" in row.transaction_time
        assert row.submicrosecond_remainder == Fraction(789, 1_000_000_000)
        assert json.loads(row.canonical_row)["cum_qty"] == "1"
        assert result.activities[1].quantity == 3
        assert json.loads(result.activities[1].canonical_row)["cum_qty"] == "4"
        assert json.loads(row.canonical_row)["provider_extra"] == {
            "keep": "source field"
        }
        assert all(
            ref.declared_journal_scope == result.declared_journal_scope
            for ref in result.source_refs
        )
        summary = result.public_summary()
        assert summary["formal_origin"] is None
        assert all(
            value is False
            for key, value in summary.items()
            if key.endswith(("accepted", "authorized", "eligible", "authenticated"))
        )


def test_three_losses_remain_descriptive_when_business_stop_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        cash = Fraction(500)
        for index in range(3):
            selection, at, cash = complete(source, owner, index=index, cash=cash)
        assert view(owner)["daily_stop_latched"]
        result = inspect(source, selection, at)
        assert len(result.observed_roundtrips) == 3
        assert "RISK_DAILY_STOP_LATCHED_NO_TRADING_CLEARANCE" in result.issues
        assert result.runtime_authorized is False and view(owner)["capacity"] == "0"


def test_postings_and_conflicting_variants_preserve_all_occurrences_without_classifying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, _):
        fee: dict[str, object] = {
            "id": "fee",
            "activity_type": "FEE",
            "net_amount": "-0.00120",
            "date": "2026-10-15",
            "description": "not classified by amount",
        }
        source.fills.extend(
            [
                fee,
                fee.copy(),
                fee | {"net_amount": "-0.00121"},
                {
                    "id": "other",
                    "activity_type": "UNRECOGNIZED",
                    "net_amount": "1.000000001",
                },
            ]
        )
        selection = source.selection(NOW)
        result = inspect(source, selection, NOW)
        assert len(result.activities) == 3
        assert result.activities[0].occurrences == ((3, 0), (3, 1))
        assert result.activities[0].net_amount == Fraction(-12, 10_000)
        assert result.activities[1].net_amount == Fraction(-121, 100_000)
        assert "ACTIVITY_ID_CONFLICT" in result.issues
        assert "UNKNOWN_NTA_CLASSIFICATION" in result.issues
        assert not result.observed_roundtrips
        assert json.loads(result.activities[0].canonical_row)["date"] == "2026-10-15"


def test_fractional_and_future_unowned_fills_remain_exact_not_rounded_or_attributed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, _):
        source.fills.append(
            {
                "id": "foreign",
                "activity_type": "FILL",
                "order_id": "not-local",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "1.250",
                "price": "10.001",
                "transaction_time": NOW.isoformat().replace(
                    "+00:00", ".000000001+00:00"
                ),
                "cum_qty": "500",
            }
        )
        selection = source.selection(NOW)
        result = inspect(source, selection, NOW)
        assert len(result.activities) == 1
        assert result.activities[0].quantity == Fraction(5, 4)
        assert result.activities[0].price == Fraction(10001, 1000)
        assert "FUTURE_EXECUTION" in result.issues
        assert not result.observed_roundtrips


@pytest.mark.parametrize("numeric_qty", [False, True])
def test_raw_json_numbers_keep_exact_numeric_type_even_when_qty_is_unqualified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    numeric_qty: bool,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, _):
        source.fills.append(
            {
                "id": "numeric-field",
                "activity_type": "FILL",
                "order_id": "foreign",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "1.25",
                "price": "10",
                "transaction_time": NOW.isoformat(),
                "provider_extra": "NUMBER_TOKEN",
            }
        )
        selection = source.selection(NOW)
        stored = source.journal.read_event(selection.capture_sequence)
        assert stored is not None
        # The inner body is JSON text in the envelope. Preserve its exact digits.
        envelope = json.loads(stored[1])
        body = envelope["requests"][3]["body"].replace(
            '"NUMBER_TOKEN"', "1.234567890123456789"
        )
        if numeric_qty:
            body = body.replace('"qty": "1.25"', '"qty": 1.25')
        envelope["requests"][3]["body"] = body
        seq = source.journal.append_next(
            source_id=stored[0].source_id,
            feed="paper",
            event_kind=stored[0].event_kind,
            payload=json.dumps(envelope).encode(),
            system_first_seen_at=NOW,
            processed_at=NOW,
        ).sequence
        result = inspect(source, replace(selection, capture_sequence=seq), NOW)
        raw = decode_broker_json(result.activities[0].canonical_row)
        assert isinstance(raw, dict)
        assert raw["provider_extra"] == Decimal("1.234567890123456789")
        assert type(raw["provider_extra"]) is Decimal
        if numeric_qty:
            assert type(raw["qty"]) is Decimal
            assert result.activities[0].quantity is None
            assert "FILL_VALUES_UNQUALIFIED" in result.issues
        else:
            assert type(raw["qty"]) is str
            assert result.activities[0].quantity == Fraction(5, 4)
        assert not result.observed_roundtrips


def test_new_capture_cannot_silently_use_previous_completed_owner_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        _, at, cash = complete(source, owner)
        later = at + timedelta(seconds=1)
        selection = source.selection(later, cash=money(cash))
        result = inspect(source, selection, later)
        assert "OWNER_CAPTURE_DIFFERS" in result.issues
        assert len(result.activities) == 2 and not result.observed_roundtrips


@pytest.mark.parametrize(
    "change",
    [
        {"through_sequence": True},
        {"through_sequence": 999999},
        {"journal_scope": "bad\nscope"},
        {"journal_scope": " "},
        {"account_ref": "another-account"},
        {"decision_at": NOW.replace(tzinfo=None)},
        {"decision_at": NOW - timedelta(seconds=1)},
        {"decision_at": NOW + timedelta(minutes=1)},
    ],
)
def test_bound_prefix_and_clocks_fail_without_mutating_or_closing_caller_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, _):
        selection = source.selection(NOW)
        before = tuple(source.journal._db.iterdump())
        source.journal._db.execute("BEGIN")
        try:
            with pytest.raises((ValueError, JournalError)):
                inspect(source, selection, NOW, **change)
            assert source.journal._db.in_transaction
        finally:
            source.journal._db.rollback()
        assert tuple(source.journal._db.iterdump()) == before


@pytest.mark.parametrize("fault", ["raw", "risk-cache", "lifecycle-cache"])
def test_current_integrity_faults_abort_instead_of_reporting_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection, at, _ = complete(source, owner)
        with sqlite3.connect(tmp_path / "private/journal.sqlite3") as db:
            if fault == "raw":
                db.execute("DROP TRIGGER events_no_update")
                db.execute(
                    "UPDATE events SET payload=? WHERE sequence=?",
                    (b"{}", selection.capture_sequence),
                )
            else:
                key = (
                    owner.state_key
                    if fault == "risk-cache"
                    else source.reconciler.state_key
                )
                db.execute("UPDATE state SET value_json='{}' WHERE key=?", (key,))
        with pytest.raises((ValueError, JournalError)):
            inspect(source, selection, at)
        assert not source.journal._db.in_transaction
