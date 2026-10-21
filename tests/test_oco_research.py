"""Synthetic declared research facts; no broker or source acceptance fixtures."""

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest

from alpaca_ross_bot.incident_state import (
    IncidentInputError,
    OrderRole,
    reduce_incident,
)
from alpaca_ross_bot.observation_admission import ObservationContract
from alpaca_ross_bot.oco_research import (
    Execution,
    LotPortion,
    OcoResearchContract,
    OcoResearchError,
    OcoResearchState,
    acknowledge_group,
    allocate_group,
    load_oco_research_contract,
    observe_execution,
    reconcile_entry_terminal,
)
from test_incident_state import POLICY, observation, order


@pytest.fixture
def state() -> OcoResearchState:
    return OcoResearchState(
        load_oco_research_contract(),
        "synthetic-account",
        "AAA",
        "entry-1",
        Fraction(10),
        Fraction(9),
    )


def _entry(
    state: OcoResearchState,
    identity: str = "entry-fill-1",
    quantity: str = "2",
    price: str = "49/5",
) -> OcoResearchState:
    return observe_execution(
        state,
        Execution(
            identity,
            state.account_ref,
            state.symbol,
            state.entry_scope,
            "ENTRY",
            Fraction(quantity),
            Fraction(price),
        ),
    )


def _allocate(
    state: OcoResearchState,
    group: str = "group-1",
    portions: tuple[tuple[str, str], ...] = (("entry-fill-1", "2"),),
) -> OcoResearchState:
    return allocate_group(
        state,
        group,
        tuple(
            LotPortion(identity, Fraction(quantity)) for identity, quantity in portions
        ),
    )


def _exit(
    state: OcoResearchState,
    identity: str = "exit-fill-1",
    quantity: str = "2",
    group: str = "group-1",
    *,
    stop: bool = False,
) -> OcoResearchState:
    return observe_execution(
        state,
        Execution(
            identity,
            state.account_ref,
            state.symbol,
            state.entry_scope,
            "STOP" if stop else "TARGET",
            Fraction(quantity),
            state.stop if stop else state.provisional_target,
            group,
        ),
    )


def _conserved(state: OcoResearchState) -> None:
    assert state.entry_quantity == (
        state.exit_quantity + state.reserved_quantity + state.unallocated_quantity
    )
    assert state.owned_quantity == state.reserved_quantity + state.unallocated_quantity


@pytest.mark.parametrize("which", ["manifest", "parent"])
def test_loader_requires_exact_manifest_and_parent_bytes(
    tmp_path: Path, which: str
) -> None:
    contract = load_oco_research_contract()
    manifest = tmp_path / "manifest.json"
    parent = tmp_path / "parent.json"
    manifest.write_bytes(
        contract.manifest_bytes + (b" " if which == "manifest" else b"")
    )
    parent.write_bytes(contract.parent_bytes + (b" " if which == "parent" else b""))
    with pytest.raises(OcoResearchError, match="pinned"):
        load_oco_research_contract(manifest, parent)


def test_original_parent_p11_cannot_enable_provisional_exits(
    state: OcoResearchState,
) -> None:
    with pytest.raises(OcoResearchError, match="pinned"):
        OcoResearchContract(state.contract.parent_bytes, state.contract.parent_bytes)
    with pytest.raises(OcoResearchError, match="explicit pinned"):
        replace(state, contract=cast(OcoResearchContract, "original-P11"))


def test_original_incident_target_guard_is_not_bypassed_by_new_contract(
    state: OcoResearchState,
) -> None:
    original = reduce_incident(
        None,
        POLICY,
        observation(
            order(quantity="3", filled="2", status="partially_filled"),
            order("target", OrderRole.TARGET, quantity="2"),
            position="2",
        ),
    )
    assert "TARGET_BEFORE_ENTRY_TERMINAL" in original.reasons
    assert original.new_exposure_allowed is False
    forged_binding = replace(
        POLICY, observation_contract=cast(ObservationContract, state.contract)
    )
    with pytest.raises(IncidentInputError, match="pinned incident"):
        forged_binding.to_dict()
    old_manifest = Path(__file__).resolve().parents[1] / (
        "evidence/hybrid-500-iex-intraday-research-v1.json"
    )
    with pytest.raises(OcoResearchError, match="pinned"):
        OcoResearchContract(old_manifest.read_bytes(), state.contract.parent_bytes)


