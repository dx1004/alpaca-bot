"""Synthetic private journals only; no source or broker acceptance."""

import hashlib
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_journal import OcoJournal, OcoJournalView
from alpaca_ross_bot.oco_research import (
    Execution,
    LotPortion,
    OcoResearchError,
    OcoResearchState,
    load_oco_research_contract,
)

NOW = datetime(2026, 10, 21, 14, tzinfo=UTC)
ACCOUNT = "sha256:" + "a" * 64


def _initial() -> OcoResearchState:
    return OcoResearchState(
        load_oco_research_contract(),
        ACCOUNT,
        "AAA",
        "entry-1",
        Fraction(10),
        Fraction(9),
    )


def _journal(tmp_path: Path, *, bind: bool = True) -> Journal:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    if bind:
        journal.record_paper_account_verification(ACCOUNT, b"synthetic", NOW)
    return journal


def _entry(
    identity: str = "fill-1", quantity: str = "2", price: str = "49/5"
) -> Execution:
    return Execution(
        identity,
        ACCOUNT,
        "AAA",
        "entry-1",
        "ENTRY",
        Fraction(quantity),
        Fraction(price),
    )


def _parts(quantity: str = "2", entry: str = "fill-1") -> tuple[LotPortion, ...]:
    return (LotPortion(entry, Fraction(quantity)),)


def _state_row(journal: Journal, key: str, value: object | None) -> None:
    if value is None:
        journal._db.execute("DELETE FROM state WHERE key=?", (key,))
    else:
        journal._db.execute(
            "UPDATE state SET value_json=? WHERE key=?", (json.dumps(value), key)
        )
    journal._db.commit()


