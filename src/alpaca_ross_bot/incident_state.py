"""Pure long-equity incident obligations; proposals never authorize broker writes."""

import hashlib
import json
from dataclasses import asdict, dataclass, fields, replace
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import cast

from .observation_admission import CANDIDATE_VERSION, ObservationContract
from .order_lifecycle import (
    LifecycleState,
    OrderObservation,
    OrderProjection,
    reduce_order,
)
from .paper_order_contract import OrderSnapshot, PaperContractError
from .research_risk_binding import OBSERVATION_IDENTITY
from .strategy_features import FeatureUnavailable, research_candidate_version

RESEARCH_POLICY_VERSION = "HYBRID_500_IEX_INTRADAY_RESEARCH_V1"


class IncidentInputError(ValueError):
    """Invalid evidence cannot clear a veto or authorize an outgoing action."""


class OrderRole(StrEnum):
    ENTRY = "ENTRY"
    STOP = "STOP"
    TARGET = "TARGET"
    CLOSE = "CLOSE"


class ProposalKind(StrEnum):
    PRESERVE_PROTECTION = "PRESERVE_PROTECTION"
    RECONCILE = "RECONCILE"
    CANCEL_ENTRY_REMAINDER = "CANCEL_ENTRY_REMAINDER"
    VERIFY_PROTECTION = "VERIFY_PROTECTION"
    REQUIRE_CLOSE_DISPOSITION = "REQUIRE_CLOSE_DISPOSITION"
    ESCALATE = "ESCALATE"


def _encoded(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _encoded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encoded(item) for item in value]
    return value


