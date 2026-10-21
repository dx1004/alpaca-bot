"""Pinned candidate incident obligations, never protection or runtime acceptance."""

import json
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.incident_journal import IncidentJournal
from alpaca_ross_bot.incident_state import (
    IncidentInputError,
    IncidentObservation,
    IncidentPolicy,
    IncidentProjection,
    OrderRole,
    ProposalKind,
    reduce_incident,
)
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.observation_admission import (
    CANDIDATE_VERSION,
    ObservationContract,
)
from alpaca_ross_bot.research_risk_binding import OBSERVATION_IDENTITY
from test_account_risk_owner import account_sources
from test_incident_journal import NOW, POLICY, _journal, _observation
from test_incident_risk_binding import policy as risk_policy
from test_incident_state import POLICY as REDUCER_POLICY
from test_incident_state import kinds, observation, order
from test_risk_ledger_audit import NOW as RISK_NOW
from test_risk_ledger_audit import POLICY as FEE_POLICY


def candidate(parent: IncidentPolicy = POLICY) -> IncidentPolicy:
    return replace(
        parent,
        version=CANDIDATE_VERSION,
        observation_contract=ObservationContract(),
    )


def selected(item: IncidentObservation) -> IncidentObservation:
    return replace(item, policy_version=CANDIDATE_VERSION)


def test_original_policy_and_projection_keep_their_serialized_shape() -> None:
    legacy = asdict(POLICY)
    del legacy["observation_contract"]
    del legacy["oco_contract"]
    legacy["rule_refs"] = list(POLICY.rule_refs)
    assert POLICY.to_dict() == legacy
    assert IncidentPolicy.from_dict(legacy) == POLICY
    original = reduce_incident(None, POLICY, _observation())
    assert original.to_dict()["policy"] == legacy
    assert IncidentProjection.from_dict(original.to_dict()) == original


def test_contract_opt_in_is_exact_and_preserves_separate_identities() -> None:
    configured = candidate()
    raw = configured.to_dict()
    assert raw["candidate_binding"] == OBSERVATION_IDENTITY
    assert raw["version"] == CANDIDATE_VERSION
    assert OBSERVATION_IDENTITY["risk_profile_version"] == POLICY.version
    assert OBSERVATION_IDENTITY["volume_source_age"] == "UNKNOWN"
    assert OBSERVATION_IDENTITY["source_admission_qualified"] is False
    assert IncidentPolicy.from_dict(raw) == configured
    state = reduce_incident(None, configured, selected(_observation()))
    assert "RESEARCH_POLICY_UNBOUND" not in state.reasons
    assert {
        "SOURCE_ADMISSION_UNQUALIFIED",
        "RUNTIME_RISK_GATE_UNACCEPTED",
        "PROTECTION_BROKER_GATE_UNACCEPTED",
    } <= set(state.reasons)
    assert not state.new_exposure_allowed
    assert IncidentProjection.from_dict(state.to_dict()) == state


@pytest.mark.parametrize(
    "configured",
    [
        replace(POLICY, version=CANDIDATE_VERSION),
        replace(POLICY, observation_contract=ObservationContract()),
        replace(
            candidate(),
            observation_contract=replace(ObservationContract(), sha256="f" * 64),
        ),
        replace(
            candidate(),
            observation_contract=replace(
                ObservationContract(), runtime_authorized=True
            ),
        ),
        replace(
            candidate(),
            observation_contract=replace(ObservationContract(), max_age_seconds=True),
        ),
    ],
)
def test_missing_forged_or_mixed_contract_cannot_bind(
    configured: IncidentPolicy,
) -> None:
    with pytest.raises(IncidentInputError):
        reduce_incident(None, configured, selected(_observation()))