def test_physical_restart_preserves_unknown_reservations_and_original_namespaces(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    owner.observe_execution(_entry(), NOW)
    original = owner.allocate_group("group-1", _parts(), NOW)
    rows = journal.read_oco_research_events()
    assert original.model.reserved_quantity == 2
    assert original.model.groups[0].acknowledgement == "UNKNOWN"
    assert not journal.read_incident_events()
    assert not journal.read_order_events()
    assert not journal.read_strategy_risk_events()
    assert not journal.read_account_risk_events()
    journal.close()

    reopened = _journal(tmp_path, bind=False)
    recovered = OcoJournal(reopened, _initial())
    assert recovered.view() == original
    assert recovered.verify_rebuild()
    assert reopened.read_oco_research_events() == rows
    with pytest.raises(OcoResearchError, match="disjoint"):
        recovered.allocate_group("new-key", _parts(), NOW)
    assert not any(
        v
        for k, v in original.model.authority.items()
        if k != "research_semantics_accepted"
    )
    reopened.close()


def test_all_four_operations_have_timestamp_independent_deduplication(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    entry = owner.observe_execution(_entry(), NOW)
    assert owner.observe_execution(_entry(), NOW + timedelta(days=1)) == entry
    allocation = owner.allocate_group("group-1", _parts(), NOW)
    assert (
        owner.allocate_group("group-1", _parts(), NOW + timedelta(seconds=1))
        == allocation
    )
    acknowledged = owner.acknowledge_group("group-1", "ack-1", NOW)
    assert (
        owner.acknowledge_group("group-1", "ack-1", NOW + timedelta(seconds=2))
        == acknowledged
    )
    terminal = owner.reconcile_entry_terminal("census-1", ("fill-1",), NOW)
    assert (
        owner.reconcile_entry_terminal(
            "census-1", ("fill-1",), NOW + timedelta(seconds=3)
        )
        == terminal
    )
    assert terminal.model.final_target == Fraction(57, 5)
    assert len(journal.read_oco_research_events()) == 5
    assert owner.verify_rebuild()
    journal.close()


def test_exact_portion_order_is_normalized_without_different_allocation_identity(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    owner.observe_execution(_entry(quantity="1/2"), NOW)
    owner.observe_execution(_entry("fill-2", "1/2"), NOW)
    parts = _parts("1/2") + _parts("1/2", "fill-2")
    first = owner.allocate_group("group-1", parts, NOW)
    assert owner.allocate_group("group-1", tuple(reversed(parts)), NOW) == first
    assert first.model.reserved_quantity == 1
    journal.close()


@pytest.mark.parametrize("operation", ["execution", "allocation", "ack", "census"])
def test_conflicting_identity_retains_original_and_latches_across_restart(
    tmp_path: Path,
    operation: str,
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    owner.observe_execution(_entry(), NOW)
    owner.allocate_group("group-1", _parts("1"), NOW)
    owner.acknowledge_group("group-1", "ack-1", NOW)
    owner.reconcile_entry_terminal("census-1", ("fill-1",), NOW)
    original = owner.view().model

    def retry() -> OcoJournalView:
        if operation == "execution":
            return owner.observe_execution(_entry(quantity="3"), NOW)
        if operation == "allocation":
            return owner.allocate_group("group-1", _parts("2"), NOW)
        if operation == "ack":
            return owner.acknowledge_group("group-1", "changed", NOW)
        return owner.reconcile_entry_terminal("census-1", (), NOW)

    before = len(journal.read_oco_research_events())
    conflict = retry()
    assert conflict.model == original
    assert "OPERATION_IDENTITY_CONFLICT" in conflict.issues
    assert len(journal.read_oco_research_events()) == before + 1
    assert retry() == conflict
    assert len(journal.read_oco_research_events()) == before + 1
    with pytest.raises(JournalError, match="conflict blocks"):
        owner.allocate_group("new-group", _parts("1"), NOW)
    journal.close()

    reopened = _journal(tmp_path, bind=False)
    recovered = OcoJournal(reopened, _initial())
    assert recovered.view() == conflict
    assert recovered.verify_rebuild()
    reopened.close()


@pytest.mark.parametrize("field", ["limit", "stop", "symbol"])
def test_existing_scope_rejects_different_frozen_binding(
    tmp_path: Path, field: str
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    original_rows = journal.read_oco_research_events()
    initial = _initial()
    changed = {
        "limit": replace(initial, limit=Fraction(11)),
        "stop": replace(initial, stop=Fraction(8)),
        "symbol": replace(initial, symbol="BBB"),
    }[field]
    with pytest.raises(JournalError, match="frozen binding"):
        OcoJournal(journal, changed)
    assert journal.read_oco_research_events() == original_rows
    assert owner.view().model == initial
    journal.close()


def test_constructor_refuses_nonempty_state_and_unbound_or_foreign_account(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    nonempty = owner.observe_execution(_entry(), NOW).model
    count = len(journal.read_oco_research_events())
    with pytest.raises(JournalError, match="empty"):
        OcoJournal(journal, nonempty)
    with pytest.raises(JournalError, match="not bound"):
        OcoJournal(journal, replace(_initial(), account_ref="foreign"))
    assert len(journal.read_oco_research_events()) == count
    journal.close()
    unbound = _journal(tmp_path / "unbound", bind=False)
    with pytest.raises(JournalError, match="not bound"):
        OcoJournal(unbound, _initial())
    assert not unbound.read_oco_research_events()
    unbound.close()


@pytest.mark.parametrize("fault", ["append", "commit"])
def test_failed_commit_rolls_back_raw_cache_projection_and_in_memory_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    original = owner.observe_execution(_entry(), NOW)
    rows = journal.read_oco_research_events()
    append = journal._append_locked

    def fail(
        event: EventEnvelope, payload: bytes, updates: Mapping[str, object] | None
    ) -> None:
        append(event, payload, updates)
        raise OSError("synthetic commit failure")

    connection = journal._db
    if fault == "append":
        monkeypatch.setattr(journal, "_append_locked", fail)
    else:
        broken = Mock(wraps=connection)
        broken.commit.side_effect = OSError("synthetic commit failure")
        monkeypatch.setattr(journal, "_db", broken)
    with pytest.raises(OSError, match="synthetic commit"):
        owner.allocate_group("group-1", _parts(), NOW)
    assert journal.read_oco_research_events() == rows
    assert owner.view() == original
    assert owner.verify_rebuild()
    monkeypatch.setattr(journal, "_append_locked", append)
    monkeypatch.setattr(journal, "_db", connection)
    assert owner.allocate_group("group-1", _parts(), NOW).model.reserved_quantity == 2
    journal.close()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "rewind",
        "authority",
        "float",
        "orphan-dedupe",
        "missing-dedupe",
        "wrong-dedupe",
    ],
)
def test_cache_corruption_is_never_repaired_on_retry_or_restart(
    tmp_path: Path, fault: str
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    earlier = journal.read_state(owner.state_key)
    owner.observe_execution(_entry(), NOW)
    current = journal.read_state(owner.state_key)
    assert isinstance(current, dict)
    cache = journal.read_oco_research_evidence_cache(ACCOUNT)
    key = next(key for key in cache if "EXECUTION" in key)
    dedupe_key = "oco_research_evidence:" + ACCOUNT + ":" + key
    if fault == "missing":
        _state_row(journal, owner.state_key, None)
    elif fault == "rewind":
        _state_row(journal, owner.state_key, earlier)
    elif fault == "authority":
        current["authority"]["runtime_authorized"] = True
        _state_row(journal, owner.state_key, current)
    elif fault == "float":
        current["model"]["executions"][0]["quantity"] = 2.0
        _state_row(journal, owner.state_key, current)
    elif fault == "orphan-dedupe":
        orphan = json.dumps(["entry-1", "EXECUTION", "orphan"], separators=(",", ":"))
        journal._db.execute(
            "INSERT INTO state(key,value_json) VALUES(?,?)",
            (
                "oco_research_evidence:" + ACCOUNT + ":" + orphan,
                json.dumps({"hash": "sha256:" + "b" * 64, "sequence": 1}),
            ),
        )
        journal._db.commit()
    elif fault == "missing-dedupe":
        _state_row(journal, dedupe_key, None)
    else:
        _state_row(journal, dedupe_key, {"hash": "sha256:" + "b" * 64, "sequence": 1})
    rows = journal.read_oco_research_events()
    with pytest.raises((JournalError, ValueError)):
        owner.observe_execution(_entry(), NOW)
    with pytest.raises((JournalError, ValueError)):
        OcoJournal(journal, _initial())
    assert journal.read_oco_research_events() == rows
    journal.close()


@pytest.mark.parametrize("canonical_digest", [False, True])
def test_raw_corruption_aborts_replay_even_if_outer_hash_is_recomputed(
    tmp_path: Path, canonical_digest: bool
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    owner.observe_execution(_entry(), NOW)
    event, raw = journal.read_oco_research_events()[-1]
    item = json.loads(raw)
    item["operation"]["quantity"] = "4/2"
    corrupted = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    journal._db.execute("DROP TRIGGER events_no_update")
    if canonical_digest:
        journal._db.execute(
            "UPDATE events SET payload=?,payload_ref=? WHERE sequence=?",
            (
                corrupted,
                "sha256:" + hashlib.sha256(corrupted).hexdigest(),
                event.sequence,
            ),
        )
    else:
        journal._db.execute(
            "UPDATE events SET payload=? WHERE sequence=?", (corrupted, event.sequence)
        )
    journal._db.commit()
    with pytest.raises((JournalError, ValueError)):
        owner.view()
    with pytest.raises((JournalError, ValueError)):
        OcoJournal(journal, _initial())
    journal.close()


def test_racing_allocations_across_two_connections_cannot_share_one_lot(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    OcoJournal(journal, _initial()).observe_execution(_entry(), NOW)
    journal.close()
    barrier = Barrier(2)

    def reserve(group: str) -> str:
        connection = _journal(tmp_path, bind=False)
        owner = OcoJournal(connection, _initial())
        barrier.wait(timeout=5)
        try:
            owner.allocate_group(group, _parts(), NOW)
            return "allocated"
        except OcoResearchError:
            return "refused"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ["group-1", "group-2"]))
    assert sorted(results) == ["allocated", "refused"]
    reopened = _journal(tmp_path, bind=False)
    final = OcoJournal(reopened, _initial()).view()
    assert len(final.model.groups) == 1
    assert final.model.reserved_quantity == 2
    reopened.close()


def test_competing_constructor_bindings_create_only_one_owner(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.close()
    barrier = Barrier(2)

    def bind(limit: int) -> str:
        connection = _journal(tmp_path, bind=False)
        barrier.wait(timeout=5)
        try:
            OcoJournal(connection, replace(_initial(), limit=Fraction(limit)))
            return "bound"
        except JournalError:
            return "refused"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(bind, [10, 11])) == ["bound", "refused"]
    reopened = _journal(tmp_path, bind=False)
    assert len(reopened.read_oco_research_events()) == 1
    reopened.close()


def test_early_exit_late_fill_and_terminal_census_are_rebuilt_exactly(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    owner = OcoJournal(journal, _initial())
    owner.observe_execution(_entry(), NOW)
    owner.allocate_group("group-1", _parts(), NOW)
    target = Execution(
        "target-fill",
        ACCOUNT,
        "AAA",
        "entry-1",
        "TARGET",
        Fraction(2),
        Fraction(12),
        "group-1",
    )
    owner.observe_execution(target, NOW)
    owner.reconcile_entry_terminal("terminal-1", ("fill-1",), NOW)
    late = owner.observe_execution(_entry("late", "1/2", "19/2"), NOW)
    assert not late.model.entry_terminal_reconciled
    assert "FRACTIONAL_RESIDUAL_UNQUALIFIED" in late.issues
    final = owner.reconcile_entry_terminal("terminal-2", ("fill-1", "late"), NOW)
    assert final.model.average_entry == Fraction(487, 50)
    assert final.model.final_target == Fraction(561, 50)
    assert final.model.owned_quantity == Fraction(1, 2)
    journal.close()
    reopened = _journal(tmp_path, bind=False)
    assert OcoJournal(reopened, _initial()).view() == final
    reopened.close()
