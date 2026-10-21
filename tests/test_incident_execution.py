"""Execution incidents from synthetic immutable lifecycle and account sources."""

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.entry_strategy import EntryDecision
from alpaca_ross_bot.incident_execution import derive_execution_incident
from alpaca_ross_bot.incident_state import (
    IncidentObservation,
    IncidentPolicy,
    OrderRole,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_research import CANDIDATE, load_oco_research_contract
from alpaca_ross_bot.order_reconciliation import OrderReconciler
from test_account_risk_owner import SourceAccount, account_sources
from test_observation_candidate_binding import AT, CID, contract, new_decision
from test_oco_entry_evidence import _prepare
from test_risk_ledger_audit import POLICY, REF


def _policy() -> IncidentPolicy:
    return IncidentPolicy(
        REF,
        CANDIDATE,
        CID,
        "AAPL",
        ("P11", "P12", "P13"),
        "sha256:" + "b" * 64,
        observation_contract=contract(),
        oco_contract=load_oco_research_contract(),
    )


@pytest.fixture(scope="module")
def candidate() -> EntryDecision:
    return new_decision()


@pytest.fixture
def prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: EntryDecision
) -> Iterator[tuple[SourceAccount, AccountRiskOwner, datetime]]:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        at = _prepare(source, risk, candidate)
        yield source, risk, at


def _derive(
    source: SourceAccount,
    at: datetime,
    *,
    policy: IncidentPolicy | None = None,
    horizon: int | None = None,
) -> dict[str, Any]:
    return derive_execution_incident(
        source.journal,
        _policy() if policy is None else policy,
        event_id="incident",
        decision_at=at,
        through_sequence=(
            source.journal.read_evidence_sequence() if horizon is None else horizon
        ),
        fee_policy=POLICY,
    )


def _observation(receipt: dict[str, object]) -> IncidentObservation:
    return IncidentObservation.from_dict(receipt["observation"])


