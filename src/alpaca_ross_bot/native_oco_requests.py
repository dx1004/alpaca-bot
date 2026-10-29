"""Durable research-only PREPARED native OCO bytes, never a send permission.

An immutable preparation links existing source-bound entry lots and their
declared disjoint allocation. No send attempt, ACK, broker ownership, risk slot
or protective coverage is created or inferred here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Never

from ._causal_risk_memo import causal_risk_verification_scope
from .journal import JournalError
from .native_risk_registry import POLICY as NATIVE_POLICY
from .oco_entry_evidence import (
    _derive_native_entry_census,
    derive_entry_census,
    derive_native_owned_entry_census,
)
from .oco_journal import OcoJournal, _causal_oco_reader, _CausalOcoJournal
from .paper_order_contract import decode_broker_json

_KIND = "PREPARE_NATIVE_OCO"
_CLASSIFICATION = "PREPARED_NOT_BROKER_VALIDATED"
_AUTHORITY = dict.fromkeys(
    (
        "source_admission_accepted",
        "local_ownership_accepted",
        "protection_accepted",
        "runtime_authorized",
        "dispatch_authorized",
        "formal_sample_eligible",
    ),
    False,
)
_SPEC = {
    "version": "2.0.1",
    "commit": "2a973a046607c42742bf141184b71f1990fa1809",
    "sha256": "e67cf32d58cd5f918c593925e966bf737eff1e0f03f15c7c0ba467368eaa6d42",
}


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _copy(value: Any) -> Any:
    return json.loads(_json(value))


def _object(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise JournalError("exact native OCO request schema required")
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise JournalError("exact native OCO request identity required")
    return value


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise JournalError("UTC native OCO request clock required")
    return value


def _clock(value: object) -> datetime:
    text = _text(value)
    parsed = _utc(datetime.fromisoformat(text))
    if parsed.isoformat() != text:
        raise JournalError("canonical native OCO request clock required")
    return parsed


def _cent(value: Fraction) -> str:
    cents = value * 100
    if value <= 0 or cents.denominator != 1:
        raise JournalError("exact positive native OCO cent price required")
    return f"{cents.numerator // 100}.{cents.numerator % 100:02d}"


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _command(
    group_id: object, client_order_id: object, decision_at: datetime
) -> dict[str, str]:
    client = _text(client_order_id)
    if len(client) > 128:
        raise JournalError("native OCO client ID exceeds 128 characters")
    return {
        "group_id": _text(group_id),
        "client_order_id": client,
        "decision_at": _utc(decision_at).isoformat(),
    }


class _KnownConflict(Exception):
    """Only verified identical conflict bytes may bypass another immutable append."""


class NativeOcoRequests:
    def __init__(self, owner: OcoJournal) -> None:
        if type(owner) is not OcoJournal:
            raise JournalError("canonical OCO owner required")
        self._initialize(owner)
        self.view()

    def _initialize(self, owner: OcoJournal) -> None:
        self.owner = owner
        self.journal = owner.journal
        self.binding: dict[str, Any] = _copy(owner.binding)
        self.state_key = (
            "native_oco_request:"
            + _json([owner.initial.account_ref, owner.initial.entry_scope]).decode()
        )

    def _bound(self) -> None:
        self.journal.require_private_order_state()
        if (
            self.journal.read_paper_account_ref() != self.owner.initial.account_ref
            or self.owner.binding != self.binding
        ):
            raise JournalError("native OCO request frozen account/binding differs")

    def _binding(self, value: object) -> dict[str, Any]:
        row = _object(value, set(self.binding))
        for item in row.values():
            _text(item)
        for name in (
            "account_ref",
            "version",
            "candidate",
            "manifest_sha256",
            "parent_candidate",
            "parent_sha256",
        ):
            if row[name] != self.binding[name]:
                raise JournalError("foreign native OCO request contract/account")
        for name in ("limit", "stop"):
            number = Fraction(row[name])
            if number <= 0 or str(number) != row[name]:
                raise JournalError("canonical native OCO request binding required")
        if row["entry_scope"] == self.binding["entry_scope"] and row != self.binding:
            raise JournalError("native OCO request scope binding changed")
        return row

    @staticmethod
    def _key(scope: str, group_id: str) -> str:
        return _json([scope, group_id]).decode()

    def _derive(
        self, command: dict[str, str], horizon: int, received_at: datetime
    ) -> dict[str, Any]:
        decision = _clock(command["decision_at"])
        if _utc(received_at) < decision:
            raise JournalError("native OCO receipt predates decision")
        view = self.owner.read_verified_at(horizon, decision)
        if view is None or view.entry_binding is None or view.issues:
            raise JournalError("clean source-bound OCO allocation required")
        group = next(
            (g for g in view.model.groups if g.group_id == command["group_id"]), None
        )
        if (
            group is None
            or group.quantity <= 0
            or group.quantity.denominator != 1
            or group.acknowledgement_id is not None
            or any(e.group_id == group.group_id for e in view.model.executions)
        ):
            raise JournalError(
                "whole unacknowledged native OCO allocation without exits required"
            )
        if command["client_order_id"] == self.binding["entry_scope"]:
            raise JournalError("native OCO client ID must differ from entry scope")
        ownership_policy = view.entry_binding.get("ownership_policy")
        if ownership_policy is not None and ownership_policy != NATIVE_POLICY:
            raise JournalError("unsupported native OCO entry ownership policy")
        derive_entry = (
            derive_native_owned_entry_census
            if ownership_policy == NATIVE_POLICY
            else (
                _derive_native_entry_census
                if isinstance(self, _CausalNativeOcoRequests)
                else derive_entry_census
            )
        )
        fresh = derive_entry(
            self.journal,
            self.owner.initial,
            decision_at=decision,
            through_sequence=horizon,
            fee_policy=Path(view.entry_binding["fee_policy"]),
        )
        model_entries = [
            {
                "execution_id": e.execution_id,
                "account_ref": e.account_ref,
                "symbol": e.symbol,
                "entry_scope": e.entry_scope,
                "role": e.role,
                "quantity": str(e.quantity),
                "price": str(e.price),
                "group_id": e.group_id,
            }
            for e in view.model.executions
            if e.role == "ENTRY"
        ]
        source_entries = [
            {name: value for name, value in row.items() if name != "transaction_time"}
            for row in fresh["entry"]["executions"]
        ]

        def by_id(row: dict[str, Any]) -> str:
            return str(row["execution_id"])

        if (
            sorted(model_entries, key=by_id) != sorted(source_entries, key=by_id)
            or fresh["reservation_ref"] != view.entry_binding["reservation_ref"]
        ):
            raise JournalError(
                "new source entry fills are unbound or reservation differs"
            )
        source_ids = {row["execution_id"] for row in source_entries}
        if any(p.entry_execution_id not in source_ids for p in group.portions):
            raise JournalError("allocation entry lots are not source-bound")
        intent = self.journal.read_order_intent(self.binding["entry_scope"])
        if not isinstance(intent, dict) or type(intent.get("request_body")) is not str:
            raise JournalError("original engineering entry request missing")
        parent_body = decode_broker_json(intent["request_body"])
        if (
            not isinstance(parent_body, dict)
            or parent_body.get("time_in_force") != "day"
        ):
            raise JournalError(
                "native OCO parent engineering time_in_force must be day"
            )
        body = {
            "symbol": self.binding["symbol"],
            "qty": str(group.quantity.numerator),
            "side": "sell",
            "type": "limit",
            "order_class": "oco",
            "time_in_force": parent_body["time_in_force"],
            "client_order_id": command["client_order_id"],
            "take_profit": {"limit_price": _cent(view.model.provisional_target)},
            "stop_loss": {"stop_price": _cent(view.model.stop)},
        }
        body_bytes = _json(body)
        allocation_ref = entry_ref = bind_ref = None
        events = (
            self.journal._read_private_events("ALPACA_OCO_RESEARCH", horizon)
            if isinstance(self, _CausalNativeOcoRequests)
            else self.journal.read_oco_research_events()
        )
        for event, payload in events:
            if event.sequence > horizon:
                continue
            item = json.loads(payload)
            if item["binding"] != self.binding:
                continue
            ref = [event.sequence, event.payload_ref]
            if item["kind"] == "BIND" and bind_ref is None:
                bind_ref = ref
            if (
                item["kind"] == "ALLOCATION"
                and item["operation"]["group_id"] == group.group_id
                and allocation_ref is None
            ):
                allocation_ref = ref
            if (
                item["kind"] == "BOUND_ENTRY"
                and item["operation"]["outcome"] == "BOUND_ENTRY_CENSUS"
                and item["operation"]["receipt"] == view.entry_binding
            ):
                entry_ref = ref
        owner_record = self.journal.read_event(view.watermark)
        if (
            allocation_ref is None
            or entry_ref is None
            or bind_ref is None
            or owner_record is None
        ):
            raise JournalError(
                "original native OCO owner/allocation/entry references missing"
            )
        owner_ref = [view.watermark, owner_record[0].payload_ref]
        refs = [
            bind_ref,
            allocation_ref,
            entry_ref,
            owner_ref,
            fresh["capture_ref"],
            fresh["reservation_ref"],
            fresh["intent_origin"]["census_ref"],
            *fresh["risk_refs"],
            *fresh["source_refs"],
        ]
        exact: dict[int, str] = {}
        for sequence, digest in refs:
            record = self.journal.read_event(sequence)
            if (
                type(sequence) is not int
                or not 0 < sequence <= horizon
                or record is None
                or record[0].payload_ref != digest
                or not record[0].visible_at(decision)
                or (sequence in exact and exact[sequence] != digest)
            ):
                raise JournalError("native OCO request source reference unavailable")
            exact[sequence] = digest
        return {
            "schema": 1,
            "classification": _CLASSIFICATION,
            "binding": _copy(self.binding),
            **command,
            "received_at": received_at.isoformat(),
            "source_horizon": horizon,
            "request_body": body_bytes.decode(),
            "request_sha256": _digest(body_bytes),
            "allocation_ref": allocation_ref,
            "owner_ref": owner_ref,
            "entry_binding_ref": entry_ref,
            "reservation_ref": fresh["reservation_ref"],
            "entry_census": fresh,
            "source_refs": [[seq, exact[seq]] for seq in sorted(exact)],
            "spec": dict(_SPEC),
            "authority": dict(_AUTHORITY),
        }

    def _validate_item(self, event: Any, payload: bytes) -> dict[str, Any]:
        item = _object(
            json.loads(payload),
            {
                "schema",
                "binding",
                "command",
                "receipt",
                "source_horizon",
                "outcome",
                "dedupe_key",
            },
        )
        binding = self._binding(item["binding"])
        command = _object(
            item["command"], {"group_id", "client_order_id", "decision_at"}
        )
        canonical = _command(
            command["group_id"],
            command["client_order_id"],
            _clock(command["decision_at"]),
        )
        if (
            type(item["schema"]) is not int
            or item["schema"] != 1
            or event.source_id != "ALPACA_NATIVE_OCO_REQUEST"
            or event.feed != "paper"
            or event.event_kind != _KIND
            or _json(item) != payload
            or command != canonical
            or event.available_at < _clock(command["decision_at"])
            or item["dedupe_key"]
            != self._key(binding["entry_scope"], command["group_id"])
            or type(item["source_horizon"]) is not int
            or not 0 <= item["source_horizon"] < event.sequence
            or item["outcome"] not in {_CLASSIFICATION, "REQUEST_IDENTITY_CONFLICT"}
        ):
            raise JournalError("native OCO request immutable metadata differs")
        receipt = _object(
            item["receipt"],
            {
                "schema",
                "classification",
                "binding",
                "group_id",
                "client_order_id",
                "decision_at",
                "received_at",
                "source_horizon",
                "request_body",
                "request_sha256",
                "allocation_ref",
                "owner_ref",
                "entry_binding_ref",
                "reservation_ref",
                "entry_census",
                "source_refs",
                "spec",
                "authority",
            },
        )
        if (
            type(receipt["schema"]) is not int
            or receipt["schema"] != 1
            or receipt["classification"] != _CLASSIFICATION
            or receipt["binding"] != binding
            or receipt["group_id"] != command["group_id"]
            or type(receipt["source_horizon"]) is not int
            or receipt["source_horizon"] != item["source_horizon"]
            or receipt["spec"] != _SPEC
            or _json(receipt["authority"]) != _json(_AUTHORITY)
            or type(receipt["request_body"]) is not str
            or receipt["request_sha256"] != _digest(receipt["request_body"].encode())
            or _clock(receipt["received_at"]) < _clock(receipt["decision_at"])
            or event.available_at < _clock(receipt["received_at"])
        ):
            raise JournalError("native OCO request receipt metadata differs")
        _command(
            receipt["group_id"],
            receipt["client_order_id"],
            _clock(receipt["decision_at"]),
        )
        if item["outcome"] == _CLASSIFICATION and (
            any(command[name] != receipt[name] for name in command)
            or event.available_at != _clock(receipt["received_at"])
        ):
            raise JournalError("native OCO original command/receipt differs")
        refs = receipt["source_refs"]
        if type(refs) is not list or not refs:
            raise JournalError("native OCO request source references missing")
        prior_sequence = 0
        for reference in refs:
            if (
                type(reference) is not list
                or len(reference) != 2
                or type(reference[0]) is not int
                or not prior_sequence < reference[0] <= item["source_horizon"]
                or type(reference[1]) is not str
            ):
                raise JournalError("native OCO request source reference schema differs")
            record = self.journal.read_event(reference[0])
            if (
                record is None
                or record[0].payload_ref != reference[1]
                or not record[0].visible_at(_clock(receipt["decision_at"]))
            ):
                raise JournalError("native OCO request source reference unavailable")
            prior_sequence = reference[0]
        if any(
            receipt[name] not in refs
            for name in (
                "allocation_ref",
                "owner_ref",
                "entry_binding_ref",
                "reservation_ref",
            )
        ):
            raise JournalError("native OCO request anchor reference missing")
        return item

    def _projection(
        self, originals: dict[str, dict[str, Any]], conflicts: set[str], watermark: int
    ) -> dict[str, Any] | None:
        if not originals:
            return None
        return {
            "schema": 1,
            "binding": _copy(self.binding),
            "requests": [originals[group] for group in sorted(originals)],
            "conflict_groups": sorted(conflicts),
            "watermark": watermark,
            "authority": dict(_AUTHORITY),
        }

    def _verified_replay(
        self,
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, dict[str, Any]],
        dict[str, set[str]],
        dict[str, set[str]],
    ]:
        return self._replay_evidence()

    def _replay_evidence(
        self,
        through_sequence: int | None = None,
        decision_at: datetime | None = None,
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, dict[str, Any]],
        dict[str, set[str]],
        dict[str, set[str]],
    ]:
        """Verify complete bounded requests, including global client conflicts."""
        if isinstance(self, _CausalNativeOcoRequests) and through_sequence is None:
            raise JournalError("causal native reader requires an explicit horizon")
        self._bound()
        originals: dict[str, dict[str, Any]] = {}
        conflicts: set[str] = set()
        seen: dict[str, set[str]] = {}
        expected: dict[str, object] = {}
        global_first: dict[str, dict[str, Any]] = {}
        clients: dict[str, str] = {}
        claimed_clients: dict[str, set[str]] = {}
        watermark = 0
        events = (
            self.journal.read_native_oco_request_events()
            if through_sequence is None
            else self.journal._read_private_events(
                "ALPACA_NATIVE_OCO_REQUEST", through_sequence
            )
        )
        for event, payload in events:
            if decision_at is not None and (
                not event.visible_at(decision_at) or event.external_clock_conflict
            ):
                raise JournalError("native request prefix is not decision-visible")
            item = self._validate_item(event, payload)
            key, command = item["dedupe_key"], item["command"]
            for client_id in (
                command["client_order_id"],
                item["receipt"]["client_order_id"],
            ):
                claimed_clients.setdefault(client_id, set()).add(key)
                if claimed_clients[client_id] != {key}:
                    raise JournalError(
                        "native OCO client ID claimed by multiple request keys"
                    )
            digest = event.payload_ref
            prior = global_first.get(key)
            conflict = prior is not None and prior["hash"] != digest
            if digest in seen.get(key, set()):
                raise JournalError("duplicate immutable native OCO request event")
            seen.setdefault(key, set()).add(digest)
            if prior is None:
                if item["outcome"] != _CLASSIFICATION:
                    raise JournalError("native OCO request conflict has no original")
                client = item["receipt"]["client_order_id"]
                if client in clients and clients[client] != key:
                    raise JournalError(
                        "native OCO client ID has multiple positive requests"
                    )
                clients[client] = key
                global_first[key] = {"hash": digest, "item": item}
            elif (
                not conflict
                or item["outcome"] != "REQUEST_IDENTITY_CONFLICT"
                or command == prior["item"]["command"]
                or item["receipt"] != prior["item"]["receipt"]
                or item["source_horizon"] != prior["item"]["source_horizon"]
            ):
                raise JournalError("native OCO request conflict rewrites original")
            if item["binding"]["entry_scope"] != self.binding["entry_scope"]:
                continue
            group = command["group_id"]
            if prior is None:
                receipt = self._derive(
                    command,
                    item["source_horizon"],
                    _clock(item["receipt"]["received_at"]),
                )
                if _json(receipt) != _json(item["receipt"]):
                    raise JournalError(
                        "native OCO request original source replay differs"
                    )
                originals[group] = {
                    "receipt": receipt,
                    "request_ref": {"sequence": event.sequence, "hash": digest},
                }
                expected[key] = {"hash": digest, "sequence": event.sequence}
            else:
                conflicts.add(group)
            watermark = event.sequence
        scoped: dict[str, object] = {}
        for key, value in self.journal.read_native_oco_request_evidence_cache(
            self.binding["account_ref"]
        ).items():
            identity = json.loads(key)
            if (
                type(identity) is not list
                or len(identity) != 2
                or any(type(part) is not str for part in identity)
                or _json(identity).decode() != key
            ):
                raise JournalError("native OCO deduplication identity differs")
            if identity[0] == self.binding["entry_scope"]:
                if through_sequence is not None:
                    if (
                        type(value) is dict
                        and type(value.get("sequence")) is int
                        and value["sequence"] > through_sequence
                    ):
                        continue
                    reference = _object(value, {"hash", "sequence"})
                    _text(reference["hash"])
                    sequence = reference["sequence"]
                    if type(sequence) is not int or sequence < 1:
                        raise JournalError("native OCO deduplication sequence differs")
                scoped[key] = value
        projected = self._projection(originals, conflicts, watermark)
        if through_sequence is None:
            cached = self.journal.read_state(self.state_key)
            if _json(cached) != _json(projected):
                raise JournalError(
                    "native OCO request projection/deduplication differs"
                )
        if _json(scoped) != _json(expected):
            raise JournalError("native OCO request projection/deduplication differs")
        return projected, originals, seen, claimed_clients

    def view(self) -> dict[str, Any] | None:
        owns_read = not self.journal._db.in_transaction
        if owns_read:
            self.journal._db.execute("BEGIN")
        try:
            value = _copy(self._verified_replay()[0])
            assert value is None or isinstance(value, dict)
            return value
        finally:
            if owns_read:
                self.journal._db.rollback()

    def verify_rebuild(self) -> bool:
        self.view()
        return True

    def read_verified_at(
        self, through_sequence: int, decision_at: datetime
    ) -> dict[str, Any] | None:
        """Verified historical request prefix, never current dispatch clearance."""
        _utc(decision_at)
        if type(through_sequence) is not int or through_sequence < 0:
            raise JournalError("invalid historical native request horizon")
        owns_read = not self.journal._db.in_transaction
        if owns_read:
            self.journal._db.execute("BEGIN")
        try:
            self._verified_replay()
            if through_sequence > self.journal.read_evidence_sequence():
                raise JournalError("invalid historical native request horizon")
            originals: dict[str, dict[str, Any]] = {}
            conflicts: set[str] = set()
            watermark = 0
            for event, payload in self.journal.read_native_oco_request_events():
                if event.sequence > through_sequence:
                    continue
                item = self._validate_item(event, payload)
                if item["binding"] != self.binding:
                    continue
                if not event.visible_at(decision_at):
                    raise JournalError("native request prefix is not decision-visible")
                group = item["command"]["group_id"]
                if group not in originals:
                    originals[group] = {
                        "receipt": item["receipt"],
                        "request_ref": {
                            "sequence": event.sequence,
                            "hash": event.payload_ref,
                        },
                    }
                else:
                    conflicts.add(group)
                watermark = event.sequence
            return self._projection(originals, conflicts, watermark)
        finally:
            if owns_read:
                self.journal._db.rollback()

    @staticmethod
    def _result(state: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
        conflicts = bool(state["conflict_groups"])
        result = _copy(
            {
                "schema": 1,
                "status": "REQUEST_IDENTITY_CONFLICT" if conflicts else _CLASSIFICATION,
                **original,
                "issues": ["REQUEST_IDENTITY_CONFLICT"] if conflicts else [],
                "authority": dict(_AUTHORITY),
            }
        )
        assert isinstance(result, dict)
        return result

    def prepare(
        self,
        group_id: str,
        client_order_id: str,
        decision_at: datetime,
        received_at: datetime,
    ) -> dict[str, Any]:
        command = _command(group_id, client_order_id, decision_at)
        _utc(received_at)
        if received_at < decision_at:
            raise JournalError("native OCO receipt predates decision")
        key = self._key(self.binding["entry_scope"], group_id)
        item: dict[str, Any] = {
            "schema": 1,
            "binding": self.binding,
            "command": command,
            "source_horizon": None,
            "receipt": None,
            "outcome": "PENDING_DERIVATION",
            "dedupe_key": key,
        }
        result: dict[str, Any] | None = None

        def prepare_payload(raw: bytes) -> bytes:
            nonlocal result
            state, originals, seen, clients = self._verified_replay()
            original = originals.get(group_id)
            if original is not None:
                receipt = original["receipt"]
                if client_order_id != receipt["client_order_id"] and (
                    clients.get(client_order_id, set()) - {key}
                    or self.journal.read_order_intent(client_order_id) is not None
                ):
                    raise JournalError("native OCO client ID collision")
                original_command = {name: receipt[name] for name in command}
                item.update(
                    receipt=receipt,
                    source_horizon=receipt["source_horizon"],
                    outcome=_CLASSIFICATION
                    if command == original_command
                    else "REQUEST_IDENTITY_CONFLICT",
                )
                if received_at < _clock(receipt["received_at"]):
                    raise JournalError("native OCO retry predates original receipt")
                assert state is not None
                result = self._result(state, original)
            else:
                if state is not None and state["conflict_groups"]:
                    raise JournalError(
                        "native OCO request conflict blocks new preparation"
                    )
                if (
                    client_order_id in clients
                    or self.journal.read_order_intent(client_order_id) is not None
                ):
                    raise JournalError("native OCO client ID collision")
                horizon = self.journal.read_evidence_sequence()
                receipt = self._derive(command, horizon, received_at)
                item.update(
                    receipt=receipt, source_horizon=horizon, outcome=_CLASSIFICATION
                )
            encoded = _json(item)
            if (
                _digest(encoded) in seen.get(key, set())
                and item["outcome"] == "REQUEST_IDENTITY_CONFLICT"
            ):
                raise _KnownConflict
            return encoded

        def reduce(old: object | None, conflict: bool, sequence: int) -> object:
            nonlocal result
            state = None if old is None else _copy(old)
            originals = (
                {}
                if state is None
                else {row["receipt"]["group_id"]: row for row in state["requests"]}
            )
            conflicts = set(() if state is None else state["conflict_groups"])
            if conflict:
                if (
                    group_id not in originals
                    or item["outcome"] != "REQUEST_IDENTITY_CONFLICT"
                ):
                    raise JournalError("native OCO request conflict lacks original")
                conflicts.add(group_id)
            else:
                originals[group_id] = {
                    "receipt": item["receipt"],
                    "request_ref": {"sequence": sequence, "hash": _digest(_json(item))},
                }
            updated = self._projection(originals, conflicts, sequence)
            assert updated is not None
            result = self._result(updated, originals[group_id])
            return updated

        try:
            self.journal.apply_native_oco_request_event(
                account_ref=self.binding["account_ref"],
                state_key=self.state_key,
                dedupe_key=key,
                event_kind=_KIND,
                payload=_json(item),
                received_at=received_at,
                prepare_payload=prepare_payload,
                reduce=reduce,
            )
        except _KnownConflict:
            pass
        if result is None:
            raise JournalError("native OCO preparation did not commit")
        return result


class _CausalNativeOcoRequests(NativeOcoRequests):
    """Private bounded request reader; never a writer-adapter input."""

    def view(self) -> Never:
        raise JournalError("causal native reader requires an explicit horizon")

    def verify_rebuild(self) -> Never:
        raise JournalError("causal native reader cannot verify current state")

    def _verified_replay(self) -> Never:
        raise JournalError("causal native reader cannot verify current state")

    def prepare(
        self,
        group_id: str,
        client_order_id: str,
        decision_at: datetime,
        received_at: datetime,
    ) -> Never:
        raise JournalError("causal native reader cannot write")

    def read_verified_at(
        self, through_sequence: int, decision_at: datetime
    ) -> dict[str, Any] | None:
        _utc(decision_at)
        if type(through_sequence) is not int or through_sequence < 0:
            raise JournalError("invalid historical native request horizon")
        owns_read = not self.journal._db.in_transaction
        if owns_read:
            self.journal._db.execute("BEGIN")
        try:
            if through_sequence > self.journal.read_evidence_sequence():
                raise JournalError("invalid historical native request horizon")
            with causal_risk_verification_scope(self.journal):
                projected, _, _, _ = self._replay_evidence(
                    through_sequence, decision_at
                )
            value = _copy(projected)
            assert value is None or isinstance(value, dict)
            return value
        finally:
            if owns_read:
                self.journal._db.rollback()


def _causal_native_requests(owner: OcoJournal) -> NativeOcoRequests:
    """Build a causal child from validated initial values without current replay."""
    if type(owner) not in {OcoJournal, _CausalOcoJournal}:
        raise JournalError("canonical OCO owner required")
    reader = object.__new__(_CausalNativeOcoRequests)
    reader._initialize(_causal_oco_reader(owner.journal, owner.initial))
    reader._bound()
    return reader
