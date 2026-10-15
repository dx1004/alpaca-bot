"""Private, synthetic persistence faults; no credentials or broker access."""

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.incident_journal import IncidentJournal
from alpaca_ross_bot.incident_state import (
    IncidentObservation,
    IncidentOrder,
    IncidentPolicy,
    OrderRole,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.order_lifecycle import LifecycleState, OrderProjection
from alpaca_ross_bot.order_reconciliation import OrderReconciler

NOW = datetime(2026, 10, 15, 18, tzinfo=UTC)
ACCOUNT = "sha256:" + "a" * 64
POLICY = IncidentPolicy(
    ACCOUNT,
    "HYBRID_500_IEX_INTRADAY_RESEARCH_V1",
    "2026-10-15:roundtrip-1",
    "AAPL",
    ("P11", "P12", "P13"),
    "sha256:" + "b" * 64,
)


def _journal(tmp_path: Path) -> Journal:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    journal.record_paper_account_verification(ACCOUNT, b"synthetic", NOW)
    return journal


def _observation(quantity: str = "2", watermark: int = 1) -> IncidentObservation:
    entry = OrderProjection(
        ACCOUNT,
        "entry-1",
        "11111111-1111-4111-8111-111111111111",
        "AAPL",
        "buy",
        Decimal(10),
        Decimal(quantity),
        LifecycleState.PARTIAL,
        "partially_filled",
        watermark,
        "sha256:" + "c" * 64,
    )
    stop = OrderProjection(
        ACCOUNT,
        "stop-1",
        "22222222-2222-4222-8222-222222222222",
        "AAPL",
        "sell",
        Decimal(quantity),
        Decimal(0),
        LifecycleState.ACCEPTED,
        "new",
        watermark,
        "sha256:" + "d" * 64,
    )
    return IncidentObservation(
        "observation-" + str(watermark),
        ACCOUNT,
        POLICY.version,
        POLICY.scope_id,
        "sha256:" + "e" * 64,
        watermark,
        Decimal(0),
        True,
        Decimal(quantity),
        (
            IncidentOrder(entry, OrderRole.ENTRY),
            IncidentOrder(stop, OrderRole.STOP, True),
        ),
        reconciliation_verified=True,
        data_verified=True,
        durable=True,
        stream_connected=True,
    )


def test_restart_preserves_fills_protection_and_requires_recovery(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    lifecycle = OrderReconciler(journal, ACCOUNT, clock=lambda: NOW)
    store = IncidentJournal(journal, POLICY)
    first = store.observe(_observation(), NOW)
    assert first.expected_quantity == Decimal(2)
    assert first.preserve_protection_ids == ("stop-1",)
    assert not first.new_exposure_allowed
    assert store.verify_rebuild() and lifecycle.verify_rebuild()
    journal.close()

    root = tmp_path / "private"
    reopened = Journal.open_private(root / "journal.sqlite3", root)
    recovered = IncidentJournal(reopened, POLICY)
    state = recovered.view()
    assert state is not None
    assert state.expected_quantity == Decimal(2)
    assert state.preserve_protection_ids == ("stop-1",)
    assert state.pending_cancel_ids == first.pending_cancel_ids == ("entry-1",)
    assert "RESTART_REQUIRES_RECOVERY" in state.reasons
    assert not state.new_exposure_allowed and recovered.verify_rebuild()
    raw = reopened.read_state(recovered.state_key)
    assert isinstance(raw, dict) and raw["dispatch_authorized"] is False
    lifecycle_recovered = OrderReconciler(reopened, ACCOUNT, clock=lambda: NOW)
    assert lifecycle_recovered.verify_rebuild()
    reopened.close()


def test_duplicate_and_changed_identity_are_atomic_and_replayable(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    store = IncidentJournal(journal, POLICY)
    observation = _observation()
    original = store.observe(observation, NOW)
    count = len(journal.read_incident_events())
    assert store.observe(observation, NOW) == original
    assert len(journal.read_incident_events()) == count
    conflicting = replace(_observation("5", 2), event_id=observation.event_id)
    state = store.observe(conflicting, NOW)
    assert "EVENT_ID_CONFLICT" in state.reasons
    assert state.expected_quantity == Decimal(2)
    assert len(journal.read_incident_events()) == count + 1
    assert store.verify_rebuild()
    journal.close()


def test_storage_failure_rolls_back_raw_projection_and_deduplication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = _journal(tmp_path)
    store = IncidentJournal(journal, POLICY)
    original = store.observe(_observation(), NOW)
    rows = journal.read_incident_events()
    append = journal._append_locked

    def fail(
        event: EventEnvelope, payload: bytes, updates: Mapping[str, object] | None
    ) -> None:
        append(event, payload, updates)
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(journal, "_append_locked", fail)
    with pytest.raises(OSError, match="synthetic disk"):
        store.observe(_observation("5", 2), NOW)
    assert store.view() == original
    assert journal.read_incident_events() == rows
    identity = json.dumps(
        [POLICY.scope_id, "observe:observation-2"], separators=(",", ":")
    )
    assert journal.read_state("incident_evidence:" + ACCOUNT + ":" + identity) is None
    assert store.verify_rebuild()
    journal.close()


@pytest.mark.parametrize("fault", ["rewind", "missing"])
def test_restart_rejects_cache_that_hides_later_incident_evidence(
    tmp_path: Path, fault: str
) -> None:
    journal = _journal(tmp_path)
    store = IncidentJournal(journal, POLICY)
    store.observe(_observation(), NOW)
    old = journal.read_state(store.state_key)
    store.observe(_observation("5", 2), NOW)
    path = tmp_path / "private/journal.sqlite3"
    with sqlite3.connect(path) as connection:
        if fault == "rewind":
            connection.execute(
                "UPDATE state SET value_json=? WHERE key=?",
                (json.dumps(old), store.state_key),
            )
        else:
            connection.execute("DELETE FROM state WHERE key=?", (store.state_key,))
    assert not store.verify_rebuild()
    count = len(journal.read_incident_events())
    with pytest.raises(JournalError, match="immutable evidence"):
        IncidentJournal(journal, POLICY)
    assert len(journal.read_incident_events()) == count
    journal.close()


@pytest.mark.parametrize("field", ["account_ref", "scope_id"])
def test_foreign_scope_cannot_modify_committed_incident(
    tmp_path: Path, field: str
) -> None:
    journal = _journal(tmp_path)
    store = IncidentJournal(journal, POLICY)
    original = store.observe(_observation(), NOW)
    count = len(journal.read_incident_events())
    foreign = (
        replace(_observation("5", 2), account_ref="foreign")
        if field == "account_ref"
        else replace(_observation("5", 2), scope_id="foreign")
    )
    with pytest.raises(JournalError, match="account/scope"):
        store.observe(foreign, NOW)
    assert store.view() == original and len(journal.read_incident_events()) == count
    journal.close()


@pytest.mark.parametrize(
    "field", ["runtime_risk_accepted", "protection_contract_accepted"]
)
def test_hash_and_flag_cannot_self_grant_runtime_authority(
    tmp_path: Path, field: str
) -> None:
    journal = _journal(tmp_path)
    policy = (
        replace(
            POLICY, runtime_risk_accepted=True, risk_acceptance_ref="sha256:" + "f" * 64
        )
        if field == "runtime_risk_accepted"
        else replace(
            POLICY,
            protection_contract_accepted=True,
            protection_acceptance_ref="sha256:" + "f" * 64,
        )
    )
    with pytest.raises(JournalError, match="verifier is unavailable"):
        IncidentJournal(journal, policy)
    assert journal.read_incident_events() == ()
    journal.close()


def test_separate_scopes_rebuild_without_sharing_projection(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    first = IncidentJournal(journal, POLICY)
    first.observe(_observation(), NOW)
    second = IncidentJournal(journal, replace(POLICY, scope_id="roundtrip-2"))
    assert second.view() is None
    assert first.verify_rebuild() and second.verify_rebuild()
    journal.close()


def test_scope_and_event_delimiters_cannot_collide(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    first_policy = replace(POLICY, scope_id="a")
    second_policy = replace(POLICY, scope_id="a:observe:b")
    first = IncidentJournal(journal, first_policy)
    second = IncidentJournal(journal, second_policy)
    one = replace(
        _observation(), scope_id=first_policy.scope_id, event_id="b:observe:c"
    )
    two = replace(_observation("5"), scope_id=second_policy.scope_id, event_id="c")
    assert first.observe(one, NOW).expected_quantity == Decimal(2)
    assert second.observe(two, NOW).expected_quantity == Decimal(5)
    assert first.verify_rebuild() and second.verify_rebuild()
    journal.close()


def test_changed_stale_event_is_preserved_as_identity_conflict(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    store = IncidentJournal(journal, POLICY)
    store.observe(_observation(watermark=10), NOW)
    stale = replace(_observation(watermark=2), event_id="stale-id")
    assert "STALE_OBSERVATION" in store.observe(stale, NOW).reasons
    count = len(journal.read_incident_events())
    changed = replace(_observation("5", 3), event_id="stale-id")
    state = store.observe(changed, NOW)
    assert "EVENT_ID_CONFLICT" in state.reasons
    assert state.expected_quantity == Decimal(2)
    assert len(journal.read_incident_events()) == count + 1
    assert store.verify_rebuild()
    journal.close()


@pytest.mark.parametrize("fault", ["orphan", "missing", "wrong_hash", "wrong_sequence"])
def test_deduplication_corruption_cannot_silently_hide_new_fills(
    tmp_path: Path, fault: str
) -> None:
    journal = _journal(tmp_path)
    store = IncidentJournal(journal, POLICY)
    original = store.observe(_observation(), NOW)
    latest = journal.read_incident_events()[-1][0].sequence
    target = _observation("5", 2)
    suffix = json.dumps(
        [POLICY.scope_id, "observe:" + target.event_id], separators=(",", ":")
    )
    key = "incident_evidence:" + ACCOUNT + ":" + suffix
    payload = json.dumps(
        {
            "kind": "OBSERVE",
            "policy": POLICY.to_dict(),
            "observation": target.to_dict(),
            "dedupe_key": "observe:" + target.event_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    value = {
        "hash": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "sequence": latest,
    }
    with sqlite3.connect(tmp_path / "private/journal.sqlite3") as connection:
        if fault == "orphan":
            connection.execute(
                "INSERT INTO state VALUES (?,?)", (key, json.dumps(value))
            )
        else:
            existing_suffix = json.dumps(
                [POLICY.scope_id, "observe:observation-1"], separators=(",", ":")
            )
            existing_key = "incident_evidence:" + ACCOUNT + ":" + existing_suffix
            if fault == "missing":
                connection.execute("DELETE FROM state WHERE key=?", (existing_key,))
            else:
                current = journal.read_state(existing_key)
                assert isinstance(current, dict)
                current["hash" if fault == "wrong_hash" else "sequence"] = (
                    "sha256:" + "0" * 64 if fault == "wrong_hash" else latest + 100
                )
                connection.execute(
                    "UPDATE state SET value_json=? WHERE key=?",
                    (json.dumps(current), existing_key),
                )
    assert not store.verify_rebuild()
    rows = journal.read_incident_events()
    with pytest.raises(JournalError, match="differs from evidence"):
        store.observe(target, NOW)
    assert journal.read_incident_events() == rows
    assert store.view() == original
    with pytest.raises(JournalError, match="immutable evidence"):
        IncidentJournal(journal, POLICY)
    journal.close()
