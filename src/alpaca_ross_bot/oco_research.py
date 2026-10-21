"""Declared OCO-first ownership model; no source acceptance or broker authority.

Execution tuple order is the declared observation order, not proof of venue order.
Reservations never establish live stop coverage. No operation releases an unknown
reservation, cancels a stop, submits an order, or supplies another risk budget.
"""

import hashlib
import json
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Literal

VERSION = "OCO_FIRST_EXECUTION_RESEARCH_V1"
CANDIDATE = "HYBRID_500_IEX_OCO_FIRST_RESEARCH_V1"
PARENT = "HYBRID_500_IEX_OBSERVATION_AGE_RESEARCH_V1"
MANIFEST_SHA256 = "fa6e29ede5a92428308ad19ba794be5e6e3f0b2a3c216818a54f3ffc9a112d80"
PARENT_SHA256 = "f5439d49ac9354abbe56b7b1fcb603c915942dee647efedebf48060e63b49e8c"
_ROOT = Path(__file__).resolve().parents[2]


class OcoResearchError(ValueError):
    """Invalid declared identity, allocation, or research contract."""


def _text(value: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise OcoResearchError("nonempty exact research identity required")


def _positive(value: Fraction) -> None:
    if type(value) is not Fraction or value <= 0:
        raise OcoResearchError("positive exact Fraction required")


def _cent(value: Fraction) -> Fraction:
    scaled = value * 100
    return Fraction(-(-scaled.numerator // scaled.denominator), 100)


@dataclass(frozen=True)
class OcoResearchContract:
    manifest_bytes: bytes = field(repr=False)
    parent_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        for raw, expected in (
            (self.manifest_bytes, MANIFEST_SHA256),
            (self.parent_bytes, PARENT_SHA256),
        ):
            if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != expected:
                raise OcoResearchError("exact pinned manifest and parent required")
        manifest = json.loads(self.manifest_bytes)
        parent = json.loads(self.parent_bytes)
        if (
            manifest["version"] != VERSION
            or manifest["candidate_version"] != CANDIDATE
            or manifest["parent_candidate_version"] != PARENT
            or manifest["parent_contract_sha256"] != PARENT_SHA256
            or parent["candidate_version_id"] != PARENT
            or manifest["authority"]["research_semantics_accepted"] is not True
            or any(
                value is not False
                for key, value in manifest["authority"].items()
                if key != "research_semantics_accepted"
            )
        ):
            raise OcoResearchError("incompatible pinned research identity")


def load_oco_research_contract(
    manifest_path: Path = _ROOT / "evidence/oco-first-execution-research-v1.json",
    parent_path: Path = _ROOT
    / "evidence/iex-snapshot-observation-age-research-v1.json",
) -> OcoResearchContract:
    return OcoResearchContract(manifest_path.read_bytes(), parent_path.read_bytes())


@dataclass(frozen=True)
class Execution:
    execution_id: str
    account_ref: str
    symbol: str
    entry_scope: str
    role: Literal["ENTRY", "TARGET", "STOP"]
    quantity: Fraction
    price: Fraction
    group_id: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.execution_id,
            self.account_ref,
            self.symbol,
            self.entry_scope,
        ):
            _text(value)
        _positive(self.quantity)
        _positive(self.price)
        if self.role not in {"ENTRY", "TARGET", "STOP"}:
            raise OcoResearchError("entry or bound stop/target execution required")
        if self.role == "ENTRY":
            if self.group_id is not None:
                raise OcoResearchError("entry execution cannot belong to an exit group")
        elif self.group_id is None:
            raise OcoResearchError("exit execution requires its declared group")
        else:
            _text(self.group_id)


@dataclass(frozen=True)
class LotPortion:
    entry_execution_id: str
    quantity: Fraction

    def __post_init__(self) -> None:
        _text(self.entry_execution_id)
        _positive(self.quantity)


@dataclass(frozen=True)
class GroupAllocation:
    group_id: str
    portions: tuple[LotPortion, ...]
    execution_count: int
    acknowledgement_id: str | None = None

    @property
    def quantity(self) -> Fraction:
        return sum((part.quantity for part in self.portions), Fraction(0))

    @property
    def acknowledgement(self) -> str:
        return "UNKNOWN" if self.acknowledgement_id is None else "DECLARED_ACKNOWLEDGED"


@dataclass(frozen=True)
class TerminalCensus:
    census_id: str
    execution_count: int
    entry_execution_ids: tuple[str, ...]


@dataclass(frozen=True)
class OcoResearchState:
    contract: OcoResearchContract
    account_ref: str
    symbol: str
    entry_scope: str
    limit: Fraction
    stop: Fraction
    executions: tuple[Execution, ...] = ()
    groups: tuple[GroupAllocation, ...] = ()
    terminal_censuses: tuple[TerminalCensus, ...] = ()

    def __post_init__(self) -> None:
        if type(self.contract) is not OcoResearchContract:
            raise OcoResearchError(
                "explicit pinned OCO-first research contract required"
            )
        self.contract.__post_init__()
        for value in (self.account_ref, self.symbol, self.entry_scope):
            _text(value)
        _positive(self.limit)
        _positive(self.stop)
        if self.stop >= self.limit:
            raise OcoResearchError("parent frozen stop must be below entry limit")
        if any((value * 100).denominator != 1 for value in (self.limit, self.stop)):
            raise OcoResearchError("parent limit and stop must be exact whole cents")
        if any(
            type(rows) is not tuple
            for rows in (self.executions, self.groups, self.terminal_censuses)
        ):
            raise OcoResearchError("immutable declared tuples required")
        ids: set[str] = set()
        for execution in self.executions:
            if type(execution) is not Execution:
                raise OcoResearchError("declared execution required")
            execution.__post_init__()
            if (execution.account_ref, execution.symbol, execution.entry_scope) != (
                self.account_ref,
                self.symbol,
                self.entry_scope,
            ) or execution.execution_id in ids:
                raise OcoResearchError("execution scope or identity conflicts")
            ids.add(execution.execution_id)
        self._validate_groups()
        census_ids: set[str] = set()
        previous_count = 0
        for census in self.terminal_censuses:
            if type(census) is not TerminalCensus:
                raise OcoResearchError("exact terminal census required")
            _text(census.census_id)
            if (
                census.census_id in census_ids
                or type(census.execution_count) is not int
                or not previous_count <= census.execution_count <= len(self.executions)
                or census.entry_execution_ids != self._entry_ids(census.execution_count)
            ):
                raise OcoResearchError(
                    "terminal census must contain exactly all entry IDs"
                )
            census_ids.add(census.census_id)
            previous_count = census.execution_count

    def _entry_ids(self, count: int) -> tuple[str, ...]:
        return tuple(
            sorted(
                execution.execution_id
                for execution in self.executions[:count]
                if execution.role == "ENTRY"
            )
        )

    def _validate_groups(self) -> None:
        group_ids: set[str] = set()
        acknowledgements: set[str] = set()
        assigned: dict[str, Fraction] = {}
        previous_count = 0
        for group in self.groups:
            if type(group) is not GroupAllocation:
                raise OcoResearchError("declared group allocation required")
            _text(group.group_id)
            if (
                group.group_id in group_ids
                or type(group.execution_count) is not int
                or not previous_count <= group.execution_count <= len(self.executions)
                or type(group.portions) is not tuple
                or not group.portions
                or any(type(part) is not LotPortion for part in group.portions)
            ):
                raise OcoResearchError("group allocation identity/order conflicts")
            if group.quantity.denominator != 1:
                raise OcoResearchError(
                    "fractional group residual unqualified; no rounding"
                )
            lots = {
                execution.execution_id: execution.quantity
                for execution in self.executions[: group.execution_count]
                if execution.role == "ENTRY"
            }
            portion_ids: set[str] = set()
            for part in group.portions:
                part.__post_init__()
                assigned[part.entry_execution_id] = (
                    assigned.get(part.entry_execution_id, Fraction(0)) + part.quantity
                )
                if (
                    part.entry_execution_id in portion_ids
                    or part.entry_execution_id not in lots
                    or assigned[part.entry_execution_id] > lots[part.entry_execution_id]
                ):
                    raise OcoResearchError(
                        "lot portions exceed disjoint owned allocation"
                    )
                portion_ids.add(part.entry_execution_id)
            if group.acknowledgement_id is not None:
                _text(group.acknowledgement_id)
                if group.acknowledgement_id in acknowledgements:
                    raise OcoResearchError("acknowledgement identity conflicts")
                acknowledgements.add(group.acknowledgement_id)
            group_ids.add(group.group_id)
            previous_count = group.execution_count
        by_id = {group.group_id: group for group in self.groups}
        for index, execution in enumerate(self.executions):
            if execution.role != "ENTRY" and (
                execution.group_id not in by_id
                or by_id[execution.group_id].execution_count > index
            ):
                raise OcoResearchError("exit requires a previously allocated group")

    @property
    def entry_quantity(self) -> Fraction:
        return sum(
            (e.quantity for e in self.executions if e.role == "ENTRY"), Fraction(0)
        )

    @property
    def exit_quantity(self) -> Fraction:
        return sum(
            (e.quantity for e in self.executions if e.role != "ENTRY"), Fraction(0)
        )

    @property
    def owned_quantity(self) -> Fraction:
        return self.entry_quantity - self.exit_quantity

    def group_residual(self, group_id: str) -> Fraction:
        group = next((g for g in self.groups if g.group_id == group_id), None)
        if group is None:
            raise OcoResearchError("unknown group")
        return group.quantity - sum(
            (e.quantity for e in self.executions if e.group_id == group_id), Fraction(0)
        )

    @property
    def reserved_quantity(self) -> Fraction:
        return sum((self.group_residual(g.group_id) for g in self.groups), Fraction(0))

    @property
    def unallocated_quantity(self) -> Fraction:
        return self.entry_quantity - sum((g.quantity for g in self.groups), Fraction(0))

    @property
    def average_entry(self) -> Fraction | None:
        if not self.entry_quantity:
            return None
        return (
            sum(
                (e.quantity * e.price for e in self.executions if e.role == "ENTRY"),
                Fraction(0),
            )
            / self.entry_quantity
        )

    @property
    def entry_terminal_reconciled(self) -> bool:
        return bool(self.terminal_censuses) and (
            self.terminal_censuses[-1].entry_execution_ids
            == self._entry_ids(len(self.executions))
        )

    @property
    def provisional_target(self) -> Fraction:
        return _cent(3 * self.limit - 2 * self.stop)

    @property
    def incidents(self) -> tuple[str, ...]:
        reasons: set[str] = set()
        if any(e.role == "ENTRY" and e.price > self.limit for e in self.executions):
            reasons.add("ENTRY_LIMIT_EXCEEDED")
        running = Fraction(0)
        for execution in self.executions:
            running += (
                execution.quantity if execution.role == "ENTRY" else -execution.quantity
            )
            if running < 0:
                reasons.add("REVERSE_INVENTORY_OBSERVED")
        if any(self.group_residual(g.group_id) < 0 for g in self.groups):
            reasons.add("GROUP_OVER_EXIT")
        for census in self.terminal_censuses:
            entries = [
                e
                for e in self.executions[: census.execution_count]
                if e.role == "ENTRY"
            ]
            quantity = sum((e.quantity for e in entries), Fraction(0))
            cost = sum((e.quantity * e.price for e in entries), Fraction(0))
            if quantity and cost / quantity <= self.stop:
                reasons.add("FINAL_TARGET_TOPOLOGY_INVALID")
        return tuple(sorted(reasons))

    @property
    def issues(self) -> tuple[str, ...]:
        reasons = set(self.incidents)
        if self.terminal_censuses and not self.entry_terminal_reconciled:
            reasons.add("ENTRY_CENSUS_CHANGED_REQUIRES_RECONCILIATION")
        if any(
            q.denominator != 1
            for q in (
                self.owned_quantity,
                self.unallocated_quantity,
                self.reserved_quantity,
                *(self.group_residual(group.group_id) for group in self.groups),
            )
        ):
            reasons.add("FRACTIONAL_RESIDUAL_UNQUALIFIED")
        return tuple(sorted(reasons))

    @property
    def final_target(self) -> Fraction | None:
        average = self.average_entry
        if not self.entry_terminal_reconciled or self.incidents or average is None:
            return None
        return _cent(3 * average - 2 * self.stop)

    @property
    def authority(self) -> dict[str, bool]:
        return {
            "research_semantics_accepted": True,
            "source_adapter_qualified": False,
            "actual_source_qualification_accepted": False,
            "entry_risk_binding_accepted": False,
            "protection_coverage_verified": False,
            "first_fill_protection_verified": False,
            "broker_group_reservations_verified": False,
            "sibling_stop_continuity_verified": False,
            "safe_close_accepted": False,
            "release_accepted": False,
            "formal_paper_authorized": False,
            "runtime_authorized": False,
            "order_authority": False,
        }


def observe_execution(
    state: OcoResearchState, execution: Execution
) -> OcoResearchState:
    for existing in state.executions:
        if existing.execution_id == execution.execution_id:
            if existing == execution:
                return state
            raise OcoResearchError("conflicting execution identity")
    return replace(state, executions=state.executions + (execution,))


def allocate_group(
    state: OcoResearchState, group_id: str, portions: tuple[LotPortion, ...]
) -> OcoResearchState:
    for group in state.groups:
        if group.group_id == group_id:
            if group.portions == portions:
                return state
            raise OcoResearchError("conflicting group identity")
    if state.incidents or (
        state.terminal_censuses and not state.entry_terminal_reconciled
    ):
        raise OcoResearchError(
            "safety incident or unreconciled entry census blocks allocation"
        )
    group = GroupAllocation(group_id, portions, len(state.executions))
    return replace(state, groups=state.groups + (group,))


def acknowledge_group(
    state: OcoResearchState, group_id: str, acknowledgement_id: str
) -> OcoResearchState:
    _text(acknowledgement_id)
    group = next((g for g in state.groups if g.group_id == group_id), None)
    if group is None:
        raise OcoResearchError("unknown group")
    if group.acknowledgement_id is not None:
        if group.acknowledgement_id == acknowledgement_id:
            return state
        raise OcoResearchError("conflicting acknowledgement identity")
    return replace(
        state,
        groups=tuple(
            replace(g, acknowledgement_id=acknowledgement_id)
            if g.group_id == group_id
            else g
            for g in state.groups
        ),
    )


def reconcile_entry_terminal(
    state: OcoResearchState, census_id: str, entry_execution_ids: tuple[str, ...]
) -> OcoResearchState:
    for census in state.terminal_censuses:
        if census.census_id == census_id:
            if census.entry_execution_ids == entry_execution_ids:
                return state
            raise OcoResearchError("conflicting terminal census identity")
    census = TerminalCensus(census_id, len(state.executions), entry_execution_ids)
    return replace(state, terminal_censuses=state.terminal_censuses + (census,))
