import json
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

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
from alpaca_ross_bot.order_lifecycle import LifecycleState, OrderProjection

REF = "sha256:" + "a" * 64
POLICY = IncidentPolicy(
    "account",
    RESEARCH_POLICY_VERSION,
    "session/round-trip-1",
    "AAPL",
    ("P11", "P12", "P13"),
    REF,
)
ACCEPTED = replace(
    POLICY,
    runtime_risk_accepted=True,
    protection_contract_accepted=True,
    risk_acceptance_ref=REF,
    protection_acceptance_ref=REF,
)


def order(
    cid: str = "entry",
    role: OrderRole = OrderRole.ENTRY,
    *,
    quantity: str = "10",
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
        "rejected": LifecycleState.REJECTED,
    }[status]
    return IncidentOrder(
        OrderProjection(
            "account",
            cid,
            "broker-" + cid,
            "AAPL",
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


def observation(
    *orders: IncidentOrder,
    watermark: int = 1,
    position: str | None = "0",
    **facts: object,
) -> IncidentObservation:
    item = IncidentObservation(
        "event-" + str(watermark),
        "account",
        POLICY.version,
        POLICY.scope_id,
        REF,
        watermark,
        Decimal(0),
        True,
        None if position is None else Decimal(position),
        orders,
        reconciliation_verified=True,
        data_verified=True,
        durable=True,
        stream_connected=True,
        recovery_verified=True,
    )
    return replace(item, **facts)  # type: ignore[arg-type]


def kinds(state: IncidentProjection) -> set[ProposalKind]:
    return {proposal.kind for proposal in state.proposals}


def protected(*, watermark: int = 1) -> IncidentObservation:
    return observation(
        order(filled="10", status="filled"),
        order("stop", OrderRole.STOP, verified=True),
        watermark=watermark,
        position="10",
    )


def test_current_research_policy_has_no_runtime_admission_authority() -> None:
    state = reduce_incident(None, POLICY, observation())
    assert not state.new_exposure_allowed
    assert set(state.reasons) == {
        "RUNTIME_RISK_GATE_UNACCEPTED",
        "PROTECTION_BROKER_GATE_UNACCEPTED",
    }
    assert not any("SUBMIT" in proposal.kind for proposal in state.proposals)
    admitted = reduce_incident(None, ACCEPTED, observation())
    assert admitted.new_exposure_allowed
    assert admitted.state == "FLAT_RECONCILED"


def test_runtime_gates_require_immutable_separate_acceptance_evidence() -> None:
    with pytest.raises(IncidentInputError, match="acceptance"):
        reduce_incident(
            None, replace(POLICY, runtime_risk_accepted=True), observation()
        )


@pytest.mark.parametrize("reason", ["lost_response", "lookup_404", "timeout"])
def test_unknown_outcomes_preserve_protection_without_retry_or_finality(
    reason: str,
) -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    update = replace(
        protected(watermark=2),
        event_id=reason,
        outcome_unknown=True,
        recovery_verified=False,
    )
    failed = reduce_incident(state, ACCEPTED, update)
    assert failed.preserve_protection_ids == ("stop",)
    assert "UNKNOWN_OUTCOME" in failed.reasons
    assert failed.expected_quantity == Decimal(10)
    assert ProposalKind.PRESERVE_PROTECTION in kinds(failed)
    assert not failed.new_exposure_allowed
    assert not any("RETRY" in proposal.kind for proposal in failed.proposals)


def test_disconnect_requires_verified_recovery_and_preserves_existing_stop() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    disconnected = reduce_incident(
        state,
        ACCEPTED,
        replace(
            protected(watermark=2),
            stream_connected=False,
            reconciliation_verified=False,
            recovery_verified=False,
        ),
    )
    assert disconnected.preserve_protection_ids == ("stop",)
    assert "STREAM_DISCONNECTED" in disconnected.reasons
    unverified = reduce_incident(
        disconnected, ACCEPTED, replace(protected(watermark=3), recovery_verified=False)
    )
    assert "STREAM_DISCONNECTED" in unverified.reasons
    recovered = reduce_incident(unverified, ACCEPTED, protected(watermark=4))
    assert not recovered.reasons
    assert recovered.state == "PROTECTED"


def test_disk_failure_fact_never_clears_obligations_or_admits_exposure() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    failed = reduce_incident(
        state,
        ACCEPTED,
        replace(protected(watermark=2), durable=False, recovery_verified=False),
    )
    assert "DURABILITY_UNCONFIRMED" in failed.reasons
    assert failed.preserve_protection_ids == ("stop",)
    assert not failed.new_exposure_allowed
    assert not hasattr(failed, "dispatch")


def test_cancel_fill_race_owns_racing_fills_and_requires_more_protection() -> None:
    initial = observation(
        order(filled="2", status="partially_filled", cancel=True),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        position="2",
    )
    state = reduce_incident(None, ACCEPTED, initial)
    assert state.pending_entry_ids == state.pending_cancel_ids == ("entry",)
    racing = observation(
        order(filled="5", status="pending_cancel", tick=2, cancel=True),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        watermark=2,
        position="5",
    )
    state = reduce_incident(state, ACCEPTED, racing)
    assert state.expected_quantity == Decimal(5)
    assert state.pending_cancel_ids == ("entry",)
    assert state.preserve_protection_ids == ("stop",)
    assert "UNPROTECTED" in state.reasons
    assert ProposalKind.VERIFY_PROTECTION in kinds(state)


def test_partial_fill_crash_rebuild_retains_quantity_and_cancel_obligations() -> None:
    initial = observation(
        order(filled="2", status="partially_filled"),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        position="2",
    )
    state = reduce_incident(None, ACCEPTED, initial)
    restored = IncidentProjection.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored == state
    crashed = reduce_incident(
        restored,
        ACCEPTED,
        replace(
            initial,
            event_id="restart",
            watermark=2,
            reconciliation_verified=False,
            stream_connected=False,
            recovery_verified=False,
        ),
    )
    assert crashed.expected_quantity == Decimal(2)
    assert crashed.pending_cancel_ids == ("entry",)
    assert crashed.pending_exit_ids == ("stop",)
    assert not crashed.new_exposure_allowed


def test_both_exits_filling_detects_reverse_without_auto_liquidation() -> None:
    state = reduce_incident(
        None,
        ACCEPTED,
        observation(
            order(filled="10", status="filled"),
            order("stop", OrderRole.STOP, filled="10", status="filled", verified=True),
            order("target", OrderRole.TARGET, filled="10", status="filled"),
            position="-10",
        ),
    )
    assert state.expected_quantity == Decimal(-10)
    assert {"DOUBLE_EXIT_FILL", "OVER_EXIT", "REVERSE_POSITION"} <= set(state.reasons)
    assert ProposalKind.ESCALATE in kinds(state)
    assert not state.new_exposure_allowed
    assert ProposalKind.REQUIRE_CLOSE_DISPOSITION not in kinds(state)


def test_daily_stop_is_latched_preserves_exits_and_cancels_entry_remainder() -> None:
    initial = observation(
        order(filled="2", status="partially_filled"),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        position="2",
        daily_stop=True,
    )
    state = reduce_incident(None, ACCEPTED, initial)
    assert state.daily_stop_latched
    assert state.pending_cancel_ids == ("entry",)
    assert state.preserve_protection_ids == ("stop",)
    later = reduce_incident(
        state,
        ACCEPTED,
        replace(initial, event_id="later", watermark=2, daily_stop=False),
    )
    assert later.daily_stop_latched
    assert "DAILY_STOP_LATCHED" in later.reasons


def test_closing_due_emits_policy_obligation_without_market_order() -> None:
    state = reduce_incident(None, ACCEPTED, replace(protected(), closing_due=True))
    assert ProposalKind.REQUIRE_CLOSE_DISPOSITION in kinds(state)
    assert state.preserve_protection_ids == ("stop",)
    assert not state.new_exposure_allowed


def test_duplicate_snapshots_never_double_count_cumulative_fills() -> None:
    item = protected()
    state = reduce_incident(None, ACCEPTED, item)
    assert reduce_incident(state, ACCEPTED, item) is state
    later = reduce_incident(state, ACCEPTED, protected(watermark=2))
    assert later.expected_quantity == Decimal(10)
    assert len(later.orders) == 2


def test_changed_event_id_is_a_persistent_conflict() -> None:
    item = protected()
    state = reduce_incident(None, ACCEPTED, item)
    conflicted = reduce_incident(state, ACCEPTED, replace(item, outcome_unknown=True))
    assert "EVENT_ID_CONFLICT" in conflicted.reasons
    recovered = reduce_incident(conflicted, ACCEPTED, protected(watermark=2))
    assert "EVENT_ID_CONFLICT" in recovered.reasons


def test_stale_snapshots_cannot_erase_fills_or_clear_a_veto() -> None:
    state = reduce_incident(None, ACCEPTED, protected(watermark=2))
    old = observation(order(), watermark=1)
    result = reduce_incident(state, ACCEPTED, old)
    assert result.expected_quantity == Decimal(10)
    assert result.watermark == 2
    assert "STALE_OBSERVATION" in result.reasons


def test_older_order_evidence_with_new_receipt_keeps_cumulative_exposure() -> None:
    initial = observation(
        order(filled="2", status="partially_filled", tick=2),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        position="2",
    )
    state = reduce_incident(None, ACCEPTED, initial)
    stale = observation(
        order(filled="1", status="partially_filled", tick=1),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        watermark=2,
        position="2",
    )
    assert reduce_incident(state, ACCEPTED, stale).expected_quantity == Decimal(2)


def test_foreign_account_cannot_change_owned_position_or_protection() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    foreign = replace(
        protected(watermark=2), account_ref="foreign", position_quantity=Decimal(999)
    )
    result = reduce_incident(state, ACCEPTED, foreign)
    assert result.expected_quantity == Decimal(10)
    assert result.preserve_protection_ids == ("stop",)
    assert "IDENTITY_CONFLICT" in result.reasons


@pytest.mark.parametrize(
    "change",
    [
        {"baseline_quantity": Decimal(-1)},
        {"position_quantity": Decimal("NaN")},
        {"watermark": True},
        {"durable": "true"},
        {"evidence_ref": "unverified"},
    ],
)
def test_malformed_input_fails_closed(change: dict[str, object]) -> None:
    with pytest.raises(IncidentInputError):
        reduce_incident(None, ACCEPTED, replace(observation(), **change))  # type: ignore[arg-type]


def test_unknown_baseline_cannot_create_a_flat_position_or_admission() -> None:
    state = reduce_incident(None, ACCEPTED, observation(baseline_verified=False))
    assert state.expected_quantity is None
    assert not state.new_exposure_allowed
    assert {"BASELINE_UNVERIFIED", "POSITION_UNRECONCILED"} <= set(state.reasons)


def test_known_baseline_is_explicit_and_cannot_be_rewritten() -> None:
    initial = observation(position="3", baseline_quantity=Decimal(3))
    state = reduce_incident(None, ACCEPTED, initial)
    assert state.expected_quantity == Decimal(3)
    changed = reduce_incident(
        state,
        ACCEPTED,
        observation(watermark=2, position="4", baseline_quantity=Decimal(4)),
    )
    assert changed.expected_quantity == Decimal(3)
    assert "BASELINE_CONFLICT" in changed.reasons


def test_roles_and_broker_identities_cannot_be_reassigned() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    initial_stop = state.orders[1]
    changed = replace(initial_stop, role=OrderRole.TARGET, protection_verified=False)
    result = reduce_incident(
        state,
        ACCEPTED,
        observation(state.orders[0], changed, watermark=2, position="10"),
    )
    assert "IDENTITY_CONFLICT" in result.reasons
    assert result.preserve_protection_ids == ("stop",)


def test_serialization_is_lossless_and_rejects_unknown_fields() -> None:
    item = protected()
    raw = json.loads(json.dumps(item.to_dict()))
    assert IncidentObservation.from_dict(raw) == item
    assert IncidentPolicy.from_dict(ACCEPTED.to_dict()) == ACCEPTED
    with pytest.raises(IncidentInputError):
        IncidentObservation.from_dict(raw | {"submit": True})


def test_expected_quantity_does_not_depend_on_decimal_context() -> None:
    value = "123456789012345678901234567890"
    item = observation(
        order(filled=value, quantity=value, status="filled"), position=value
    )
    with localcontext() as context:
        context.prec = 2
        state = reduce_incident(None, ACCEPTED, item)
        assert state.expected_quantity == Decimal(value)


def test_recovery_flag_without_verified_facts_cannot_clear_disconnect() -> None:
    state = reduce_incident(
        None, ACCEPTED, replace(protected(), stream_connected=False)
    )
    result = reduce_incident(
        state,
        ACCEPTED,
        replace(protected(watermark=2), data_verified=False, recovery_verified=True),
    )
    assert "STREAM_DISCONNECTED" in result.reasons
    assert "DATA_UNVERIFIED" in result.reasons


def test_missing_owned_evidence_preserves_known_exposure() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    result = reduce_incident(state, ACCEPTED, observation(watermark=2, position="10"))
    assert result.expected_quantity == Decimal(10)
    assert result.preserve_protection_ids == ("stop",)
    assert "OWNED_ORDER_EVIDENCE_MISSING" in result.reasons


def test_unverified_current_stop_preserves_identity_without_claiming_coverage() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    item = observation(
        order(filled="10", status="filled"),
        order("stop", OrderRole.STOP),
        watermark=2,
        position="10",
    )
    result = reduce_incident(state, ACCEPTED, item)
    assert result.preserve_protection_ids == ("stop",)
    assert {"PROTECTION_VERIFICATION_LOST", "UNPROTECTED"} <= set(result.reasons)


def test_cancel_pending_stop_is_not_assumed_to_cover_live_inventory() -> None:
    item = observation(
        order(filled="10", status="filled"),
        order("stop", OrderRole.STOP, status="pending_cancel", verified=True),
        position="10",
    )
    state = reduce_incident(None, ACCEPTED, item)
    assert "UNPROTECTED" in state.reasons
    assert state.preserve_protection_ids == ("stop",)


def test_target_is_blocked_until_entry_terminal_reconciles_actual_fills() -> None:
    item = observation(
        order(filled="2", status="partially_filled"),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        order("target", OrderRole.TARGET, quantity="2"),
        position="2",
    )
    state = reduce_incident(None, ACCEPTED, item)
    assert "TARGET_BEFORE_ENTRY_TERMINAL" in state.reasons
    assert state.pending_cancel_ids == ("entry",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseline_quantity", "NaN"),
        ("baseline_quantity", "-1"),
        ("baseline_verified", "false"),
        ("watermark", -1),
        ("observed_position", "Infinity"),
        ("seen_events", []),
        ("daily_stop_latched", 1),
    ],
)
def test_malformed_serialized_projection_is_rejected(field: str, value: object) -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    with pytest.raises(IncidentInputError):
        IncidentProjection.from_dict(state.to_dict() | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_ref", 1),
        ("runtime_risk_accepted", "false"),
        ("rule_refs", "P11"),
        ("risk_acceptance_ref", "unchecked"),
    ],
)
def test_malformed_serialized_policy_is_rejected(field: str, value: object) -> None:
    with pytest.raises(IncidentInputError):
        IncidentPolicy.from_dict(POLICY.to_dict() | {field: value})