@pytest.mark.parametrize(
    "key,value",
    [
        ("strategy_version", POLICY.version),
        ("risk_profile_version", CANDIDATE_VERSION),
        ("data_contract_sha256", "f" * 64),
        ("volume_source_age", "0"),
        ("source_admission_qualified", True),
        ("source_admission_qualified", 0),
    ],
)
def test_serialized_candidate_identity_cannot_change(key: str, value: object) -> None:
    raw = candidate().to_dict()
    raw["candidate_binding"] = dict(OBSERVATION_IDENTITY) | {key: value}
    with pytest.raises(IncidentInputError, match="binding differs"):
        IncidentPolicy.from_dict(raw)


def test_candidate_keeps_protection_target_cancel_and_close_obligations() -> None:
    configured = candidate(REDUCER_POLICY)
    item = selected(
        observation(
            order(filled="2", status="partially_filled"),
            order("stop", OrderRole.STOP, quantity="2", verified=True),
            order("target", OrderRole.TARGET, quantity="2"),
            position="2",
            closing_due=True,
            stream_connected=False,
        )
    )
    state = reduce_incident(None, configured, item)
    assert state.preserve_protection_ids == ("stop",)
    assert state.pending_cancel_ids == ("entry",)
    assert "TARGET_BEFORE_ENTRY_TERMINAL" in state.reasons
    assert "STREAM_DISCONNECTED" in state.reasons
    assert {
        ProposalKind.PRESERVE_PROTECTION,
        ProposalKind.CANCEL_ENTRY_REMAINDER,
        ProposalKind.REQUIRE_CLOSE_DISPOSITION,
        ProposalKind.RECONCILE,
    } <= kinds(state)
    assert not state.new_exposure_allowed
    missing_rule = replace(configured, rule_refs=("P11", "P12"))
    assert (
        "RESEARCH_POLICY_UNBOUND" in reduce_incident(None, missing_rule, item).reasons
    )


def test_candidate_preserves_sticky_reverse_inventory_faults() -> None:
    configured = candidate(REDUCER_POLICY)
    item = selected(
        observation(
            order(filled="10", status="filled"),
            order("target", OrderRole.TARGET, filled="4", status="canceled"),
            order(
                "stop",
                OrderRole.STOP,
                quantity="7",
                filled="7",
                status="filled",
                verified=True,
            ),
            position="-1",
        )
    )
    state = reduce_incident(None, configured, item)
    assert state.expected_quantity == Decimal(-1)
    faults = {"DOUBLE_EXIT_FILL", "OVER_EXIT", "REVERSE_POSITION"}
    assert faults <= set(state.reasons)
    later = replace(item, event_id="recovery", watermark=2, recovery_verified=True)
    recovered = reduce_incident(state, configured, later)
    assert faults <= set(recovered.reasons)
    assert ProposalKind.ESCALATE in kinds(recovered)
    assert not recovered.new_exposure_allowed


def test_candidate_daily_stop_and_identity_fault_cannot_clear_protection() -> None:
    configured = candidate(REDUCER_POLICY)
    item = selected(
        observation(
            order(filled="2", status="partially_filled"),
            order("stop", OrderRole.STOP, quantity="2", verified=True),
            position="2",
            daily_stop=True,
        )
    )
    state = reduce_incident(None, configured, item)
    foreign = replace(
        item, event_id="foreign", watermark=2, policy_version=POLICY.version
    )
    rejected = reduce_incident(state, configured, foreign)
    assert "IDENTITY_CONFLICT" in rejected.reasons
    assert rejected.preserve_protection_ids == ("stop",)
    assert rejected.pending_cancel_ids == ("entry",)
    recovered = reduce_incident(
        rejected,
        configured,
        replace(
            item,
            event_id="later",
            watermark=3,
            daily_stop=False,
            recovery_verified=True,
        ),
    )
    assert recovered.daily_stop_latched
    assert {"IDENTITY_CONFLICT", "DAILY_STOP_LATCHED"} <= set(recovered.reasons)
    assert not recovered.new_exposure_allowed


