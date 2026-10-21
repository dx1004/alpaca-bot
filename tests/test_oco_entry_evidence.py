"""Source-shaped entry-only bridge; engineering records are never formal samples."""

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from unittest.mock import Mock

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.entry_strategy import EntryDecision
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.incident_risk import IncidentRiskRequest
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import (
    Execution,
    LotPortion,
    OcoResearchState,
    load_oco_research_contract,
)
from test_account_risk_owner import SourceAccount, account_sources
from test_observation_candidate_binding import AT, CID, contract, new_decision
from test_risk_ledger_audit import POLICY, REF


def _initial(**changes: object) -> OcoResearchState:
    state = OcoResearchState(
        load_oco_research_contract(),
        REF,
        "AAPL",
        CID,
        Fraction(1001, 100),
        Fraction(19, 2),
    )
    return replace(state, **changes)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def candidate() -> EntryDecision:
    return new_decision()


def _prepare(
    source: SourceAccount,
    risk: AccountRiskOwner,
    candidate: EntryDecision,
    *,
    oco: bool = True,
    split_fills: bool = False,
) -> datetime:
    quantity = risk.reserve_research_decision(
        "reservation",
        source.selection(AT),
        AT,
        client_order_id=CID,
        decision=candidate,
        observation_contract=contract(),
        oco_contract=load_oco_research_contract() if oco else None,
    )["quantity"]
    assert quantity == 4
    at = AT + timedelta(seconds=1)
    source.intent(CID, at, qty=4, price="10.01")
    source.order(CID, at, "partially_filled", 2)
    if split_fills:
        source.fill(CID, at, 1, "10")
        source.fills[-1]["cum_qty"] = "1"
        source.fill(CID, at, 1, "10")
        source.fills[-1]["cum_qty"] = "2"
    else:
        source.fill(CID, at, 2, "10")
    risk.observe("partial-source", source.selection(at, cash="480", position=2), at)
    current = risk.view()
    assert current is not None and current["issues"] == []
    return at


@pytest.fixture
def prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: EntryDecision
) -> Iterator[tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime]]:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        at = _prepare(source, risk, candidate)
        yield source, risk, OcoJournal(source.journal, _initial()), at