def test_daily_stop_fact_cannot_be_removed_by_a_cached_missing_reason() -> None:
    state = reduce_incident(None, ACCEPTED, observation(daily_stop=True))
    assert not replace(state, reasons=()).new_exposure_allowed


def test_foreign_round_trip_cannot_clear_existing_incident() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    result = reduce_incident(
        state, ACCEPTED, replace(protected(watermark=2), scope_id="other-round-trip")
    )
    assert "IDENTITY_CONFLICT" in result.reasons
    assert result.preserve_protection_ids == ("stop",)


def test_unbound_policy_has_only_generic_incident_obligations() -> None:
    unbound = replace(POLICY, version="other-research", rule_refs=("OTHER",))
    item = replace(
        observation(
            order(filled="2", status="partially_filled"), position="2", closing_due=True
        ),
        policy_version=unbound.version,
    )
    state = reduce_incident(None, unbound, item)
    assert "RESEARCH_POLICY_UNBOUND" in state.reasons
    assert ProposalKind.CANCEL_ENTRY_REMAINDER not in kinds(state)
    assert ProposalKind.REQUIRE_CLOSE_DISPOSITION not in kinds(state)
    assert not state.new_exposure_allowed


def test_identity_conflict_preserves_prior_cancel_and_protection_obligations() -> None:
    item = observation(
        order(filled="2", status="partially_filled"),
        order("stop", OrderRole.STOP, quantity="2", verified=True),
        position="2",
    )
    state = reduce_incident(None, ACCEPTED, item)
    result = reduce_incident(
        state,
        ACCEPTED,
        replace(item, event_id="foreign", watermark=2, account_ref="foreign"),
    )
    assert result.pending_cancel_ids == ("entry",)
    assert result.preserve_protection_ids == ("stop",)
    assert ProposalKind.PRESERVE_PROTECTION in kinds(result)


