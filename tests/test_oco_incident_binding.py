"""Declared OCO incident semantics; synthetic facts never qualify native protection."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca_ross_bot import incident_state
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.incident_journal import IncidentJournal
from alpaca_ross_bot.incident_state import (
    RESEARCH_POLICY_VERSION,
    IncidentInputError,
    IncidentObservation,
    IncidentOrder,
    IncidentPolicy,
    IncidentProjection,
    OrderRole,
    ProposalKind,
    reduce_incident,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.observation_admission import CANDIDATE_VERSION, ObservationContract
from alpaca_ross_bot.oco_research import CANDIDATE as OCO_CANDIDATE
from alpaca_ross_bot.oco_research import (
    OcoResearchContract,
    OcoResearchError,
    load_oco_research_contract,
)
from alpaca_ross_bot.order_lifecycle import LifecycleState, OrderProjection
from alpaca_ross_bot.research_risk_binding import (
    OBSERVATION_IDENTITY,
    OCO_EXECUTION_IDENTITY,
)

ACCOUNT = "sha256:" + "a" * 64
REF = "sha256:" + "b" * 64
NOW = datetime(2026, 10, 20, 18, tzinfo=UTC)
LEGACY = IncidentPolicy(
    ACCOUNT,
    RESEARCH_POLICY_VERSION,
    "legacy-roundtrip",
    "XYZ",
    ("P11", "P12", "P13"),
    REF,
)
PARENT = replace(
    LEGACY,
    version=CANDIDATE_VERSION,
    scope_id="parent-roundtrip",
    observation_contract=ObservationContract(),
)
GATES = {
    "SOURCE_ADMISSION_UNQUALIFIED",
    "RUNTIME_RISK_GATE_UNACCEPTED",
    "PROTECTION_BROKER_GATE_UNACCEPTED",
}


@pytest.fixture
def oco_policy() -> IncidentPolicy:
    return replace(
        PARENT,
        version=OCO_CANDIDATE,
        scope_id="oco-research-roundtrip",
        oco_contract=load_oco_research_contract(),
    )


def _order(
    policy: IncidentPolicy,
    cid: str,
    role: OrderRole,
    *,
    quantity: str = "2",
    filled: str = "0",
    status: str = "new",
    tick: int = 1,
    verified: bool = False,
    cancel: bool = False,
) -> IncidentOrder:
    state = {
        "new": LifecycleState.ACCEPTED,
        "partially_filled": LifecycleState.PARTIAL,
        "pending_cancel": LifecycleState.CANCEL_PENDING,
        "filled": LifecycleState.FILLED,
        "canceled": LifecycleState.CANCELED,
    }[status]
    return IncidentOrder(
        OrderProjection(
            policy.account_ref,
            cid,
            "broker-" + cid,
            policy.symbol,
            "buy" if role == OrderRole.ENTRY else "sell",
            Decimal(quantity),
            Decimal(filled),
            state,
            status,
            tick,
            REF,
        ),
        role,
        verified,
        cancel,
    )


def _orders(
    policy: IncidentPolicy,
    *,
    filled: str = "2",
    stop_quantity: str = "2",
    tick: int = 1,
) -> tuple[IncidentOrder, ...]:
    return (
        _order(
            policy,
            "entry",
            OrderRole.ENTRY,
            quantity="10",
            filled=filled,
            status="partially_filled",
            tick=tick,
        ),
        _order(
            policy,
            "stop-group-1",
            OrderRole.STOP,
            quantity=stop_quantity,
            verified=True,
            tick=tick,
        ),
        _order(policy, "target-group-1", OrderRole.TARGET, tick=tick),
    )


def _observation(
    policy: IncidentPolicy,
    orders: tuple[IncidentOrder, ...] | None = None,
    *,
    watermark: int = 1,
    position: str = "2",
) -> IncidentObservation:
    return IncidentObservation(
        "event-" + str(watermark),
        policy.account_ref,
        policy.version,
        policy.scope_id,
        REF,
        watermark,
        Decimal(0),
        True,
        Decimal(position),
        _orders(policy, tick=watermark) if orders is None else orders,
        reconciliation_verified=True,
        data_verified=True,
        durable=True,
        stream_connected=True,
        recovery_verified=True,
    )


def _assert_no_authority(state: IncidentProjection) -> None:
    assert not state.new_exposure_allowed
    assert GATES <= set(state.reasons)
    assert {item.kind for item in state.proposals} <= set(ProposalKind)
    assert state.policy.runtime_risk_accepted is False
    assert state.policy.protection_contract_accepted is False


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_original_policy_bytes_and_target_guard_are_unchanged() -> None:
    original = {
        "account_ref": ACCOUNT,
        "version": RESEARCH_POLICY_VERSION,
        "scope_id": "legacy-roundtrip",
        "symbol": "XYZ",
        "rule_refs": ["P11", "P12", "P13"],
        "evidence_ref": REF,
        "runtime_risk_accepted": False,
        "protection_contract_accepted": False,
        "risk_acceptance_ref": None,
        "protection_acceptance_ref": None,
    }
    assert _json(LEGACY.to_dict()) == _json(original)
    assert IncidentPolicy.from_dict(original) == LEGACY
    state = reduce_incident(None, LEGACY, _observation(LEGACY))
    assert "TARGET_BEFORE_ENTRY_TERMINAL" in state.reasons
    assert state.pending_cancel_ids == ("entry",)
    assert state.preserve_protection_ids == ("stop-group-1",)


def test_full_identity_roundtrips_without_contract_bytes(
    oco_policy: IncidentPolicy,
) -> None:
    original_parent = PARENT.to_dict()
    encoded = oco_policy.to_dict()
    assert encoded["version"] == OCO_CANDIDATE
    assert encoded["candidate_binding"] == OBSERVATION_IDENTITY
    assert encoded["execution_research_binding"] == OCO_EXECUTION_IDENTITY
    assert "oco_contract" not in encoded and "observation_contract" not in encoded
    assert "execution_research_binding" not in original_parent
    assert original_parent["version"] == CANDIDATE_VERSION
    assert IncidentPolicy.from_dict(json.loads(_json(encoded))) == oco_policy
    assert IncidentPolicy.from_dict(original_parent) == PARENT
    state = reduce_incident(None, oco_policy, _observation(oco_policy))
    assert IncidentProjection.from_dict(json.loads(_json(state.to_dict()))) == state
    _assert_no_authority(state)


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_version", CANDIDATE_VERSION),
        ("entry_candidate_version", OCO_CANDIDATE),
        ("execution_contract_version", "other"),
        ("execution_contract_sha256", "0" * 64),
        ("classification", "SOURCE_ACCEPTED"),
        ("research_semantics_accepted", False),
        ("research_semantics_accepted", 1),
        ("entry_risk_version_binding_accepted", True),
        ("entry_risk_version_binding_accepted", 0),
        ("source_admission_qualified", True),
        ("source_admission_qualified", 0),
        ("strategy_plan_qualified", True),
        ("strategy_plan_qualified", 0),
        ("runtime_authorized", True),
        ("runtime_authorized", 0),
        ("order_authority", True),
        ("order_authority", 0),
        ("extra", False),
    ],
)
def test_execution_metadata_rejects_forgery_and_integer_flags(
    oco_policy: IncidentPolicy, field: str, value: object
) -> None:
    encoded = oco_policy.to_dict()
    binding = dict(OCO_EXECUTION_IDENTITY)
    binding[field] = value
    encoded["execution_research_binding"] = binding
    with pytest.raises(IncidentInputError, match="execution binding"):
        IncidentPolicy.from_dict(encoded)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_execution",
        "missing_parent",
        "missing_execution_field",
        "null_execution",
        "wrong_parent_version",
        "wrong_parent_flag",
        "extra_parent_field",
        "parent_full_version",
        "legacy_full_version",
        "raw_contract",
    ],
)
def test_serialized_opt_in_is_never_inferred_or_mixed(
    oco_policy: IncidentPolicy, fault: str
) -> None:
    encoded = oco_policy.to_dict()
    if fault == "missing_execution":
        del encoded["execution_research_binding"]
    elif fault == "missing_parent":
        del encoded["candidate_binding"]
    elif fault == "missing_execution_field":
        encoded["execution_research_binding"] = {
            key: value
            for key, value in OCO_EXECUTION_IDENTITY.items()
            if key != "order_authority"
        }
    elif fault == "null_execution":
        encoded["execution_research_binding"] = None
    elif fault.startswith("wrong_parent") or fault == "extra_parent_field":
        field, value = {
            "wrong_parent_version": ("strategy_version", OCO_CANDIDATE),
            "wrong_parent_flag": ("source_admission_qualified", 0),
            "extra_parent_field": ("extra", False),
        }[fault]
        encoded["candidate_binding"] = OBSERVATION_IDENTITY | {field: value}
    elif fault.endswith("full_version"):
        encoded["version"] = (
            CANDIDATE_VERSION
            if fault == "parent_full_version"
            else RESEARCH_POLICY_VERSION
        )
    else:
        encoded["oco_contract"] = {}
    with pytest.raises(IncidentInputError):
        IncidentPolicy.from_dict(encoded)


@pytest.mark.parametrize(
    "fault",
    [
        "no_parent",
        "no_oco",
        "wrong_parent",
        "wrong_oco",
        "risk",
        "protection",
        "version",
    ],
)
def test_in_process_contracts_and_authority_are_validated_every_time(
    oco_policy: IncidentPolicy, fault: str
) -> None:
    if fault == "no_parent":
        policy = replace(oco_policy, observation_contract=None)
    elif fault == "no_oco":
        policy = replace(oco_policy, oco_contract=None)
    elif fault == "wrong_parent":
        policy = replace(
            oco_policy,
            observation_contract=replace(ObservationContract(), max_age_seconds=True),
        )
    elif fault == "wrong_oco":
        forged = object.__new__(OcoResearchContract)
        object.__setattr__(forged, "manifest_bytes", b"forged")
        object.__setattr__(forged, "parent_bytes", b"forged")
        policy = replace(oco_policy, oco_contract=forged)
    elif fault == "risk":
        policy = replace(
            oco_policy, runtime_risk_accepted=True, risk_acceptance_ref=REF
        )
    elif fault == "protection":
        policy = replace(
            oco_policy, protection_contract_accepted=True, protection_acceptance_ref=REF
        )
    else:
        policy = replace(oco_policy, version=CANDIDATE_VERSION)
    with pytest.raises(IncidentInputError):
        policy.to_dict()
    with pytest.raises(IncidentInputError):
        reduce_incident(None, policy, _observation(policy))


@pytest.mark.parametrize("error", [OSError, OcoResearchError])
def test_decoder_requires_available_exact_pinned_artifacts(
    oco_policy: IncidentPolicy,
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
) -> None:
    encoded = oco_policy.to_dict()

    def unavailable() -> OcoResearchContract:
        raise error("synthetic pinned artifact failure")

    monkeypatch.setattr(incident_state, "load_oco_research_contract", unavailable)
    with pytest.raises(IncidentInputError, match="pinned incident OCO"):
        IncidentPolicy.from_dict(encoded)


def test_explicit_oco_changes_only_early_target_reason(
    oco_policy: IncidentPolicy,
) -> None:
    parent = reduce_incident(None, PARENT, _observation(PARENT))
    oco = reduce_incident(None, oco_policy, _observation(oco_policy))
    assert set(parent.reasons) - set(oco.reasons) == {"TARGET_BEFORE_ENTRY_TERMINAL"}
    assert set(oco.reasons) == GATES
    assert parent.proposals == oco.proposals
    assert oco.pending_cancel_ids == ("entry",)
    assert oco.preserve_protection_ids == ("stop-group-1",)
    _assert_no_authority(oco)


def test_distinct_early_target_executions_do_not_spend_the_research_opt_in(
    oco_policy: IncidentPolicy,
) -> None:
    orders = _orders(oco_policy, filled="5", stop_quantity="5")
    first_target = _order(
        oco_policy,
        "target-group-1",
        OrderRole.TARGET,
        filled="1",
        status="partially_filled",
    )
    first = reduce_incident(
        None,
        oco_policy,
        _observation(oco_policy, (*orders[:2], first_target), position="4"),
    )
    second_target = _order(
        oco_policy,
        "target-group-2",
        OrderRole.TARGET,
        filled="1",
        status="partially_filled",
        tick=2,
    )
    second = reduce_incident(
        first,
        oco_policy,
        _observation(
            oco_policy,
            (
                *_orders(oco_policy, filled="5", stop_quantity="5", tick=2)[:2],
                replace(
                    first_target, order=replace(first_target.order, last_time_ns=2)
                ),
                second_target,
            ),
            watermark=2,
            position="3",
        ),
    )
    assert first.expected_quantity == 4 and second.expected_quantity == 3
    assert all(
        "TARGET_BEFORE_ENTRY_TERMINAL" not in state.reasons for state in (first, second)
    )
    # Stop coverage is never silently resized after either target execution.
    assert "EXCESS_PROTECTION_QUANTITY" in second.reasons
    assert second.preserve_protection_ids == ("stop-group-1",)
    assert second.pending_cancel_ids == ("entry",)
    _assert_no_authority(second)


@pytest.mark.parametrize(
    "fault,reasons",
    [
        ("lost_response", {"UNKNOWN_OUTCOME"}),
        ("disconnect", {"STREAM_DISCONNECTED", "RECONCILIATION_REQUIRED"}),
        ("persistence", {"DURABILITY_UNCONFIRMED"}),
        ("cancel_fill_race", {"UNPROTECTED", "UNKNOWN_OUTCOME"}),
        ("partial_fill", {"UNPROTECTED"}),
        ("both_exits", {"DOUBLE_EXIT_FILL", "OVER_EXIT", "REVERSE_POSITION"}),
    ],
)
def test_fault_groups_keep_protection_cancellation_and_vetoes(
    oco_policy: IncidentPolicy, fault: str, reasons: set[str]
) -> None:
    first = reduce_incident(None, oco_policy, _observation(oco_policy))
    update = _observation(oco_policy, watermark=2)
    if fault == "lost_response":
        update = replace(update, outcome_unknown=True)
    elif fault == "disconnect":
        update = replace(update, stream_connected=False, reconciliation_verified=False)
    elif fault == "persistence":
        update = replace(update, durable=False)
    elif fault in {"cancel_fill_race", "partial_fill"}:
        rows = _orders(oco_policy, filled="5", tick=2)
        if fault == "cancel_fill_race":
            rows = (
                replace(rows[0], cancel_requested=True),
                *rows[1:],
            )
        update = replace(
            update,
            orders=rows,
            position_quantity=Decimal(5),
            outcome_unknown=fault == "cancel_fill_race",
        )
    else:
        rows = _orders(oco_policy, tick=2)
        update = replace(
            update,
            orders=(
                rows[0],
                _order(
                    oco_policy,
                    "stop-group-1",
                    OrderRole.STOP,
                    filled="2",
                    status="filled",
                    verified=True,
                    tick=2,
                ),
                _order(
                    oco_policy,
                    "target-group-1",
                    OrderRole.TARGET,
                    filled="2",
                    status="filled",
                    tick=2,
                ),
            ),
            position_quantity=Decimal(-2),
        )
    result = reduce_incident(first, oco_policy, update)
    assert reasons <= set(result.reasons)
    assert "TARGET_BEFORE_ENTRY_TERMINAL" not in result.reasons
    assert result.pending_cancel_ids == ("entry",)
    kinds = {item.kind for item in result.proposals}
    assert ProposalKind.RECONCILE in kinds
    if fault != "both_exits":
        assert result.preserve_protection_ids == ("stop-group-1",)
        assert ProposalKind.PRESERVE_PROTECTION in kinds
    if "UNPROTECTED" in reasons:
        assert ProposalKind.VERIFY_PROTECTION in kinds
        assert ProposalKind.ESCALATE in kinds
    _assert_no_authority(result)


def test_unverified_stop_ack_and_closing_obligations_stay_blocked(
    oco_policy: IncidentPolicy,
) -> None:
    rows = _orders(oco_policy)
    update = replace(
        _observation(oco_policy),
        orders=(rows[0], replace(rows[1], protection_verified=False), rows[2]),
        closing_due=True,
    )
    state = reduce_incident(None, oco_policy, update)
    assert {"UNPROTECTED", "CLOSING_DUE"} <= set(state.reasons)
    assert not state.preserve_protection_ids
    assert {ProposalKind.VERIFY_PROTECTION, ProposalKind.REQUIRE_CLOSE_DISPOSITION} <= {
        item.kind for item in state.proposals
    }
    _assert_no_authority(state)


def _journal(tmp_path: Path) -> Journal:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    journal.record_paper_account_verification(ACCOUNT, b"synthetic", NOW)
    return journal


def test_partial_fill_restart_preserves_policy_history_and_faults(
    tmp_path: Path, oco_policy: IncidentPolicy
) -> None:
    journal = _journal(tmp_path)
    parent_store = IncidentJournal(journal, PARENT)
    parent = parent_store.observe(_observation(PARENT), NOW)
    rows = journal.read_incident_events()
    with pytest.raises(JournalError, match="policy changed"):
        IncidentJournal(journal, replace(oco_policy, scope_id=PARENT.scope_id))
    assert journal.read_incident_events() == rows
    assert parent_store.view() == parent
    store = IncidentJournal(journal, oco_policy)
    initial = store.observe(_observation(oco_policy), NOW)
    late_fill = replace(
        _observation(oco_policy, _orders(oco_policy, filled="5", tick=2), watermark=2),
        position_quantity=Decimal(5),
    )
    before = store.observe(late_fill, NOW)
    assert "UNPROTECTED" in before.reasons and before.expected_quantity == 5
    assert store.verify_rebuild() and parent_store.verify_rebuild()
    events = journal.read_incident_events()
    journal.close()

    root = tmp_path / "private"
    reopened = Journal.open_private(root / "journal.sqlite3", root)
    recovered = IncidentJournal(reopened, oco_policy)
    after = recovered.view()
    assert after is not None
    assert after.policy == oco_policy
    assert after.expected_quantity == before.expected_quantity == Decimal(5)
    assert after.preserve_protection_ids == initial.preserve_protection_ids
    assert after.pending_cancel_ids == before.pending_cancel_ids == ("entry",)
    assert set(before.reasons) | {"RESTART_REQUIRES_RECOVERY"} == set(after.reasons)
    assert reopened.read_incident_events()[: len(events)] == events
    assert recovered.verify_rebuild()
    raw = reopened.read_state(recovered.state_key)
    assert isinstance(raw, dict) and raw["dispatch_authorized"] is False
    assert IncidentJournal(reopened, PARENT).verify_rebuild()
    _assert_no_authority(after)
    reopened.close()


def test_failed_commit_rolls_back_oco_evidence_and_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, oco_policy: IncidentPolicy
) -> None:
    journal = _journal(tmp_path)
    store = IncidentJournal(journal, oco_policy)
    before = store.observe(_observation(oco_policy), NOW)
    events = journal.read_incident_events()
    evidence = journal.read_incident_evidence_cache(ACCOUNT)
    append = journal._append_locked

    def fail(
        event: EventEnvelope, payload: bytes, updates: Mapping[str, object] | None
    ) -> None:
        append(event, payload, updates)
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(journal, "_append_locked", fail)
    with pytest.raises(OSError, match="synthetic disk"):
        store.observe(_observation(oco_policy, watermark=2), NOW)
    assert store.view() == before
    assert journal.read_incident_events() == events
    assert journal.read_incident_evidence_cache(ACCOUNT) == evidence
    assert store.verify_rebuild()
    _assert_no_authority(before)
    journal.close()