def test_portions_of_one_lot_can_be_disjointly_allocated_to_several_groups(
    state: OcoResearchState,
) -> None:
    state = _entry(state, quantity="3")
    state = _allocate(state, "group-1", (("entry-fill-1", "1"),))
    state = _allocate(state, "group-2", (("entry-fill-1", "2"),))
    assert state.owned_quantity == state.reserved_quantity == 3
    assert state.unallocated_quantity == 0
    assert [g.acknowledgement for g in state.groups] == ["UNKNOWN", "UNKNOWN"]
    _conserved(state)
    with pytest.raises(OcoResearchError, match="disjoint"):
        _allocate(state, "group-3", (("entry-fill-1", "1"),))


def test_unknown_ack_holds_reservation_and_exact_allocation_retry_is_idempotent(
    state: OcoResearchState,
) -> None:
    state = _allocate(_entry(state))
    assert _allocate(state) is state
    with pytest.raises(OcoResearchError, match="conflicting group"):
        _allocate(state, portions=(("entry-fill-1", "1"),))
    with pytest.raises(OcoResearchError, match="disjoint"):
        _allocate(state, "retry-new-id")
    assert state.reserved_quantity == 2
    assert state.groups[0].acknowledgement == "UNKNOWN"
    assert not state.authority["protection_coverage_verified"]
    _conserved(state)


def test_declared_full_ack_is_not_live_protection_or_permission(
    state: OcoResearchState,
) -> None:
    state = acknowledge_group(_allocate(_entry(state)), "group-1", "ack-1")
    assert state.groups[0].acknowledgement == "DECLARED_ACKNOWLEDGED"
    assert acknowledge_group(state, "group-1", "ack-1") is state
    assert state.reserved_quantity == 2
    assert not any(
        v for k, v in state.authority.items() if k != "research_semantics_accepted"
    )
    with pytest.raises(OcoResearchError, match="conflicting acknowledgement"):
        acknowledge_group(state, "group-1", "changed-ack")
    # A returned mutable summary cannot grant authority to the underlying model.
    state.authority["runtime_authorized"] = True
    assert state.authority["runtime_authorized"] is False


def test_ack_identity_cannot_be_reused_for_another_group(
    state: OcoResearchState,
) -> None:
    state = _allocate(_entry(state, quantity="3"), portions=(("entry-fill-1", "1"),))
    state = _allocate(state, "group-2", (("entry-fill-1", "2"),))
    state = acknowledge_group(state, "group-1", "ack")
    with pytest.raises(OcoResearchError, match="acknowledgement identity"):
        acknowledge_group(state, "group-2", "ack")


def test_duplicate_execution_is_idempotent_changed_identity_is_refused(
    state: OcoResearchState,
) -> None:
    state = _entry(state)
    execution = state.executions[0]
    assert observe_execution(state, execution) is state
    for changed in (
        replace(execution, quantity=Fraction(1)),
        replace(execution, price=Fraction(9)),
    ):
        with pytest.raises(OcoResearchError, match="conflicting execution"):
            observe_execution(state, changed)
    assert state.entry_quantity == 2


@pytest.mark.parametrize("field", ["account_ref", "symbol", "entry_scope"])
def test_foreign_execution_scope_cannot_enter_owned_ledger(
    state: OcoResearchState,
    field: str,
) -> None:
    execution = Execution(
        "foreign",
        state.account_ref,
        state.symbol,
        state.entry_scope,
        "ENTRY",
        Fraction(1),
        Fraction(10),
    )
    changed = {
        "account_ref": replace(execution, account_ref="foreign"),
        "symbol": replace(execution, symbol="foreign"),
        "entry_scope": replace(execution, entry_scope="foreign"),
    }[field]
    with pytest.raises(OcoResearchError, match="scope"):
        observe_execution(state, changed)
    assert not state.executions


