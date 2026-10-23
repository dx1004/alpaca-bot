"""Durable PREPARED-to-GET associations, never send/ACK or protective ownership."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any

from .journal import JournalError
from .native_oco_evidence import _instant, inspect_native_oco_capture
from .native_oco_requests import (
    _AUTHORITY,
    NativeOcoRequests,
    _clock,
    _copy,
    _digest,
    _json,
    _object,
    _text,
    _utc,
)

_KIND = "OBSERVE_NATIVE_OCO"
_CLASS = "REPORTED_NATIVE_OCO_ASSOCIATION_NOT_SEND_OR_OWNERSHIP"


def _command(
    event_id: str,
    group_id: str,
    capture_sequence: int,
    decision_at: datetime,
    max_age: timedelta,
) -> dict[str, Any]:
    if type(capture_sequence) is not int or capture_sequence <= 0:
        raise JournalError("positive native OCO capture sequence required")
    if type(max_age) is not timedelta or max_age <= timedelta(0):
        raise JournalError("positive native OCO association age required")
    return {
        "event_id": _text(event_id),
        "group_id": _text(group_id),
        "capture_sequence": capture_sequence,
        "decision_at": _utc(decision_at).isoformat(),
        "max_age_microseconds": max_age // timedelta(microseconds=1),
    }


def _decoded_command(value: object) -> dict[str, Any]:
    row = _object(
        value,
        {
            "event_id",
            "group_id",
            "capture_sequence",
            "decision_at",
            "max_age_microseconds",
        },
    )
    if type(row["max_age_microseconds"]) is not int or row["max_age_microseconds"] <= 0:
        raise JournalError("canonical native OCO association age required")
    rebuilt = _command(
        row["event_id"],
        row["group_id"],
        row["capture_sequence"],
        _clock(row["decision_at"]),
        timedelta(microseconds=row["max_age_microseconds"]),
    )
    if _json(row) != _json(rebuilt):
        raise JournalError("canonical native OCO association command required")
    return row


class _KnownConflict(Exception):
    pass


class NativeOcoCorrelations:
    def __init__(self, requests: NativeOcoRequests) -> None:
        if type(requests) is not NativeOcoRequests:
            raise JournalError("canonical native OCO requests required")
        self.requests = requests
        self.journal = requests.journal
        self.binding: dict[str, Any] = _copy(requests.binding)
        self.state_key = (
            "native_oco_correlation:"
            + _json(
                [
                    self.binding["account_ref"],
                    self.binding["entry_scope"],
                ]
            ).decode()
        )
        self.view()

    def _key(self, event_id: str) -> str:
        return _json([self.binding["entry_scope"], event_id]).decode()

    @staticmethod
    def _group_key(binding: dict[str, Any], group: str) -> str:
        return _json([binding["entry_scope"], group]).decode()

    def _capture_ref(
        self, command: dict[str, Any], horizon: int
    ) -> tuple[list[Any], bytes]:
        record = self.journal.read_event(command["capture_sequence"])
        if (
            record is None
            or record[0].sequence > horizon
            or not record[0].visible_at(_clock(command["decision_at"]))
            or (record[0].source_id, record[0].feed, record[0].event_kind)
            != ("ALPACA_NATIVE_OCO", "paper", "NATIVE_OCO_CAPTURE")
        ):
            raise JournalError("native OCO association capture reference unavailable")
        return [record[0].sequence, record[0].payload_ref], record[1]

    def _derive(
        self,
        command: dict[str, Any],
        horizon: int,
        received_at: datetime,
        assignments: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        decision = _clock(command["decision_at"])
        if _utc(received_at) < decision:
            raise JournalError("native OCO association receipt predates decision")
        for event, payload in self.journal.read_native_oco_correlation_events():
            if (
                event.sequence <= horizon
                and json.loads(payload)["binding"] == self.binding
            ):
                if not event.visible_at(decision):
                    raise JournalError(
                        "native OCO association prefix is not decision-visible"
                    )
        prefix = self.requests.read_verified_at(horizon, decision)
        if prefix is None or prefix["conflict_groups"]:
            raise JournalError("clean historical native OCO preparation required")
        prepared = next(
            (
                row
                for row in prefix["requests"]
                if row["receipt"]["group_id"] == command["group_id"]
            ),
            None,
        )
        if prepared is None:
            raise JournalError("native OCO group preparation missing")
        request_ref, request = prepared["request_ref"], prepared["receipt"]
        capture_ref, capture_raw = self._capture_ref(command, horizon)
        facts = inspect_native_oco_capture(
            self.journal,
            command["capture_sequence"],
            account_ref=self.binding["account_ref"],
            decision_at=decision,
            max_age=timedelta(microseconds=command["max_age_microseconds"]),
            through_sequence=horizon,
        )
        capture = json.loads(capture_raw)
        if capture_ref[0] <= request_ref["sequence"] or _instant(
            capture["started_at"]
        ) < _instant(request["received_at"]):
            raise JournalError("native OCO capture predates immutable preparation")
        issues = list(facts.issues)
        identity: dict[str, str] | None = None
        relation = "QUANTITY_RELATION_UNQUALIFIED"
        if facts.topology_observed and len(facts.orders) == 2:
            root, stop = facts.orders
            identity = {
                "root_order_id": root.order_id,
                "stop_order_id": stop.order_id,
                "broker_group_id": root.group_id,
            }
            body = json.loads(request["request_body"])
            quantity = Fraction(body["qty"])
            required = (
                (
                    root.client_order_id == request["client_order_id"],
                    "ROOT_CLIENT_ID_MISMATCH",
                ),
                (root.symbol == stop.symbol == body["symbol"], "SYMBOL_MISMATCH"),
                (root.side == stop.side == "sell", "SIDE_MISMATCH"),
                (root.quantity == quantity, "ROOT_QUANTITY_MISMATCH"),
                (
                    root.time_in_force == stop.time_in_force == body["time_in_force"],
                    "TIME_IN_FORCE_MISMATCH",
                ),
                (
                    root.order_type == "limit" and root.observed_order_class == "oco",
                    "ROOT_TYPE_CLASS_MISMATCH",
                ),
                (
                    root.limit_price == Fraction(body["take_profit"]["limit_price"]),
                    "TARGET_PRICE_MISMATCH",
                ),
                (
                    stop.order_type == "stop"
                    and stop.limit_price is None
                    and stop.stop_price == Fraction(body["stop_loss"]["stop_price"]),
                    "STOP_TERMS_MISMATCH",
                ),
            )
            issues.extend(code for valid, code in required if not valid)
            if any(
                _instant(order.created_at) < _instant(request["received_at"])
                for order in facts.orders
            ):
                issues.append(
                    "ORDER_CREATION_PRECEDES_PREPARATION_OR_CLOCK_UNQUALIFIED"
                )
            exits = sum((fill.quantity for fill in facts.fills), Fraction())
            if (
                facts.accounting_complete
                and root.quantity == quantity
                and exits <= quantity
            ):
                if root.filled_quantity == 0 and stop.quantity == quantity:
                    relation = "REQUESTED_QTY_REPORTED"
                elif (
                    root.filled_quantity > 0
                    and stop.quantity == quantity - root.filled_quantity
                    and stop.filled_quantity <= stop.quantity
                ):
                    relation = "OBSERVED_RESIDUAL_AFTER_TARGET_FILLS"
            if relation == "QUANTITY_RELATION_UNQUALIFIED":
                issues.append(relation)
        else:
            issues.append("NATIVE_TOPOLOGY_UNQUALIFIED")
        if not facts.accounting_complete:
            issues.append("CAPTURE_ACCOUNTING_INCOMPLETE")
        basis: list[dict[str, Any]] = []
        status = "BLOCKED" if issues else "MATCHED_REPORTED_FIELDS"
        if not issues and identity is not None:
            group_key = self._group_key(self.binding, command["group_id"])
            for key, prior in assignments.items():
                changed = key == group_key and any(
                    prior[name] != identity[name] for name in identity
                )
                reused = key != group_key and (
                    {prior["root_order_id"], prior["stop_order_id"]}
                    & {identity["root_order_id"], identity["stop_order_id"]}
                    or prior["broker_group_id"] == identity["broker_group_id"]
                )
                if changed or reused:
                    issues.append(
                        "LOCAL_GROUP_IDENTITY_CHANGED"
                        if changed
                        else "BROKER_ID_ASSIGNED_TO_OTHER_REQUEST"
                    )
                    basis.append(prior["association_ref"])
            if basis:
                status = "IDENTITY_CONFLICT"
        references = [
            *request["source_refs"],
            [request_ref["sequence"], request_ref["hash"]],
            capture_ref,
            *[[ref["sequence"], ref["hash"]] for ref in basis],
        ]
        exact: dict[int, str] = {}
        for sequence, digest in references:
            record = self.journal.read_event(sequence)
            if (
                type(sequence) is not int
                or not 0 < sequence <= horizon
                or record is None
                or record[0].payload_ref != digest
                or not record[0].visible_at(decision)
                or (sequence in exact and exact[sequence] != digest)
            ):
                raise JournalError("native OCO association source prefix unavailable")
            exact[sequence] = digest
        return {
            "schema": 1,
            "classification": _CLASS,
            "binding": _copy(self.binding),
            "command": command,
            "source_horizon": horizon,
            "received_at": received_at.isoformat(),
            "prepared_ref": request_ref,
            "prepared_receipt": request,
            "capture_ref": capture_ref,
            "source_refs": [[seq, exact[seq]] for seq in sorted(exact)],
            "source_observation": facts.to_dict(),
            "quantity_relation": relation,
            "candidate_identity": identity,
            "assignment_basis": basis,
            "status": status,
            "issues": list(dict.fromkeys(issues)),
            "authority": dict(_AUTHORITY),
            "send_proven": False,
            "ack_proven": False,
        }

    def _item(self, event: Any, payload: bytes) -> dict[str, Any]:
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
        binding = self.requests._binding(item["binding"])
        command = _decoded_command(item["command"])
        if (
            type(item["schema"]) is not int
            or item["schema"] != 1
            or _json(item) != payload
            or event.feed != "paper"
            or event.source_id != "ALPACA_NATIVE_OCO_CORRELATION"
            or event.event_kind != _KIND
            or item["dedupe_key"]
            != _json([binding["entry_scope"], command["event_id"]]).decode()
            or type(item["source_horizon"]) is not int
            or not 0 <= item["source_horizon"] < event.sequence
            or item["outcome"] not in {"OBSERVED", "REQUEST_IDENTITY_CONFLICT"}
            or event.available_at < _clock(command["decision_at"])
        ):
            raise JournalError("native OCO association immutable metadata differs")
        self._capture_ref(command, event.sequence - 1)
        receipt = _object(
            item["receipt"],
            {
                "schema",
                "classification",
                "binding",
                "command",
                "source_horizon",
                "received_at",
                "prepared_ref",
                "prepared_receipt",
                "capture_ref",
                "source_refs",
                "source_observation",
                "quantity_relation",
                "candidate_identity",
                "assignment_basis",
                "status",
                "issues",
                "authority",
                "send_proven",
                "ack_proven",
            },
        )
        if (
            type(receipt["schema"]) is not int
            or receipt["schema"] != 1
            or receipt["classification"] != _CLASS
            or receipt["binding"] != binding
            or type(receipt["source_horizon"]) is not int
            or receipt["source_horizon"] != item["source_horizon"]
            or _json(receipt["authority"]) != _json(_AUTHORITY)
            or receipt["send_proven"] is not False
            or receipt["ack_proven"] is not False
            or receipt["status"]
            not in {"MATCHED_REPORTED_FIELDS", "BLOCKED", "IDENTITY_CONFLICT"}
            or type(receipt["issues"]) is not list
            or any(type(issue) is not str for issue in receipt["issues"])
            or _clock(receipt["received_at"])
            < _clock(_decoded_command(receipt["command"])["decision_at"])
            or event.available_at < _clock(receipt["received_at"])
        ):
            raise JournalError("native OCO association receipt metadata differs")
        if item["outcome"] == "OBSERVED" and (
            _json(command) != _json(receipt["command"])
            or event.available_at != _clock(receipt["received_at"])
        ):
            raise JournalError("native OCO association original command differs")
        if type(receipt["source_refs"]) is not list:
            raise JournalError("native OCO association references missing")
        previous = 0
        for ref in receipt["source_refs"]:
            if (
                type(ref) is not list
                or len(ref) != 2
                or type(ref[0]) is not int
                or not previous < ref[0] <= item["source_horizon"]
            ):
                raise JournalError("native OCO association reference schema differs")
            record = self.journal.read_event(ref[0])
            if (
                record is None
                or record[0].payload_ref != ref[1]
                or not record[0].visible_at(_clock(receipt["command"]["decision_at"]))
            ):
                raise JournalError("native OCO association reference unavailable")
            previous = ref[0]
        return item

    def _projection(
        self,
        observations: dict[str, dict[str, Any]],
        assignments: dict[str, dict[str, Any]],
        conflicts: set[str],
        watermark: int,
    ) -> dict[str, Any] | None:
        if not observations:
            return None
        return {
            "schema": 1,
            "binding": _copy(self.binding),
            "observations": [observations[key] for key in sorted(observations)],
            "assignments": [
                row
                for key, row in sorted(assignments.items())
                if row["entry_scope"] == self.binding["entry_scope"]
            ],
            "conflict_keys": sorted(conflicts),
            "watermark": watermark,
            "authority": dict(_AUTHORITY),
            "send_proven": False,
            "ack_proven": False,
        }

    def _verified_replay(
        self,
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, dict[str, Any]],
        dict[str, set[str]],
        dict[str, dict[str, Any]],
    ]:
        self.requests._bound()
        self.requests.view()
        if self.requests.binding != self.binding:
            raise JournalError("native OCO association frozen binding differs")
        observations: dict[str, dict[str, Any]] = {}
        assignments: dict[str, dict[str, Any]] = {}
        originals: dict[str, dict[str, Any]] = {}
        seen: dict[str, set[str]] = {}
        expected: dict[str, object] = {}
        conflicts: set[str] = set()
        watermark = 0
        for event, payload in self.journal.read_native_oco_correlation_events():
            item = self._item(event, payload)
            key, digest = item["dedupe_key"], event.payload_ref
            prior = originals.get(key)
            if digest in seen.get(key, set()):
                raise JournalError("duplicate immutable native OCO association")
            seen.setdefault(key, set()).add(digest)
            if prior is None:
                if item["outcome"] != "OBSERVED":
                    raise JournalError("native OCO association conflict lacks original")
                originals[key] = item
            elif (
                item["outcome"] != "REQUEST_IDENTITY_CONFLICT"
                or _json(item["command"]) == _json(prior["command"])
                or _json(item["receipt"]) != _json(prior["receipt"])
                or item["source_horizon"] != prior["source_horizon"]
            ):
                raise JournalError("native OCO association conflict rewrites original")
            own = item["binding"] == self.binding
            receipt = item["receipt"]
            if own and prior is None:
                derived = self._derive(
                    item["command"],
                    item["source_horizon"],
                    _clock(receipt["received_at"]),
                    assignments,
                )
                if _json(derived) != _json(receipt):
                    raise JournalError("native OCO association source replay differs")
                observations[item["command"]["event_id"]] = {
                    "receipt": receipt,
                    "association_ref": {"sequence": event.sequence, "hash": digest},
                }
                expected[key] = {"hash": digest, "sequence": event.sequence}
            if prior is None and receipt["status"] == "MATCHED_REPORTED_FIELDS":
                identity = _object(
                    receipt["candidate_identity"],
                    {"root_order_id", "stop_order_id", "broker_group_id"},
                )
                for value in identity.values():
                    _text(value)
                group_key = self._group_key(
                    item["binding"], item["command"]["group_id"]
                )
                for other_key, row in assignments.items():
                    if (
                        other_key == group_key
                        and any(row[name] != identity[name] for name in identity)
                    ) or (
                        other_key != group_key
                        and (
                            {row["root_order_id"], row["stop_order_id"]}
                            & {identity["root_order_id"], identity["stop_order_id"]}
                            or row["broker_group_id"] == identity["broker_group_id"]
                        )
                    ):
                        raise JournalError(
                            "native OCO immutable positive assignments collide"
                        )
                assignments.setdefault(
                    group_key,
                    {
                        "group_id": item["command"]["group_id"],
                        "entry_scope": item["binding"]["entry_scope"],
                        **identity,
                        "prepared_ref": receipt["prepared_ref"],
                        "association_ref": {"sequence": event.sequence, "hash": digest},
                    },
                )
            if own:
                if prior is not None or receipt["status"] == "IDENTITY_CONFLICT":
                    conflicts.add(key)
                watermark = event.sequence
        scoped = {}
        for key, value in self.journal.read_native_oco_correlation_evidence_cache(
            self.binding["account_ref"]
        ).items():
            identity = json.loads(key)
            if (
                type(identity) is not list
                or len(identity) != 2
                or any(type(v) is not str for v in identity)
                or _json(identity).decode() != key
            ):
                raise JournalError(
                    "native OCO association deduplication identity differs"
                )
            if identity[0] == self.binding["entry_scope"]:
                scoped[key] = value
        state = self._projection(observations, assignments, conflicts, watermark)
        if _json(state) != _json(self.journal.read_state(self.state_key)) or _json(
            scoped
        ) != _json(expected):
            raise JournalError(
                "native OCO association projection/deduplication differs"
            )
        return state, observations, seen, assignments

    def _result(
        self, state: dict[str, Any], original: dict[str, Any]
    ) -> dict[str, Any]:
        issues = ["ASSOCIATION_IDENTITY_CONFLICT"] if state["conflict_keys"] else []
        current = self.requests.view()
        if current is not None and current["conflict_groups"]:
            issues.append("PREPARATION_IDENTITY_CONFLICT")
        value = _copy(
            {
                "schema": 1,
                "status": "CURRENT_CONFLICT"
                if issues
                else original["receipt"]["status"],
                **original,
                "issues": list(dict.fromkeys(original["receipt"]["issues"] + issues)),
                "authority": dict(_AUTHORITY),
                "send_proven": False,
                "ack_proven": False,
            }
        )
        assert isinstance(value, dict)
        return value

    def view(self) -> dict[str, Any] | None:
        owns_read = not self.journal._db.in_transaction
        if owns_read:
            self.journal._db.execute("BEGIN")
        try:
            state = _copy(self._verified_replay()[0])
            assert state is None or isinstance(state, dict)
            if state is not None:
                current = self.requests.view()
                state["issues"] = (
                    ["ASSOCIATION_IDENTITY_CONFLICT"] if state["conflict_keys"] else []
                ) + (
                    ["PREPARATION_IDENTITY_CONFLICT"]
                    if current is not None and current["conflict_groups"]
                    else []
                )
            return state
        finally:
            if owns_read:
                self.journal._db.rollback()

    def verify_rebuild(self) -> bool:
        self.view()
        return True

    def observe(
        self,
        event_id: str,
        group_id: str,
        capture_sequence: int,
        decision_at: datetime,
        max_age: timedelta,
        received_at: datetime,
    ) -> dict[str, Any]:
        command = _command(event_id, group_id, capture_sequence, decision_at, max_age)
        if _utc(received_at) < decision_at:
            raise JournalError("native OCO association receipt predates decision")
        key = self._key(event_id)
        item: dict[str, Any] = {
            "schema": 1,
            "binding": self.binding,
            "command": command,
            "receipt": None,
            "source_horizon": None,
            "outcome": "PENDING_DERIVATION",
            "dedupe_key": key,
        }
        result: dict[str, Any] | None = None

        def prepare(raw: bytes) -> bytes:
            nonlocal result
            state, originals, seen, assignments = self._verified_replay()
            original = originals.get(event_id)
            if original is not None:
                receipt = original["receipt"]
                changed = _json(receipt["command"]) != _json(command)
                if changed:
                    self._capture_ref(command, self.journal.read_evidence_sequence())
                if received_at < _clock(receipt["received_at"]):
                    raise JournalError("native OCO retry predates original association")
                item.update(
                    receipt=receipt,
                    source_horizon=receipt["source_horizon"],
                    outcome="REQUEST_IDENTITY_CONFLICT" if changed else "OBSERVED",
                )
                assert state is not None
                result = self._result(state, original)
            else:
                current = self.requests.view()
                if current is not None and current["conflict_groups"]:
                    raise JournalError(
                        "preparation conflict blocks new native association"
                    )
                horizon = self.journal.read_evidence_sequence()
                item.update(
                    receipt=self._derive(command, horizon, received_at, assignments),
                    source_horizon=horizon,
                    outcome="OBSERVED",
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
            observations = (
                {}
                if state is None
                else {
                    row["receipt"]["command"]["event_id"]: row
                    for row in state["observations"]
                }
            )
            assignments = (
                {}
                if state is None
                else {
                    self._group_key(self.binding, row["group_id"]): row
                    for row in state["assignments"]
                }
            )
            conflicts = set(() if state is None else state["conflict_keys"])
            if conflict:
                if (
                    event_id not in observations
                    or item["outcome"] != "REQUEST_IDENTITY_CONFLICT"
                ):
                    raise JournalError("native OCO association conflict lacks original")
                conflicts.add(key)
            else:
                receipt = item["receipt"]
                ref = {"sequence": sequence, "hash": _digest(_json(item))}
                observations[event_id] = {"receipt": receipt, "association_ref": ref}
                if receipt["status"] == "IDENTITY_CONFLICT":
                    conflicts.add(key)
                elif receipt["status"] == "MATCHED_REPORTED_FIELDS":
                    assignments.setdefault(
                        self._group_key(self.binding, group_id),
                        {
                            "group_id": group_id,
                            "entry_scope": self.binding["entry_scope"],
                            **receipt["candidate_identity"],
                            "prepared_ref": receipt["prepared_ref"],
                            "association_ref": ref,
                        },
                    )
            updated = self._projection(observations, assignments, conflicts, sequence)
            assert updated is not None
            result = self._result(updated, observations[event_id])
            return updated

        try:
            self.journal.apply_native_oco_correlation_event(
                account_ref=self.binding["account_ref"],
                state_key=self.state_key,
                dedupe_key=key,
                event_kind=_KIND,
                payload=_json(item),
                received_at=received_at,
                prepare_payload=prepare,
                reduce=reduce,
            )
        except _KnownConflict:
            pass
        if result is None:
            raise JournalError("native OCO association did not commit")
        return result
