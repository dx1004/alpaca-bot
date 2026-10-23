"""Synthetic private evidence; no actual broker/source/protection acceptance."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from alpaca_ross_bot.crossed_stop import (
    MANIFEST_SHA256,
    CrossedStopJournal,
    DeclaredExitEvidence,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import (
    Execution,
    LotPortion,
    OcoResearchState,
    load_oco_research_contract,
)

ACCOUNT = "sha256:" + "a" * 64


def initial() -> OcoResearchState:
    return OcoResearchState(
        load_oco_research_contract(),
        ACCOUNT,
        "AAA",
        "entry",
        Fraction(10),
        Fraction(19, 2),
    )


def opened(root: Path, *, bind: bool = True) -> tuple[Journal, OcoJournal]:
    journal = Journal.open_private(root / "journal.sqlite3", root)
    if bind:
        journal.record_paper_account_verification(
            ACCOUNT, b"synthetic", datetime.now(UTC)
        )
    return journal, OcoJournal(journal, initial())


def entry(identity: str, quantity: str = "2") -> Execution:
    return Execution(
        identity, ACCOUNT, "AAA", "entry", "ENTRY", Fraction(quantity), Fraction(49, 5)
    )


def seeded(
    tmp_path: Path,
) -> tuple[Journal, OcoJournal, CrossedStopJournal, DeclaredExitEvidence]:
    journal, owner = opened(tmp_path / "private")
    at = datetime.now(UTC) + timedelta(seconds=1)
    owner.observe_execution(entry("old"), at)
    owner.allocate_group("old-unknown", (LotPortion("old", Fraction(2)),), at)
    owner.observe_execution(entry("new"), at)
    owner.reconcile_entry_terminal("terminal", ("new", "old"), at)
    evidence = DeclaredExitEvidence(
        ACCOUNT,
        "AAA",
        "entry",
        at,
        Fraction(949, 100),
        at,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
        Fraction(4),
        Fraction(2),
    )
    return journal, owner, CrossedStopJournal(owner), evidence


def test_pin_and_parent_unchanged() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (
        hashlib.sha256(
            (root / "evidence/crossed-stop-residual-exit-research-v1.json").read_bytes()
        ).hexdigest()
        == MANIFEST_SHA256
    )
    assert hashlib.sha256(
        (root / "evidence/oco-first-execution-research-v1.json").read_bytes()
    ).hexdigest() == (
        "fa6e29ede5a92428308ad19ba794be5e6e3f0b2a3c216818a54f3ffc9a112d80"
    )


def test_exact_residual_excludes_unknown_old_group_and_preserves_original(
    tmp_path: Path,
) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    before = owner.view()
    state = emergency.prepare("close-1", evidence)
    close = state["closes"]["close-1"]
    assert close["portions"] == {"new": "2"}
    assert close["request"]["qty"] == "2"
    assert close["dispatch_compatible"] is False
    assert owner.view() == before
    assert state["authority"]["research_semantics_accepted"]
    assert not any(
        v for k, v in state["authority"].items() if k != "research_semantics_accepted"
    )
    assert not journal.read_order_intent_ids()
    assert not journal.read_order_events()
    assert not journal.read_account_risk_events()
    assert not journal.read_strategy_risk_events()
    with pytest.raises(JournalError, match="blocks new OCO"):
        owner.allocate_group(
            "new-group", (LotPortion("new", Fraction(2)),), evidence.as_of
        )
    # Historical exact retries and acknowledgement preserve the existing stop book.
    assert (
        owner.allocate_group(
            "old-unknown", (LotPortion("old", Fraction(2)),), evidence.as_of
        )
        == before
    )
    assert owner.acknowledge_group("old-unknown", "old-ack", evidence.as_of)
    journal.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("inputs_current", False),
        ("terminal_entry_confirmed", False),
        ("conflicting_buys_absent", False),
        ("ownership_reconciled", False),
        ("regular_session_open", False),
        ("asset_tradable", False),
        ("halted", True),
        ("available_quantity", Fraction(1)),
        ("position_quantity", Fraction(5)),
        ("account_ref", "foreign"),
        ("symbol", "BBB"),
        ("entry_scope", "foreign"),
    ],
)
def test_failed_gate_cannot_persist_close(
    tmp_path: Path, field: str, value: Any
) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.observe("crossed", evidence)
    before = emergency.view()
    with pytest.raises(JournalError):
        emergency.prepare("close", replace(evidence, **{field: value}))
    assert emergency.view() == before
    journal.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote_qualified_fresh", False),
        ("inputs_current", False),
        ("bid", Fraction(951, 100)),
    ],
)
def test_no_invented_crossing(tmp_path: Path, field: str, value: Any) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    assert not emergency.observe("not-crossed", replace(evidence, **{field: value}))[
        "latched"
    ]
    with pytest.raises(JournalError, match="latched"):
        emergency.prepare("close", replace(evidence, **{field: value}))
    journal.close()


def test_sticky_trigger_survives_rebound_and_missing_quote(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    assert emergency.observe("crossed", evidence)["latched"]
    later = replace(
        evidence,
        as_of=evidence.as_of + timedelta(seconds=1),
        bid=None,
        quote_at=None,
        quote_qualified_fresh=False,
    )
    assert emergency.prepare("close", later)["latched"]
    journal.close()


def test_cancel_ack_without_terminal_census_cannot_prepare(tmp_path: Path) -> None:
    journal, owner = opened(tmp_path / "private")
    at = datetime.now(UTC) + timedelta(seconds=1)
    owner.observe_execution(entry("new"), at)
    evidence = DeclaredExitEvidence(
        ACCOUNT,
        "AAA",
        "entry",
        at,
        Fraction(9),
        at,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
        Fraction(2),
        Fraction(2),
    )
    with pytest.raises(JournalError, match="terminal"):
        CrossedStopJournal(owner).prepare("close", evidence)
    journal.close()


def test_restart_partial_exit_unknown_attempt_retains_allocation(
    tmp_path: Path,
) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    emergency.mark_attempt("close", "attempt", evidence)
    state = emergency.observe_exit("close", "exit-1", Fraction(1, 2), evidence.as_of)
    assert state["closes"]["close"]["filled"] == "1/2"
    journal.close()
    reopened, recovered_owner = opened(tmp_path / "private", bind=False)
    recovered = CrossedStopJournal(recovered_owner)
    assert recovered.view() == state
    assert recovered.mark_attempt("close", "attempt", evidence) == state
    assert (
        recovered.observe_exit("close", "exit-1", Fraction(1, 2), evidence.as_of)
        == state
    )
    with pytest.raises(JournalError, match="unresolved"):
        recovered.prepare(
            "another-close", replace(evidence, position_quantity=Fraction(7, 2))
        )
    with pytest.raises(JournalError, match="blocks new OCO"):
        recovered_owner.allocate_group(
            "other", (LotPortion("new", Fraction(1)),), evidence.as_of
        )
    reopened.close()


def test_late_fill_invalidates_prepare_before_attempt(tmp_path: Path) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    owner.observe_execution(entry("late", "1/4"), evidence.as_of)
    with pytest.raises(JournalError, match="terminal"):
        emergency.mark_attempt("close", "attempt", evidence)
    owner.reconcile_entry_terminal(
        "new-terminal", ("late", "new", "old"), evidence.as_of
    )
    with pytest.raises(JournalError, match="census changed"):
        emergency.mark_attempt(
            "close",
            "attempt",
            replace(
                evidence,
                position_quantity=Fraction(17, 4),
                available_quantity=Fraction(9, 4),
            ),
        )
    assert emergency.view()["closes"]["close"]["attempt"] is None
    journal.close()


def test_late_fill_after_completed_exit_gets_disjoint_fractional_residual(
    tmp_path: Path,
) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    emergency.mark_attempt("close", "attempt", evidence)
    emergency.observe_exit("close", "exit", Fraction(2), evidence.as_of)
    owner.observe_execution(entry("late", "1/4"), evidence.as_of)
    owner.reconcile_entry_terminal("again", ("late", "new", "old"), evidence.as_of)
    second = replace(
        evidence, position_quantity=Fraction(9, 4), available_quantity=Fraction(1, 4)
    )
    state = emergency.prepare("late-close", second)
    assert state["closes"]["late-close"]["portions"] == {"late": "1/4"}
    assert state["closes"]["late-close"]["request"]["qty"] == "0.25"
    assert state["latched"]
    journal.close()


@pytest.mark.parametrize("quantity", ["1/3", "1/10000000000"])
def test_unrepresentable_residual_not_rounded(tmp_path: Path, quantity: str) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    owner.observe_execution(entry("fractional", quantity), evidence.as_of)
    owner.reconcile_entry_terminal(
        "again", ("fractional", "new", "old"), evidence.as_of
    )
    bad = replace(
        evidence,
        position_quantity=Fraction(4) + Fraction(quantity),
        available_quantity=Fraction(2) + Fraction(quantity),
    )
    with pytest.raises(JournalError, match="no rounding"):
        emergency.prepare("close", bad)
    assert not emergency.view()["closes"]
    journal.close()


def test_changed_command_is_durable_conflict_not_retry(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    emergency.mark_attempt("close", "first", evidence)
    state = emergency.mark_attempt("close", "changed", evidence)
    assert state["conflicts"]
    assert state["closes"]["close"]["attempt"] == "first"
    journal.close()
    reopened, owner = opened(tmp_path / "private", bind=False)
    assert CrossedStopJournal(owner).view() == state
    reopened.close()


@pytest.mark.parametrize("corruption", ["projection", "dedupe", "raw"])
def test_corruption_blocks_recovery(tmp_path: Path, corruption: str) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    if corruption == "projection":
        journal._db.execute("DELETE FROM state WHERE key=?", (emergency.state_key,))
    elif corruption == "dedupe":
        journal._db.execute(
            "DELETE FROM state WHERE key LIKE 'crossed_stop_evidence:%'"
        )
    else:
        row = journal.read_crossed_stop_events()[0][0]
        journal._db.execute("DROP TRIGGER events_no_update")
        journal._db.execute(
            "UPDATE events SET payload=? WHERE sequence=?", (b"corrupt", row.sequence)
        )
    journal._db.commit()
    with pytest.raises((JournalError, ValueError)):
        emergency.view()
    journal.close()


def test_session_and_availability_rechecked_before_attempt(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    for bad in (
        replace(evidence, regular_session_open=False),
        replace(evidence, available_quantity=Fraction(1)),
        replace(evidence, halted=True),
    ):
        with pytest.raises(JournalError):
            emergency.mark_attempt("close", "attempt", bad)
    assert emergency.view()["closes"]["close"]["attempt"] is None
    journal.close()


def test_single_attempt_race_across_connections(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    journal.close()
    barrier = Barrier(2)

    def worker(attempt: str) -> None:
        j, o = opened(tmp_path / "private", bind=False)
        e = CrossedStopJournal(o)
        barrier.wait(timeout=10)
        e.mark_attempt("close", attempt, evidence)
        j.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, ("a", "b")))
    reopened, owner = opened(tmp_path / "private", bind=False)
    state = CrossedStopJournal(owner).view()
    assert state["closes"]["close"]["attempt"] in {"a", "b"}
    assert len(state["conflicts"]) == 1
    reopened.close()


def test_over_exit_retains_incident_no_budget_release(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    emergency.mark_attempt("close", "attempt", evidence)
    state = emergency.observe_exit("close", "exit", Fraction(3), evidence.as_of)
    assert state["closes"]["close"]["filled"] == "3"
    assert "EMERGENCY_OVER_EXIT" in state["conflicts"]
    assert not journal.read_account_risk_events()
    journal.close()


def test_caller_transaction_is_not_committed(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    journal._db.execute("BEGIN")
    with pytest.raises(JournalError, match="own transaction"):
        emergency.prepare("close", evidence)
    assert journal._db.in_transaction
    journal._db.rollback()
    journal.close()


def test_returned_views_cannot_promote_authority_or_rebind(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    empty = emergency.view()
    empty["authority"]["order_authority"] = True
    empty["binding"]["account_ref"] = "foreign"
    emergency.authority["runtime_authorized"] = True
    emergency.binding["account_ref"] = "foreign"
    state = emergency.observe("crossed", evidence)
    assert state["binding"]["account_ref"] == ACCOUNT
    assert state["authority"]["order_authority"] is False
    assert state["authority"]["runtime_authorized"] is False
    state["authority"]["order_authority"] = True
    assert emergency.view()["authority"]["order_authority"] is False
    journal.close()


def test_identical_conflicting_retries_never_append_again(tmp_path: Path) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    emergency.mark_attempt("close", "first", evidence)
    state = emergency.mark_attempt("close", "changed", evidence)
    rows = journal.read_crossed_stop_events()
    for _ in range(3):
        assert emergency.mark_attempt("close", "changed", evidence) == state
        assert emergency.mark_attempt("close", "first", evidence) == state
    assert journal.read_crossed_stop_events() == rows
    journal.close()


def test_parent_later_reuses_emergency_execution_id_fails_closed(
    tmp_path: Path,
) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    emergency.mark_attempt("close", "attempt", evidence)
    emergency.observe_exit("close", "exit", Fraction(2), evidence.as_of)
    owner.observe_execution(entry("exit"), evidence.as_of)
    owner.reconcile_entry_terminal("again", ("exit", "new", "old"), evidence.as_of)
    parent_rows = journal.read_oco_research_events()
    emergency_rows = journal.read_crossed_stop_events()
    with pytest.raises(JournalError, match="overlaps"):
        emergency.view()
    with pytest.raises(JournalError, match="overlaps"):
        emergency.prepare("unsafe", evidence)
    assert journal.read_oco_research_events() == parent_rows
    assert journal.read_crossed_stop_events() == emergency_rows
    journal.close()


def test_existing_parent_execution_id_cannot_be_reused_for_close(
    tmp_path: Path,
) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    emergency.prepare("close", evidence)
    emergency.mark_attempt("close", "attempt", evidence)
    with pytest.raises(JournalError, match="already assigned"):
        emergency.observe_exit("close", "new", Fraction(1), evidence.as_of)
    journal.close()


def test_append_failure_rolls_back_attempt_and_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    before = emergency.prepare("close", evidence)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(journal, "_append_locked", fail)
        with pytest.raises(OSError, match="synthetic storage"):
            emergency.mark_attempt("close", "attempt", evidence)
    assert emergency.view() == before
    assert (
        emergency.mark_attempt("close", "attempt", evidence)["closes"]["close"][
            "attempt"
        ]
        == "attempt"
    )
    journal.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"quote_at": "future"},
        {"quote_at": None},
        {"bid": None},
        {"position_quantity": 4},
        {"available_quantity": Fraction(5)},
        {"halted": 0},
        {"bid": Fraction(0)},
    ],
)
def test_malformed_declarations_cannot_create_intent(
    tmp_path: Path,
    bad: dict[str, Any],
) -> None:
    journal, _, emergency, evidence = seeded(tmp_path)
    if bad == {"quote_at": "future"}:
        bad = {"quote_at": evidence.as_of + timedelta(seconds=1)}
    with pytest.raises(JournalError):
        emergency.prepare("close", replace(evidence, **bad))
    assert not emergency.view()["closes"]
    journal.close()


def test_parent_group_exits_and_new_close_account_for_distinct_lots(
    tmp_path: Path,
) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    owner.observe_execution(
        Execution(
            "old-exit",
            ACCOUNT,
            "AAA",
            "entry",
            "STOP",
            Fraction(1),
            Fraction(9),
            "old-unknown",
        ),
        evidence.as_of,
    )
    state = emergency.prepare("close", replace(evidence, position_quantity=Fraction(3)))
    assert state["closes"]["close"]["portions"] == {"new": "2"}
    assert owner.view().model.group_residual("old-unknown") == 1
    journal.close()


def test_two_preparations_serialize_one_exact_reservation(tmp_path: Path) -> None:
    journal, _, _, evidence = seeded(tmp_path)
    journal.close()
    barrier = Barrier(2)

    def worker(identity: str) -> str:
        j, o = opened(tmp_path / "private", bind=False)
        e = CrossedStopJournal(o)
        barrier.wait(timeout=10)
        try:
            e.prepare(identity, evidence)
            return "prepared"
        except JournalError:
            return "blocked"
        finally:
            j.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ("a", "b")))
    assert sorted(results) == ["blocked", "prepared"]
    reopened, owner = opened(tmp_path / "private", bind=False)
    assert len(CrossedStopJournal(owner).view()["closes"]) == 1
    reopened.close()


def test_fill_average_below_frozen_stop_can_prepare_disposal_without_target(
    tmp_path: Path,
) -> None:
    journal, owner = opened(tmp_path / "private")
    at = datetime.now(UTC) + timedelta(seconds=1)
    below = replace(entry("below"), price=Fraction(47, 5))
    owner.observe_execution(below, at)
    owner.reconcile_entry_terminal("terminal", ("below",), at)
    evidence = DeclaredExitEvidence(
        ACCOUNT,
        "AAA",
        "entry",
        at,
        Fraction(939, 100),
        at,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
        Fraction(2),
        Fraction(2),
    )
    emergency = CrossedStopJournal(owner)
    state = emergency.prepare("close", evidence)
    assert state["closes"]["close"]["request"]["qty"] == "2"
    assert "FINAL_TARGET_TOPOLOGY_INVALID" in owner.view().model.incidents
    assert owner.view().model.stop == Fraction(19, 2)
    assert owner.view().model.final_target is None
    assert not journal.read_account_risk_events()
    journal.close()


def test_parent_reverse_inventory_still_blocks_emergency_preparation(
    tmp_path: Path,
) -> None:
    journal, owner, emergency, evidence = seeded(tmp_path)
    owner.observe_execution(
        Execution(
            "old-over-exit",
            ACCOUNT,
            "AAA",
            "entry",
            "STOP",
            Fraction(3),
            Fraction(9),
            "old-unknown",
        ),
        evidence.as_of,
    )
    emergency.observe("crossed", evidence)
    with pytest.raises(JournalError, match="clean latched"):
        emergency.prepare(
            "close",
            replace(
                evidence, position_quantity=Fraction(1), available_quantity=Fraction(1)
            ),
        )
    assert "GROUP_OVER_EXIT" in owner.view().model.incidents
    journal.close()