def test_early_target_then_late_entry_uses_all_entry_fills_for_final_average(
    state: OcoResearchState,
) -> None:
    state = _exit(_allocate(_entry(state)))
    assert state.owned_quantity == state.reserved_quantity == 0
    assert state.final_target is None
    assert "TARGET_BEFORE_ENTRY_TERMINAL" not in state.issues
    # A racing remainder fill is retained; this model never creates a new entry.
    state = _entry(state, "late-entry", "2", "48/5")
    assert state.average_entry == Fraction(97, 10)
    state = _allocate(state, "late-group", (("late-entry", "2"),))
    state = reconcile_entry_terminal(
        state, "terminal-1", ("entry-fill-1", "late-entry")
    )
    assert state.provisional_target == 12
    assert state.final_target == Fraction(111, 10)
    assert state.final_target != Fraction(108, 10)  # Remaining inventory's wrong basis.
    assert state.provisional_target >= state.final_target
    assert state.owned_quantity == state.reserved_quantity == 2
    assert not state.incidents
    assert not state.authority["runtime_authorized"]
    _conserved(state)


def test_exited_lot_does_not_become_available_for_replacement_entry_or_exit(
    state: OcoResearchState,
) -> None:
    state = _exit(_allocate(_entry(state)))
    assert state.unallocated_quantity == 0
    with pytest.raises(OcoResearchError, match="disjoint"):
        _allocate(state, "recycled-exit")
    _conserved(state)


def test_fractional_execution_lots_can_combine_into_exact_whole_group(
    state: OcoResearchState,
) -> None:
    state = _entry(state, quantity="1/2")
    assert state.owned_quantity == Fraction(1, 2)
    assert "FRACTIONAL_RESIDUAL_UNQUALIFIED" in state.issues
    with pytest.raises(OcoResearchError, match="fractional"):
        _allocate(state, portions=(("entry-fill-1", "1/2"),))
    state = _entry(state, "second-half", "1/2")
    state = _allocate(state, portions=(("entry-fill-1", "1/2"), ("second-half", "1/2")))
    assert state.reserved_quantity == 1
    assert not state.issues
    assert not state.authority["protection_coverage_verified"]
    _conserved(state)


def test_fractional_exit_residual_stays_exact_reserved_and_unqualified(
    state: OcoResearchState,
) -> None:
    state = _exit(_allocate(_entry(state)), quantity="1/3")
    assert state.owned_quantity == state.reserved_quantity == Fraction(5, 3)
    assert state.group_residual("group-1") == Fraction(5, 3)
    assert "FRACTIONAL_RESIDUAL_UNQUALIFIED" in state.issues
    assert state.unallocated_quantity == 0
    with pytest.raises(OcoResearchError, match="disjoint"):
        _allocate(state, "unknown-residual-replay")
    _conserved(state)


def test_terminal_census_requires_all_and_only_current_entry_execution_ids(
    state: OcoResearchState,
) -> None:
    state = _entry(_entry(state), "entry-2", "1")
    for ids in (
        ("entry-fill-1",),
        ("entry-2", "entry-fill-1", "extra"),
        ("entry-fill-1", "entry-fill-1"),
        ("entry-fill-1", "entry-2"),
    ):
        with pytest.raises(OcoResearchError, match="exactly all entry IDs"):
            reconcile_entry_terminal(state, "bad", ids)
    state = reconcile_entry_terminal(state, "good", ("entry-2", "entry-fill-1"))
    assert state.entry_terminal_reconciled
    assert reconcile_entry_terminal(state, "good", ("entry-2", "entry-fill-1")) is state
    with pytest.raises(OcoResearchError, match="conflicting terminal census"):
        reconcile_entry_terminal(state, "good", ("entry-fill-1",))


