"""Immutable synthetic source integration, not real account or broker acceptance."""

import json
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop import CrossedStopJournal, DeclaredExitEvidence
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from alpaca_ross_bot.risk_baseline import session_interval_from_evidence
from alpaca_ross_bot.risk_evidence_request import IncidentRiskRequest
from test_account_risk_owner import SourceAccount
from test_native_oco_requests import prepared as prepared
from test_risk_ledger_audit import POLICY, REF


def cross(source: SourceAccount, at: datetime, *, bid: str = "9.49") -> datetime:
    risk = AccountRiskOwner(source.journal, REF, POLICY)
    later = at + timedelta(seconds=1)
    risk.observe(
        "crossing-risk", source.selection(later, cash="480", position=2, bid=bid), later
    )
    return later


def declared(at: datetime) -> DeclaredExitEvidence:
    from test_observation_candidate_binding import CID

    return DeclaredExitEvidence(
        REF,
        "AAPL",
        CID,
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
        Fraction(2),
        Fraction(1),
    )


def test_raw_quote_slot_calendar_and_positive_origin_bind_without_truth_flags(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    before_risk = source.journal.read_account_risk_events()
    before_intents = source.journal.read_order_intent_ids()
    before_oco = owner.view()
    emergency = CrossedStopJournal(owner)
    state = emergency.observe_source("source-crossing", later)
    assert state["latched"]
    receipt = state["source_observations"][0]
    assert receipt["reported_bid"] == "949/100"
    assert receipt["component_qualified"] and receipt["crossing_observed"]
    assert receipt["stop"] == "19/2"
    assert before_oco.entry_binding is not None
    assert (
        receipt["entry_reservation_ref"] == before_oco.entry_binding["reservation_ref"]
    )
    assert receipt["calendar_ref"] in receipt["source_refs"]
    assert receipt["quote_ref"][:2] in receipt["source_refs"]
    assert (
        receipt["source_horizon"]
        < source.journal.read_crossed_stop_events()[-1][0].sequence
    )
    assert not receipt["order_authority"] and not receipt["source_admission_accepted"]
    assert not state["authority"]["runtime_authorized"]
    assert source.journal.read_account_risk_events() == before_risk
    assert source.journal.read_order_intent_ids() == before_intents
    assert owner.view() == before_oco
    with pytest.raises(JournalError, match="blocks new OCO"):
        owner.allocate_group(
            "forbidden",
            (LotPortion(before_oco.model.executions[0].execution_id, Fraction(1)),),
            later,
        )
    with pytest.raises(JournalError, match="declared exit"):
        emergency.prepare("not-cleared", declared(later))


def test_source_retry_rebuild_uses_original_not_new_quote(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    emergency = CrossedStopJournal(owner)
    original = emergency.observe_source("cross", later)
    rows = source.journal.read_crossed_stop_events()
    next_at = later + timedelta(seconds=1)
    AccountRiskOwner(source.journal, REF, POLICY).observe(
        "rebound", source.selection(next_at, cash="480", position=2, bid="10"), next_at
    )
    assert emergency.observe_source("cross", later) == original
    assert source.journal.read_crossed_stop_events() == rows
    rebound = emergency.observe_source("rebound", next_at)
    assert rebound["latched"]
    assert not rebound["source_observations"][-1]["crossing_observed"]
    assert rebound["source_observations"][0] == original["source_observations"][0]
    assert CrossedStopJournal(owner).view() == rebound


def test_changed_source_retry_preserves_old_receipt_and_deduplicates_conflict(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    emergency = CrossedStopJournal(owner)
    original = emergency.observe_source("cross", later)
    conflict = emergency.observe_source("cross", later + timedelta(seconds=1))
    assert conflict["conflicts"]
    assert conflict["source_observations"] == original["source_observations"]
    rows = source.journal.read_crossed_stop_events()
    assert emergency.observe_source("cross", later + timedelta(seconds=1)) == conflict
    assert source.journal.read_crossed_stop_events() == rows


def test_declared_and_source_modes_cannot_mix_either_direction(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    emergency = CrossedStopJournal(owner)
    emergency.observe("manual", declared(later))
    with pytest.raises(JournalError, match="cannot mix"):
        emergency.observe_source("source", later)


def test_stale_current_source_cannot_be_redeclared_fresh(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    emergency = CrossedStopJournal(owner)
    with pytest.raises(JournalError, match="stale"):
        emergency.observe_source("stale", later + timedelta(minutes=1))
    assert not source.journal.read_crossed_stop_events()
    assert not emergency.view()["latched"]


def test_calendar_interval_matches_official_early_close_instead_of_fixed_close(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, _, at = prepared
    later = cross(source, at)
    risk = AccountRiskOwner(source.journal, REF, POLICY).view()
    assert risk is not None
    sequence = risk["last_snapshot"]["selection"]["calendar_sequence"]
    stored = source.journal.read_event(sequence)
    assert stored is not None
    raw: dict[str, Any] = json.loads(stored[1])
    rows = json.loads(raw["request"]["body"])
    rows[0]["close"] = "13:00"
    raw["request"]["body"] = json.dumps(rows)
    event = source.journal.append_next(
        source_id=stored[0].source_id,
        feed="paper",
        event_kind=stored[0].event_kind,
        payload=json.dumps(raw).encode(),
        system_first_seen_at=later,
        processed_at=later,
    )
    opened, closed = session_interval_from_evidence(
        source.journal, event.sequence, REF, later.date(), later, timedelta(seconds=30)
    )
    assert opened.hour == 13 and opened.minute == 30
    assert closed.hour == 17 and closed.minute == 0


def test_nonvisible_risk_suffix_is_not_silently_replaced_with_older_snapshot(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    future = later + timedelta(microseconds=1)
    AccountRiskOwner(source.journal, REF, POLICY).observe(
        "future-risk",
        source.selection(future, cash="480", position=2, bid="10"),
        future,
    )
    emergency = CrossedStopJournal(owner)
    with pytest.raises(JournalError, match="risk prefix"):
        emergency.observe_source("cannot-skip", later)
    assert not source.journal.read_crossed_stop_events()


def test_daily_stop_does_not_hide_observed_crossing_or_grant_exit_clearance(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at, bid="1")
    state = CrossedStopJournal(owner).observe_source("loss-incident", later)
    assert state["latched"], state["source_observations"][-1]
    receipt = state["source_observations"][0]
    assert receipt["daily_stop_latched_observed"] is True
    assert receipt["component_qualified"]
    assert state["authority"]["runtime_authorized"] is False
    with pytest.raises(JournalError, match="declared exit"):
        CrossedStopJournal(owner).mark_attempt("none", "none", declared(later))


def test_equal_total_but_replaced_entry_execution_ids_cannot_reuse_old_binding(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    source.fills[0]["id"] = "replaced-entry-identity"
    later = cross(source, at)
    with pytest.raises(JournalError, match="executions changed"):
        CrossedStopJournal(owner).observe_source("stale-lot-binding", later)
    assert not source.journal.read_crossed_stop_events()


def test_source_trigger_recovers_after_physical_journal_reopen(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    from alpaca_ross_bot.journal import Journal
    from test_oco_entry_evidence import _initial

    source, owner, _, at = prepared
    later = cross(source, at)
    original = CrossedStopJournal(owner).observe_source("cross", later)
    path, root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        emergency = CrossedStopJournal(OcoJournal(reopened, _initial()))
        before = reopened.read_crossed_stop_events()
        assert emergency.view() == original
        assert emergency.observe_source("cross", later) == original
        assert reopened.read_crossed_stop_events() == before
    finally:
        reopened.close()


def test_recomputed_outer_hash_cannot_relabel_derived_bid(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    import hashlib

    source, owner, _, at = prepared
    later = cross(source, at)
    emergency = CrossedStopJournal(owner)
    emergency.observe_source("cross", later)
    event, payload = source.journal.read_crossed_stop_events()[0]
    item = json.loads(payload)
    item["data"]["receipt"]["reported_bid"] = "1"
    changed = json.dumps(
        item, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    source.journal._db.execute("DROP TRIGGER events_no_update")
    source.journal._db.execute(
        "UPDATE events SET payload=?,payload_ref=? WHERE sequence=?",
        (changed, "sha256:" + hashlib.sha256(changed).hexdigest(), event.sequence),
    )
    source.journal._db.commit()
    with pytest.raises(JournalError, match="differs from original raw prefix"):
        emergency.view()


def test_identity_veto_is_retained_while_observing_original_fresh_crossing(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    risk = AccountRiskOwner(source.journal, REF, POLICY)
    next_at = later + timedelta(microseconds=1)
    current = risk.view()
    assert current is not None
    original_selection = IncidentRiskRequest.from_dict(
        current["last_snapshot"]["selection"]
    )
    risk.observe(
        "crossing-risk",
        original_selection,
        next_at,
    )
    state = CrossedStopJournal(owner).observe_source("vetoed-source", next_at)
    assert state["latched"]
    assert state["source_observations"][0]["risk_veto_observed"] is True
    assert state["authority"]["order_authority"] is False
    current = risk.view()
    assert current is not None and current["veto"] is True


def test_newer_local_quote_prevents_reusing_old_crossing_even_under_risk_veto(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, _, at = prepared
    later = cross(source, at)
    next_at = later + timedelta(microseconds=1)
    AccountRiskOwner(source.journal, REF, POLICY).observe(
        "crossing-risk",
        source.selection(next_at, cash="480", position=2, bid="10"),
        next_at,
    )
    state = CrossedStopJournal(owner).observe_source("superseded-quote", next_at)
    assert not state["latched"]
    receipt = state["source_observations"][0]
    assert not receipt["component_qualified"]
    assert receipt["issues"]
    assert receipt["risk_veto_observed"] is True