def test_partial_then_terminal_census_binds_original_parent_without_new_budget(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, risk, owner, at = prepared
    risk_before = json.dumps(risk.view(), sort_keys=True)
    risk_rows = source.journal.read_account_risk_events()
    intents = source.journal.read_order_intent_ids()
    partial = owner.bind_entry_census("partial", at, POLICY, at)
    assert partial.model.entry_quantity == 2
    assert not partial.model.entry_terminal_reconciled
    receipt = partial.entry_binding
    assert receipt is not None
    assert receipt["entry"]["filled_quantity"] == "2"
    assert receipt["entry"]["reserved_quantity"] == 4
    assert receipt["intent_origin"]["purpose"] == "ENGINEERING_TEST"
    assert receipt["formal_sample_eligible"] is False
    assert receipt["reservation"]["plan"]["client_order_id"] == CID
    assert (
        receipt["reservation"]["research_signal"]["version"]
        == "HYBRID_500_IEX_OBSERVATION_AGE_RESEARCH_V1"
    )
    assert receipt["reported_entry_census_bound"] is True
    assert not any(
        value
        for key, value in receipt["authority"].items()
        if key != "research_semantics_accepted"
    )
    assert json.dumps(risk.view(), sort_keys=True) == risk_before
    assert source.journal.read_account_risk_events() == risk_rows
    assert source.journal.read_order_intent_ids() == intents
    terminal_at = at + timedelta(seconds=1)
    source.order(CID, terminal_at, "canceled", 2)
    risk.observe(
        "terminal-source",
        source.selection(terminal_at, cash="480", position=2),
        terminal_at,
    )
    terminal = owner.bind_entry_census("terminal", terminal_at, POLICY, terminal_at)
    assert terminal.model.entry_terminal_reconciled
    assert terminal.model.final_target == Fraction(11)
    assert owner.verify_rebuild()


def test_retry_keeps_original_horizon_and_receipt_after_new_source_observations(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, risk, owner, at = prepared
    original = owner.bind_entry_census("same", at, POLICY, at)
    rows = source.journal.read_oco_research_events()
    later = at + timedelta(seconds=1)
    source.order(CID, later, "canceled", 2)
    risk.observe("later-source", source.selection(later, cash="480", position=2), later)
    assert (
        owner.bind_entry_census("same", at, POLICY, later + timedelta(days=1))
        == original
    )
    assert owner.view() == original
    assert source.journal.read_oco_research_events() == rows
    raw_receipt = json.loads(rows[-1][1])["operation"]
    assert raw_receipt["source_horizon"] < rows[-1][0].sequence


def test_changed_request_same_id_preserves_conflict_not_new_derivation(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, _, owner, at = prepared
    original = owner.bind_entry_census("same", at, POLICY, at)
    conflict = owner.bind_entry_census("same", at + timedelta(days=1), POLICY, at)
    assert conflict.model == original.model
    assert conflict.entry_binding == original.entry_binding
    assert "OPERATION_IDENTITY_CONFLICT" in conflict.issues
    count = len(source.journal.read_oco_research_events())
    assert (
        owner.bind_entry_census("same", at + timedelta(days=1), POLICY, at) == conflict
    )
    assert len(source.journal.read_oco_research_events()) == count
    with pytest.raises(JournalError, match="conflict blocks"):
        owner.allocate_group("new", (LotPortion("execution-0", Fraction(2)),), at)


@pytest.mark.parametrize("fault", ["scope", "limit", "stop", "stale", "future", "veto"])
def test_bad_identity_or_current_risk_inputs_append_no_derived_facts(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
    fault: str,
) -> None:
    source, risk, owner, at = prepared
    if fault == "scope":
        owner = OcoJournal(source.journal, _initial(entry_scope="wrong-scope"))
    elif fault == "limit":
        # Use an otherwise empty separate scope with a wrong config for the held CID.
        source.journal._db.execute("SELECT 1")
        owner = OcoJournal(source.journal, _initial())
        # Derivation itself receives the incompatible initial state; no cache rewrite.
        from alpaca_ross_bot.oco_entry_evidence import derive_entry_census

        with pytest.raises((JournalError, ValueError)):
            derive_entry_census(
                source.journal,
                _initial(limit=Fraction(11)),
                decision_at=at,
                through_sequence=source.journal.read_evidence_sequence(),
                fee_policy=POLICY,
            )
        return
    elif fault == "stop":
        from alpaca_ross_bot.oco_entry_evidence import derive_entry_census

        with pytest.raises((JournalError, ValueError)):
            derive_entry_census(
                source.journal,
                _initial(stop=Fraction(9)),
                decision_at=at,
                through_sequence=source.journal.read_evidence_sequence(),
                fee_policy=POLICY,
            )
        return
    elif fault == "stale":
        at += timedelta(seconds=31)
    elif fault == "future":
        at -= timedelta(microseconds=1)
    else:
        at += timedelta(seconds=1)
        risk.observe(
            "daily-stop", source.selection(at, cash="480", position=2, bid="7"), at
        )
        stopped = risk.view()
        assert stopped is not None and stopped["daily_stop_latched"] is True
    rows = source.journal.read_oco_research_events()
    with pytest.raises((JournalError, ValueError)):
        owner.bind_entry_census("bad", at, POLICY, at)
    assert source.journal.read_oco_research_events() == rows
    assert owner.view().model.entry_quantity == 0


def test_legacy_risk_without_oco_binding_cannot_be_upgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: EntryDecision
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        at = _prepare(source, risk, candidate, oco=False)
        owner = OcoJournal(source.journal, _initial())
        rows = source.journal.read_oco_research_events()
        with pytest.raises(JournalError, match="binding differs"):
            owner.bind_entry_census("legacy", at, POLICY, at)
        assert source.journal.read_oco_research_events() == rows


def test_cumulative_qty_is_ignored_and_per_execution_qty_is_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate: EntryDecision,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        at = _prepare(source, risk, candidate, split_fills=True)
        owner = OcoJournal(source.journal, _initial())
        result = owner.bind_entry_census("cum", at, POLICY, at)
        assert result.model.entry_quantity == 2
        receipt = result.entry_binding
        assert receipt is not None
        assert [row["quantity"] for row in receipt["entry"]["executions"]] == ["1", "1"]


@pytest.mark.parametrize("fault", ["changed-price", "shrinking-census"])
def test_existing_model_entries_must_be_unchanged_subset(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
    fault: str,
) -> None:
    source, _, owner, at = prepared
    declared = Execution(
        "execution-0" if fault == "changed-price" else "not-in-source",
        REF,
        "AAPL",
        CID,
        "ENTRY",
        Fraction(2),
        Fraction(999, 100),
    )
    original = owner.observe_execution(declared, at)
    rows = source.journal.read_oco_research_events()
    with pytest.raises(JournalError, match="shrank or changed"):
        owner.bind_entry_census("bad-subset", at, POLICY, at)
    assert owner.view() == original
    assert source.journal.read_oco_research_events() == rows


def test_fractional_individual_source_fills_remain_explicitly_unsupported(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, risk, owner, at = prepared
    original = source.fills[0]
    source.fills[:] = [
        original | {"qty": "0.5"},
        original | {"id": "second-half", "qty": "1.5"},
    ]
    at += timedelta(seconds=1)
    risk.observe("fractional-source", source.selection(at, cash="480", position=2), at)
    rows = source.journal.read_oco_research_events()
    with pytest.raises(JournalError, match="fractional entry source seam"):
        owner.bind_entry_census("fractional", at, POLICY, at)
    assert source.journal.read_oco_research_events() == rows
    assert owner.view().model.entry_quantity == 0


@pytest.mark.parametrize("fault", ["append", "commit"])
def test_derived_receipt_and_model_roll_back_together(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    source, _, owner, at = prepared
    journal = source.journal
    original = owner.view()
    rows = journal.read_oco_research_events()
    append, connection = journal._append_locked, journal._db

    def fail(
        event: EventEnvelope, payload: bytes, updates: Mapping[str, object] | None
    ) -> None:
        append(event, payload, updates)
        raise OSError("synthetic bound-entry persistence failure")

    with monkeypatch.context() as patch:
        if fault == "append":
            patch.setattr(journal, "_append_locked", fail)
        else:
            broken = Mock(wraps=connection)
            broken.commit.side_effect = OSError(
                "synthetic bound-entry persistence failure"
            )
            patch.setattr(journal, "_db", broken)
        with pytest.raises(OSError, match="bound-entry persistence"):
            owner.bind_entry_census("rollback", at, POLICY, at)
    assert owner.view() == original
    assert journal.read_oco_research_events() == rows
    assert owner.bind_entry_census("rollback", at, POLICY, at).model.entry_quantity == 2


def test_physical_reopen_rederives_original_receipt(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
    tmp_path: Path,
) -> None:
    source, _, owner, at = prepared
    original = owner.bind_entry_census("restart", at, POLICY, at)
    source.journal.close()
    root = tmp_path / "private"
    reopened = Journal.open_private(root / "journal.sqlite3", root)
    try:
        recovered = OcoJournal(reopened, _initial())
        assert recovered.view() == original
        assert recovered.verify_rebuild()
    finally:
        reopened.close()


def test_source_only_decoder_keys_cannot_be_silently_ignored(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, _, owner, at = prepared
    owner.bind_entry_census("bound", at, POLICY, at)
    event, payload = source.journal.read_oco_research_events()[-1]
    item = json.loads(payload)
    item["operation"]["source_qualified"] = True
    changed = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    source.journal._db.execute("DROP TRIGGER events_no_update")
    source.journal._db.execute(
        "UPDATE events SET payload=?,payload_ref=? WHERE sequence=?",
        (changed, "sha256:" + hashlib.sha256(changed).hexdigest(), event.sequence),
    )
    source.journal._db.commit()
    with pytest.raises(JournalError, match="schema"):
        owner.view()


def test_reservation_origin_is_positive_transition_not_matching_zero_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate: EntryDecision,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):

        def reserve(
            event_id: str, stamp: datetime, power: str | None = None
        ) -> dict[str, object]:
            return risk.reserve_research_decision(
                event_id,
                source.selection(stamp, power=power),
                stamp,
                client_order_id=CID,
                decision=candidate,
                observation_contract=contract(),
                oco_contract=load_oco_research_contract(),
            )

        zero = reserve("zero", AT, "0")
        assert zero["quantity"] == 0
        success_at = AT + timedelta(milliseconds=100)
        positive = reserve("positive", success_at)
        assert positive["quantity"] == 4
        positive_event = source.journal.read_account_risk_events()[-1][0]
        occupied_at = AT + timedelta(milliseconds=200)
        occupied = reserve("occupied", occupied_at)
        assert occupied["quantity"] == 0 and occupied["reason"] == "SLOT_OCCUPIED"
        filled_at = AT + timedelta(milliseconds=300)
        source.intent(CID, filled_at, qty=4, price="10.01")
        source.order(CID, filled_at, "partially_filled", 2)
        source.fill(CID, filled_at, 2, "10")
        risk.observe(
            "actual-fill",
            source.selection(filled_at, cash="480", position=2),
            filled_at,
        )
        receipt = (
            OcoJournal(source.journal, _initial())
            .bind_entry_census(
                "origin",
                filled_at,
                POLICY,
                filled_at,
            )
            .entry_binding
        )
        assert receipt is not None
        assert receipt["reservation"]["event_id"] == "positive"
        assert receipt["reservation_ref"] == [
            positive_event.sequence,
            positive_event.payload_ref,
        ]


def _early_engineering_exit(
    source: SourceAccount,
    risk: AccountRiskOwner,
    owner: OcoJournal,
    at: datetime,
    *,
    terminal: bool,
) -> datetime:
    owner.bind_entry_census("before-early-exit", at, POLICY, at)
    owner.allocate_group("group", (LotPortion("execution-0", Fraction(2)),), at)
    at += timedelta(seconds=1)
    if terminal:
        source.order(CID, at, "canceled", 2)
    source.intent("engineering-exit", at, side="sell", qty=2, price="11.10")
    risk.bind_exit("bind-engineering-exit", at, "engineering-exit", "TARGET")
    source.order("engineering-exit", at, "filled", 2)
    source.fill("engineering-exit", at, 2, "11.10")
    risk.observe(
        "early-exit-source", source.selection(at, cash="502.20", position=0), at
    )
    owner.observe_execution(
        Execution(
            "declared-exit",
            REF,
            "AAPL",
            CID,
            "TARGET",
            Fraction(2),
            Fraction(111, 10),
            "group",
        ),
        at,
    )
    return at


def test_pending_entry_early_exit_then_late_fill_uses_lifetime_entry_average(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, risk, owner, at = prepared
    at = _early_engineering_exit(source, risk, owner, at, terminal=False)
    pending = risk.view()
    assert pending is not None and pending["held"] is not None
    at += timedelta(seconds=1)
    source.order(CID, at, "canceled", 3)
    source.fill(CID, at, 1, "9.90")
    risk.observe(
        "late-entry-source", source.selection(at, cash="492.30", position=1), at
    )
    bound = owner.bind_entry_census("late-entry", at, POLICY, at)
    assert bound.model.entry_quantity == 3
    assert bound.model.exit_quantity == 2
    assert bound.model.average_entry == Fraction(299, 30)
    assert bound.model.final_target == Fraction(109, 10)
    assert bound.model.owned_quantity == 1
    assert bound.entry_binding is not None
    assert bound.entry_binding["formal_sample_eligible"] is False
    assert bound.model.authority["source_adapter_qualified"] is False
    assert bound.model.authority["protection_coverage_verified"] is False
    assert owner.verify_rebuild()


def test_completed_flat_slot_is_not_resurrected_for_discovered_original_entry_fill(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, risk, owner, at = prepared
    at = _early_engineering_exit(source, risk, owner, at, terminal=True)
    completed = risk.view()
    assert completed is not None and completed["held"] is None
    assert CID in completed["completed"]
    at += timedelta(seconds=1)
    source.order(CID, at, "canceled", 3)
    source.fill(CID, at, 1, "9.90")
    risk.observe(
        "late-after-completion", source.selection(at, cash="492.30", position=1), at
    )
    observed = risk.view()
    assert observed is not None and observed["held"] is None and observed["issues"]
    retained = source.journal.read_account_risk_events()
    original = owner.view()
    rows = source.journal.read_oco_research_events()
    with pytest.raises(JournalError, match="held source risk"):
        owner.bind_entry_census("unsupported-recovery", at, POLICY, at)
    assert owner.view() == original
    assert source.journal.read_account_risk_events() == retained
    assert source.journal.read_oco_research_events() == rows
    final_risk = risk.view()
    assert final_risk is not None and final_risk["held"] is None


@pytest.mark.parametrize("fault", ["source-raw", "derived-receipt", "binding-cache"])
def test_source_receipt_and_binding_cache_tampering_cannot_be_replayed(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
    fault: str,
) -> None:
    source, _, owner, at = prepared
    bound = owner.bind_entry_census("tamper", at, POLICY, at)
    assert bound.entry_binding is not None
    if fault == "binding-cache":
        cached = source.journal.read_state(owner.state_key)
        assert isinstance(cached, dict)
        cached["entry_binding"]["formal_sample_eligible"] = True
        source.journal._db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (json.dumps(cached), owner.state_key),
        )
    else:
        source.journal._db.execute("DROP TRIGGER events_no_update")
        if fault == "source-raw":
            sequence = bound.entry_binding["capture_ref"][0]
            source.journal._db.execute(
                "UPDATE events SET payload=? WHERE sequence=?", (b"{}", sequence)
            )
        else:
            event, payload = source.journal.read_oco_research_events()[-1]
            changed = json.loads(payload)
            changed["operation"]["receipt"]["entry"]["filled_quantity"] = "200"
            raw = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
            source.journal._db.execute(
                "UPDATE events SET payload=?,payload_ref=? WHERE sequence=?",
                (raw, "sha256:" + hashlib.sha256(raw).hexdigest(), event.sequence),
            )
    source.journal._db.commit()
    with pytest.raises((JournalError, ValueError)):
        owner.view()
    with pytest.raises((JournalError, ValueError)):
        OcoJournal(source.journal, _initial())


def test_missing_source_risk_state_cannot_create_entry_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        assert risk.view() is None
        owner = OcoJournal(source.journal, _initial())
        original = owner.view()
        rows = source.journal.read_oco_research_events()
        with pytest.raises(JournalError, match="source risk prefix missing"):
            owner.bind_entry_census("missing", AT, POLICY, AT)
        assert owner.view() == original
        assert source.journal.read_oco_research_events() == rows
        assert source.journal.read_order_intent_ids() == ()


def test_native_group_exit_source_remains_unsupported_and_preserves_raw_history(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, risk, owner, at = prepared
    held = risk.view()
    assert held is not None
    selection = IncidentRiskRequest.from_dict(held["last_snapshot"]["selection"])
    at += timedelta(seconds=1)
    source.intent("native-exit", at, side="sell", qty=2, price="11.03")
    risk.bind_exit("declare-native-role", at, "native-exit", "TARGET")
    native = source.order("native-exit", at, "new", 0)
    native["order_class"] = "oco"
    with pytest.raises(AssertionError):
        source.selection(at, cash="480", position=2)
    risk.observe("unsupported-native-source", selection, at)
    vetoed = risk.view()
    assert vetoed is not None and vetoed["last_snapshot"]["qualified"] is False
    retained_source = source.journal.read_order_events()
    rows = source.journal.read_oco_research_events()
    with pytest.raises(JournalError, match="held source risk"):
        owner.bind_entry_census("native-unqualified", at, POLICY, at)
    assert source.journal.read_order_events() == retained_source
    assert source.journal.read_oco_research_events() == rows
    assert owner.view().model.entry_quantity == 0


def test_new_backdated_bound_entry_is_refused_without_partial_commit(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, _, owner, at = prepared
    original = owner.view()
    rows = source.journal.read_oco_research_events()
    source_rows = source.journal.read_account_risk_events()
    with pytest.raises(JournalError, match="receive time predates"):
        owner.bind_entry_census("backdated", at, POLICY, at - timedelta(seconds=1))
    assert owner.view() == original
    assert source.journal.read_oco_research_events() == rows
    assert source.journal.read_account_risk_events() == source_rows


def test_original_event_metadata_backdated_before_receipt_is_not_replayable(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> None:
    source, _, owner, at = prepared
    owner.bind_entry_census("causal", at, POLICY, at)
    event, _ = source.journal.read_oco_research_events()[-1]
    before = (at - timedelta(seconds=1)).isoformat()
    source.journal._db.execute("DROP TRIGGER events_no_update")
    source.journal._db.execute(
        "UPDATE events SET system_first_seen_at=?,processed_at=?,available_at=? "
        "WHERE sequence=?",
        (before, before, before, event.sequence),
    )
    source.journal._db.commit()
    with pytest.raises(JournalError, match="event predates"):
        owner.view()
    with pytest.raises(JournalError, match="event predates"):
        OcoJournal(source.journal, _initial())
