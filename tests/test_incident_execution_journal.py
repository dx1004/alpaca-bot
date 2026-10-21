"""Synthetic source-prefix incident receipts; no broker/native-protection proof."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.incident_execution import derive_execution_incident
from alpaca_ross_bot.incident_journal import IncidentJournal
from alpaca_ross_bot.incident_state import (
    IncidentObservation,
    IncidentPolicy,
    ProposalKind,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import CANDIDATE, load_oco_research_contract
from test_account_risk_owner import SourceAccount
from test_observation_candidate_binding import CID, contract
from test_oco_entry_evidence import candidate as candidate
from test_oco_entry_evidence import prepared as prepared
from test_risk_ledger_audit import POLICY, REF

type Prepared = tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime]


def _policy() -> IncidentPolicy:
    return IncidentPolicy(
        REF,
        CANDIDATE,
        CID,
        "AAPL",
        ("P11", "P12", "P13"),
        REF,
        observation_contract=contract(),
        oco_contract=load_oco_research_contract(),
    )


def _observe(
    store: IncidentJournal,
    at: datetime,
    event_id: str = "sample",
    *,
    received_at: datetime | None = None,
) -> dict[str, object]:
    return store.observe_execution(
        event_id,
        at,
        at if received_at is None else received_at,
        account_risk_fee_policy=POLICY,
    )


def _late(source: SourceAccount, risk: AccountRiskOwner, at: datetime) -> datetime:
    later = at + timedelta(seconds=1)
    source.order(CID, later, "partially_filled", 3)
    source.fill(CID, later, 1, "10")
    risk.observe("late", source.selection(later, cash="470", position=3), later)
    return later


def test_source_receipt_and_projection_commit_without_risk_or_order_mutation(
    prepared: Prepared,
) -> None:
    source, risk, book, at = prepared
    store = IncidentJournal(source.journal, _policy())
    risk_before, book_before = risk.view(), book.view()
    intent_ids = source.journal.read_order_intent_ids()
    receipt = _observe(store, at)
    state = store.view()
    assert state is not None and state.expected_quantity == Decimal(2)
    assert state.observed_position == Decimal(2)
    assert state.pending_entry_ids == (CID,)
    assert state.preserve_protection_ids == ()
    assert not state.new_exposure_allowed
    assert {"UNPROTECTED", "PROTECTION_BROKER_GATE_UNACCEPTED"} <= set(state.reasons)
    assert ProposalKind.VERIFY_PROTECTION in {p.kind for p in state.proposals}
    authority = receipt["authority"]
    assert isinstance(authority, dict) and all(not flag for flag in authority.values())
    assert (
        receipt["classification"]
        == "LOCAL_REPORTED_EXECUTION_INCIDENT_NOT_SOURCE_ADMISSION"
    )
    assert risk.view() == risk_before and book.view() == book_before
    assert source.journal.read_order_intent_ids() == intent_ids
    assert store.verify_rebuild() and risk.verify_rebuild() and book.verify_rebuild()


def test_retry_keeps_original_horizon_and_conflict_cannot_reinterpret_it(
    prepared: Prepared,
) -> None:
    source, risk, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    original = _observe(store, at)
    later = _late(source, risk, at)
    count = len(source.journal.read_incident_events())
    assert _observe(store, at, received_at=later) == original
    assert len(source.journal.read_incident_events()) == count
    assert _observe(store, later) == original
    conflict = store.view()
    assert conflict is not None and "EVENT_ID_CONFLICT" in conflict.reasons
    assert conflict.expected_quantity == 2
    assert len(source.journal.read_incident_events()) == count + 1
    assert _observe(store, later, received_at=later + timedelta(seconds=1)) == original
    assert len(source.journal.read_incident_events()) == count + 1
    assert store.verify_rebuild()


def test_retry_old_request_after_new_observation_returns_old_receipt_only(
    prepared: Prepared,
) -> None:
    source, risk, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    original = _observe(store, at)
    later = _late(source, risk, at)
    newer = _observe(store, later, "next")
    assert newer["source_horizon"] != original["source_horizon"]
    state = store.view()
    assert state is not None and state.expected_quantity == 3
    rows = source.journal.read_incident_events()
    assert _observe(store, at, received_at=later) == original
    assert store.view() == state
    assert source.journal.read_incident_events() == rows
    assert store.verify_rebuild()


def test_derived_append_failure_preserves_committed_source_and_original_receipt(
    prepared: Prepared, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    _observe(store, at)
    later = _late(source, risk, at)
    old = source.journal.read_state(store.state_key)
    rows = source.journal.read_incident_events()
    risk_before = risk.view()
    append = source.journal._append_locked

    def fail(
        event: EventEnvelope, payload: bytes, updates: Mapping[str, object] | None
    ) -> None:
        append(event, payload, updates)
        raise OSError("synthetic incident receipt failure")

    monkeypatch.setattr(source.journal, "_append_locked", fail)
    with pytest.raises(OSError, match="receipt failure"):
        _observe(store, later, "failed")
    assert source.journal.read_state(store.state_key) == old
    assert source.journal.read_incident_events() == rows
    assert risk.view() == risk_before
    assert risk_before is not None and risk_before["held"]["filled"] == 3
    assert store.verify_rebuild() and risk.verify_rebuild()


def test_receive_time_cannot_precede_derived_source_decision(
    prepared: Prepared,
) -> None:
    source, _, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    rows = source.journal.read_incident_events()
    with pytest.raises(JournalError, match="predates decision"):
        _observe(store, at, received_at=at - timedelta(microseconds=1))
    assert store.view() is None
    assert source.journal.read_incident_events() == rows
    assert store.verify_rebuild()


@pytest.mark.parametrize("source_first", [False, True])
def test_source_and_declared_observation_modes_cannot_mix(
    prepared: Prepared, source_first: bool
) -> None:
    source, _, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    receipt = derive_execution_incident(
        source.journal,
        store.policy,
        event_id="declared",
        decision_at=at,
        through_sequence=source.journal.read_evidence_sequence(),
        fee_policy=POLICY,
    )
    observation = IncidentObservation.from_dict(receipt["observation"])
    if source_first:
        _observe(store, at)
    else:
        store.observe(observation, at)
    rows = source.journal.read_incident_events()
    state = source.journal.read_state(store.state_key)
    with pytest.raises(JournalError, match="scope"):
        if source_first:
            store.observe(observation, at)
        else:
            _observe(store, at)
    assert source.journal.read_state(store.state_key) == state
    assert source.journal.read_incident_events() == rows
    assert store.verify_rebuild()


def test_restart_retains_original_source_receipt_and_refusal(
    prepared: Prepared, tmp_path: Path
) -> None:
    source, _, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    original = _observe(store, at)
    state = store.view()
    assert state is not None
    root = tmp_path / "private"
    path = root / "journal.sqlite3"
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        recovered = IncidentJournal(reopened, _policy())
        projection = recovered.view()
        assert projection is not None
        assert projection.expected_quantity == state.expected_quantity
        assert "RESTART_REQUIRES_RECOVERY" in projection.reasons
        assert not projection.new_exposure_allowed
        assert (
            _observe(recovered, at, received_at=at + timedelta(seconds=1)) == original
        )
        assert recovered.verify_rebuild()
    finally:
        reopened.close()


@pytest.mark.parametrize("mutation", ["receipt", "horizon", "authority", "extra"])
def test_replay_refuses_forged_derived_receipt(
    prepared: Prepared, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    _observe(store, at)
    read = source.journal.read_incident_events

    def changed() -> tuple[tuple[EventEnvelope, bytes], ...]:
        rows = []
        for event, payload in read():
            item = json.loads(payload)
            if item["kind"] == "BOUND_EXECUTION":
                if mutation == "receipt":
                    item["execution_receipt"]["observation"]["position_quantity"] = (
                        "999"
                    )
                elif mutation == "horizon":
                    item["source_horizon"] = True
                elif mutation == "authority":
                    item["execution_receipt"]["authority"]["runtime_authorized"] = True
                else:
                    item["extra"] = True
                payload = json.dumps(
                    item, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            rows.append((event, payload))
        return tuple(rows)

    monkeypatch.setattr(source.journal, "read_incident_events", changed)
    with pytest.raises(JournalError):
        store.verify_rebuild()


def test_replay_refuses_backdated_event_metadata(
    prepared: Prepared, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, _, at = prepared
    store = IncidentJournal(source.journal, _policy())
    _observe(store, at)
    read = source.journal.read_incident_events

    def changed() -> tuple[tuple[EventEnvelope, bytes], ...]:
        return tuple(
            (
                replace(
                    event,
                    system_first_seen_at=at - timedelta(microseconds=1),
                    processed_at=at - timedelta(microseconds=1),
                    available_at=at - timedelta(microseconds=1),
                )
                if event.event_kind == "BOUND_EXECUTION"
                else event,
                payload,
            )
            for event, payload in read()
        )

    monkeypatch.setattr(source.journal, "read_incident_events", changed)
    with pytest.raises(JournalError, match="predates decision"):
        store.verify_rebuild()