def test_candidate_unknown_response_keeps_protection_and_cannot_authorize_retry(
    tmp_path: Path,
) -> None:
    with closing(_journal(tmp_path)) as journal:
        owner = IncidentJournal(journal, candidate())
        owner.observe(selected(_observation()), NOW)
        unknown = replace(
            selected(_observation(watermark=2)),
            event_id="entry-response-lost",
            outcome_unknown=True,
            recovery_verified=False,
        )
        state = owner.observe(unknown, NOW)
        assert state.expected_quantity == Decimal(2)
        assert state.preserve_protection_ids == ("stop-1",)
        assert state.pending_entry_ids == state.pending_cancel_ids == ("entry-1",)
        assert "UNKNOWN_OUTCOME" in state.reasons
        assert ProposalKind.PRESERVE_PROTECTION in kinds(state)
        assert not any(
            "RETRY" in proposal.kind or "SUBMIT" in proposal.kind
            for proposal in state.proposals
        )
        assert not state.new_exposure_allowed
        count = len(journal.read_incident_events())
        assert owner.observe(unknown, NOW) == state
        assert len(journal.read_incident_events()) == count
        assert journal.read_order_intent_ids() == ()
        wrapper = journal.read_state(owner.state_key)
        assert isinstance(wrapper, dict) and wrapper["dispatch_authorized"] is False
        assert owner.verify_rebuild()


def test_candidate_cancel_fill_race_owns_fills_beyond_existing_stop_coverage() -> None:
    configured = candidate(REDUCER_POLICY)
    initial = selected(
        observation(
            order(filled="2", status="partially_filled"),
            order("stop", OrderRole.STOP, quantity="2", verified=True),
            position="2",
        )
    )
    state = reduce_incident(None, configured, initial)
    cancel_pending = selected(
        observation(
            order(filled="2", status="pending_cancel", tick=2, cancel=True),
            order("stop", OrderRole.STOP, quantity="2", tick=2, verified=True),
            watermark=2,
            position="2",
        )
    )
    pending = reduce_incident(state, configured, cancel_pending)
    assert pending.expected_quantity == Decimal(2)
    assert pending.pending_entry_ids == pending.pending_cancel_ids == ("entry",)
    assert "UNPROTECTED" not in pending.reasons
    racing = selected(
        observation(
            order(filled="5", status="pending_cancel", tick=3, cancel=True),
            order("stop", OrderRole.STOP, quantity="2", tick=3, verified=True),
            watermark=3,
            position="5",
        )
    )
    result = reduce_incident(pending, configured, racing)
    assert result.expected_quantity == result.observed_position == Decimal(5)
    entry = next(item for item in result.orders if item.role == OrderRole.ENTRY)
    assert entry.cancel_requested and entry.order.raw_status == "pending_cancel"
    assert entry.order.filled_quantity == Decimal(5)
    assert result.pending_entry_ids == result.pending_cancel_ids == ("entry",)
    assert result.preserve_protection_ids == ("stop",)
    assert "UNPROTECTED" in result.reasons
    assert "POSITION_UNRECONCILED" not in result.reasons
    assert {ProposalKind.VERIFY_PROTECTION, ProposalKind.ESCALATE} <= kinds(result)
    assert not result.new_exposure_allowed


@pytest.mark.parametrize("gate", ["risk", "protection"])
def test_candidate_flags_and_hashes_cannot_grant_acceptance(
    tmp_path: Path, gate: str
) -> None:
    configured = (
        replace(
            candidate(),
            runtime_risk_accepted=True,
            risk_acceptance_ref=POLICY.evidence_ref,
        )
        if gate == "risk"
        else replace(
            candidate(),
            protection_contract_accepted=True,
            protection_acceptance_ref=POLICY.evidence_ref,
        )
    )
    with closing(_journal(tmp_path)) as journal:
        with pytest.raises(JournalError, match="verifier is unavailable"):
            IncidentJournal(journal, configured)
        assert journal.read_incident_events() == ()
    with pytest.raises(IncidentInputError, match="cannot grant acceptance"):
        reduce_incident(None, configured, selected(_observation()))