def test_new_entry_invalidates_finalization_and_old_exact_retry_cannot_restore_it(
    state: OcoResearchState,
) -> None:
    state = reconcile_entry_terminal(_entry(state), "first-terminal", ("entry-fill-1",))
    assert state.final_target == Fraction(57, 5)
    state = _entry(state, "late", "1", "19/2")
    assert not state.entry_terminal_reconciled
    assert state.final_target is None
    assert "ENTRY_CENSUS_CHANGED_REQUIRES_RECONCILIATION" in state.issues
    assert reconcile_entry_terminal(state, "first-terminal", ("entry-fill-1",)) is state
    with pytest.raises(OcoResearchError, match="unreconciled"):
        _allocate(state)
    state = reconcile_entry_terminal(state, "new-terminal", ("entry-fill-1", "late"))
    assert state.final_target == Fraction(111, 10)
    assert "ENTRY_CENSUS_CHANGED_REQUIRES_RECONCILIATION" not in state.issues


def test_target_rounding_is_exact_cent_ceiling_not_float_rounding(
    state: OcoResearchState,
) -> None:
    state = reconcile_entry_terminal(
        _entry(state, price="9801/1000"), "terminal", ("entry-fill-1",)
    )
    assert state.final_target == Fraction(1141, 100)


def test_limit_violating_entry_is_retained_but_cannot_support_bound_or_allocations(
    state: OcoResearchState,
) -> None:
    state = _entry(state, price="1001/100")
    assert state.owned_quantity == 2
    assert state.average_entry == Fraction(1001, 100)
    assert "ENTRY_LIMIT_EXCEEDED" in state.incidents
    state = reconcile_entry_terminal(state, "terminal", ("entry-fill-1",))
    assert state.final_target is None
    with pytest.raises(OcoResearchError, match="safety incident"):
        _allocate(state)
    _conserved(state)


@pytest.mark.parametrize("price", ["9", "899/100"])
def test_invalid_final_average_topology_is_retained_without_invented_target(
    state: OcoResearchState,
    price: str,
) -> None:
    state = _allocate(_entry(state, price=price))
    state = reconcile_entry_terminal(state, "terminal", ("entry-fill-1",))
    assert state.average_entry == Fraction(price)
    assert state.final_target is None
    assert "FINAL_TARGET_TOPOLOGY_INVALID" in state.incidents
    assert state.reserved_quantity == 2  # No existing stop/group is discarded.
    assert state.authority["safe_close_accepted"] is False
    _conserved(state)


def test_over_exit_keeps_signed_quantity_and_sticky_incident_even_after_late_entry(
    state: OcoResearchState,
) -> None:
    state = _exit(_allocate(_entry(state)), quantity="1", stop=True)
    state = _exit(state, "racing-target", "2")
    assert state.owned_quantity == state.reserved_quantity == -1
    assert state.group_residual("group-1") == -1
    assert set(state.incidents) == {"GROUP_OVER_EXIT", "REVERSE_INVENTORY_OBSERVED"}
    _conserved(state)
    state = _entry(state, "late-entry", "2")
    assert state.owned_quantity == 1
    assert state.reserved_quantity == -1
    assert state.unallocated_quantity == 2
    assert set(state.incidents) == {"GROUP_OVER_EXIT", "REVERSE_INVENTORY_OBSERVED"}
    with pytest.raises(OcoResearchError, match="safety incident"):
        _allocate(state, "new-group", (("late-entry", "2"),))
    _conserved(state)


def test_unknown_or_future_group_cannot_absorb_execution_evidence(
    state: OcoResearchState,
) -> None:
    state = _entry(state)
    with pytest.raises(OcoResearchError, match="previously allocated"):
        _exit(state)


def test_declared_stop_and_state_have_no_mutation_operation(
    state: OcoResearchState,
) -> None:
    with pytest.raises(FrozenInstanceError):
        frozen_field = "stop"
        setattr(state, frozen_field, Fraction(8))
    with pytest.raises(OcoResearchError, match="below entry limit"):
        replace(state, stop=state.limit)


@pytest.mark.parametrize("quantity", [0, -1, 0.5, True])
def test_execution_requires_positive_exact_fraction_not_coercion(
    state: OcoResearchState,
    quantity: object,
) -> None:
    with pytest.raises(OcoResearchError, match="exact Fraction"):
        Execution(
            "bad",
            state.account_ref,
            state.symbol,
            state.entry_scope,
            "ENTRY",
            cast(Fraction, quantity),
            Fraction(10),
        )