def test_partial_source_retains_facts_without_qualification_or_any_write(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    before = source.journal.read_evidence_sequence()
    original = json.dumps(risk.view(), sort_keys=True)
    receipt = _derive(source, at)
    assert set(receipt) == {
        "schema",
        "classification",
        "request",
        "source_horizon",
        "source_refs",
        "observation",
        "quarantine_reasons",
        "authority",
    }
    observation = _observation(receipt)
    assert observation.watermark == before
    assert observation.baseline_verified and observation.baseline_quantity == 0
    assert observation.position_quantity == 2
    assert observation.stream_connected
    assert len(observation.orders) == 1
    assert observation.orders[0].role == OrderRole.ENTRY
    assert observation.orders[0].order.filled_quantity == 2
    assert not observation.orders[0].protection_verified
    assert not any(
        (
            observation.reconciliation_verified,
            observation.data_verified,
            observation.durable,
            observation.recovery_verified,
            observation.closing_due,
        )
    )
    assert not any(receipt["authority"].values())
    assert "ENGINEERING_TEST_SOURCE_NOT_FORMAL_SAMPLE" in receipt["quarantine_reasons"]
    for ref in receipt["source_refs"]:
        stored = source.journal.read_event(ref["sequence"])
        assert stored is not None and stored[0].payload_ref == ref["hash"]
    assert source.journal.read_evidence_sequence() == before
    assert json.dumps(risk.view(), sort_keys=True) == original


def test_disconnect_does_not_erase_known_partial_or_report_connected(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, _, at = prepared
    at += timedelta(seconds=1)
    source.reconciler.stream_health("DISCONNECTED", at, "source-fixture")
    receipt = _derive(source, at)
    observation = _observation(receipt)
    assert observation.orders[0].order.filled_quantity == 2
    assert not observation.stream_connected and observation.outcome_unknown
    assert "STREAM_CONTINUITY_UNQUALIFIED" in receipt["quarantine_reasons"]
    assert not observation.recovery_verified


def test_missing_risk_keeps_recorded_baseline_but_does_not_infer_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, _):
        receipt = _derive(source, AT)
        observation = _observation(receipt)
        assert observation.baseline_verified
        assert observation.position_quantity is None
        assert observation.orders == ()
        assert "SOURCE_RISK_MISSING" in receipt["quarantine_reasons"]


def test_missing_baseline_never_infers_verified_flat(tmp_path: Path) -> None:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    try:
        journal.record_paper_account_verification(REF, b"synthetic", AT)
        OrderReconciler(journal, REF, clock=lambda: AT)
        receipt = derive_execution_incident(
            journal,
            _policy(),
            event_id="missing-baseline",
            decision_at=AT,
            through_sequence=journal.read_evidence_sequence(),
            fee_policy=POLICY,
        )
        observation = _observation(receipt)
        assert not observation.baseline_verified
        assert observation.position_quantity is None
        assert "RECORDED_BASELINE_MISSING" in receipt["quarantine_reasons"]
    finally:
        journal.close()


def test_stale_position_becomes_unknown_without_dropping_order_fills(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, _, at = prepared
    receipt = _derive(source, at + timedelta(seconds=31))
    observation = _observation(receipt)
    assert observation.position_quantity is None
    assert observation.orders[0].order.filled_quantity == 2
    assert "SOURCE_POSITION_STALE_OR_FUTURE" in receipt["quarantine_reasons"]


def test_invisible_source_suffix_is_quarantined_without_citing_future_rows(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, _, at = prepared
    decision = at - timedelta(microseconds=1)
    receipt = _derive(source, decision)
    observation = _observation(receipt)
    assert "SOURCE_PREFIX_UNAVAILABLE" in receipt["quarantine_reasons"]
    assert observation.position_quantity is None and not observation.stream_connected
    assert observation.outcome_unknown
    for ref in receipt["source_refs"]:
        stored = source.journal.read_event(ref["sequence"])
        assert stored is not None and stored[0].visible_at(decision)
    assert "INTENT_ORIGIN_UNQUALIFIED" in receipt["quarantine_reasons"]


@pytest.mark.parametrize(
    "fault", ["account", "scope", "symbol", "legacy", "missing-opt-in"]
)
def test_wrong_account_scope_symbol_or_policy_cannot_be_promoted(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
    fault: str,
) -> None:
    source, _, at = prepared
    policy = _policy()
    changed = {
        "account": replace(policy, account_ref="foreign"),
        "scope": replace(policy, scope_id="other-entry"),
        "symbol": replace(policy, symbol="OTHER"),
        "legacy": replace(
            policy,
            version="HYBRID_500_IEX_INTRADAY_RESEARCH_V1",
            oco_contract=None,
            observation_contract=None,
        ),
        "missing-opt-in": replace(policy, oco_contract=None),
    }[fault]
    before = source.journal.read_evidence_sequence()
    with pytest.raises((JournalError, ValueError)):
        _derive(source, at, policy=changed)
    assert source.journal.read_evidence_sequence() == before


def test_pending_cancel_is_derived_from_lifecycle_not_risk_policy(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    at += timedelta(seconds=1)
    source.order(CID, at, "pending_cancel", 2)
    risk.observe("pending-cancel", source.selection(at, cash="480", position=2), at)
    observation = _observation(_derive(source, at))
    assert observation.orders[0].cancel_requested
    assert observation.outcome_unknown
    assert observation.orders[0].order.filled_quantity == 2


def test_daily_stop_retains_position_without_inventing_cancel_ack(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    at += timedelta(seconds=1)
    risk.observe("loss", source.selection(at, cash="480", position=2, bid="7"), at)
    receipt = _derive(source, at)
    observation = _observation(receipt)
    assert observation.daily_stop and observation.position_quantity == 2
    assert not observation.orders[0].cancel_requested
    assert "SOURCE_RISK_QUARANTINED" in receipt["quarantine_reasons"]


def test_explicit_owned_stop_role_is_not_native_protection_acceptance(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    at += timedelta(seconds=1)
    source.intent("stop", at, side="sell", order_type="stop", qty=2, price="9.50")
    risk.bind_exit("bind-stop", at, "stop", "STOP")
    source.order("stop", at, "new", 0)
    risk.observe("stop-source", source.selection(at, cash="480", position=2), at)
    observation = _observation(_derive(source, at))
    stop = next(item for item in observation.orders if item.role == OrderRole.STOP)
    assert stop.order.client_order_id == "stop" and not stop.protection_verified
    assert not observation.recovery_verified


def test_unowned_order_keeps_raw_evidence_and_quarantine_without_invented_role(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    at += timedelta(seconds=1)
    source.intent("unowned", at, qty=1)
    source.order("unowned", at, "new", 0)
    risk.observe("foreign-order", source.selection(at, cash="480", position=2), at)
    receipt = _derive(source, at)
    observation = _observation(receipt)
    assert [item.order.client_order_id for item in observation.orders] == [CID]
    assert "FOREIGN_OR_UNOWNED_LIFECYCLE_ORDER:unowned" in receipt["quarantine_reasons"]
    assert receipt["source_refs"]


def test_fractional_source_seam_keeps_exact_lifecycle_fill_and_quarantines(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    at += timedelta(seconds=1)
    source.orders[CID]["filled_qty"] = "2.5"
    source.fill(CID, at, 1, "10")
    source.fills[-1]["qty"] = "0.5"
    selection = source.selection(at, cash="475", position=cast(int, Decimal("2.5")))
    risk.observe("fractional", selection, at)
    receipt = _derive(source, at)
    observation = _observation(receipt)
    assert observation.orders[0].order.filled_quantity == Decimal("2.5")
    assert "FRACTIONAL_SOURCE_SEAM_UNQUALIFIED" in receipt["quarantine_reasons"]
    assert observation.position_quantity is None


def test_completed_slot_late_original_fill_retains_facts_without_resurrection(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    at += timedelta(seconds=1)
    source.order(CID, at, "canceled", 2)
    source.intent("exit", at, side="sell", qty=2, price="11.10")
    risk.bind_exit("bind-exit", at, "exit", "TARGET")
    source.order("exit", at, "filled", 2)
    source.fill("exit", at, 2, "11.10")
    risk.observe("complete", source.selection(at, cash="502.20", position=0), at)
    complete = risk.view()
    assert (
        complete is not None
        and complete["held"] is None
        and CID in complete["completed"]
    )
    at += timedelta(seconds=1)
    source.order(CID, at, "canceled", 3)
    source.fill(CID, at, 1, "9.90")
    risk.observe("late-entry", source.selection(at, cash="492.30", position=1), at)
    original = json.dumps(risk.view(), sort_keys=True)
    before = source.journal.read_evidence_sequence()
    receipt = _derive(source, at)
    observation = _observation(receipt)
    assert observation.position_quantity == 1
    assert {
        item.order.client_order_id: item.order.filled_quantity
        for item in observation.orders
    } == {CID: Decimal(3), "exit": Decimal(2)}
    assert "COMPLETED_SCOPE_LATE_ENTRY_OR_NONFLAT" in receipt["quarantine_reasons"]
    assert "SOURCE_RISK_QUARANTINED" in receipt["quarantine_reasons"]
    assert not observation.reconciliation_verified and not observation.recovery_verified
    assert not any(receipt["authority"].values())
    assert source.journal.read_evidence_sequence() == before
    assert json.dumps(risk.view(), sort_keys=True) == original


def test_original_horizon_does_not_change_after_new_source_records(
    prepared: tuple[SourceAccount, AccountRiskOwner, datetime],
) -> None:
    source, risk, at = prepared
    horizon = source.journal.read_evidence_sequence()
    original = _derive(source, at, horizon=horizon)
    later = at + timedelta(seconds=1)
    source.order(CID, later, "canceled", 2)
    risk.observe("later", source.selection(later, cash="480", position=2), later)
    assert _derive(source, at, horizon=horizon) == original


def test_post_horizon_intent_cannot_rewrite_missing_origin_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate: EntryDecision,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        reserved = risk.reserve_research_decision(
            "reservation",
            source.selection(AT),
            AT,
            client_order_id=CID,
            decision=candidate,
            observation_contract=contract(),
            oco_contract=load_oco_research_contract(),
        )
        assert reserved["quantity"] == 4
        horizon = source.journal.read_evidence_sequence()
        original = _derive(source, AT, horizon=horizon)
        assert "INTENT_ORIGIN_UNQUALIFIED" in original["quarantine_reasons"]
        source.intent(CID, AT + timedelta(seconds=1), qty=4, price="10.01")
        assert _derive(source, AT, horizon=horizon) == original
