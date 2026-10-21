"""Synthetic entry-source integration; declared exits/ACKs prove no native coverage.

ENGINEERING_TEST intents are never natural samples. Only entry facts are bound;
declared stop/target projections and hypothetical reverse inventory remain local
research scenarios, with source, reconciliation, protection and runtime gates shut.
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.incident_journal import IncidentJournal
from alpaca_ross_bot.incident_state import (
    IncidentObservation,
    IncidentOrder,
    IncidentPolicy,
    IncidentProjection,
    OrderRole,
    ProposalKind,
)
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import (
    CANDIDATE,
    Execution,
    LotPortion,
    OcoResearchError,
    load_oco_research_contract,
)
from alpaca_ross_bot.order_lifecycle import LifecycleState, OrderProjection
from alpaca_ross_bot.research_risk_binding import OCO_EXECUTION_IDENTITY
from test_account_risk_owner import SourceAccount
from test_observation_candidate_binding import CID, contract
from test_oco_entry_evidence import _initial
from test_oco_entry_evidence import candidate as candidate
from test_oco_entry_evidence import prepared as prepared
from test_risk_ledger_audit import POLICY, REF

type Linked = tuple[
    SourceAccount, AccountRiskOwner, OcoJournal, IncidentJournal, datetime
]
PARTS = (LotPortion("execution-0", Fraction(2)),)
DECLARED_REF = "sha256:" + "e" * 64


@pytest.fixture
def linked(
    prepared: tuple[SourceAccount, AccountRiskOwner, OcoJournal, datetime],
) -> Linked:
    source, risk, book, at = prepared
    risk_before = risk.view()
    risk_rows = source.journal.read_account_risk_events()
    intents = source.journal.read_order_intent_ids()
    receipt = book.bind_entry_census("initial", at, POLICY, at).entry_binding
    assert receipt is not None
    assert receipt["intent_origin"]["purpose"] == "ENGINEERING_TEST"
    assert receipt["formal_sample_eligible"] is False
    reservation = receipt["reservation"]
    signal = reservation["research_signal"]
    assert signal["version"] == contract().candidate_version
    assert signal["execution_research_binding"] == OCO_EXECUTION_IDENTITY
    assert receipt["entry"]["client_order_id"] == CID
    assert [row["execution_id"] for row in receipt["entry"]["executions"]] == [
        row["id"] for row in source.fills
    ]
    assert sum(Fraction(str(row["qty"])) for row in source.fills) == 2
    assert risk.view() == risk_before and risk_before is not None
    assert risk_before["held"]["client_order_id"] == CID
    assert risk_before["held"]["quantity"] == receipt["entry"]["reserved_quantity"] == 4
    assert set(risk_before["last_snapshot"]["orders"]) == {CID}
    assert source.journal.read_account_risk_events() == risk_rows
    assert source.journal.read_order_intent_ids() == intents == (CID,)
    assert all(
        flag is False
        for key, flag in receipt["authority"].items()
        if key != "research_semantics_accepted"
    )
    book.allocate_group("declared-group", PARTS, at)
    policy = IncidentPolicy(
        REF,
        CANDIDATE,
        CID,
        "AAPL",
        ("P11", "P12", "P13"),
        REF,
        observation_contract=contract(),
        oco_contract=load_oco_research_contract(),
    )
    return source, risk, book, IncidentJournal(source.journal, policy), at


def _observation(linked: Linked, tick: int = 1) -> IncidentObservation:
    source, risk, _, store, _ = linked
    current = risk.view()
    assert current is not None
    snapshot = current["last_snapshot"]
    entry = OrderProjection.from_dict(snapshot["orders"][CID])
    declared = tuple(
        IncidentOrder(
            OrderProjection(
                REF,
                "DECLARED-" + role,
                "DECLARED-broker-" + role,
                "AAPL",
                "sell",
                Decimal(2),
                Decimal(0),
                LifecycleState.ACCEPTED,
                "new",
                tick,
                DECLARED_REF,
            ),
            OrderRole(role),
            protection_verified=False,
        )
        for role in ("STOP", "TARGET")
    )
    lifecycle = source.reconciler.view()
    assert lifecycle["baseline"] == {}
    return IncidentObservation(
        "integration-" + str(tick),
        REF,
        store.policy.version,
        CID,
        entry.evidence_ref,
        tick,
        Decimal(0),
        lifecycle["baseline"] == {},
        Decimal(snapshot["positions"]["AAPL"]),
        (
            IncidentOrder(
                entry,
                OrderRole.ENTRY,
                cancel_requested=entry.state == LifecycleState.CANCEL_PENDING,
            ),
            *declared,
        ),
        reconciliation_verified=False,
        data_verified=False,
        durable=True,
        stream_connected=lifecycle["stream_status"] == "SUBSCRIBED",
    )


def _blocked(state: IncidentProjection) -> None:
    assert not state.new_exposure_allowed
    assert {
        "SOURCE_ADMISSION_UNQUALIFIED",
        "RUNTIME_RISK_GATE_UNACCEPTED",
        "PROTECTION_BROKER_GATE_UNACCEPTED",
        "RECONCILIATION_REQUIRED",
        "DATA_UNVERIFIED",
    } <= set(state.reasons)
    assert "TARGET_BEFORE_ENTRY_TERMINAL" not in state.reasons
    assert not state.preserve_protection_ids
    assert state.pending_cancel_ids == (CID,)
    assert ProposalKind.RECONCILE in {row.kind for row in state.proposals}
    assert all(not row.protection_verified for row in state.orders)


def _late_fill(linked: Linked, *, cancel: bool = False) -> datetime:
    source, risk, _, _, at = linked
    later = at + timedelta(seconds=1)
    source.order(CID, later, "pending_cancel" if cancel else "partially_filled", 3)
    source.fill(CID, later, 1, "10")
    risk.observe("racing-fill", source.selection(later, cash="470", position=3), later)
    return later


def test_unknown_ack_retry_retains_same_durable_group(linked: Linked) -> None:
    source, risk, book, store, at = linked
    before, rows = book.view(), source.journal.read_oco_research_events()
    retry = book.allocate_group("declared-group", PARTS, at + timedelta(days=1))
    assert retry == before
    assert source.journal.read_oco_research_events() == rows
    assert before.model.groups[0].acknowledgement == "UNKNOWN"
    assert before.model.reserved_quantity == 2
    state = store.observe(replace(_observation(linked), outcome_unknown=True), at)
    assert {"UNKNOWN_OUTCOME", "UNPROTECTED"} <= set(state.reasons)
    _blocked(state)
    assert risk.verify_rebuild() and book.verify_rebuild() and store.verify_rebuild()


def test_disconnect_keeps_reservation_and_exposure_blocked(linked: Linked) -> None:
    source, risk, book, store, at = linked
    held = risk.view()
    session = source.reconciler.view()["session_id"]
    assert isinstance(session, str)
    source.reconciler.stream_health("DISCONNECTED", at, session)
    state = store.observe(_observation(linked), at)
    assert "STREAM_DISCONNECTED" in state.reasons
    assert risk.view() == held and book.view().model.reserved_quantity == 2
    assert book.view().model.groups[0].acknowledgement == "UNKNOWN"
    _blocked(state)


def test_failed_bound_entry_append_preserves_new_canonical_fill(
    linked: Linked, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, book, store, _ = linked
    before = book.view()
    later = _late_fill(linked)
    risk_after, risk_rows = risk.view(), source.journal.read_account_risk_events()
    orders = source.journal.read_order_events()
    rows = source.journal.read_oco_research_events()
    horizon = source.journal.read_evidence_sequence()
    append = source.journal._append_locked

    def fail(
        event: EventEnvelope, raw: bytes, updates: Mapping[str, object] | None
    ) -> None:
        append(event, raw, updates)
        if event.event_kind == "BOUND_ENTRY":
            raise OSError("synthetic BOUND_ENTRY append failure")

    with monkeypatch.context() as patch:
        patch.setattr(source.journal, "_append_locked", fail)
        with pytest.raises(OSError, match="BOUND_ENTRY append"):
            book.bind_entry_census("late", later, POLICY, later)
    assert source.journal.read_evidence_sequence() == horizon
    assert source.journal.read_oco_research_events() == rows and book.view() == before
    assert risk.view() == risk_after
    assert source.journal.read_account_risk_events() == risk_rows
    assert source.journal.read_order_events() == orders
    state = store.observe(replace(_observation(linked), durable=False), later)
    assert {"DURABILITY_UNCONFIRMED", "UNPROTECTED"} <= set(state.reasons)
    assert state.expected_quantity == 3 and book.view().model.entry_quantity == 2
    _blocked(state)
    assert risk.verify_rebuild() and book.verify_rebuild() and store.verify_rebuild()


def test_cancel_fill_race_retains_entry_and_cancellation_duties(linked: Linked) -> None:
    source, risk, book, store, _ = linked
    later = _late_fill(linked, cancel=True)
    bound = book.bind_entry_census("racing", later, POLICY, later)
    assert bound.model.entry_quantity == 3 and bound.model.unallocated_quantity == 1
    assert bound.entry_binding is not None
    assert len(bound.entry_binding["entry"]["executions"]) == len(source.fills) == 2
    state = store.observe(replace(_observation(linked), outcome_unknown=True), later)
    assert state.expected_quantity == 3
    entry = next(row for row in state.orders if row.role == OrderRole.ENTRY)
    assert entry.cancel_requested
    assert {"UNKNOWN_OUTCOME", "UNPROTECTED"} <= set(state.reasons)
    _blocked(state)
    assert risk.verify_rebuild() and book.verify_rebuild()


def test_physical_restart_retains_receipt_unknown_group_and_recovery(
    linked: Linked, tmp_path: Path
) -> None:
    source, risk, book, store, at = linked
    initial = store.observe(_observation(linked), at)
    risk_before, book_before = risk.view(), book.view()
    risk_rows = source.journal.read_account_risk_events()
    book_rows = source.journal.read_oco_research_events()
    incident_rows = source.journal.read_incident_events()
    source.journal.close()
    root = tmp_path / "private"
    reopened = Journal.open_private(root / "journal.sqlite3", root)
    try:
        recovered_risk = AccountRiskOwner(reopened, REF, POLICY)
        recovered_book = OcoJournal(reopened, _initial())
        recovered_store = IncidentJournal(reopened, store.policy)
        assert recovered_risk.view() == risk_before
        assert recovered_book.view() == book_before
        assert recovered_book.view().entry_binding is not None
        assert recovered_book.view().model.groups[0].acknowledgement == "UNKNOWN"
        assert reopened.read_account_risk_events() == risk_rows
        assert reopened.read_oco_research_events() == book_rows
        assert reopened.read_incident_events()[: len(incident_rows)] == incident_rows
        state = recovered_store.view()
        assert state is not None
        assert state.expected_quantity == initial.expected_quantity
        assert "RESTART_REQUIRES_RECOVERY" in state.reasons
        _blocked(state)
        assert recovered_risk.verify_rebuild() and recovered_book.verify_rebuild()
        assert recovered_store.verify_rebuild()
    finally:
        reopened.close()


def test_both_declared_exit_fills_keep_signed_incident_and_forbid_allocation(
    linked: Linked,
) -> None:
    _, _, book, store, at = linked
    roles: tuple[Literal["TARGET", "STOP"], ...] = ("TARGET", "STOP")
    for role in roles:
        result = book.observe_execution(
            Execution(
                "DECLARED-fill-" + role,
                REF,
                "AAPL",
                CID,
                role,
                Fraction(2),
                Fraction(10),
                "declared-group",
            ),
            at,
        )
    assert result.model.owned_quantity == result.model.reserved_quantity == -2
    with pytest.raises(OcoResearchError, match="safety incident"):
        book.allocate_group("forbidden", PARTS, at)
    observation = _observation(linked)
    declared_fills = tuple(
        replace(
            row,
            order=replace(
                row.order,
                filled_quantity=Decimal(2),
                state=LifecycleState.FILLED,
                raw_status="filled",
            ),
        )
        if row.role != OrderRole.ENTRY
        else row
        for row in observation.orders
    )
    # This reverse inventory is a declared scenario, never a source-account claim.
    state = store.observe(
        replace(observation, orders=declared_fills, position_quantity=Decimal(-2)), at
    )
    assert {"OVER_EXIT", "REVERSE_POSITION", "DOUBLE_EXIT_FILL"} <= set(state.reasons)
    _blocked(state)
    assert book.verify_rebuild() and store.verify_rebuild()