def test_untyped_order_quantities_are_not_allowed_to_enter_arithmetic() -> None:
    malformed = replace(order(), order=replace(order().order, quantity=10.0))  # type: ignore[arg-type]
    state = reduce_incident(None, ACCEPTED, observation(malformed))
    assert "INVALID_OBSERVATION" in state.reasons
    assert not state.new_exposure_allowed


def test_stale_event_remembers_original_identity_before_changed_reuse() -> None:
    state = reduce_incident(None, ACCEPTED, protected(watermark=3))
    old = observation(order(), watermark=1)
    stale = reduce_incident(state, ACCEPTED, old)
    fingerprint = dict(stale.seen_events)[old.event_id]
    assert reduce_incident(stale, ACCEPTED, old) is stale
    changed = replace(old, position_quantity=Decimal(999), watermark=4)
    result = reduce_incident(stale, ACCEPTED, changed)
    assert "EVENT_ID_CONFLICT" in result.reasons
    assert dict(result.seen_events)[old.event_id] == fingerprint
    assert result.watermark == 3
    assert result.expected_quantity == Decimal(10)


def test_identity_veto_remembers_original_id_and_blocks_changed_reuse() -> None:
    state = reduce_incident(None, ACCEPTED, protected())
    foreign = replace(protected(watermark=2), account_ref="foreign")
    rejected = reduce_incident(state, ACCEPTED, foreign)
    fingerprint = dict(rejected.seen_events)[foreign.event_id]
    result = reduce_incident(rejected, ACCEPTED, protected(watermark=2))
    assert "EVENT_ID_CONFLICT" in result.reasons
    assert dict(result.seen_events)[foreign.event_id] == fingerprint
    assert result.watermark == 1
    assert result.expected_quantity == Decimal(10)


