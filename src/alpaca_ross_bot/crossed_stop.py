"""Durable crossed-stop research decisions, NOT source or broker qualification.

Reuses the OCO lot book and private journal. No transport, callback, credentials,
order-intent primitive, risk release or authority to modify old protection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any

from ._causal_risk_memo import causal_risk_verification_scope
from .crossed_stop_contract import MANIFEST_SHA256 as MANIFEST_SHA256
from .crossed_stop_contract import VERSION as VERSION
from .crossed_stop_contract import load_crossed_stop_contract
from .journal import JournalError
from .oco_journal import OcoJournal, _json
from .oco_research import OcoResearchState

_ORIGIN = "DECLARED_RESEARCH_INPUT_NOT_SOURCE_OR_BROKER_PROOF"
_SOURCE_ORIGIN = "SOURCE_REFERENCED_TRIGGER_NOT_EXIT_OR_BROKER_QUALIFICATION"


class _KnownConflict(Exception):
    """An identical conflicting command is already immutable evidence."""


def _text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise JournalError("exact crossed-stop identity required")
    return value


def _time(value: datetime) -> str:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise JournalError("UTC crossed-stop clock required")
    return value.isoformat()


def _fraction(value: object, *, positive: bool = False) -> Fraction:
    if type(value) is not str:
        raise JournalError("canonical exact rational required")
    try:
        q = Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise JournalError("canonical exact rational required") from None
    if str(q) != value or q < 0 or (positive and not q):
        raise JournalError("canonical nonnegative rational required")
    return q


def _decimal(q: Fraction) -> str:
    denominator = q.denominator
    for prime in (2, 5):
        while denominator % prime == 0:
            denominator //= prime
    if denominator != 1:
        raise JournalError("broker decimal quantity unqualified; no rounding")
    digits = 0
    while (q * 10**digits).denominator != 1:
        digits += 1
    if digits > 9:
        raise JournalError("broker quantity precision unqualified; no rounding")
    units = (q * 10**digits).numerator
    if not digits:
        return str(units)
    text = str(units).zfill(digits + 1)
    return text[:-digits] + "." + text[-digits:]


@dataclass(frozen=True)
class DeclaredExitEvidence:
    """Caller declarations for deterministic tests; cannot grant source acceptance.

    Freshness booleans refer to an external qualifier, not a new numeric TTL.
    Matching decision time only prevents reuse/backdating of this declared view.
    """

    account_ref: str
    symbol: str
    entry_scope: str
    as_of: datetime
    bid: Fraction | None
    quote_at: datetime | None
    quote_qualified_fresh: bool
    inputs_current: bool
    terminal_entry_confirmed: bool
    conflicting_buys_absent: bool
    ownership_reconciled: bool
    regular_session_open: bool
    asset_tradable: bool
    halted: bool
    position_quantity: Fraction
    available_quantity: Fraction

    def to_dict(self) -> dict[str, Any]:
        for value in (self.account_ref, self.symbol, self.entry_scope):
            _text(value)
        _time(self.as_of)
        for name in (
            "quote_qualified_fresh",
            "inputs_current",
            "terminal_entry_confirmed",
            "conflicting_buys_absent",
            "ownership_reconciled",
            "regular_session_open",
            "asset_tradable",
            "halted",
        ):
            if type(getattr(self, name)) is not bool:
                raise JournalError("exact declared boolean required")
        for quantity in (self.position_quantity, self.available_quantity):
            if type(quantity) is not Fraction or quantity < 0:
                raise JournalError("nonnegative exact declared quantity required")
        if self.available_quantity > self.position_quantity:
            raise JournalError("available quantity exceeds declared position")
        if self.bid is not None and (type(self.bid) is not Fraction or self.bid <= 0):
            raise JournalError("positive exact bid required")
        if self.quote_at is not None:
            _time(self.quote_at)
            if self.quote_at > self.as_of:
                raise JournalError("future quote cannot qualify")
        if self.quote_qualified_fresh and (self.bid is None or self.quote_at is None):
            raise JournalError("fresh quote declaration lacks bid/clock")
        row = asdict(self)
        row.update(
            as_of=_time(self.as_of),
            bid=None if self.bid is None else str(self.bid),
            quote_at=None if self.quote_at is None else _time(self.quote_at),
            position_quantity=str(self.position_quantity),
            available_quantity=str(self.available_quantity),
        )
        return row

    @classmethod
    def parse(cls, value: dict[str, Any]) -> DeclaredExitEvidence:
        row = dict(value)
        row["as_of"] = datetime.fromisoformat(row["as_of"])
        if row["quote_at"] is not None:
            row["quote_at"] = datetime.fromisoformat(row["quote_at"])
        for key in ("bid", "position_quantity", "available_quantity"):
            if row[key] is not None:
                row[key] = _fraction(row[key], positive=key == "bid")
        parsed = cls(**row)
        if parsed.to_dict() != value:
            raise JournalError("noncanonical declared exit evidence")
        return parsed


class CrossedStopJournal:
    """Immutable replay and transaction-fenced, non-dispatchable exit proposals."""

    def __init__(self, owner: OcoJournal) -> None:
        if type(owner) is not OcoJournal:
            raise JournalError("canonical OCO lot owner required")
        manifest = json.loads(load_crossed_stop_contract().raw)
        self.owner, self.journal = owner, owner.journal
        self._binding_bytes = _json(
            dict(
                owner.binding,
                emergency_version=VERSION,
                emergency_manifest_sha256=MANIFEST_SHA256,
            )
        )
        self._authority_bytes = _json(manifest["authority"])
        self.state_key = (
            "crossed_stop:"
            + _json([owner.initial.account_ref, owner.initial.entry_scope]).decode()
        )

    @property
    def binding(self) -> dict[str, Any]:
        return dict(json.loads(self._binding_bytes))

    @property
    def authority(self) -> dict[str, bool]:
        return dict(json.loads(self._authority_bytes))

    def _empty(self) -> dict[str, Any]:
        return {
            "binding": self.binding,
            "origin": _ORIGIN,
            "latched": False,
            "last_at": None,
            "closes": {},
            "conflicts": [],
            "authority": self.authority,
        }

    def _validate_item(self, item: dict[str, Any]) -> None:
        if set(item) != {"binding", "kind", "key", "at", "data", "horizon"}:
            raise JournalError("exact crossed-stop event schema required")
        kind, data = item["kind"], item["data"]
        keys = {
            "SOURCE_OBSERVE": {"receipt"},
            "SOURCE_PREPARE": {"close_id", "request", "receipt"},
            "OBSERVE": {"evidence"},
            "PREPARE": {"close_id", "evidence"},
            "ATTEMPT": {"close_id", "attempt_id", "evidence"},
            "EXECUTION": {"close_id", "execution_id", "quantity"},
        }
        if kind not in keys or type(data) is not dict or set(data) != keys[kind]:
            raise JournalError("exact crossed-stop operation schema required")
        identity = json.loads(item["key"])
        if (
            type(identity) is not list
            or len(identity) != 3
            or identity[:2] != [self.binding["entry_scope"], kind]
            or _json(identity).decode() != item["key"]
        ):
            raise JournalError("crossed-stop operation key differs")
        _text(identity[2])
        if (
            kind not in {"OBSERVE", "SOURCE_OBSERVE"}
            and identity[2]
            != data["execution_id" if kind == "EXECUTION" else "close_id"]
        ):
            raise JournalError("crossed-stop command identity differs")

    def _reduce(
        self, state: dict[str, Any], item: dict[str, Any], conflict: bool
    ) -> dict[str, Any]:
        state = json.loads(_json(state))
        self._validate_item(item)
        if conflict:
            state["conflicts"] = sorted(set(state["conflicts"]) | {item["key"]})
            return state
        at = datetime.fromisoformat(item["at"])
        if _time(at) != item["at"] or (
            state["last_at"] is not None
            and at < datetime.fromisoformat(state["last_at"])
        ):
            raise JournalError("crossed-stop event clock regressed")
        view = self.owner.read_verified_at(item["horizon"], at)
        if view is None or view.conflict_keys:
            raise JournalError("clean OCO prefix required")
        model = view.model
        kind, data = item["kind"], item["data"]
        closes = state["closes"]
        if kind == "SOURCE_OBSERVE":
            from .crossed_stop_source import derive_source_crossing

            if state["origin"] == _ORIGIN and state["last_at"] is not None:
                raise JournalError("declared and source crossing modes cannot mix")
            receipt = derive_source_crossing(self.owner, horizon=item["horizon"], at=at)
            if _json(receipt) != _json(data["receipt"]):
                raise JournalError("source crossing differs from original raw prefix")
            state["origin"] = _SOURCE_ORIGIN
            state.setdefault("source_observations", []).append(receipt)
            state["latched"] = state["latched"] or receipt["crossing_observed"]
        elif kind == "SOURCE_PREPARE":
            from .crossed_stop_preparation import derive_source_preparation

            if (
                state["origin"] != _SOURCE_ORIGIN
                or not state["latched"]
                or state["conflicts"]
                or set(model.incidents) - {"FINAL_TARGET_TOPOLOGY_INVALID"}
            ):
                raise JournalError("clean source-latched emergency scope required")
            receipt = derive_source_preparation(
                self.owner, horizon=item["horizon"], at=at, request=data["request"]
            )
            if _json(receipt) != _json(data["receipt"]):
                raise JournalError(
                    "source preparation differs from original raw prefix"
                )
            self._reserve_close(
                state,
                model,
                _text(data["close_id"]),
                _fraction(receipt["reported_available_quantity"]),
                item["horizon"],
            )
            closes[data["close_id"]]["source_preparation"] = receipt
        elif state["origin"] == _SOURCE_ORIGIN:
            raise JournalError("declared exit inputs cannot clear source crossing")
        elif kind in {"OBSERVE", "PREPARE", "ATTEMPT"}:
            evidence = DeclaredExitEvidence.parse(data["evidence"])
            if (evidence.account_ref, evidence.symbol, evidence.entry_scope) != (
                model.account_ref,
                model.symbol,
                model.entry_scope,
            ) or evidence.as_of != at:
                raise JournalError("declared evidence scope/decision differs")
            if (
                model.unallocated_quantity > 0
                and evidence.inputs_current
                and evidence.quote_qualified_fresh
                and evidence.bid is not None
                and evidence.bid <= model.stop
            ):
                state["latched"] = True
            if kind != "OBSERVE":
                # A fill below S makes the parent's final TARGET invalid. That
                # does not invalidate exact ownership of a risk-reducing exit.
                # Keep the parent incident; quantity/identity incidents still veto.
                blocking = set(model.incidents) - {"FINAL_TARGET_TOPOLOGY_INVALID"}
                if state["conflicts"] or blocking or not state["latched"]:
                    raise JournalError("clean latched emergency scope required")
                if not (
                    evidence.inputs_current
                    and evidence.terminal_entry_confirmed
                    and model.entry_terminal_reconciled
                    and evidence.conflicting_buys_absent
                    and evidence.ownership_reconciled
                    and evidence.regular_session_open
                    and evidence.asset_tradable
                    and not evidence.halted
                ):
                    raise JournalError("terminal/source/ownership/session gate failed")
                exited = sum(
                    (_fraction(c["filled"]) for c in closes.values()), Fraction(0)
                )
                if evidence.position_quantity != model.owned_quantity - exited:
                    raise JournalError("position/lot census differs")
                close_id = _text(data["close_id"])
                if kind == "PREPARE":
                    self._reserve_close(
                        state,
                        model,
                        close_id,
                        evidence.available_quantity,
                        item["horizon"],
                    )
                else:
                    close = closes.get(close_id)
                    if close is None or close["attempt"] is not None:
                        raise JournalError("prepared unattempted close required")
                    prior = self.owner.read_verified_at(close["parent_horizon"], at)
                    if prior is None or prior != view:
                        raise JournalError("parent census changed since preparation")
                    if evidence.available_quantity < _fraction(close["quantity"]):
                        raise JournalError("broker availability changed")
                    close["attempt"] = _text(data["attempt_id"])
        elif kind == "EXECUTION":
            close = closes.get(_text(data["close_id"]))
            if close is None or close["attempt"] is None:
                raise JournalError("declared exit requires original attempt")
            execution_id = _text(data["execution_id"])
            if any(execution_id in c["executions"] for c in closes.values()) or any(
                e.execution_id == execution_id for e in model.executions
            ):
                raise JournalError("execution identity already assigned")
            q = _fraction(data["quantity"], positive=True)
            close["executions"][execution_id] = str(q)
            close["filled"] = str(_fraction(close["filled"]) + q)
            if _fraction(close["filled"]) > _fraction(close["quantity"]):
                state["conflicts"] = sorted(
                    set(state["conflicts"]) | {"EMERGENCY_OVER_EXIT"}
                )
        else:
            raise JournalError("unknown crossed-stop operation")
        state["last_at"] = item["at"]
        return state

    def _reserve_close(
        self,
        state: dict[str, Any],
        model: OcoResearchState,
        close_id: str,
        available: Fraction,
        horizon: int,
    ) -> None:
        closes = state["closes"]
        if any(
            _fraction(c["filled"]) != _fraction(c["quantity"]) for c in closes.values()
        ):
            raise JournalError("existing emergency reservation unresolved")
        if close_id in closes:
            raise JournalError("close identity already reserved")
        assigned: dict[str, Fraction] = {}
        for group in model.groups:
            for part in group.portions:
                assigned[part.entry_execution_id] = (
                    assigned.get(part.entry_execution_id, Fraction(0)) + part.quantity
                )
        for close in closes.values():
            for entry, quantity in close["portions"].items():
                assigned[entry] = assigned.get(entry, Fraction(0)) + _fraction(quantity)
        portions = {
            e.execution_id: str(e.quantity - assigned.get(e.execution_id, Fraction(0)))
            for e in model.executions
            if e.role == "ENTRY"
            and e.quantity > assigned.get(e.execution_id, Fraction(0))
        }
        quantity = sum((_fraction(q) for q in portions.values()), Fraction(0))
        if not quantity or available < quantity:
            raise JournalError("exact unreserved quantity unavailable")
        client_id = (
            "cs-" + hashlib.sha256(_json([self.binding, close_id])).hexdigest()[:40]
        )
        closes[close_id] = {
            "portions": portions,
            "quantity": str(quantity),
            "filled": "0",
            "executions": {},
            "attempt": None,
            "parent_horizon": horizon,
            "request": {
                "symbol": model.symbol,
                "side": "sell",
                "type": "market",
                "time_in_force": "day",
                "qty": _decimal(quantity),
                "client_order_id": client_id,
            },
            "dispatch_compatible": False,
        }

    def _replay(
        self,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, set[str]]]:
        with causal_risk_verification_scope(self.journal):
            return self._replay_verified()

    def _replay_verified(
        self,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, set[str]]]:
        state = self._empty()
        first: dict[str, str] = {}
        seen: dict[str, set[str]] = {}
        expected: dict[str, object] = {}
        for event, raw in self.journal.read_crossed_stop_events():
            item = json.loads(raw)
            if item["binding"]["account_ref"] != self.binding["account_ref"]:
                raise JournalError("foreign crossed-stop account")
            if item["binding"]["entry_scope"] != self.binding["entry_scope"]:
                continue
            if (
                item["binding"] != self.binding
                or _json(item) != raw
                or set(item) != {"binding", "kind", "key", "at", "data", "horizon"}
                or event.feed != "paper"
                or event.event_kind != item["kind"]
                or type(item["horizon"]) is not int
                or not 0 <= item["horizon"] < event.sequence
                or event.available_at != datetime.fromisoformat(item["at"])
            ):
                raise JournalError("crossed-stop immutable event differs")
            key = item["key"]
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            if digest in seen.get(key, set()):
                raise JournalError("duplicate immutable crossed-stop evidence")
            state = self._reduce(state, item, key in first and first[key] != digest)
            first.setdefault(key, digest)
            seen.setdefault(key, set()).add(digest)
            expected.setdefault(key, {"hash": digest, "sequence": event.sequence})
        cached = self.journal.read_state(self.state_key)
        if cached is not None and _json(cached) != _json(state):
            raise JournalError("crossed-stop projection differs from evidence")
        if expected and cached is None:
            raise JournalError("crossed-stop projection missing")
        scoped = {}
        for key, value in self.journal.read_crossed_stop_evidence_cache(
            self.binding["account_ref"]
        ).items():
            identity = json.loads(key)
            if identity[0] == self.binding["entry_scope"]:
                scoped[key] = value
        if _json(scoped) != _json(expected):
            raise JournalError("crossed-stop deduplication differs from evidence")
        projected, _, _ = self.owner._verified_replay()
        if projected is None:
            raise JournalError("current OCO binding missing")
        model = self.owner._view(projected).model
        emergency_ids = {
            identity
            for close in state["closes"].values()
            for identity in close["executions"]
        }
        if emergency_ids & {e.execution_id for e in model.executions}:
            raise JournalError("execution identity overlaps current OCO census")
        return state, first, seen

    def view(self) -> dict[str, Any]:
        """Verified read; caller-owned transactions are never committed."""
        owns = not self.journal._db.in_transaction
        if owns:
            self.journal._db.execute("BEGIN")
        try:
            self.journal.require_private_order_state()
            if self.journal.read_paper_account_ref() != self.binding["account_ref"]:
                raise JournalError("crossed-stop account not bound")
            return self._replay()[0]
        finally:
            if owns:
                self.journal._db.rollback()

    def _record(
        self, kind: str, identity: str, at: datetime, data: dict[str, Any]
    ) -> dict[str, Any]:
        if self.journal._db.in_transaction:
            raise JournalError("crossed-stop requires own transaction")
        key = _json([self.binding["entry_scope"], kind, _text(identity)]).decode()
        item: dict[str, Any] = {
            "binding": self.binding,
            "kind": kind,
            "key": key,
            "at": _time(at),
            "data": data,
            "horizon": 0,
        }
        result: dict[str, Any] | None = None

        def prepare(payload: bytes) -> bytes:
            with causal_risk_verification_scope(self.journal):
                return prepare_verified(payload)

        def prepare_verified(_: bytes) -> bytes:
            nonlocal result
            result, first, seen = self._replay()
            # Exact repeat reuses original prefix; changed commands latch conflict.
            if key in first:
                for _event, raw in self.journal.read_crossed_stop_events():
                    old = json.loads(raw)
                    if old["key"] == key and old["binding"] == self.binding:
                        item["horizon"] = old["horizon"]
                        if kind in {"SOURCE_OBSERVE", "SOURCE_PREPARE"}:
                            data["receipt"] = old["data"]["receipt"]
                        break
            else:
                item["horizon"] = self.journal.read_evidence_sequence()
                if kind == "SOURCE_OBSERVE":
                    from .crossed_stop_source import derive_source_crossing

                    data["receipt"] = derive_source_crossing(
                        self.owner, horizon=item["horizon"], at=at
                    )
                elif kind == "SOURCE_PREPARE":
                    from .crossed_stop_preparation import derive_source_preparation

                    data["receipt"] = derive_source_preparation(
                        self.owner,
                        horizon=item["horizon"],
                        at=at,
                        request=data["request"],
                        _require_current_census=True,
                    )
            raw = _json(item)
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            if key in result["conflicts"] and digest in seen.get(key, set()):
                raise _KnownConflict
            return raw

        def reduce(_: object, conflict: bool, sequence: int) -> object:
            nonlocal result
            with causal_risk_verification_scope(self.journal):
                assert result is not None
                result = self._reduce(result, item, conflict)
                return result

        try:
            self.journal.apply_crossed_stop_event(
                account_ref=self.binding["account_ref"],
                state_key=self.state_key,
                dedupe_key=key,
                event_kind=kind,
                payload=_json(item),
                received_at=at,
                reduce=reduce,
                prepare_payload=prepare,
            )
        except _KnownConflict:
            pass
        assert result is not None
        return result

    def observe(self, identity: str, evidence: DeclaredExitEvidence) -> dict[str, Any]:
        return self._record(
            "OBSERVE", identity, evidence.as_of, {"evidence": evidence.to_dict()}
        )

    def observe_source(self, identity: str, at: datetime) -> dict[str, Any]:
        """Derive the risk-selected raw quote; no caller truth/freshness flags."""
        return self._record("SOURCE_OBSERVE", identity, at, {"receipt": None})

    def prepare(self, close_id: str, evidence: DeclaredExitEvidence) -> dict[str, Any]:
        return self._record(
            "PREPARE",
            close_id,
            evidence.as_of,
            {"close_id": close_id, "evidence": evidence.to_dict()},
        )

    def prepare_source(
        self,
        close_id: str,
        at: datetime,
        *,
        asset_sequence: int,
        status_sequence: int,
        status_record_index: int,
        max_asset_age: timedelta,
        max_status_age: timedelta,
    ) -> dict[str, Any]:
        """Reserve raw-referenced lots locally; not no-halt or dispatch clearance."""
        from .crossed_stop_preparation import preparation_request

        return self._record(
            "SOURCE_PREPARE",
            close_id,
            at,
            {
                "close_id": close_id,
                "request": preparation_request(
                    asset_sequence,
                    status_sequence,
                    status_record_index,
                    max_asset_age,
                    max_status_age,
                ),
                "receipt": None,
            },
        )

    def prepare_native_source(
        self,
        close_id: str,
        at: datetime,
        *,
        asset_sequence: int,
        status_sequence: int,
        status_record_index: int,
        max_asset_age: timedelta,
        max_status_age: timedelta,
    ) -> dict[str, Any]:
        """Reserve only source-owned unallocated lots; no dispatch clearance."""
        from .crossed_stop_preparation import native_preparation_request

        return self._record(
            "SOURCE_PREPARE",
            close_id,
            at,
            {
                "close_id": close_id,
                "request": native_preparation_request(
                    asset_sequence,
                    status_sequence,
                    status_record_index,
                    max_asset_age,
                    max_status_age,
                ),
                "receipt": None,
            },
        )

    def mark_attempt(
        self, close_id: str, attempt_id: str, evidence: DeclaredExitEvidence
    ) -> dict[str, Any]:
        return self._record(
            "ATTEMPT",
            close_id,
            evidence.as_of,
            {
                "close_id": close_id,
                "attempt_id": attempt_id,
                "evidence": evidence.to_dict(),
            },
        )

    def observe_exit(
        self, close_id: str, execution_id: str, quantity: Fraction, at: datetime
    ) -> dict[str, Any]:
        if type(quantity) is not Fraction or quantity <= 0:
            raise JournalError("positive exact declared execution required")
        return self._record(
            "EXECUTION",
            execution_id,
            at,
            {
                "close_id": close_id,
                "execution_id": execution_id,
                "quantity": str(quantity),
            },
        )
