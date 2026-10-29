"""Native residual preparation is local, disjoint and never stop clearance."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop import CrossedStopJournal
from alpaca_ross_bot.crossed_stop_contract import load_crossed_stop_contract
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from alpaca_ross_bot.risk_account_inputs import KIND, SOURCE
from test_crossed_stop_preparation import asset, status
from test_native_account_risk_owner import full_book
from test_native_account_risk_owner import native_risk_book as native_risk_book
from test_native_owned_entry import late_entry, native_partial
from test_observation_candidate_binding import CID
from test_oco_exit_evidence import capture
from test_risk_ledger_audit import POLICY
from test_risk_marks import QUOTE, put


def available_book(
    book: Any, at: datetime, available: str, *, bid: str = "9.49", cash: str = "481.63"
) -> Any:
    source, _, _, _, reported = book
    selection = full_book(source, reported, at, cash=cash, position=2)
    event, raw = source.journal.read_event(selection.capture_sequence)
    envelope = json.loads(raw)
    for row in envelope["requests"]:
        if row["path"] == "/v2/positions":
            positions = json.loads(row["body"])
            positions[0]["qty_available"] = available
            row["body"] = json.dumps(positions)
    sequence = source.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(envelope).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence
    mark = selection.mark_refs[0]
    quote = put(
        source.journal,
        [QUOTE | {"bp": bid, "ap": bid, "t": at.isoformat().replace("+00:00", "Z")}],
        at=at,
    )
    return replace(
        selection, capture_sequence=sequence, mark_refs=((mark[0], quote, 0, mark[3]),)
    )


@pytest.fixture
def native_ready(native_risk_book: Any, request: pytest.FixtureRequest) -> Any:
    options = getattr(request, "param", {})
    source, risk, owner, fixture, reported = native_risk_book
    cash = "481.63"
    if options.get("late_price"):
        at = native_partial(native_risk_book) + timedelta(seconds=1)
        price = options["late_price"]
        source.order(CID, at, "canceled", 3)
        source.fill(CID, at, 1, price)
        cash = str(Decimal("491.03") - Decimal(price))
        risk.observe_native(
            "native-low-entry-account",
            full_book(source, reported, at, cash=cash, position=2),
            at,
        )
    else:
        at = late_entry(native_risk_book)
    reported.order["legs"][0].update(
        qty=options.get("stop_quantity", "1"),
        status=options.get("stop_status", "new"),
        updated_at=at.isoformat(),
    )
    owner.bind_native_exit_census(
        "current-native-stop-shape",
        "group-a",
        capture(fixture, reported, at),
        at,
        timedelta(seconds=30),
        at,
    )
    risk.bind_emergency_policy(
        "native-emergency-policy",
        at,
        client_order_id=CID,
        contract=load_crossed_stop_contract(),
    )
    at += timedelta(seconds=1)
    available = options.get("available", "1")
    risk.observe_native(
        "native-emergency-account",
        available_book(native_risk_book, at, available, cash=cash),
        at,
    )
    owner.bind_native_entry_census("native-emergency-entry", at, POLICY, at)
    at += timedelta(seconds=1)
    risk.observe_native(
        "native-emergency-current-prefix",
        available_book(
            native_risk_book,
            at,
            available,
            bid="1" if options.get("daily_stop") else "9.49",
            cash=cash,
        ),
        at,
    )
    snapshot = risk.view()
    assert snapshot["last_snapshot"]["qualified"], snapshot["issues"]
    assert snapshot["held"]["filled"] == 3
    assert snapshot["held"]["native_ownership"]["exit_quantity"] == "1"
    expected_issues = {"NATIVE_FINALITY_PENDING"}
    if options.get("daily_stop"):
        expected_issues.add("DAILY_STOP_LATCHED")
    if options.get("late_price"):
        expected_issues.add("FINAL_TARGET_TOPOLOGY_INVALID")
    assert set(snapshot["issues"]) == expected_issues
    assert owner.view().model.unallocated_quantity == 1
    emergency = CrossedStopJournal(owner)
    observed = emergency.observe_source("native-crossing", at)
    assert observed["latched"]
    return native_risk_book, emergency, at, asset(source, at), status(source, at)


def prepare_native(ready: Any, close_id: str = "native-residual") -> Any:
    _, emergency, at, assets, statuses = ready
    return emergency.prepare_native_source(
        close_id,
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
    )


def test_native_residual_excludes_original_group_preserves_risk_and_interlocks(
    native_ready: Any,
) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, _, _ = book
    before_risk, before_oco = risk.view(), owner.view()
    before_intents = source.journal.read_order_intent_ids()
    state = prepare_native(native_ready)
    close = state["closes"]["native-residual"]
    assert close["quantity"] == "1"
    assert close["portions"] == {source.fills[-1]["id"]: "1"}
    assert close["request"]["qty"] == "1"
    assert close["attempt"] is None and close["dispatch_compatible"] is False
    receipt = close["source_preparation"]
    assert receipt["reported_position_quantity"] == "2"
    assert receipt["reported_available_quantity"] == "1"
    assert receipt["native_ownership"]["transport_origin"] == "INJECTED_EXCHANGE"
    for key in (
        "current_no_halt_proven",
        "continuous_status_coverage_proven",
        "broker_reservation_atomicity_proven",
        "source_admission_accepted",
        "dispatch_authorized",
        "runtime_authorized",
        "formal_paper_authorized",
    ):
        assert receipt[key] is False
    assert owner.view() == before_oco
    assert risk.view() == before_risk
    assert source.journal.read_order_intent_ids() == before_intents
    assert (
        source.journal.read_crossed_stop_events()[-1][0].event_kind == "SOURCE_PREPARE"
    )
    with pytest.raises(JournalError, match="emergency reservation blocks"):
        source.intent("must-not-submit", at, side="sell", order_type="market", qty=1)
    with pytest.raises(JournalError):
        owner.allocate_group(
            "must-not-reallocate",
            (LotPortion(source.fills[-1]["id"], Fraction(1)),),
            at,
        )
    with pytest.raises(JournalError, match="reservation unresolved"):
        prepare_native(native_ready, "second-close")
    count = source.journal.read_evidence_sequence()
    recovered = CrossedStopJournal(OcoJournal(source.journal, owner.initial))
    assert recovered.view() == state
    assert prepare_native((book, recovered, at, assets, statuses)) == state
    assert source.journal.read_evidence_sequence() == count
    assert risk.view() == before_risk
    # Reopen the actual private SQLite connection, not just owner objects.
    path, root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        reopened_owner = OcoJournal(reopened, owner.initial)
        restarted = CrossedStopJournal(reopened_owner)
        assert restarted.view() == state
        assert prepare_native((book, restarted, at, assets, statuses)) == state
        assert reopened.read_evidence_sequence() == count
        assert (
            AccountRiskOwner(reopened, risk.account_ref, POLICY).view() == before_risk
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("native_ready", [{"stop_quantity": "2"}], indirect=True)
def test_working_stop_overhang_cannot_claim_new_unallocated_share(
    native_ready: Any,
) -> None:
    book, emergency, _, _, _ = native_ready
    source, risk, owner, _, reported = book
    assert reported.order["legs"][0]["qty"] == "2"
    assert owner.view().model.group_residual("group-a") == 1
    before = risk.view()
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="remaining|residual|overhang"):
        prepare_native(native_ready)
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before


@pytest.mark.parametrize("native_ready", [{"available": "0"}], indirect=True)
def test_reported_unavailable_residual_is_not_reserved(native_ready: Any) -> None:
    book, emergency, _, _, _ = native_ready
    source, risk, _, _, _ = book
    before = risk.view()
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="unreserved quantity unavailable"):
        prepare_native(native_ready)
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before


def test_new_native_request_conflict_after_risk_observation_vetoes(
    native_ready: Any,
) -> None:
    book, emergency, at, _, _ = native_ready
    source, risk, owner, _, _ = book
    before_risk = risk.view()
    changed = NativeOcoRequests(owner).prepare(
        "group-a", "changed-native-client", at, at
    )
    assert changed["status"] == "REQUEST_IDENTITY_CONFLICT"
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="clean native preparation inventory"):
        prepare_native(native_ready)
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before_risk


def test_new_unbound_native_capture_is_not_silently_ignored(native_ready: Any) -> None:
    book, emergency, at, _, _ = native_ready
    source, risk, _, fixture, reported = book
    capture(fixture, reported, at)
    before = risk.view()
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="capture|supersed|refresh"):
        prepare_native(native_ready)
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before


def test_legacy_preparation_still_refuses_native_groups(native_ready: Any) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, _, _, _ = book
    before = risk.view()
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="native/declared exit ownership"):
        emergency.prepare_source(
            "legacy-must-refuse",
            at,
            asset_sequence=assets,
            status_sequence=statuses,
            status_record_index=0,
            max_asset_age=timedelta(seconds=1),
            max_status_age=timedelta(seconds=1),
        )
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before


@pytest.mark.parametrize("native_ready", [{"daily_stop": True}], indirect=True)
def test_daily_stop_stays_latched_when_only_local_residual_is_reserved(
    native_ready: Any,
) -> None:
    book, _, _, _, _ = native_ready
    source, risk, owner, _, _ = book
    before_risk, before_oco = risk.view(), owner.view()
    assert before_risk["daily_stop_latched"] and before_risk["capacity"] == "0"
    state = prepare_native(native_ready)
    assert state["closes"]["native-residual"]["quantity"] == "1"
    assert state["closes"]["native-residual"]["attempt"] is None
    assert risk.view() == before_risk
    assert owner.view() == before_oco
    assert source.journal.read_order_intent_ids() == (CID,)


def test_new_account_capture_without_risk_refresh_vetoes(native_ready: Any) -> None:
    book, emergency, at, _, _ = native_ready
    source, risk, _, _, _ = book
    # Add ONLY a newer account observation: no quote, metadata or lifecycle
    # update may hide the intended account-version veto behind another gate.
    selected = risk.view()["last_snapshot"]["selection"]["capture_sequence"]
    _, raw = source.journal.read_event(selected)
    envelope = json.loads(raw)
    for row in envelope["requests"]:
        if row["path"] == "/v2/positions":
            positions = json.loads(row["body"])
            positions[0]["qty_available"] = "0"
            row["body"] = json.dumps(positions)
    source.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(envelope).encode(),
        system_first_seen_at=at,
        processed_at=at,
    )
    before = risk.view()
    count = source.journal.read_evidence_sequence()
    with pytest.raises(
        JournalError, match="account.*(capture|refresh|risk)|source risk.*refresh"
    ):
        prepare_native(native_ready)
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before


def test_backdated_preparation_cannot_ignore_known_future_native_capture(
    native_ready: Any,
) -> None:
    book, emergency, at, _, _ = native_ready
    source, risk, _, fixture, reported = book
    capture(fixture, reported, at + timedelta(seconds=1))
    before = risk.view()
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="capture.*(clock|visible)|native.*decision"):
        prepare_native(native_ready)
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before


def test_same_close_identity_cannot_change_native_ownership_mode(
    native_ready: Any,
) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, _, _, _ = book
    original = prepare_native(native_ready)
    before = risk.view()
    changed = emergency.prepare_source(
        "native-residual",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
    )
    assert changed["conflicts"]
    assert changed["closes"] == original["closes"]
    assert risk.view() == before
    assert source.journal.read_order_intent_ids() == (CID,)


@pytest.mark.parametrize("native_ready", [{"late_price": "8.00"}], indirect=True)
def test_invalid_final_target_does_not_abandon_owned_emergency_residual(
    native_ready: Any,
) -> None:
    book, _, _, _, _ = native_ready
    source, risk, owner, _, _ = book
    before_risk, before_oco = risk.view(), owner.view()
    assert before_oco.model.average_entry <= before_oco.model.stop
    assert before_oco.model.incidents == ("FINAL_TARGET_TOPOLOGY_INVALID",)
    assert before_risk["capacity"] == "0"
    state = prepare_native(native_ready)
    assert state["closes"]["native-residual"]["quantity"] == "1"
    assert state["closes"]["native-residual"]["attempt"] is None
    assert risk.view() == before_risk
    assert owner.view() == before_oco
    assert source.journal.read_order_intent_ids() == (CID,)


@pytest.mark.parametrize(
    "native_ready",
    [{"stop_status": "pending_cancel"}, {"stop_status": "unrecognized-status"}],
    indirect=True,
)
def test_unknown_or_changing_native_order_state_vetoes_new_residual_reservation(
    native_ready: Any,
) -> None:
    book, emergency, _, _, _ = native_ready
    source, risk, _, _, _ = book
    before = risk.view()
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="status|unknown|stable|state"):
        prepare_native(native_ready)
    assert source.journal.read_evidence_sequence() == count
    assert not emergency.view()["closes"]
    assert risk.view() == before


def test_native_anchored_intent_handoff_preserves_existing_group_and_risk(
    native_ready: Any,
) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, _, _ = book
    before = risk.view()
    original = owner.view()
    prepared = emergency.prepare_native_source(
        "native-handoff",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
        for_handoff=True,
    )
    state = emergency.bind_source_intent("native-handoff", at)
    close = state["closes"]["native-handoff"]
    receipt = close["source_intent_binding"]
    cid = json.loads(receipt["intent"]["request_body"])["client_order_id"]
    assert close["quantity"] == "1"
    assert (
        close["portions"]
        == prepared["closes"]["native-handoff"]["source_preparation"]["reservation"][
            "portions"
        ]
    )
    risk.bind_exit("native-handoff-role", at + timedelta(seconds=1), cid, "CLOSE")
    after = risk.view()
    assert after["held"]["client_order_id"] == CID
    assert after["held"]["exits"][cid] == "CLOSE"
    assert after["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert after["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    assert after["capacity"] == "0"
    assert owner.view() == original
    assert close["filled"] == "0" and close["attempt"] is None
    assert not receipt["source_fill_ownership_accepted"]
    with pytest.raises(JournalError, match="source emergency"):
        source.journal.mark_order_send_attempt(cid, at + timedelta(seconds=2))
    assert not source.journal.has_order_send_attempt(cid)


def test_fault_injected_native_residual_close_cannot_release_source_ownership(
    native_ready: Any,
) -> None:
    from alpaca_ross_bot.oco_exit_evidence import NATIVE_TERMINAL_STOP_POLICY
    from test_source_close_handoff import fault_inject_source_send_marker

    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, fixture, reported = book
    emergency.prepare_native_source(
        "fault-native-close",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
        for_handoff=True,
    )
    state = emergency.bind_source_intent("fault-native-close", at)
    close = state["closes"]["fault-native-close"]
    cid = close["request"]["client_order_id"]
    later = at + timedelta(seconds=1)
    risk.bind_exit("fault-native-close-role", later, cid, "CLOSE")
    before = risk.view()
    fault_inject_source_send_marker(source, cid, later)
    source.order(cid, later, "filled", 1)
    source.fill(cid, later, 1, "9.49")
    reported.order.update(status="filled", filled_qty="2", updated_at=later.isoformat())
    reported.order["legs"][0].update(status="canceled", updated_at=later.isoformat())
    root = reported.order["id"]
    reported.fills[root].append(
        reported.fills[root][0]
        | {"id": "native-target-final", "transaction_time": later.isoformat()}
    )
    owner.bind_native_exit_census(
        "fault-native-terminal",
        "group-a",
        capture(fixture, reported, later),
        later,
        timedelta(seconds=30),
        later,
        observation_policy=NATIVE_TERMINAL_STOP_POLICY,
    )
    selection = full_book(source, reported, later, cash="502.15", position=0)
    risk.observe("fault-native-close-flat", selection, later)
    after = risk.view()
    assert after["last_snapshot"]["qualified"], after["issues"]
    assert after["last_snapshot"]["flat"]
    assert CID in after["last_snapshot"]["native"]["final_scopes"]
    assert after["held"] is not None
    assert after["held"]["client_order_id"] == CID
    assert after["completed"] == before["completed"]
    assert after["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert after["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    assert after["capacity"] == "0"
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" in after["issues"]
    assert owner.view().model.unallocated_quantity == 1
    assert emergency.view()["closes"]["fault-native-close"]["filled"] == "0"
    assert not close["source_intent_binding"]["source_fill_ownership_accepted"]
    # Legacy-equivalent counterexample with the NEW native reader: remove only
    # the new guard marker from a transient reducer input. No source proof or
    # projection is mocked, and no marker-free event is persisted.
    event, raw = source.journal.read_account_risk_events()[-1]
    legacy = json.loads(raw)
    assert (
        legacy.pop("source_close_ownership_policy")
        == "LATCH_UNRECONCILED_SOURCE_CLOSE_V1"
    )
    legacy_result = risk._reduce(before, legacy, False, event.sequence)
    assert legacy_result["last_snapshot"]["qualified"]
    assert legacy_result["last_snapshot"]["flat"]
    assert legacy_result["held"] is None
    assert CID in legacy_result["completed"]
    assert emergency.view()["closes"]["fault-native-close"]["filled"] == "0"
    assert risk.view() == after
    next_at = later + timedelta(seconds=1)
    risk.observe(
        "fault-native-close-flat-again",
        full_book(source, reported, next_at, cash="502.15", position=0),
        next_at,
    )
    repeated = risk.view()
    assert repeated["held"] is not None
    assert repeated["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert (
        repeated["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    )
    assert (
        AccountRiskOwner(source.journal, risk.account_ref, risk.fee_policy).view()
        == repeated
    )
    assert risk.verify_rebuild()
    path, runtime_root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, runtime_root)
    try:
        recovered = AccountRiskOwner(reopened, risk.account_ref, risk.fee_policy)
        assert recovered.view() == repeated
        assert recovered.verify_rebuild()
    finally:
        reopened.close()
