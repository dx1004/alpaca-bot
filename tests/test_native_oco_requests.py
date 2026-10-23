"""Synthetic source-bound request provenance; never a broker dispatch test."""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from test_account_risk_owner import SourceAccount, account_sources
from test_observation_candidate_binding import AT, CID, new_decision
from test_oco_entry_evidence import _initial, _prepare
from test_risk_ledger_audit import POLICY


def _rows(journal: Journal) -> tuple[object, ...]:
    return (
        tuple(journal._db.execute("SELECT * FROM events ORDER BY sequence")),
        tuple(journal._db.execute("SELECT * FROM state ORDER BY key")),
    )


@pytest.fixture
def prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime]]:
    # Inject only the synthetic BIND receipt clock. Production visibility checks
    # stay intact; retrospective account fixtures must not need a wall-clock bypass.
    record = OcoJournal._record

    def timed_record(
        self: OcoJournal, kind: str, operation: dict[str, object], at: datetime
    ) -> object:
        return record(self, kind, operation, AT if kind == "BIND" else at)

    monkeypatch.setattr(OcoJournal, "_record", timed_record)
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        at = _prepare(source, risk, new_decision())
        owner = OcoJournal(source.journal, _initial())
        bound = owner.bind_entry_census("bound-partial", at, POLICY, at)
        owner.allocate_group(
            "group-a",
            (LotPortion(bound.model.executions[0].execution_id, Fraction(1)),),
            at,
        )
        yield source, owner, NativeOcoRequests(owner), at