def test_candidate_replay_idempotency_restart_and_policy_migration(
    tmp_path: Path,
) -> None:
    with closing(_journal(tmp_path)) as journal:
        configured = candidate()
        owner = IncidentJournal(journal, configured)
        item = selected(_observation())
        initial = owner.observe(item, NOW)
        count = len(journal.read_incident_events())
        assert owner.observe(item, NOW) == initial
        assert len(journal.read_incident_events()) == count
        with pytest.raises(JournalError, match="policy changed without acceptance"):
            IncidentJournal(journal, POLICY)
        assert len(journal.read_incident_events()) == count
    with closing(_journal(tmp_path)) as recovered_journal:
        reopened = IncidentJournal(recovered_journal, configured)
        recovered = reopened.view()
        assert recovered is not None
        assert recovered.policy == configured
        assert recovered.expected_quantity == Decimal(2)
        assert recovered.preserve_protection_ids == ("stop-1",)
        assert recovered.pending_cancel_ids == ("entry-1",)
        assert "RESTART_REQUIRES_RECOVERY" in recovered.reasons
        assert "SOURCE_ADMISSION_UNQUALIFIED" in recovered.reasons
        assert not recovered.new_exposure_allowed and reopened.verify_rebuild()
        wrapper = recovered_journal.read_state(reopened.state_key)
        assert isinstance(wrapper, dict) and wrapper["dispatch_authorized"] is False


def test_candidate_commit_failure_rolls_back_binding_and_obligation_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(_journal(tmp_path)) as journal:
        owner = IncidentJournal(journal, candidate())
        before = owner.observe(selected(_observation()), NOW)
        rows = journal.read_incident_events()
        append = journal._append_locked

        def fail(
            event: EventEnvelope, payload: bytes, updates: Mapping[str, object] | None
        ) -> None:
            append(event, payload, updates)
            raise OSError("synthetic disk failure")

        monkeypatch.setattr(journal, "_append_locked", fail)
        with pytest.raises(OSError, match="synthetic disk"):
            owner.observe(selected(_observation("5", 2)), NOW)
        assert owner.view() == before and journal.read_incident_events() == rows
        assert owner.verify_rebuild()


def test_candidate_reads_the_shared_source_risk_owner_without_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        selection = source.selection(RISK_NOW)
        risk.observe("actual", selection, RISK_NOW)
        original_risk = source.journal.read_state(risk.state_key)
        original_risk_events = source.journal.read_account_risk_events()
        configured = candidate(risk_policy())
        incident = IncidentJournal(source.journal, configured)
        item = replace(
            selected(_observation()),
            account_ref=configured.account_ref,
            scope_id=configured.scope_id,
            orders=(),
            position_quantity=Decimal(0),
        )
        incident.observe(item, RISK_NOW)
        result = incident.observe_risk(
            "actual", selection, RISK_NOW, account_risk_fee_policy=FEE_POLICY
        )
        assert result["daily_state_source"] == "SOURCE_BOUND_ACCOUNT_RISK"
        assert result["reported_inputs_qualified"] is True
        assert result["daily_state_present"] is True
        assert result["runtime_authorized"] is False
        reasons = result["reasons"]
        assert isinstance(reasons, list)
        assert "BOUND_RISK_RUNTIME_RELEASE_UNACCEPTED" in reasons
        assert "BOUND_RISK_DATA_CONTRACT_UNQUALIFIED" in reasons
        state = incident.view()
        assert state is not None and not state.new_exposure_allowed
        assert "SOURCE_ADMISSION_UNQUALIFIED" in state.reasons
        assert state.policy.to_dict()["candidate_binding"] == OBSERVATION_IDENTITY
        assert source.journal.read_order_intent_ids() == ()
        assert (
            risk.state_key
            == "account_risk:" + configured.account_ref + ":" + POLICY.version
        )
        assert source.journal.read_state(risk.state_key) == original_risk
        assert source.journal.read_account_risk_events() == original_risk_events
        assert (
            source.journal.read_state(
                "account_risk:" + configured.account_ref + ":" + CANDIDATE_VERSION
            )
            is None
        )
        assert risk.verify_rebuild() and incident.verify_rebuild()
        for _, payload in source.journal.read_incident_events():
            assert json.loads(payload)["policy"] == configured.to_dict()