def _mapping(value: object, names: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != names:
        raise IncidentInputError("incident record fields are invalid")
    return dict(value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IncidentInputError("incident text is invalid")
    return value


def _flag(value: object) -> bool:
    if type(value) is not bool:
        raise IncidentInputError("incident fact must be an explicit boolean")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise IncidentInputError("incident list is invalid")
    return value


def _watermark(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise IncidentInputError("incident watermark is invalid")
    return value


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise IncidentInputError("incident quantity is invalid")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise IncidentInputError("incident quantity is invalid") from exc
    if not result.is_finite():
        raise IncidentInputError("incident quantity is not finite")
    return result


def _reference(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _sum(values: list[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = max(
            28,
            sum(
                len(item.as_tuple().digits) + abs(int(item.as_tuple().exponent))
                for item in values
            )
            + 3,
        )
        return sum(values, Decimal(0))


@dataclass(frozen=True)
class IncidentPolicy:
    account_ref: str
    version: str
    scope_id: str
    symbol: str
    rule_refs: tuple[str, ...]
    evidence_ref: str
    runtime_risk_accepted: bool = False
    protection_contract_accepted: bool = False
    risk_acceptance_ref: str | None = None
    protection_acceptance_ref: str | None = None
    observation_contract: ObservationContract | None = None

    def to_dict(self) -> dict[str, object]:
        item = asdict(self)
        del item["observation_contract"]
        if self.observation_contract is not None or self.version == CANDIDATE_VERSION:
            _validate_policy(self)
            item["candidate_binding"] = dict(OBSERVATION_IDENTITY)
        return cast(dict[str, object], _encoded(item))

    @classmethod
    def from_dict(cls, value: object) -> "IncidentPolicy":
        names = {f.name for f in fields(cls)} - {"observation_contract"}
        bound = isinstance(value, dict) and "candidate_binding" in value
        item = _mapping(value, names | ({"candidate_binding"} if bound else set()))
        contract = None
        if bound:
            identity = item["candidate_binding"]
            if (
                not isinstance(identity, dict)
                or identity != OBSERVATION_IDENTITY
                or identity.get("source_admission_qualified") is not False
            ):
                raise IncidentInputError("incident candidate/data/risk binding differs")
            contract = ObservationContract()
        result = cls(
            _text(item["account_ref"]),
            _text(item["version"]),
            _text(item["scope_id"]),
            _text(item["symbol"]),
            tuple(_text(ref) for ref in _list(item["rule_refs"])),
            _text(item["evidence_ref"]),
            _flag(item["runtime_risk_accepted"]),
            _flag(item["protection_contract_accepted"]),
            None
            if item["risk_acceptance_ref"] is None
            else _text(item["risk_acceptance_ref"]),
            None
            if item["protection_acceptance_ref"] is None
            else _text(item["protection_acceptance_ref"]),
            contract,
        )
        _validate_policy(result)
        return result


@dataclass(frozen=True)
class IncidentOrder:
    order: OrderProjection
    role: OrderRole
    protection_verified: bool = False
    cancel_requested: bool = False

    @classmethod
    def from_dict(cls, value: object) -> "IncidentOrder":
        item = _mapping(value, {f.name for f in fields(cls)})
        raw = item["order"]
        if not isinstance(raw, dict):
            raise IncidentInputError("incident order is invalid")
        try:
            return cls(
                OrderProjection.from_dict(raw),
                OrderRole(_text(item["role"])),
                _flag(item["protection_verified"]),
                _flag(item["cancel_requested"]),
            )
        except (ValueError, TypeError) as exc:
            raise IncidentInputError("incident order is invalid") from exc


@dataclass(frozen=True)
class IncidentObservation:
    event_id: str
    account_ref: str
    policy_version: str
    scope_id: str
    evidence_ref: str
    watermark: int
    baseline_quantity: Decimal
    baseline_verified: bool
    position_quantity: Decimal | None
    orders: tuple[IncidentOrder, ...]
    reconciliation_verified: bool = False
    data_verified: bool = False
    durable: bool = False
    stream_connected: bool = False
    outcome_unknown: bool = False
    daily_stop: bool = False
    closing_due: bool = False
    recovery_verified: bool = False

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], _encoded(asdict(self)))

    @classmethod
    def from_dict(cls, value: object) -> "IncidentObservation":
        item = _mapping(value, {f.name for f in fields(cls)})
        position = item["position_quantity"]
        result = cls(
            _text(item["event_id"]),
            _text(item["account_ref"]),
            _text(item["policy_version"]),
            _text(item["scope_id"]),
            _text(item["evidence_ref"]),
            _watermark(item["watermark"]),
            _decimal(item["baseline_quantity"]),
            _flag(item["baseline_verified"]),
            None if position is None else _decimal(position),
            tuple(IncidentOrder.from_dict(row) for row in _list(item["orders"])),
            _flag(item["reconciliation_verified"]),
            _flag(item["data_verified"]),
            _flag(item["durable"]),
            _flag(item["stream_connected"]),
            _flag(item["outcome_unknown"]),
            _flag(item["daily_stop"]),
            _flag(item["closing_due"]),
            _flag(item["recovery_verified"]),
        )
        _validate_observation(result)
        return result


@dataclass(frozen=True)
class IncidentProposal:
    kind: ProposalKind
    order_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IncidentProjection:
    policy: IncidentPolicy
    baseline_quantity: Decimal
    baseline_verified: bool
    orders: tuple[IncidentOrder, ...]
    watermark: int
    seen_events: tuple[tuple[str, str], ...]
    observed_position: Decimal | None
    daily_stop_latched: bool
    reasons: tuple[str, ...]
    proposals: tuple[IncidentProposal, ...]

    @property
    def expected_quantity(self) -> Decimal | None:
        if not self.baseline_verified:
            return None
        return _sum(
            [self.baseline_quantity]
            + [
                item.order.filled_quantity
                if item.role == OrderRole.ENTRY
                else item.order.filled_quantity.copy_negate()
                for item in self.orders
            ]
        )

    @property
    def preserve_protection_ids(self) -> tuple[str, ...]:
        return tuple(
            item.order.client_order_id
            for item in self.orders
            if item.role == OrderRole.STOP
            and item.protection_verified
            and _working(item.order)
        )

    @property
    def pending_entry_ids(self) -> tuple[str, ...]:
        return tuple(
            item.order.client_order_id
            for item in self.orders
            if item.role == OrderRole.ENTRY and _working(item.order)
        )

    @property
    def pending_cancel_ids(self) -> tuple[str, ...]:
        return tuple(
            cid
            for proposal in self.proposals
            if proposal.kind == ProposalKind.CANCEL_ENTRY_REMAINDER
            for cid in proposal.order_ids
        )

    @property
    def pending_exit_ids(self) -> tuple[str, ...]:
        return tuple(
            item.order.client_order_id
            for item in self.orders
            if item.role != OrderRole.ENTRY and _working(item.order)
        )

    @property
    def new_exposure_allowed(self) -> bool:
        return (
            not self.reasons
            and not self.daily_stop_latched
            and self.policy.runtime_risk_accepted
            and self.policy.protection_contract_accepted
            and self.policy.observation_contract is None
            and self.expected_quantity == self.observed_position == 0
            and not any(_working(item.order) for item in self.orders)
        )

    @property
    def state(self) -> str:
        if any(
            item in self.reasons
            for item in ("REVERSE_POSITION", "OVER_EXIT", "DOUBLE_EXIT_FILL")
        ):
            return "REVERSE_OR_DOUBLE_EXIT"
        if "UNPROTECTED" in self.reasons:
            return "PROTECTION_UNCONFIRMED"
        if self.reasons:
            return "RECOVERY_REQUIRED"
        return "PROTECTED" if self.expected_quantity else "FLAT_RECONCILED"

    def to_dict(self) -> dict[str, object]:
        item = asdict(self)
        item["policy"] = self.policy.to_dict()
        return cast(dict[str, object], _encoded(item))

    @classmethod
    def from_dict(cls, value: object) -> "IncidentProjection":
        item = _mapping(value, {f.name for f in fields(cls)})
        seen: list[tuple[str, str]] = []
        for raw in _list(item["seen_events"]):
            pair = _list(raw)
            if len(pair) != 2 or not _reference("sha256:" + _text(pair[1])):
                raise IncidentInputError("incident event fingerprint is invalid")
            seen.append((_text(pair[0]), _text(pair[1])))
        if not seen or len({row[0] for row in seen}) != len(seen):
            raise IncidentInputError("incident event identities are invalid")
        proposals: list[IncidentProposal] = []
        for raw in _list(item["proposals"]):
            row = _mapping(raw, {"kind", "order_ids"})
            proposals.append(
                IncidentProposal(
                    ProposalKind(_text(row["kind"])),
                    tuple(_text(cid) for cid in _list(row["order_ids"])),
                )
            )
        position = item["observed_position"]
        baseline = _decimal(item["baseline_quantity"])
        if baseline < 0:
            raise IncidentInputError("baseline quantity is invalid")
        result = cls(
            IncidentPolicy.from_dict(item["policy"]),
            baseline,
            _flag(item["baseline_verified"]),
            tuple(IncidentOrder.from_dict(row) for row in _list(item["orders"])),
            _watermark(item["watermark"]),
            tuple(seen),
            None if position is None else _decimal(position),
            _flag(item["daily_stop_latched"]),
            tuple(_text(reason) for reason in _list(item["reasons"])),
            tuple(proposals),
        )
        _validate_observation(
            IncidentObservation(
                "cached-state",
                result.policy.account_ref,
                result.policy.version,
                result.policy.scope_id,
                result.policy.evidence_ref,
                result.watermark,
                result.baseline_quantity,
                result.baseline_verified,
                result.observed_position,
                result.orders,
            )
        )
        return result


def _validate_policy(policy: IncidentPolicy) -> None:
    if (
        any(
            not isinstance(value, str) or not value.strip()
            for value in (
                policy.account_ref,
                policy.version,
                policy.scope_id,
                policy.symbol,
            )
        )
        or not policy.rule_refs
        or not isinstance(policy.rule_refs, tuple)
        or any(not isinstance(ref, str) or not ref.strip() for ref in policy.rule_refs)
        or len(set(policy.rule_refs)) != len(policy.rule_refs)
        or not _reference(policy.evidence_ref)
    ):
        raise IncidentInputError("incident policy identity/evidence is invalid")
    if policy.observation_contract is not None:
        try:
            version = research_candidate_version(policy.observation_contract)
        except FeatureUnavailable as exc:
            raise IncidentInputError(
                "pinned incident observation contract required"
            ) from exc
        if (
            policy.version != version
            or policy.runtime_risk_accepted is not False
            or policy.protection_contract_accepted is not False
        ):
            raise IncidentInputError(
                "incident research candidate cannot grant acceptance"
            )
    elif policy.version == CANDIDATE_VERSION:
        raise IncidentInputError(
            "explicit pinned incident observation contract required"
        )
    for accepted, reference in (
        (policy.runtime_risk_accepted, policy.risk_acceptance_ref),
        (policy.protection_contract_accepted, policy.protection_acceptance_ref),
    ):
        if (
            type(accepted) is not bool
            or (accepted and reference is None)
            or (reference is not None and not _reference(reference))
        ):
            raise IncidentInputError("runtime acceptance lacks immutable evidence")


def _validate_observation(observation: IncidentObservation) -> None:
    if not _reference(observation.evidence_ref):
        raise IncidentInputError("incident evidence reference is invalid")
    for text in (
        observation.event_id,
        observation.account_ref,
        observation.policy_version,
        observation.scope_id,
    ):
        _text(text)
    _watermark(observation.watermark)
    if (
        not isinstance(observation.baseline_quantity, Decimal)
        or _decimal(observation.baseline_quantity) < 0
    ):
        raise IncidentInputError("baseline quantity is invalid")
    if observation.position_quantity is not None:
        if not isinstance(observation.position_quantity, Decimal):
            raise IncidentInputError("observed position is invalid")
        _decimal(observation.position_quantity)
    if not isinstance(observation.orders, tuple) or any(
        not isinstance(item, IncidentOrder)
        or not isinstance(item.order, OrderProjection)
        for item in observation.orders
    ):
        raise IncidentInputError("incident orders are invalid")
    for field in fields(observation):
        if field.name.endswith("verified") or field.name in {
            "durable",
            "stream_connected",
            "outcome_unknown",
            "daily_stop",
            "closing_due",
        }:
            _flag(getattr(observation, field.name))


def _working(order: OrderProjection) -> bool:
    return order.state not in {
        LifecycleState.FILLED,
        LifecycleState.CANCELED,
        LifecycleState.EXPIRED,
        LifecycleState.REJECTED,
    }


_STICKY = {
    "EVENT_ID_CONFLICT",
    "IDENTITY_CONFLICT",
    "BASELINE_CONFLICT",
    "ORDER_CONFLICT",
    "DOUBLE_EXIT_FILL",
    "REVERSE_POSITION",
    "OVER_EXIT",
    "INVALID_OBSERVATION",
}


def _veto(
    previous: IncidentProjection, reason: str, event_id: str, digest: str
) -> IncidentProjection:
    seen = dict(previous.seen_events)
    seen.setdefault(event_id, digest)
    proposals = previous.proposals + (IncidentProposal(ProposalKind.RECONCILE),)
    if reason in _STICKY:
        proposals += (IncidentProposal(ProposalKind.ESCALATE),)
    return replace(
        previous,
        seen_events=tuple(sorted(seen.items())),
        reasons=tuple(sorted(set(previous.reasons) | {reason})),
        proposals=tuple(dict.fromkeys(proposals)),
    )


def reduce_incident(
    previous: IncidentProjection | None,
    policy: IncidentPolicy,
    observation: IncidentObservation,
) -> IncidentProjection:
    """Reduce cumulative evidence, never dispatch; caller must durably commit first.

    Scope is one versioned long-equity inventory/round-trip. Baseline is explicit;
    this component never infers flat, OCO atomicity, or cancel/404 finality.
    """
    _validate_policy(policy)
    _validate_observation(observation)
    if previous is not None and previous.policy != policy:
        raise IncidentInputError("incident policy migration requires a separate gate")
    digest = hashlib.sha256(
        json.dumps(
            observation.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    reasons = set(previous.reasons) & _STICKY if previous else set()
    seen = dict(previous.seen_events) if previous else {}
    if observation.event_id in seen:
        if seen[observation.event_id] == digest:
            assert previous is not None
            return previous
        assert previous is not None
        return _veto(previous, "EVENT_ID_CONFLICT", observation.event_id, digest)
    baseline = observation.baseline_quantity
    if (
        observation.account_ref != policy.account_ref
        or observation.policy_version != policy.version
        or observation.scope_id != policy.scope_id
    ):
        if previous is None:
            raise IncidentInputError("incident evidence identity differs")
        return _veto(previous, "IDENTITY_CONFLICT", observation.event_id, digest)
    if previous and observation.watermark <= previous.watermark:
        return _veto(previous, "STALE_OBSERVATION", observation.event_id, digest)
    if previous and baseline != previous.baseline_quantity:
        reasons.add("BASELINE_CONFLICT")
        baseline = previous.baseline_quantity
    orders = (
        {item.order.client_order_id: item for item in previous.orders}
        if previous
        else {}
    )
    incoming_ids: set[str] = set()
    unverified_protection: set[str] = set()
    for item in observation.orders:
        order = item.order
        try:
            OrderProjection.from_dict(order.to_dict())
            if (
                not isinstance(item.role, OrderRole)
                or type(item.protection_verified) is not bool
                or type(item.cancel_requested) is not bool
                or not _reference(order.evidence_ref)
                or order.last_time_ns < 0
                or not isinstance(order.quantity, Decimal)
                or not isinstance(order.filled_quantity, Decimal)
                or (item.protection_verified and item.role != OrderRole.STOP)
            ):
                raise IncidentInputError("incident order metadata is invalid")
        except (PaperContractError, IncidentInputError):
            reasons.add("INVALID_OBSERVATION")
            continue
        cid = order.client_order_id
        if (
            cid in incoming_ids
            or order.account_ref != policy.account_ref
            or order.symbol != policy.symbol
            or order.side != ("buy" if item.role == OrderRole.ENTRY else "sell")
            or any(
                other.order.broker_order_id == order.broker_order_id
                and other.order.client_order_id != cid
                for other in orders.values()
            )
        ):
            reasons.add("IDENTITY_CONFLICT")
            continue
        incoming_ids.add(cid)
        old = orders.get(cid)
        if old and old.role != item.role:
            reasons.add("IDENTITY_CONFLICT")
            continue
        if old and old.protection_verified and not item.protection_verified:
            unverified_protection.add(cid)
            reasons.add("PROTECTION_VERIFICATION_LOST")
        reduced = reduce_order(
            old.order if old else None,
            OrderObservation(
                order.account_ref,
                OrderSnapshot(
                    order.broker_order_id,
                    cid,
                    order.raw_status,
                    order.quantity,
                    order.filled_quantity,
                ),
                order.symbol,
                order.side,
                order.last_time_ns,
                "REST" if observation.reconciliation_verified else "WS",
                order.evidence_ref,
            ),
            authoritative=observation.reconciliation_verified,
        )
        if order.issues or reduced.issues:
            reasons.add("ORDER_CONFLICT")
        orders[cid] = IncidentOrder(
            reduced,
            item.role,
            item.protection_verified or bool(old and old.protection_verified),
            item.cancel_requested or bool(old and old.cancel_requested),
        )
    if set(orders) - incoming_ids:
        reasons.add("OWNED_ORDER_EVIDENCE_MISSING")
    seen[observation.event_id] = digest
    baseline_verified = observation.baseline_verified or bool(
        previous and previous.baseline_verified
    )
    latched = observation.daily_stop or bool(previous and previous.daily_stop_latched)
    state = IncidentProjection(
        policy,
        baseline,
        baseline_verified,
        tuple(orders[key] for key in sorted(orders)),
        observation.watermark,
        tuple(sorted(seen.items())),
        observation.position_quantity,
        latched,
        (),
        (),
    )
    expected = state.expected_quantity
    bound = (
        policy.version == RESEARCH_POLICY_VERSION
        or policy.observation_contract is not None
    ) and {"P11", "P12", "P13"} <= set(policy.rule_refs)
    if not bound:
        reasons.add("RESEARCH_POLICY_UNBOUND")
    if policy.observation_contract is not None:
        reasons.add("SOURCE_ADMISSION_UNQUALIFIED")
    for verified, reason in (
        (baseline_verified, "BASELINE_UNVERIFIED"),
        (observation.reconciliation_verified, "RECONCILIATION_REQUIRED"),
        (observation.data_verified, "DATA_UNVERIFIED"),
        (observation.durable, "DURABILITY_UNCONFIRMED"),
        (observation.stream_connected, "STREAM_DISCONNECTED"),
        (policy.runtime_risk_accepted, "RUNTIME_RISK_GATE_UNACCEPTED"),
        (policy.protection_contract_accepted, "PROTECTION_BROKER_GATE_UNACCEPTED"),
    ):
        if not verified:
            reasons.add(reason)
    if observation.outcome_unknown:
        reasons.add("UNKNOWN_OUTCOME")
    if latched:
        reasons.add("DAILY_STOP_LATCHED")
    if observation.closing_due:
        reasons.add("CLOSING_DUE")
    if (
        expected is None
        or observation.position_quantity is None
        or expected != observation.position_quantity
    ):
        reasons.add("POSITION_UNRECONCILED")
    if expected is not None and expected < 0:
        reasons.add("OVER_EXIT")
    if observation.position_quantity is not None and observation.position_quantity < 0:
        reasons.add("REVERSE_POSITION")
    exits = [item for item in state.orders if item.role != OrderRole.ENTRY]
    if (
        bound
        and any(
            item.role == OrderRole.TARGET and _working(item.order) for item in exits
        )
        and state.pending_entry_ids
    ):
        reasons.add("TARGET_BEFORE_ENTRY_TERMINAL")
    multiple_fills = len([item for item in exits if item.order.filled_quantity > 0]) > 1
    if multiple_fills and (
        (expected is not None and expected < 0)
        or (
            observation.position_quantity is not None
            and observation.position_quantity < 0
        )
    ):
        reasons.add("DOUBLE_EXIT_FILL")
    if expected == 0 and any(_working(item.order) for item in exits):
        reasons.add("UNRESOLVED_EXIT_WHILE_FLAT")
    coverage = _sum(
        [
            _sum([item.order.quantity, item.order.filled_quantity.copy_negate()])
            for item in exits
            if item.role == OrderRole.STOP
            and item.protection_verified
            and not item.cancel_requested
            and item.order.client_order_id not in unverified_protection
            and item.order.state in {LifecycleState.ACCEPTED, LifecycleState.PARTIAL}
        ]
    )
    if expected is not None and expected > 0 and coverage < expected:
        reasons.add("UNPROTECTED")
    if expected is not None and coverage > max(Decimal(0), expected):
        reasons.add("EXCESS_PROTECTION_QUANTITY")
    recovery = (
        observation.recovery_verified
        and observation.reconciliation_verified
        and observation.data_verified
        and observation.durable
        and observation.stream_connected
        and baseline_verified
        and expected == observation.position_quantity
        and not observation.outcome_unknown
        and not reasons
        & (
            _STICKY
            | {
                "POSITION_UNRECONCILED",
                "OWNED_ORDER_EVIDENCE_MISSING",
                "PROTECTION_VERIFICATION_LOST",
                "UNPROTECTED",
                "EXCESS_PROTECTION_QUANTITY",
                "TARGET_BEFORE_ENTRY_TERMINAL",
                "UNRESOLVED_EXIT_WHILE_FLAT",
            }
        )
    )
    if previous and not recovery:
        reasons.update(set(previous.reasons) - _STICKY)
    proposals = (
        [
            IncidentProposal(
                ProposalKind.PRESERVE_PROTECTION, state.preserve_protection_ids
            )
        ]
        if state.preserve_protection_ids
        else []
    )
    entry_ids = tuple(
        item.order.client_order_id
        for item in state.orders
        if item.role == OrderRole.ENTRY
        and _working(item.order)
        and item.order.quantity > item.order.filled_quantity
        and bound
        and (
            item.cancel_requested
            or reasons
            or any(
                other.role == OrderRole.ENTRY and other.order.filled_quantity > 0
                for other in state.orders
            )
        )
    )
    if entry_ids:
        proposals.append(
            IncidentProposal(ProposalKind.CANCEL_ENTRY_REMAINDER, entry_ids)
        )
    if "UNPROTECTED" in reasons:
        proposals.append(IncidentProposal(ProposalKind.VERIFY_PROTECTION))
    if bound and observation.closing_due and expected != 0:
        proposals.append(IncidentProposal(ProposalKind.REQUIRE_CLOSE_DISPOSITION))
    if reasons:
        proposals.append(IncidentProposal(ProposalKind.RECONCILE))
    if reasons & (_STICKY | {"UNPROTECTED", "EXCESS_PROTECTION_QUANTITY"}):
        proposals.append(IncidentProposal(ProposalKind.ESCALATE))
    return replace(state, reasons=tuple(sorted(reasons)), proposals=tuple(proposals))
