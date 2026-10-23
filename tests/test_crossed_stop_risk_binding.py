"""Explicit policy/slot identity with synthetic sources, never broker authority."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop import CrossedStopJournal
from alpaca_ross_bot.crossed_stop_contract import (
    CrossedStopContract,
    load_crossed_stop_contract,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from test_account_risk_owner import PLAN, SourceAccount, account_sources
from test_crossed_stop_source import cross
from test_native_oco_requests import prepared as prepared
from test_observation_candidate_binding import AT, CID
from test_risk_ledger_audit import POLICY, REF


def current(source: SourceAccount) -> tuple[AccountRiskOwner, dict[str, Any]]:
    risk = AccountRiskOwner(source.journal, REF, POLICY)
    state = risk.view()
    assert state is not None
    return risk, state


def bind(
    risk: AccountRiskOwner,
    at: datetime,
    *,
    event: str = "emergency-policy",
    cid: str = CID,
) -> dict[str, object]:
    return risk.bind_emergency_policy(
        event, at, client_order_id=cid, contract=load_crossed_stop_contract()
    )


@pytest.mark.parametrize("field", ["raw", "parent"])
def test_contract_tampering_rejected(field: str) -> None:
    contract = load_crossed_stop_contract()
    with pytest.raises(ValueError, match="pinned"):
        CrossedStopContract(
            contract.raw + (b" " if field == "raw" else b""),
            contract.parent + (b" " if field == "parent" else b""),
        )


def test_same_slot_frozen_prices_quantity_budget_and_signal_are_preserved(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, before = current(source)
    raw_before = source.journal.read_account_risk_events()
    intents = source.journal.read_order_intent_ids()
    outcome = bind(risk, at)
    after = risk.view()
    assert after is not None
    assert outcome["research_policy_bound_to_current_slot"] is True
    attached = after["held"].pop("emergency_execution_binding")
    assert after["held"] == before["held"]
    assert after["e0"] == before["e0"] and after["net"] == before["net"]
    for key in (
        "pending_open_loss_reserve",
        "additional_local_cash_hold",
        "veto",
        "daily_stop_latched",
        "losing_streak",
        "used_plan_ids",
    ):
        assert after[key] == before[key]
    assert after["capacity"] == "0" and "REFRESH_REQUIRED" in after["issues"]
    assert attached["prior_slot_ref"] == [
        raw_before[-1][0].sequence,
        raw_before[-1][0].payload_ref,
    ]
    event, payload = source.journal.read_account_risk_events()[-1]
    assert attached["binding_ref"] == [event.sequence, event.payload_ref]
    assert (
        attached["research_signal_sha256"]
        == hashlib.sha256(
            json.dumps(
                before["held"]["research_signal"],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    assert json.loads(payload)["kind"] == "BIND_EMERGENCY_POLICY"
    assert source.journal.read_order_intent_ids() == intents
    assert outcome["runtime_authorized"] is False
    assert outcome["entry_risk_version_binding_accepted"] is False
    assert risk.verify_rebuild()


def test_binding_joins_source_trigger_without_clearing_risk_refresh(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    risk, _ = current(source)
    result = bind(risk, later)
    state = CrossedStopJournal(owner).observe_source("source-bound-opt-in", later)
    receipt = state["source_observations"][0]
    assert state["latched"]
    assert receipt["emergency_risk_policy_opt_in_observed"] is True
    assert receipt["emergency_risk_binding"] == result["receipt"]
    assert receipt["emergency_risk_binding"]["binding_ref"] in receipt["source_refs"]
    assert receipt["emergency_risk_binding"]["prior_slot_ref"] in receipt["source_refs"]
    assert state["authority"]["runtime_authorized"] is False
    assert "REFRESH_REQUIRED" in current(source)[1]["issues"]


def test_retry_reopen_retains_first_binding_and_original_clock(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, _ = current(source)
    original = bind(risk, at)
    rows = source.journal.read_account_risk_events()
    assert bind(risk, at) == original
    path, root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        recovered = AccountRiskOwner(reopened, REF, POLICY)
        assert bind(recovered, at) == original
        assert reopened.read_account_risk_events() == rows
    finally:
        reopened.close()


def test_wrong_scope_or_stale_source_cannot_attach_policy(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, before = current(source)
    for identity, stamp in (("wrong-entry", at), (CID, at + timedelta(minutes=1))):
        with pytest.raises(JournalError):
            bind(risk, stamp, cid=identity)
    assert risk.view() == before


def test_no_held_slot_and_legacy_slot_cannot_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        with pytest.raises(JournalError, match="positive OCO"):
            bind(risk, AT)
        risk.reserve("legacy", source.selection(AT), AT, PLAN)
        with pytest.raises(JournalError, match="positive OCO"):
            bind(risk, AT, cid=PLAN.client_order_id)


def test_daily_stop_and_existing_risk_reasons_survive_attachment(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    later = cross(source, at, bid="1")
    risk, before = current(source)
    assert before["daily_stop_latched"]
    outcome = bind(risk, later)
    after = risk.view()
    assert after is not None
    assert outcome["daily_stop_retained"] is True
    assert set(before["issues"]) <= set(after["issues"])
    assert after["daily_stop_latched"] and after["capacity"] == "0"
    assert after["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]


def test_changed_command_latches_risk_veto_without_rebinding(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, _ = current(source)
    original = bind(risk, at)
    changed = bind(risk, at, cid="another-entry")
    assert changed["research_policy_bound_to_current_slot"] is False
    state = risk.view()
    assert state is not None and state["veto"]
    assert state["held"]["emergency_execution_binding"] == original["receipt"]


def test_old_binding_retry_after_completion_is_not_current_slot_acceptance(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, _ = current(source)
    bind(risk, at)
    later = at + timedelta(seconds=1)
    source.order(CID, later, "canceled", 2)
    source.intent("close", later, side="sell", order_type="market", qty=2)
    risk.bind_exit("close-binding", later, "close", "CLOSE")
    source.order("close", later, "filled", 2)
    source.fill("close", later, 2, "10")
    risk.observe("closed", source.selection(later, cash="500"), later)
    state = risk.view()
    assert state is not None and state["held"] is None
    rows = source.journal.read_account_risk_events()
    retried = bind(risk, at)
    assert retried["research_policy_bound_to_current_slot"] is False
    assert retried["receipt"] is None
    assert source.journal.read_account_risk_events() == rows
    with pytest.raises(JournalError, match="positive OCO"):
        bind(risk, later, event="new-bind-closed")


def test_storage_failure_rolls_back_whole_binding(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, _, at = prepared
    risk, before = current(source)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(source.journal, "_append_locked", fail)
        with pytest.raises(OSError):
            bind(risk, at)
    assert risk.view() == before
    assert bind(risk, at)["research_policy_bound_to_current_slot"]


def test_caller_transaction_is_not_rolled_back_or_committed(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, _ = current(source)
    source.journal._db.execute("BEGIN")
    with pytest.raises(JournalError, match="own transaction"):
        bind(risk, at)
    assert source.journal._db.in_transaction
    source.journal._db.rollback()


def test_two_connections_keep_one_original_policy_receipt(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    path, root = source.journal._path, source.journal._runtime_root
    barrier = Barrier(2)

    def worker(event: str) -> dict[str, object]:
        journal = Journal.open_private(path, root)
        try:
            risk = AccountRiskOwner(journal, REF, POLICY)
            barrier.wait(timeout=10)
            return bind(risk, at, event=event)
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ("a", "b")))
    assert all(r["research_policy_bound_to_current_slot"] for r in results)
    assert results[0]["receipt"] == results[1]["receipt"]
    assert current(source)[0].verify_rebuild()


@pytest.mark.parametrize("hidden_not_latest", [False, True])
def test_invisible_prior_conflict_cannot_be_used_as_policy_binding_origin(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    hidden_not_latest: bool,
) -> None:
    from alpaca_ross_bot.risk_evidence_request import IncidentRiskRequest

    source, _, _, at = prepared
    risk, before = current(source)
    selection = IncidentRiskRequest.from_dict(before["last_snapshot"]["selection"])
    # Original observation key is reused at a future clock to create a veto
    # without advancing risk.at. An earlier second conflict can hide the future
    # record behind a decision-visible latest watermark.
    risk.observe("partial-source", selection, at + timedelta(seconds=3))
    if hidden_not_latest:
        risk.observe("partial-source", selection, at + timedelta(seconds=1))
    state_before = risk.view()
    rows = source.journal.read_account_risk_events()
    with pytest.raises(JournalError, match="prior risk prefix"):
        bind(risk, at + timedelta(seconds=2))
    assert risk.view() == state_before
    assert source.journal.read_account_risk_events() == rows


def test_unqualified_snapshot_without_selection_refuses_cleanly(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, _ = current(source)
    selection = source.selection(at, cash="480", position=2)
    from alpaca_ross_bot.risk_evidence_request import IncidentRiskRequest

    invalid = IncidentRiskRequest(
        selection.calendar_sequence,
        selection.calendar_sequence,
        selection.max_age,
        selection.max_metadata_age,
        selection.mark_refs,
    )
    risk.observe("invalid-source", invalid, at)
    before = risk.view()
    assert before is not None and not before["source_snapshot_qualified"]
    with pytest.raises(JournalError, match="current source snapshot"):
        bind(risk, at)
    assert risk.view() == before


def test_contract_identity_is_detached_and_cannot_promote_authority() -> None:
    contract = load_crossed_stop_contract()
    forged = contract.identity
    forged["order_authority"] = True
    forged["runtime_authorized"] = True
    assert contract.identity["order_authority"] is False
    assert contract.identity["runtime_authorized"] is False


def test_known_conflicting_policy_command_is_idempotent_even_after_reopen(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    risk, _ = current(source)
    bind(risk, at)
    changed = bind(risk, at, cid="changed")
    rows = source.journal.read_account_risk_events()
    for _ in range(3):
        assert bind(risk, at, cid="changed") == changed
    assert source.journal.read_account_risk_events() == rows
    path, root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        assert (
            bind(AccountRiskOwner(reopened, REF, POLICY), at, cid="changed") == changed
        )
        assert reopened.read_account_risk_events() == rows
    finally:
        reopened.close()


def test_canonical_reservation_conflicts_also_deduplicate_without_new_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alpaca_ross_bot.account_risk_owner import EntryRiskPlan

    with account_sources(tmp_path, monkeypatch) as (source, risk):
        selection = source.selection(AT)
        first = risk.reserve("stable", selection, AT, PLAN)
        quantity = first["quantity"]
        assert type(quantity) is int and quantity > 0
        changed_plan = EntryRiskPlan("changed", "AAPL", "10", "9.50")
        changed = risk.reserve("stable", selection, AT, changed_plan)
        rows = source.journal.read_account_risk_events()
        for _ in range(3):
            assert risk.reserve("stable", selection, AT, changed_plan) == changed
        assert source.journal.read_account_risk_events() == rows
        assert changed["quantity"] == 0
        state = risk.view()
        assert (
            state is not None
            and state["held"]["client_order_id"] == PLAN.client_order_id
        )
        assert state["veto"] and state["capacity"] == "0"
