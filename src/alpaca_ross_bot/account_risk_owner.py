"""Source-bound daily accounting and serialized conservative risk reservations.

Declares risk plans, never strategy eligibility or broker dispatch authority.
The legacy RiskJournal remains an isolated offline-fixture arithmetic seam.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .crossed_stop_contract import CrossedStopContract, load_crossed_stop_contract
from .entry_strategy import EntryDecision
from .equity_fees import planned_charge_bounds
from .journal import Journal, JournalError
from .native_risk_registry import POLICY as NATIVE_POLICY
from .native_risk_registry import derive_native_registry
from .observation_admission import ObservationContract
from .oco_research import OcoResearchContract
from .order_lifecycle import OrderProjection
from .order_reconciliation import OrderReconciler
from .owned_equity_ledger import audit_owned_equity
from .paper_order_contract import decode_broker_json, validate_saved_request
from .research_risk_binding import (
    OCO_EXECUTION_IDENTITY,
    RESEARCH_BINDING,
    _declared_research_plan,
    _research_declaration,
)
from .risk_baseline import read_baseline, session_open_from_evidence
from .risk_evidence_request import IncidentRiskRequest
from .risk_input_evidence import inspect_risk_inputs
from .risk_ledger_audit import _activities
from .strategy_risk import VERSION

SOURCE = "ALPACA_ACCOUNT_RISK"
TERMINAL = {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}


class _KnownRiskConflict(Exception):
    """This exact conflicting command is already retained under the write lock."""


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _utc(at: datetime) -> None:
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise JournalError("UTC source-owner clock required")


def _price(value: str) -> Fraction:
    if type(value) is not str:
        raise JournalError("exact price text required")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise JournalError("positive finite price required")
    result = Fraction(number)
    if (result * 100).denominator != 1:
        raise JournalError("this research risk profile requires whole-cent prices")
    return result


@dataclass(frozen=True)
class EntryRiskPlan:
    client_order_id: str
    symbol: str
    limit: str
    stop: str

    def to_dict(self) -> dict[str, str]:
        for value in (self.client_order_id, self.symbol):
            if type(value) is not str or not value.strip() or value != value.strip():
                raise JournalError("exact risk-plan identity required")
        if _price(self.stop) >= _price(self.limit):
            raise JournalError("invalid frozen risk-plan topology")
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "limit": self.limit,
            "stop": self.stop,
        }


class AccountRiskOwner:
    """One private account/version owner for observed P and pending/open reserves.

    A positive computed quantity is a locally reserved risk proposal. Source
    strategy/asset eligibility, execution protection and release gates remain
    separate; no outgoing order operation exists here.
    """

    def __init__(self, journal: Journal, account_ref: str, fee_policy: Path) -> None:
        if journal.read_paper_account_ref() != account_ref:
            raise JournalError("source risk account is not bound")
        self.journal, self.account_ref, self.fee_policy = (
            journal,
            account_ref,
            fee_policy,
        )
        self.state_key = "account_risk:" + account_ref + ":" + VERSION
        if not self.verify_rebuild():
            raise JournalError("source risk cache differs from immutable evidence")

    def view(self) -> dict[str, Any] | None:
        raw = self.journal.read_state(self.state_key)
        if raw is not None and (
            not isinstance(raw, dict)
            or raw.get("account_ref") != self.account_ref
            or raw.get("version") != VERSION
            or raw.get("runtime_authorized") is not False
        ):
            raise JournalError("invalid source risk projection")
        return raw

    @classmethod
    def read_evidence_at(
        cls,
        journal: Journal,
        account_ref: str,
        decision_at: datetime,
        fee_policy: Path,
        *,
        through_sequence: int | None = None,
    ) -> tuple[dict[str, Any] | None, tuple[int, ...]]:
        """Read the actual source owner's visible prefix without appending events."""
        _utc(decision_at)
        if through_sequence is not None and (
            type(through_sequence) is not int or through_sequence < 0
        ):
            raise JournalError("invalid source-owner sequence horizon")
        reader = cls(journal, account_ref, fee_policy)
        state: object = None
        refs: list[int] = []
        seen: dict[str, str] = {}
        unavailable = False
        for event, raw in journal.read_account_risk_events():
            if through_sequence is not None and event.sequence > through_sequence:
                continue
            if not event.visible_at(decision_at):
                unavailable = True
                continue
            if unavailable or event.external_clock_conflict:
                raise JournalError("source-owner evidence prefix unavailable")
            item = json.loads(raw)
            fingerprint = hashlib.sha256(raw).hexdigest()
            key = str(item["key"])
            conflict = key in seen and seen[key] != fingerprint
            seen.setdefault(key, fingerprint)
            state = reader._reduce(state, item, conflict, event.sequence)
            refs.append(event.sequence)
        if state is not None and not isinstance(state, dict):
            raise JournalError("invalid source-owner historical projection")
        return state, tuple(refs)

    @classmethod
    def _read_native_causal_evidence_at(
        cls,
        journal: Journal,
        account_ref: str,
        decision_at: datetime,
        fee_policy: Path,
        *,
        through_sequence: int,
    ) -> tuple[dict[str, Any] | None, tuple[int, ...]]:
        """Replay a decreasing native dependency prefix, never whole-current state."""
        _utc(decision_at)
        if (
            type(through_sequence) is not int
            or not 0 <= through_sequence <= journal.read_evidence_sequence()
            or journal.read_paper_account_ref() != account_ref
        ):
            raise JournalError("bound causal risk horizon required")
        reader = object.__new__(cls)
        reader.journal, reader.account_ref, reader.fee_policy = (
            journal,
            account_ref,
            fee_policy,
        )
        reader.state_key = "account_risk:" + account_ref + ":" + VERSION
        state: object = None
        refs: list[int] = []
        seen: dict[str, str] = {}
        expected: dict[str, object] = {}
        for event, raw in journal.read_account_risk_events():
            if event.sequence > through_sequence:
                continue
            item = json.loads(raw)
            common = {
                "key",
                "account_ref",
                "version",
                "kind",
                "at",
                "selection",
                "census",
                "source_horizon",
            }
            actions = {
                "OBSERVE": set(),
                "OBSERVE_NATIVE": {"native_policy_requested"},
                "RESERVE": {"plan"},
                "ABANDON_UNSENT": set(),
                "BIND_EXIT": {"exit_id", "role", "exit_request"},
                "BIND_EMERGENCY_POLICY": {"client_order_id", "execution_binding"},
            }
            if not isinstance(item, dict) or item.get("kind") not in actions:
                raise JournalError("causal risk exact event schema differs")
            required = common | actions[item["kind"]]
            if item.get("selection") is not None:
                required.add("snapshot")
            optional = {
                "quantity_incident_policy",
                "research_signal",
                "_native_policy",
                "_native_context",
            }
            if not required <= set(item) or set(item) - required - optional:
                raise JournalError("causal risk exact event schema differs")
            if (
                not event.visible_at(decision_at)
                or event.feed != "paper"
                or event.event_kind != item.get("kind")
                or item.get("account_ref") != account_ref
                or item.get("version") != VERSION
                or item.get("at") != event.available_at.isoformat()
                or event.external_clock_conflict
                or _bytes(item) != raw
            ):
                raise JournalError("causal risk event identity unavailable")
            digest = event.payload_ref
            key = str(item["key"])
            conflict = key in seen and seen[key] != digest
            seen.setdefault(key, digest)
            expected.setdefault(
                json.dumps([VERSION, key], separators=(",", ":")),
                {"hash": digest, "sequence": event.sequence},
            )
            state = reader._reduce(state, item, conflict, event.sequence)
            refs.append(event.sequence)
        current = journal.read_account_risk_evidence_cache(account_ref)
        bounded_cache = {}
        for key, value in current.items():
            identity = json.loads(key)
            if (
                not isinstance(identity, list)
                or len(identity) != 2
                or identity[0] != VERSION
                or type(identity[1]) is not str
                or json.dumps(identity, separators=(",", ":")) != key
                or not isinstance(value, dict)
                or set(value) != {"hash", "sequence"}
                or type(value["sequence"]) is not int
                or value["sequence"] < 1
                or type(value["hash"]) is not str
            ):
                raise JournalError("causal risk deduplication identity malformed")
            if value["sequence"] <= through_sequence:
                bounded_cache[key] = value
        if bounded_cache != expected:
            raise JournalError("causal risk deduplication differs")
        if state is not None and not isinstance(state, dict):
            raise JournalError("invalid causal risk projection")
        return state, tuple(refs)

    def observe(
        self, event_id: str, selection: IncidentRiskRequest, at: datetime
    ) -> None:
        self._record(event_id, at, "OBSERVE", selection)

    def observe_native(
        self, event_id: str, selection: IncidentRiskRequest, at: datetime
    ) -> None:
        """Opt in to versioned native economic ownership, never dispatch authority."""
        self._record(
            event_id,
            at,
            "OBSERVE_NATIVE",
            selection,
            native_policy_requested=NATIVE_POLICY,
        )

    def _native_context(self, state: dict[str, Any], horizon: int) -> dict[str, Any]:
        episodes = [
            dict(row["native_context"], closed=True)
            for row in state["completed"].values()
            if "native_context" in row
        ]
        held = state["held"]
        active = None
        if held is not None:
            scope = held["client_order_id"]
            own = [
                json.loads(raw)
                for event, raw in self.journal.read_oco_research_events()
                if event.sequence <= horizon
                and json.loads(raw)["binding"]["entry_scope"] == scope
            ]
            if any(item["kind"] == "ALLOCATION" for item in own):
                if (
                    type(held.get("quantity")) is not int
                    or held["quantity"] <= 0
                    or _bytes(
                        held.get("research_signal", {}).get(
                            "execution_research_binding"
                        )
                    )
                    != _bytes(OCO_EXECUTION_IDENTITY)
                ):
                    raise JournalError("original positive OCO risk slot required")
                active = scope
                episodes.append(
                    {
                        "entry_scope": scope,
                        "symbol": held["symbol"],
                        "quantity": held["quantity"],
                        "limit": held["limit"],
                        "stop": held["stop"],
                        "closed": False,
                        "census_refs": [],
                    }
                )
        if not episodes:
            raise JournalError("source native accounting episode required")
        prior = self.journal.read_event(state["watermark"])
        if prior is None or prior[0].sequence > horizon:
            raise JournalError("native risk preceding slot evidence unavailable")
        return {
            "policy": NATIVE_POLICY,
            "episodes": episodes,
            "active_scope": active,
            "prior_risk_ref": [prior[0].sequence, prior[0].payload_ref],
            "frozen_risk_reserve": state["pending_open_loss_reserve"],
            "frozen_cash_hold": state["additional_local_cash_hold"],
        }

    def reserve(
        self,
        event_id: str,
        selection: IncidentRiskRequest,
        at: datetime,
        plan: EntryRiskPlan,
    ) -> dict[str, object]:
        self._record(event_id, at, "RESERVE", selection, plan=plan.to_dict())
        return self._reservation_result(plan.client_order_id)

    def reserve_research_decision(
        self,
        event_id: str,
        selection: IncidentRiskRequest,
        at: datetime,
        *,
        client_order_id: str,
        decision: EntryDecision,
        observation_contract: ObservationContract | None = None,
        oco_contract: OcoResearchContract | None = None,
    ) -> dict[str, object]:
        """Trace declared reducer L/S into one non-dispatch risk reservation.

        This neither admits source market inputs nor consumes the P06 attempt.
        OCO opt-in records execution identity while preserving the parent signal
        and this same account-risk owner; it grants no operational acceptance.
        """
        declared = _research_declaration(
            decision,
            observation_contract=observation_contract,
            oco_contract=oco_contract,
        )
        plan = EntryRiskPlan(**_declared_research_plan(client_order_id, declared, at))
        self._record(
            event_id,
            at,
            "RESERVE",
            selection,
            plan=plan.to_dict(),
            research_signal=declared,
        )
        result = self._reservation_result(client_order_id)
        current = self.view()
        held = current.get("held") if isinstance(current, dict) else None
        if result["quantity"] and (
            result.get("client_order_id") != client_order_id
            or result.get("signal_id") != declared["signal_id"]
            or not isinstance(held, dict)
            or held.get("research_signal") != declared
        ):
            result.update(quantity=0, reason="RESERVATION_NOT_CURRENT")
        result.update(
            strategy_plan_qualified=False,
            binding_classification=RESEARCH_BINDING,
        )
        if "data_contract_version" in declared:
            result.update(
                {
                    key: declared[key]
                    for key in (
                        "strategy_version",
                        "risk_profile_version",
                        "data_contract_version",
                        "data_contract_sha256",
                        "volume_source_age",
                        "source_admission_qualified",
                    )
                }
            )
        if "execution_research_binding" in declared:
            result["execution_research_binding"] = dict(
                declared["execution_research_binding"]
            )
        return result

    def _reservation_result(self, client_order_id: str) -> dict[str, object]:
        state = self.view()
        assert state is not None
        result = dict(state["decision"])
        held = state["held"]
        if result["quantity"] and (
            held is None
            or held["client_order_id"] != client_order_id
            or result.get("client_order_id") != client_order_id
            or state["issues"]
            or state["daily_stop_latched"]
            or state["veto"]
        ):
            result.update(quantity=0, reason="RESERVATION_NOT_CURRENT")
        return result

    def bind_emergency_policy(
        self,
        event_id: str,
        at: datetime,
        *,
        client_order_id: str,
        contract: CrossedStopContract,
    ) -> dict[str, object]:
        """Explicit same-slot policy opt-in; neither reserve nor runtime clearance."""
        if self.journal._db.in_transaction:
            raise JournalError("emergency policy binding requires its own transaction")
        if type(contract) is not CrossedStopContract:
            raise JournalError("explicit pinned emergency contract required")
        contract.__post_init__()
        if (
            type(client_order_id) is not str
            or not client_order_id
            or client_order_id != client_order_id.strip()
        ):
            raise JournalError("exact emergency entry identity required")
        self._record(
            event_id,
            at,
            "BIND_EMERGENCY_POLICY",
            None,
            client_order_id=client_order_id,
            execution_binding=contract.identity,
        )
        current = self.view()
        held = None if current is None else current.get("held")
        receipt = (
            None
            if not isinstance(held, dict)
            else held.get("emergency_execution_binding")
        )
        matched = (
            isinstance(held, dict)
            and held.get("client_order_id") == client_order_id
            and isinstance(receipt, dict)
            and receipt["identity"] == contract.identity
        )
        return {
            "classification": "RESEARCH_POLICY_REFERENCE_NOT_RISK_CLEARANCE",
            "research_policy_bound_to_current_slot": matched,
            "receipt": receipt if matched else None,
            "risk_veto_retained": None if current is None else current["veto"],
            "daily_stop_retained": None
            if current is None
            else current["daily_stop_latched"],
            "entry_risk_version_binding_accepted": False,
            "runtime_authorized": False,
            "order_authority": False,
        }

    def bind_exit(
        self, event_id: str, at: datetime, client_order_id: str, role: str
    ) -> None:
        if role not in {"STOP", "TARGET", "CLOSE"}:
            raise JournalError("unsupported declared exit role")
        self._record(
            event_id, at, "BIND_EXIT", None, exit_id=client_order_id, role=role
        )

    def abandon_unsent(
        self, event_id: str, selection: IncidentRiskRequest, at: datetime
    ) -> None:
        self._record(event_id, at, "ABANDON_UNSENT", selection)

    def _census(self) -> list[dict[str, object]]:
        return [
            {
                "id": cid,
                "hash": hashlib.sha256(
                    _bytes(self.journal.read_order_intent(cid))
                ).hexdigest(),
                "send_started_at": self.journal.read_order_send_started_at(cid),
            }
            for cid in sorted(self.journal.read_order_intent_ids())
        ]

    def _validate_census(self, raw: object) -> tuple[str, ...]:
        if not isinstance(raw, list):
            raise JournalError("immutable source-owner census required")
        ids: list[str] = []
        for row in raw:
            if not isinstance(row, dict) or set(row) != {
                "id",
                "hash",
                "send_started_at",
            }:
                raise JournalError("invalid source-owner census row")
            cid = row["id"]
            if not isinstance(cid, str) or not cid:
                raise JournalError("invalid census client identity")
            intent = self.journal.read_order_intent(cid)
            if (
                intent is None
                or hashlib.sha256(_bytes(intent)).hexdigest() != row["hash"]
            ):
                raise JournalError("source-owner intent evidence conflicts")
            started = row["send_started_at"]
            if started is not None and (
                not isinstance(started, str)
                or self.journal.read_order_send_started_at(cid) != started
            ):
                raise JournalError("source-owner send evidence conflicts")
            if started is not None:
                _utc(datetime.fromisoformat(started))
            ids.append(cid)
        if ids != sorted(set(ids)):
            raise JournalError("source-owner census must be unique and sorted")
        return tuple(ids)

    def _snapshot(self, item: dict[str, Any]) -> dict[str, Any]:
        at = datetime.fromisoformat(item["at"])
        _utc(at)
        ids = self._validate_census(item["census"])
        horizon = item["source_horizon"]
        if type(horizon) is not int or horizon < 0:
            raise JournalError("invalid source observation horizon")
        selection = IncidentRiskRequest.from_dict(item["selection"])
        if any(
            seq > horizon
            for seq in (
                selection.capture_sequence,
                selection.calendar_sequence,
                *(row[1] for row in selection.mark_refs),
                *(row[3] for row in selection.mark_refs),
            )
        ):
            raise JournalError("source selection exceeds locked observation horizon")
        day = at.astimezone(ZoneInfo("America/New_York")).date()
        try:
            native = None
            if item.get("_native_policy") == NATIVE_POLICY:
                native = derive_native_registry(
                    self.journal,
                    item["_native_context"],
                    capture_sequence=selection.capture_sequence,
                    decision_at=at,
                    max_age=selection.max_age,
                    through_sequence=horizon,
                )
            audit = audit_owned_equity(
                self.journal,
                selection.capture_sequence,
                selection.calendar_sequence,
                account_ref=self.account_ref,
                decision_at=at,
                max_age=selection.max_age,
                fee_policy=self.fee_policy,
                mark_refs=selection.mark_refs,
                max_metadata_age=selection.max_metadata_age,
                _intent_ids=ids,
                _source_horizon=horizon,
                _native_registry=native,
            )
            baseline = read_baseline(
                self.journal,
                account_ref=self.account_ref,
                session_day=day,
                decision_at=at,
                through_sequence=horizon,
            )
            opened = session_open_from_evidence(
                self.journal,
                selection.calendar_sequence,
                self.account_ref,
                day,
                at,
                selection.max_age,
            )
            inputs = inspect_risk_inputs(
                self.journal,
                selection.capture_sequence,
                account_ref=self.account_ref,
                decision_at=at,
                session_open=opened,
                max_age=selection.max_age,
                mark_refs=selection.mark_refs,
                max_metadata_age=selection.max_metadata_age,
                _intent_ids=ids,
                _source_horizon=horizon,
                _native_registry=native,
            )
            lifecycle, _ = OrderReconciler.read_evidence_at(
                self.journal, self.account_ref, at, through_sequence=horizon
            )
            orders = lifecycle["orders"]
            if not isinstance(orders, dict):
                raise JournalError("source-owned orders missing")
            positions = {
                symbol: str(qty)
                for symbol, qty in inputs.ownership.owned_long_positions
            }
            session_orders: set[str] = set()
            if baseline is not None:
                old_ids = _activities(self.journal, baseline.account_sequence)
                for activity_id, row in _activities(
                    self.journal, selection.capture_sequence
                ).items():
                    if activity_id not in old_ids and row["activity_type"] == "FILL":
                        session_orders.add(str(row["order_id"]))
            flat = (
                not positions
                and not any(
                    OrderProjection.from_dict(row).state.value not in TERMINAL
                    for row in orders.values()
                )
                and (
                    native is None
                    or not any(
                        order.status
                        not in {"filled", "canceled", "expired", "rejected"}
                        for order in native.orders
                    )
                )
            )
            captured = self.journal.read_event(selection.capture_sequence)
            assert captured is not None  # source inspectors verified the event
            envelope = decode_broker_json(captured[1])
            assert isinstance(envelope, dict) and isinstance(envelope["requests"], list)
            observed = min(
                datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
                for row in envelope["requests"]
            )
            result: dict[str, Any] = {
                "qualified": audit.reported_conservation_verified,
                "observed_at": observed.isoformat(),
                "selection": item["selection"],
                "issues": list(audit.issues),
                "session": day.isoformat(),
                "e0": None if baseline is None else str(Fraction(baseline.equity)),
                "baseline_ref": None
                if baseline is None
                else baseline.receipt_payload_ref,
                "net": None
                if audit.modelled_net_since_baseline is None
                else str(audit.modelled_net_since_baseline),
                "funds": None
                if inputs.ownership.reported_available_funds is None
                else str(Fraction(inputs.ownership.reported_available_funds)),
                "positions": positions,
                "orders": orders,
                "flat": flat,
                "intent_ids": list(ids),
                "session_order_ids": sorted(session_orders),
                "bids": {mark.symbol: str(mark.bid) for mark in inputs.marks},
                "source_refs": [list(row) for row in audit.source_refs],
                "fee_document_ref": audit.fee_document_ref,
            }
            if native is not None:
                result["native"] = {
                    "policy": NATIVE_POLICY,
                    "context": item["_native_context"],
                    "exit_by_scope": {
                        scope: str(quantity) for scope, quantity in native.exit_by_scope
                    },
                    "final_scopes": list(native.final_scopes),
                    "census_refs": {
                        scope: [list(ref) for ref in refs]
                        for scope, refs in native.census_refs
                    },
                    "risk_issues": list(native.risk_issues),
                    "order_ids": [order.order_id for order in native.orders],
                    "source_refs": [
                        [seq, source[0].payload_ref]
                        for seq in native.source_refs
                        if (source := self.journal.read_event(seq)) is not None
                    ],
                    "runtime_authorized": False,
                }
            return result
        except JournalError:
            if item.get("_native_policy") == NATIVE_POLICY:
                return {
                    "qualified": False,
                    "issues": ["NATIVE_SOURCE_INVALID_OR_UNAVAILABLE"],
                    "session": day.isoformat(),
                    "flat": False,
                    "observed_at": item["at"],
                    "selection": item["selection"],
                }
            raise
        except ValueError:
            return {
                "qualified": False,
                "issues": ["SOURCE_INPUT_INVALID_OR_UNAVAILABLE"],
                "session": day.isoformat(),
                "flat": False,
                "observed_at": item["at"],
            }

    def _request(self, cid: str) -> dict[str, object]:
        intent = self.journal.read_order_intent(cid)
        if (
            not isinstance(intent, dict)
            or intent.get("paper_account_ref") != self.account_ref
        ):
            raise JournalError("owned intent identity is unavailable")
        if (
            intent.get("paper_endpoint") != "https://paper-api.alpaca.markets"
            or intent.get("purpose") != "ENGINEERING_TEST"
        ):
            raise JournalError("unsupported actual intent profile")
        body = intent.get("request_body")
        if not isinstance(body, str):
            raise JournalError("owned request body missing")
        request = validate_saved_request(body)
        if request["client_order_id"] != cid:
            raise JournalError("owned client identity differs")
        return request

    def _record(
        self,
        event_id: str,
        at: datetime,
        kind: str,
        selection: IncidentRiskRequest | None,
        **action: object,
    ) -> None:
        _utc(at)
        if type(event_id) is not str or not event_id.strip():
            raise JournalError("source-owner event identity required")
        item: dict[str, Any] = {
            "key": event_id,
            "account_ref": self.account_ref,
            "version": VERSION,
            "kind": kind,
            "at": at.isoformat(),
            "selection": None if selection is None else selection.to_dict(),
            **action,
        }

        def prepare(_: bytes) -> bytes:
            if not self.verify_rebuild():
                raise JournalError("source-owner cache or deduplication differs")
            # Retry identity is the caller's command, not later observations.
            # Preserve the original locked source census and assessment bytes.
            first_for_key = True
            for _event, raw in self.journal.read_account_risk_events():
                saved = json.loads(raw)
                if saved["key"] != event_id:
                    continue
                caller = {
                    key: value
                    for key, value in saved.items()
                    if key
                    not in {
                        "census",
                        "snapshot",
                        "exit_request",
                        "source_horizon",
                        "quantity_incident_policy",
                        "_native_context",
                        "_native_policy",
                    }
                }
                if _bytes(caller) == _bytes(item):
                    if not first_for_key:
                        raise _KnownRiskConflict
                    item.clear()
                    item.update(saved)
                    return raw
                first_for_key = False
            item["census"] = self._census()
            item["source_horizon"] = self.journal.read_evidence_sequence()
            item["quantity_incident_policy"] = "LATCH_UNSUPPORTED_FRACTION_V1"
            previous = self.view()
            if kind == "OBSERVE_NATIVE" or (
                isinstance(previous, dict)
                and previous.get("_native_policy") == NATIVE_POLICY
            ):
                if previous is None:
                    raise JournalError(
                        "existing source risk slot required for native opt-in"
                    )
                item["_native_policy"] = NATIVE_POLICY
                item["_native_context"] = self._native_context(
                    previous, item["source_horizon"]
                )
            if selection is not None:
                item["snapshot"] = self._snapshot(item)
            if kind == "BIND_EXIT":
                item["exit_request"] = self._request(str(item["exit_id"]))
            return _bytes(item)

        try:
            self.journal.apply_account_risk_event(
                account_ref=self.account_ref,
                state_key=self.state_key,
                dedupe_key=json.dumps([VERSION, event_id], separators=(",", ":")),
                event_kind=kind,
                payload=b"",
                received_at=at,
                prepare_payload=prepare,
                reduce=lambda old, conflict, seq: self._reduce(
                    old, item, conflict, seq
                ),
            )
        except _KnownRiskConflict:
            return

    def _charges(self, qty: int, stop: Fraction) -> tuple[Fraction, Fraction]:
        if qty == 0:
            return Fraction(0), Fraction(0)
        entry, exit_fee = planned_charge_bounds(self.fee_policy, qty, stop)
        return Fraction(entry), Fraction(exit_fee)

    def _reduce(
        self, old: object, item: dict[str, Any], conflict: bool, sequence: int
    ) -> dict[str, Any]:
        if item["account_ref"] != self.account_ref or item["version"] != VERSION:
            raise JournalError("source-owner account/version differs")
        if (
            type(item["source_horizon"]) is not int
            or not 0 <= item["source_horizon"] < sequence
        ):
            raise JournalError("source-owner sequence horizon differs")
        self._validate_census(item["census"])
        state: dict[str, Any] = (
            json.loads(_bytes(old))
            if isinstance(old, dict)
            else {
                "schema": 1,
                "account_ref": self.account_ref,
                "version": VERSION,
                "session": None,
                "e0": None,
                "baseline_ref": None,
                "net": None,
                "losing_streak": 0,
                "daily_stop_latched": False,
                "held": None,
                "completed": {},
                "used_plan_ids": [],
                "veto": False,
                "last_snapshot": None,
                "decision": None,
                "at": None,
                "runtime_authorized": False,
            }
        )
        at = datetime.fromisoformat(item["at"])
        _utc(at)
        research_signal = item.get("research_signal")
        if item.get("kind") == "BIND_EMERGENCY_POLICY":
            if type(item.get("execution_binding")) is not dict or _bytes(
                item["execution_binding"]
            ) != _bytes(load_crossed_stop_contract().identity):
                raise JournalError("pinned emergency risk identity differs")
        if research_signal is not None:
            if item.get("kind") != "RESERVE":
                raise JournalError("research signal requires a research reservation")
            if not isinstance(research_signal, dict) or (
                _declared_research_plan(
                    item["plan"]["client_order_id"], research_signal, at
                )
                != item["plan"]
            ):
                raise JournalError("research-derived risk plan differs")
        reasons: set[str] = set()
        if conflict:
            state.update(
                veto=True,
                issues=["EVENT_ID_CONFLICT"],
                capacity="0",
                watermark=sequence,
                decision={
                    "quantity": 0,
                    "reason": "EVENT_ID_CONFLICT",
                    "runtime_authorized": False,
                },
            )
            return state
        if state["at"] is not None and at < datetime.fromisoformat(state["at"]):
            state.update(
                veto=True,
                issues=["CLOCK_REWIND"],
                capacity="0",
                watermark=sequence,
                decision={
                    "quantity": 0,
                    "reason": "CLOCK_REWIND",
                    "runtime_authorized": False,
                },
            )
            return state
        held = state["held"]
        if held is not None and "quantity_incident" in held:
            reasons.add("UNSUPPORTED_FRACTIONAL_RISK_QUANTITY")
        kind = item["kind"]
        if item.get("_native_policy") == NATIVE_POLICY:
            if (
                not isinstance(old, dict)
                or (
                    kind == "OBSERVE_NATIVE"
                    and item.get("native_policy_requested") != NATIVE_POLICY
                )
                or (
                    kind != "OBSERVE_NATIVE"
                    and state.get("_native_policy") != NATIVE_POLICY
                )
            ):
                raise JournalError("explicit native risk policy origin required")
            if _bytes(item.get("_native_context")) != _bytes(
                self._native_context(state, item["source_horizon"])
            ):
                raise JournalError(
                    "native risk context differs from preceding risk slot"
                )
            state["_native_policy"] = NATIVE_POLICY
        elif state.get("_native_policy") == NATIVE_POLICY:
            raise JournalError("enabled native accounting cannot silently downgrade")
        if kind == "BIND_EMERGENCY_POLICY":
            reasons.update(state.get("issues", []))
            declaration = None if held is None else held.get("research_signal")
            if (
                held is None
                or held.get("client_order_id") != item["client_order_id"]
                or type(held.get("quantity")) is not int
                or held["quantity"] <= 0
                or not isinstance(declaration, dict)
                or _bytes(declaration.get("execution_research_binding"))
                != _bytes(OCO_EXECUTION_IDENTITY)
            ):
                raise JournalError("original positive OCO risk slot required")
            snapshot = state.get("last_snapshot")
            if (
                not isinstance(snapshot, dict)
                or type(snapshot.get("selection")) is not dict
                or type(snapshot.get("observed_at")) is not str
            ):
                raise JournalError(
                    "current source snapshot required for policy binding"
                )
            for event, _ in self.journal.read_account_risk_events():
                if event.sequence <= item["source_horizon"] and (
                    not event.visible_at(at) or event.external_clock_conflict
                ):
                    raise JournalError("emergency policy prior risk prefix unavailable")
            if not 0 < state["watermark"] <= item["source_horizon"] < sequence:
                raise JournalError("emergency policy prior risk horizon differs")
            selection = IncidentRiskRequest.from_dict(snapshot["selection"])
            observed = datetime.fromisoformat(snapshot["observed_at"])
            _utc(observed)
            if not timedelta(0) <= at - observed <= selection.max_age:
                raise JournalError("emergency risk binding source stale or future")
            prior = held.get("emergency_execution_binding")
            if prior is None:
                previous = self.journal.read_event(state["watermark"])
                if previous is None or previous[0].source_id != SOURCE:
                    raise JournalError("previous held risk evidence missing")
                held["emergency_execution_binding"] = {
                    "identity": item["execution_binding"],
                    "client_order_id": held["client_order_id"],
                    "symbol": held["symbol"],
                    "quantity": held["quantity"],
                    "limit": held["limit"],
                    "stop": held["stop"],
                    "research_signal_sha256": hashlib.sha256(
                        _bytes(declaration)
                    ).hexdigest(),
                    "prior_slot_ref": [previous[0].sequence, previous[0].payload_ref],
                    "binding_ref": [
                        sequence,
                        "sha256:" + hashlib.sha256(_bytes(item)).hexdigest(),
                    ],
                    "bound_at": item["at"],
                    "runtime_authorized": False,
                }
            elif _bytes(prior["identity"]) != _bytes(item["execution_binding"]):
                raise JournalError("emergency policy cannot be rebound")
            reasons.add("REFRESH_REQUIRED")
        elif kind == "BIND_EXIT":
            cid, role, request = item["exit_id"], item["role"], item["exit_request"]
            if request != self._request(cid) or held is None:
                raise JournalError("exit binding lacks current immutable risk slot")
            if (
                request["symbol"] != held["symbol"]
                or request["side"] != "sell"
                or request["type"]
                != {"STOP": "stop", "TARGET": "limit", "CLOSE": "market"}.get(role)
                or Fraction(str(request["qty"])) > held["quantity"]
                or (
                    role == "STOP"
                    and Fraction(str(request["stop_price"])) != Fraction(held["stop"])
                )
            ):
                raise JournalError(
                    "exit request differs from declared frozen risk plan"
                )
            prior = held["exits"].get(cid)
            if prior is not None and prior != role:
                raise JournalError("exit role cannot be reassigned")
            held["exits"][cid] = role
            reasons.add("REFRESH_REQUIRED")
        else:
            snapshot = self._snapshot(item)
            if snapshot != item["snapshot"]:
                raise JournalError("source risk snapshot differs from immutable replay")
            if held is not None and isinstance(snapshot.get("native"), dict):
                native = snapshot["native"]
                scope = held["client_order_id"]
                # Source quantities remain observable when the surrounding
                # account book vetoes. They never substitute for qualified
                # account positions or reduce the frozen reservation.
                held["native_source_observation"] = {
                    "exit_quantity": native["exit_by_scope"].get(scope),
                    "source_refs": native["source_refs"],
                    "census_refs": native["census_refs"].get(scope, []),
                    "risk_issues": native["risk_issues"],
                    "account_economics_qualified": snapshot["qualified"],
                    "observed_at": snapshot["observed_at"],
                    "runtime_authorized": False,
                }
            if (
                held is not None
                and item.get("quantity_incident_policy")
                == "LATCH_UNSUPPORTED_FRACTION_V1"
            ):
                entry = snapshot.get("orders", {}).get(held["client_order_id"])
                if entry is not None and "quantity_incident" not in held:
                    filled = Fraction(entry["filled_quantity"])
                    open_quantity = Fraction(
                        snapshot["positions"].get(held["symbol"], "0")
                    )
                    if filled.denominator != 1 or open_quantity.denominator != 1:
                        # An unqualified snapshot may veto; it never grants
                        # ownership or capacity. Preserve the first diagnostic.
                        held["quantity_incident"] = {
                            "filled_quantity": str(filled),
                            "open_quantity": str(open_quantity),
                            "selection": snapshot["selection"],
                            "observed_at": snapshot["observed_at"],
                            "source_snapshot_qualified": snapshot["qualified"],
                            "reservation_frozen": True,
                            "runtime_authorized": False,
                        }
                if "quantity_incident" in held:
                    reasons.add("UNSUPPORTED_FRACTIONAL_RISK_QUANTITY")
            if research_signal is not None:
                if research_signal["session_day"] != snapshot["session"]:
                    raise JournalError("research/risk session identity differs")
                if snapshot["qualified"]:
                    selection = IncidentRiskRequest.from_dict(item["selection"])
                    opened = session_open_from_evidence(
                        self.journal,
                        selection.calendar_sequence,
                        self.account_ref,
                        at.astimezone(ZoneInfo("America/New_York")).date(),
                        at,
                        selection.max_age,
                    )
                    if opened.isoformat() != research_signal["session"]["open_at"]:
                        raise JournalError("research/risk session calendar differs")
            if not snapshot["qualified"]:
                reasons.update(snapshot["issues"])
            else:
                if state["session"] != snapshot["session"]:
                    if state["session"] is not None and (
                        snapshot["session"] < state["session"]
                        or held is not None
                        or not state["last_snapshot"]["qualified"]
                        or not state["last_snapshot"]["flat"]
                        or not snapshot["flat"]
                    ):
                        raise JournalError(
                            "new daily risk requires reconciled flat history"
                        )
                    state.update(
                        session=snapshot["session"],
                        e0=snapshot["e0"],
                        baseline_ref=snapshot["baseline_ref"],
                        losing_streak=0,
                        daily_stop_latched=False,
                    )
                if (
                    state["e0"] != snapshot["e0"]
                    or state["baseline_ref"] != snapshot["baseline_ref"]
                ):
                    raise JournalError("same-session E0 identity changed")
                state["net"] = snapshot["net"]
                bound_ids = {
                    oid
                    for row in state["completed"].values()
                    for oid in row["order_ids"]
                }
                if held is not None:
                    bound_ids.update((held["client_order_id"], *held["exits"]))
                if any(
                    row["broker_order_id"] in snapshot["session_order_ids"]
                    and cid not in bound_ids
                    for cid, row in snapshot["orders"].items()
                ):
                    reasons.add("UNBOUND_EXECUTION")
                if Fraction(state["net"]) <= -Fraction(state["e0"]) / 100:
                    state["daily_stop_latched"] = True
                if held is not None:
                    if item.get("_native_policy") == NATIVE_POLICY:
                        self._sync_native_held(state, snapshot, reasons)
                    else:
                        self._sync_held(state, snapshot, reasons)
                    held = state["held"]
                elif not snapshot["flat"]:
                    reasons.add("UNBOUND_POSITION_OR_ORDER")
                if kind == "ABANDON_UNSENT":
                    sent = {row["id"]: row["send_started_at"] for row in item["census"]}
                    if (
                        held is None
                        or sent.get(held["client_order_id"]) is not None
                        or held["exits"]
                        or not snapshot["flat"]
                    ):
                        raise JournalError("only an unsent flat plan may be abandoned")
                    state["held"] = held = None
                if kind == "RESERVE":
                    self._reserve(
                        state, item["plan"], snapshot, reasons, research_signal
                    )
            state["last_snapshot"] = snapshot
        if kind == "RESERVE" and not item["snapshot"]["qualified"]:
            state["decision"] = {
                "quantity": 0,
                "reason": "SOURCE_INPUT_VETO",
                "runtime_authorized": False,
            }
        if state["veto"]:
            reasons.add("IDENTITY_OR_CLOCK_VETO")
        if state["daily_stop_latched"]:
            reasons.add("DAILY_STOP_LATCHED")
        state.update(
            issues=sorted(reasons),
            at=item["at"],
            watermark=sequence,
            runtime_authorized=False,
        )
        self._totals(state, bool(reasons))
        return state

    def _sync_native_held(
        self, state: dict[str, Any], snapshot: dict[str, Any], reasons: set[str]
    ) -> None:
        held = state["held"]
        native = snapshot["native"]
        cid = held["client_order_id"]
        if native["context"]["active_scope"] != cid:
            self._sync_held(state, snapshot, reasons)
            return
        orders = snapshot["orders"]
        entry = orders.get(cid)
        if entry is None:
            reasons.add("NATIVE_ENTRY_OWNERSHIP_UNAVAILABLE")
            return
        request = self._request(cid)
        if (
            request["side"] != "buy"
            or request["type"] != "limit"
            or request["symbol"] != held["symbol"]
            or Fraction(str(request["qty"])) != held["quantity"]
            or Fraction(str(request["limit_price"])) != Fraction(held["limit"])
        ):
            raise JournalError("native risk entry differs from original reservation")
        filled = Fraction(entry["filled_quantity"])
        opened = Fraction(snapshot["positions"].get(held["symbol"], "0"))
        native_exit = Fraction(native["exit_by_scope"].get(cid, "0"))
        simple_exit = sum(
            (
                Fraction(orders[key]["filled_quantity"])
                for key in held["exits"]
                if key in orders
            ),
            Fraction(),
        )
        if opened != filled - native_exit - simple_exit:
            reasons.add("NATIVE_RISK_SLOT_QUANTITY_MISMATCH")
        known = {cid, *held["exits"]}
        completed_ids = {
            key for row in state["completed"].values() for key in row["order_ids"]
        }
        if set(snapshot["positions"]) - {held["symbol"]}:
            reasons.add("UNBOUND_POSITION")
        if any(
            key not in known | completed_ids
            and (
                row["state"] not in TERMINAL
                or row["broker_order_id"] in snapshot["session_order_ids"]
            )
            for key, row in orders.items()
        ):
            reasons.add("UNBOUND_ORDER_OR_EXECUTION")
        held.setdefault(
            "native_frozen_risk_reserve", native["context"]["frozen_risk_reserve"]
        )
        held.setdefault(
            "native_frozen_cash_hold", native["context"]["frozen_cash_hold"]
        )
        held["native_ownership"] = {
            "policy": NATIVE_POLICY,
            "exit_quantity": str(native_exit),
            "open_quantity": str(opened),
            "filled_quantity": str(filled),
            "source_refs": native["source_refs"],
            "census_refs": native["census_refs"].get(cid, []),
            "native_order_ids": native["order_ids"],
            "runtime_authorized": False,
        }
        reasons.update(native["risk_issues"])
        if (
            "quantity_incident" in held
            or filled.denominator != 1
            or opened.denominator != 1
        ):
            reasons.add("UNSUPPORTED_FRACTIONAL_RISK_QUANTITY")
            return
        held.update(
            filled=int(filled),
            open_quantity=int(opened),
            pending_quantity=0
            if entry["state"] in TERMINAL
            else held["quantity"] - int(filled),
            broker_hold_represented=entry["state"] not in TERMINAL,
            bid=snapshot["bids"].get(held["symbol"], held["stop"]),
        )
        final = (
            cid in native["final_scopes"]
            and opened == 0
            and entry["state"] in TERMINAL
            and snapshot["flat"]
            and not state["veto"]
            and not reasons
            and all(
                key in orders and orders[key]["state"] in TERMINAL
                for key in held["exits"]
            )
        )
        if not final:
            reasons.add("NATIVE_FINALITY_PENDING")
            return
        net = Fraction(snapshot["net"]) - Fraction(held["net_at_start"])
        if filled > 0:
            state["losing_streak"] = (
                state["losing_streak"] + 1
                if net < 0
                else 0
                if net > 0
                else state["losing_streak"]
            )
            state["daily_stop_latched"] = (
                state["daily_stop_latched"] or state["losing_streak"] >= 3
            )
        episode = next(
            row for row in native["context"]["episodes"] if row["entry_scope"] == cid
        )
        state["completed"][cid] = {
            "net": str(net),
            "filled": int(filled),
            "order_ids": sorted(known),
            "native_order_ids": native["order_ids"],
            "native_exit_quantity": str(native_exit),
            "native_context": dict(
                episode, closed=True, census_refs=native["census_refs"][cid]
            ),
        }
        state["held"] = None

    def _sync_held(
        self, state: dict[str, Any], snapshot: dict[str, Any], reasons: set[str]
    ) -> None:
        held = state["held"]
        if "quantity_incident" in held:
            # Unsupported ownership cannot be healed by a later whole/flat
            # aggregate. Retain the last supported reservation until a separately
            # qualified recovery path exists; this is not current exposure sizing.
            reasons.add("UNSUPPORTED_FRACTIONAL_RISK_QUANTITY")
            return
        cid = held["client_order_id"]
        orders = snapshot["orders"]
        known = {cid, *held["exits"]}
        completed_orders = {
            oid for row in state["completed"].values() for oid in row["order_ids"]
        }
        for key, row in orders.items():
            if (
                row["broker_order_id"] in snapshot["session_order_ids"]
                and key not in known | completed_orders
            ):
                reasons.add("UNBOUND_EXECUTION")
        if set(snapshot["positions"]) - {held["symbol"]}:
            reasons.add("UNBOUND_POSITION")
        if any(
            row["state"] not in TERMINAL and key not in known
            for key, row in orders.items()
        ):
            reasons.add("UNBOUND_WORKING_ORDER")
        entry = orders.get(cid)
        if entry is None:
            if not snapshot["flat"]:
                reasons.add("ENTRY_OWNERSHIP_UNAVAILABLE")
            return
        request = self._request(cid)
        if (
            request["side"] != "buy"
            or request["type"] != "limit"
            or request["symbol"] != held["symbol"]
            or Fraction(str(request["qty"])) != held["quantity"]
            or Fraction(str(request["limit_price"])) != Fraction(held["limit"])
        ):
            raise JournalError("actual entry differs from frozen risk proposal")
        filled = Fraction(entry["filled_quantity"])
        opened = Fraction(snapshot["positions"].get(held["symbol"], "0"))
        if filled.denominator != 1 or opened.denominator != 1:
            # Preserve reduction semantics for pre-policy immutable events.
            raise JournalError("nonwhole entry/open risk cannot be released")
        unrelated_exits = [
            key
            for key, row in orders.items()
            if row["symbol"] == held["symbol"]
            and row["side"] == "sell"
            and key not in held["exits"]
            and key not in state["completed"]
        ]
        # Older completed-round orders are allowed only through their stored IDs.
        if any(
            key not in completed_orders
            and orders[key]["broker_order_id"] in snapshot["session_order_ids"]
            for key in unrelated_exits
        ):
            reasons.add("UNBOUND_EXIT_ORDER")
        expected_open = filled - sum(
            (
                Fraction(orders[key]["filled_quantity"])
                for key in held["exits"]
                if key in orders
            ),
            Fraction(0),
        )
        if opened != expected_open:
            reasons.add("RISK_SLOT_QUANTITY_MISMATCH")
        held.update(
            filled=int(filled),
            open_quantity=int(opened),
            pending_quantity=0
            if entry["state"] in TERMINAL
            else held["quantity"] - int(filled),
            broker_hold_represented=entry["state"] not in TERMINAL,
            bid=snapshot["bids"].get(held["symbol"], held["stop"]),
        )
        if (
            opened == 0
            and entry["state"] in TERMINAL
            and snapshot["flat"]
            and not reasons
        ):
            if any(
                key not in orders or orders[key]["state"] not in TERMINAL
                for key in held["exits"]
            ):
                reasons.add("EXIT_FINALITY_PENDING")
                return
            net = Fraction(snapshot["net"]) - Fraction(held["net_at_start"])
            if filled > 0:
                state["losing_streak"] = (
                    state["losing_streak"] + 1
                    if net < 0
                    else 0
                    if net > 0
                    else state["losing_streak"]
                )
                state["daily_stop_latched"] = (
                    state["daily_stop_latched"] or state["losing_streak"] >= 3
                )
            state["completed"][cid] = {
                "net": str(net),
                "filled": int(filled),
                "order_ids": sorted(known),
            }
            state["held"] = None

    def _reserve(
        self,
        state: dict[str, Any],
        raw: dict[str, str],
        snapshot: dict[str, Any],
        reasons: set[str],
        research_signal: dict[str, Any] | None = None,
    ) -> None:
        plan = EntryRiskPlan(**raw)
        plan.to_dict()
        limit, stop = _price(plan.limit), _price(plan.stop)
        reason = "FEASIBLE"
        quantity = 0
        if state["veto"] or state["daily_stop_latched"] or reasons:
            reason = "RISK_VETO"
        elif state["held"] is not None or not snapshot["flat"]:
            reason = "SLOT_OCCUPIED"
        elif (
            plan.client_order_id in state["used_plan_ids"]
            or plan.client_order_id in snapshot["intent_ids"]
        ):
            reason = "PLAN_ID_REUSED"
        else:
            equity, net, funds = (
                Fraction(snapshot[key]) for key in ("e0", "net", "funds")
            )
            capacity = max(Fraction(0), min(equity / 100, equity / 100 + net))
            budget = min(equity / 200, capacity)
            bound = min(int(funds // limit), int(budget // (limit - stop)))
            for q in range(1, bound + 1):
                entry_fee, exit_fee = self._charges(q, stop)
                if (
                    q * limit + entry_fee <= funds
                    and q * (limit - stop) + entry_fee + exit_fee <= budget
                ):
                    quantity = q
            if quantity:
                state["used_plan_ids"].append(plan.client_order_id)
                held_plan: dict[str, Any] = plan.to_dict() | {
                    "quantity": quantity,
                    "pending_quantity": quantity,
                    "open_quantity": 0,
                    "filled": 0,
                    "bid": plan.limit,
                    "broker_hold_represented": False,
                    "exits": {},
                    "net_at_start": snapshot["net"],
                }
                if research_signal is not None:
                    held_plan["research_signal"] = research_signal
                state["held"] = held_plan
            else:
                reason = "NO_FEASIBLE_QUANTITY"
        state["decision"] = {
            "client_order_id": plan.client_order_id,
            "quantity": quantity,
            "reason": reason,
            "runtime_authorized": False,
            "strategy_plan_qualified": False,
        }
        if research_signal is not None:
            state["decision"].update(
                signal_id=research_signal["signal_id"],
                binding_classification=RESEARCH_BINDING,
                research_sha256=research_signal["research_sha256"],
            )
            if "data_contract_version" in research_signal:
                state["decision"].update(
                    {
                        key: research_signal[key]
                        for key in (
                            "strategy_version",
                            "risk_profile_version",
                            "data_contract_version",
                            "data_contract_sha256",
                            "volume_source_age",
                            "source_admission_qualified",
                        )
                    }
                )
            if "execution_research_binding" in research_signal:
                state["decision"]["execution_research_binding"] = dict(
                    research_signal["execution_research_binding"]
                )

    def _totals(self, state: dict[str, Any], vetoed: bool) -> None:
        risk = cash = Fraction(0)
        held = state["held"]
        if held is not None:
            pending, opened = held["pending_quantity"], held["open_quantity"]
            limit, stop, bid = (Fraction(held[key]) for key in ("limit", "stop", "bid"))
            entry_fee, pending_stop_fee = self._charges(pending, stop)
            _, open_exit_fee = self._charges(opened, stop)
            risk = pending * (limit - stop) + entry_fee + pending_stop_fee
            risk += opened * max(Fraction(0), bid - stop) + open_exit_fee
            cash = entry_fee + (
                0 if held["broker_hold_represented"] else pending * limit
            )
            if "native_frozen_risk_reserve" in held:
                risk = max(risk, Fraction(held["native_frozen_risk_reserve"]))
                cash = max(cash, Fraction(held["native_frozen_cash_hold"]))
        snapshot = state["last_snapshot"]
        healthy = snapshot is not None and snapshot["qualified"] and not vetoed
        cap = Fraction(0)
        if healthy:
            equity, net = Fraction(state["e0"]), Fraction(state["net"])
            cap = max(Fraction(0), min(equity / 100, equity / 100 + net) - risk)
        state.update(
            capacity=str(cap),
            pending_open_loss_reserve=str(risk),
            additional_local_cash_hold=str(cash),
            source_snapshot_qualified=bool(snapshot and snapshot["qualified"]),
        )

    def verify_rebuild(self) -> bool:
        projected: object = None
        expected: dict[str, object] = {}
        seen: dict[str, str] = {}
        for event, raw in self.journal.read_account_risk_events():
            item = json.loads(raw)
            if (
                event.feed != "paper"
                or event.event_kind != item.get("kind")
                or item.get("account_ref") != self.account_ref
                or item.get("version") != VERSION
                or item.get("at") != event.available_at.isoformat()
                or event.external_clock_conflict
            ):
                raise JournalError("source-owner immutable event identity differs")
            digest = hashlib.sha256(raw).hexdigest()
            key = str(item["key"])
            conflict = key in seen and seen[key] != digest
            seen.setdefault(key, digest)
            expected.setdefault(
                json.dumps([VERSION, key], separators=(",", ":")),
                {"hash": "sha256:" + digest, "sequence": event.sequence},
            )
            projected = self._reduce(projected, item, conflict, event.sequence)
        return (
            self.view() == projected
            and self.journal.read_account_risk_evidence_cache(self.account_ref)
            == expected
        )