def test_prepared_bytes_have_exact_source_terms_without_send_or_new_risk(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    risk_rows = source.journal.read_account_risk_events()
    order_rows = source.journal.read_order_events()
    intents = source.journal.read_order_intent_ids()
    oco_rows = source.journal.read_oco_research_events()
    result = requests.prepare("group-a", "native-cid-a", at, at)
    receipt = result["receipt"]
    assert receipt["classification"] == "PREPARED_NOT_BROKER_VALIDATED"
    assert json.loads(receipt["request_body"]) == {
        "client_order_id": "native-cid-a",
        "symbol": "AAPL",
        "qty": "1",
        "side": "sell",
        "type": "limit",
        "order_class": "oco",
        "time_in_force": "day",
        "take_profit": {"limit_price": "11.03"},
        "stop_loss": {"stop_price": "9.50"},
    }
    assert (
        receipt["request_sha256"]
        == "sha256:" + hashlib.sha256(receipt["request_body"].encode()).hexdigest()
    )
    assert receipt["source_horizon"] < result["request_ref"]["sequence"]
    assert receipt["reservation_ref"] == receipt["entry_census"]["reservation_ref"]
    for seq, digest in receipt["source_refs"]:
        original = source.journal.read_event(seq)
        assert original is not None and original[0].payload_ref == digest
        assert seq <= receipt["source_horizon"]
        assert original[0].visible_at(at)
    assert not any(receipt["authority"].values())
    assert not any(result["authority"].values())
    assert result["issues"] == []
    assert source.journal.read_account_risk_events() == risk_rows
    assert source.journal.read_order_events() == order_rows
    assert source.journal.read_order_intent_ids() == intents
    assert source.journal.read_oco_research_events() == oco_rows
    assert source.journal.read_order_send_started_at("native-cid-a") is None
    assert owner.view().model.groups[0].acknowledgement == "UNKNOWN"
    assert requests.verify_rebuild()


def test_retry_preserves_original_prefix_after_later_allocation_and_staleness(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    owner.allocate_group(
        "group-b",
        (LotPortion(owner.view().model.executions[0].execution_id, Fraction(1)),),
        at + timedelta(seconds=1),
    )
    before = _rows(source.journal)
    retry = requests.prepare("group-a", "native-cid-a", at, at + timedelta(days=1))
    assert retry == original
    assert _rows(source.journal) == before
    assert not any(retry["authority"].values())


@pytest.mark.parametrize("changed", ["cid", "decision"])
def test_changed_request_keeps_original_and_latches_conflict(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    changed: str,
) -> None:
    source, owner, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    result = requests.prepare(
        "group-a",
        "changed-cid" if changed == "cid" else "native-cid-a",
        at + timedelta(seconds=1) if changed == "decision" else at,
        at + timedelta(seconds=1),
    )
    assert result["status"] == "REQUEST_IDENTITY_CONFLICT"
    assert result["receipt"] == original["receipt"]
    assert result["request_ref"] == original["request_ref"]
    assert result["issues"]
    assert NativeOcoRequests(owner).view() == requests.view()
    before = _rows(source.journal)
    assert requests.prepare("group-a", "native-cid-a", at, at + timedelta(seconds=2))[
        "issues"
    ]
    assert _rows(source.journal) == before
    assert requests.verify_rebuild()


def test_duplicate_client_id_cannot_prepare_second_allocated_group(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    owner.allocate_group(
        "group-b",
        (LotPortion(owner.view().model.executions[0].execution_id, Fraction(1)),),
        at,
    )
    before = _rows(source.journal)
    with pytest.raises(JournalError):
        requests.prepare("group-b", "native-cid-a", at, at)
    assert _rows(source.journal) == before


@pytest.mark.parametrize(
    "failure", ["stale", "receive-before-decision", "entry-cid", "missing-group"]
)
def test_invalid_first_preparation_never_creates_receipt(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    failure: str,
) -> None:
    source, _, requests, at = prepared
    decision = at + timedelta(days=1) if failure == "stale" else at
    received = (
        at - timedelta(seconds=1) if failure == "receive-before-decision" else decision
    )
    before = _rows(source.journal)
    with pytest.raises((JournalError, ValueError)):
        requests.prepare(
            "missing" if failure == "missing-group" else "group-a",
            CID if failure == "entry-cid" else "native-cid-a",
            decision,
            received,
        )
    assert _rows(source.journal) == before


def test_storage_failure_cannot_escape_as_successful_preparation(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, requests, at = prepared
    before = _rows(source.journal)
    append = source.journal._append_locked

    def write_then_fail(*args: Any, **kwargs: Any) -> None:
        append(*args, **kwargs)
        raise OSError("disk fault after append")

    monkeypatch.setattr(source.journal, "_append_locked", write_then_fail)
    with pytest.raises(OSError):
        requests.prepare("group-a", "native-cid-a", at, at)
    assert _rows(source.journal) == before
    assert requests.view() is None


def test_repeat_conflict_is_idempotent_and_scope_cannot_prepare_another_group(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    first = requests.prepare("group-a", "changed-cid", at, at)
    before = _rows(source.journal)
    assert (
        requests.prepare("group-a", "changed-cid", at, at + timedelta(seconds=1))
        == first
    )
    assert _rows(source.journal) == before
    owner.allocate_group(
        "group-b",
        (LotPortion(owner.view().model.executions[0].execution_id, Fraction(1)),),
        at,
    )
    before = _rows(source.journal)
    with pytest.raises(JournalError):
        requests.prepare("group-b", "different-cid", at, at)
    assert _rows(source.journal) == before


def test_returned_receipt_is_not_a_mutable_cache_handle(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    result = requests.prepare("group-a", "native-cid-a", at, at)
    original = json.loads(json.dumps(result))
    before = _rows(source.journal)
    result["receipt"]["request_body"] = "changed by consumer"
    result["authority"]["dispatch_authorized"] = True
    assert requests.prepare("group-a", "native-cid-a", at, at) == original
    assert _rows(source.journal) == before


@pytest.mark.parametrize("fault", ["owner-conflict", "declared-ack"])
def test_current_owner_conflict_or_declared_ack_cannot_be_promoted(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    fault: str,
) -> None:
    from dataclasses import replace

    source, owner, requests, at = prepared
    if fault == "owner-conflict":
        original = owner.view().model.executions[0]
        owner.observe_execution(original, at)
        owner.observe_execution(replace(original, quantity=Fraction(3)), at)
    else:
        owner.acknowledge_group("group-a", "declared-not-broker-ack", at)
    before = _rows(source.journal)
    with pytest.raises(JournalError):
        requests.prepare("group-a", "native-cid-a", at, at)
    assert _rows(source.journal) == before
    assert requests.view() is None


def test_physical_restart_preserves_request_and_no_send_marker(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    path, root = source.journal._path, source.journal._runtime_root
    before = _rows(source.journal)
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        owner = OcoJournal(reopened, _initial())
        recovered = NativeOcoRequests(owner)
        assert (
            recovered.prepare("group-a", "native-cid-a", at, at + timedelta(days=1))
            == original
        )
        assert recovered.verify_rebuild()
        assert reopened.read_order_send_started_at("native-cid-a") is None
        assert reopened.read_order_intent("native-cid-a") is None
        assert _rows(reopened) == before
    finally:
        reopened.close()


@pytest.mark.parametrize("kind", ["projection", "dedupe", "raw"])
def test_registry_corruption_blocks_even_identical_retry(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    kind: str,
) -> None:
    source, _, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    if kind == "raw":
        source.journal._db.execute("DROP TRIGGER events_no_update")
        source.journal._db.execute(
            "UPDATE events SET payload=? WHERE sequence=?",
            (b"{}", original["request_ref"]["sequence"]),
        )
    else:
        keys = [
            row[0]
            for row in source.journal._db.execute("SELECT key FROM state ORDER BY key")
            if row[0].startswith("native_oco_request")
            and (("evidence:" in row[0]) == (kind == "dedupe"))
        ]
        assert keys
        source.journal._db.execute("DELETE FROM state WHERE key=?", (keys[0],))
    source.journal._db.commit()
    before = _rows(source.journal)
    with pytest.raises(JournalError):
        requests.prepare("group-a", "native-cid-a", at, at)
    assert _rows(source.journal) == before


def test_two_connections_cannot_assign_one_client_id_to_two_groups(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    source, owner, requests, at = prepared
    owner.allocate_group(
        "group-b",
        (LotPortion(owner.view().model.executions[0].execution_id, Fraction(1)),),
        at,
    )
    path, root = source.journal._path, source.journal._runtime_root
    barrier = Barrier(2)

    def attempt(group: str) -> str:
        journal = Journal.open_private(path, root)
        try:
            other = NativeOcoRequests(OcoJournal(journal, _initial()))
            barrier.wait(timeout=10)
            try:
                other.prepare(group, "racing-cid", at, at)
            except JournalError:
                return "rejected"
            return "prepared"
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("group-a", "group-b")))
    assert sorted(results) == ["prepared", "rejected"]
    current = requests.view()
    assert current is not None and len(current["requests"]) == 1
    assert source.journal.read_order_intent("racing-cid") is None
    assert requests.verify_rebuild()


def test_later_simple_intent_cannot_steal_prepared_native_identity(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    before = _rows(source.journal)
    with pytest.raises(JournalError):
        source.intent("native-cid-a", at, sent=False)
    assert source.journal.read_order_intent("native-cid-a") is None
    assert _rows(source.journal) == before


def test_simple_and_native_preparation_race_has_only_one_identity_winner(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    source, _, requests, at = prepared
    path, root = source.journal._path, source.journal._runtime_root
    barrier = Barrier(2)

    def attempt(native: bool) -> str:
        journal = Journal.open_private(path, root)
        try:
            registry = (
                NativeOcoRequests(OcoJournal(journal, _initial())) if native else None
            )
            barrier.wait(timeout=10)
            try:
                if registry is None:
                    journal.reserve_order_intent(
                        "mixed-race-cid",
                        {"purpose": "ENGINEERING_TEST", "synthetic": True},
                    )
                else:
                    registry.prepare("group-a", "mixed-race-cid", at, at)
            except JournalError:
                return "rejected"
            return "prepared"
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (True, False)))
    assert sorted(results) == ["prepared", "rejected"]
    current = requests.view()
    native_count = 0 if current is None else len(current["requests"])
    simple_count = int(source.journal.read_order_intent("mixed-race-cid") is not None)
    assert native_count + simple_count == 1
    assert source.journal.read_order_send_started_at("mixed-race-cid") is None


def test_other_entry_scope_cannot_reuse_a_prepared_client_id(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    from dataclasses import replace

    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    other_owner = OcoJournal(
        source.journal, replace(_initial(), entry_scope="other-entry-scope")
    )
    other = NativeOcoRequests(other_owner)
    before = _rows(source.journal)
    with pytest.raises(JournalError, match="collision"):
        other.prepare("other-group", "native-cid-a", at, at)
    assert _rows(source.journal) == before
    assert other.view() is None


def test_new_source_entry_fill_must_be_bound_before_preparing_request(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    later = at + timedelta(seconds=1)
    risk = AccountRiskOwner(source.journal, owner.initial.account_ref, POLICY)
    source.order(CID, later, "partially_filled", 3)
    source.fill(CID, later, 1, "10")
    risk.observe("later-fill", source.selection(later, cash="470", position=3), later)
    before = _rows(source.journal)
    with pytest.raises(JournalError):
        requests.prepare("group-a", "native-cid-a", later, later)
    assert _rows(source.journal) == before
    assert requests.view() is None
    owner.bind_entry_census("rebound", later, POLICY, later)
    result = requests.prepare("group-a", "native-cid-a", later, later)
    assert result["receipt"]["entry_census"]["entry"]["filled_quantity"] == "3"
    assert json.loads(result["receipt"]["request_body"])["qty"] == "1"


@pytest.mark.parametrize("claimant", ["simple", "other-native"])
def test_changed_command_cannot_claim_another_requests_identity(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    claimant: str,
) -> None:
    source, owner, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    if claimant == "simple":
        source.intent("already-owned", at, sent=False)
    else:
        owner.allocate_group(
            "group-b",
            (LotPortion(owner.view().model.executions[0].execution_id, Fraction(1)),),
            at,
        )
        requests.prepare("group-b", "already-owned", at, at)
    before = _rows(source.journal)
    with pytest.raises(JournalError, match="collision"):
        requests.prepare("group-a", "already-owned", at, at)
    assert _rows(source.journal) == before
    assert requests.prepare("group-a", "native-cid-a", at, at) == original


def test_conflict_identity_can_be_reobserved_only_within_its_original_group(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    original = requests.prepare("group-a", "native-cid-a", at, at)
    requests.prepare("group-a", "changed-cid", at, at)
    changed = requests.prepare(
        "group-a", "changed-cid", at + timedelta(seconds=1), at + timedelta(seconds=1)
    )
    assert changed["issues"]
    assert changed["receipt"] == original["receipt"]
    before = _rows(source.journal)
    assert (
        requests.prepare(
            "group-a",
            "changed-cid",
            at + timedelta(seconds=1),
            at + timedelta(seconds=2),
        )
        == changed
    )
    assert _rows(source.journal) == before