def test_partial_target_then_remaining_stop_can_reconcile_flat() -> None:
    item = observation(
        order(filled="10", status="filled"),
        order("target", OrderRole.TARGET, filled="4", status="canceled"),
        order(
            "stop",
            OrderRole.STOP,
            quantity="6",
            filled="6",
            status="filled",
            verified=True,
        ),
    )
    state = reduce_incident(None, ACCEPTED, item)
    assert state.expected_quantity == 0
    assert not state.reasons
    assert state.state == "FLAT_RECONCILED"
    assert not state.pending_exit_ids


def test_partial_exits_keep_remaining_quantity_without_false_alarm() -> None:
    item = observation(
        order(filled="10", status="filled"),
        order("target", OrderRole.TARGET, filled="4", status="canceled"),
        order(
            "stop",
            OrderRole.STOP,
            quantity="6",
            filled="2",
            status="partially_filled",
            verified=True,
        ),
        position="4",
    )
    state = reduce_incident(None, ACCEPTED, item)
    assert state.expected_quantity == 4
    assert not state.reasons
    assert state.state == "PROTECTED"
    assert state.preserve_protection_ids == ("stop",)


def test_live_exit_at_flat_requires_cancel_finality() -> None:
    item = observation(
        order(filled="10", status="filled"),
        order("target", OrderRole.TARGET, filled="4", status="partially_filled"),
        order(
            "stop",
            OrderRole.STOP,
            quantity="6",
            filled="6",
            status="filled",
            verified=True,
        ),
    )
    state = reduce_incident(None, ACCEPTED, item)
    assert "UNRESOLVED_EXIT_WHILE_FLAT" in state.reasons
    assert "DOUBLE_EXIT_FILL" not in state.reasons
    assert not state.new_exposure_allowed
    assert state.pending_exit_ids == ("target",)


def test_partial_exit_oversell_is_not_suppressed() -> None:
    item = observation(
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
    state = reduce_incident(None, ACCEPTED, item)
    assert state.expected_quantity == -1
    assert {"DOUBLE_EXIT_FILL", "OVER_EXIT", "REVERSE_POSITION"} <= set(state.reasons)
