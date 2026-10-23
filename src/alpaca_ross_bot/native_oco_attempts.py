"""One durable precall and raw outcome through an injected TEST callback only.

There is no HTTP, credentials, default transport, configuration or CLI here.
Precall intent proves a local fence, not transmission. Even matching 2xx bytes
are test observations, never broker ACK, inventory, protection or release proof.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any

from .journal import JournalError
from .native_oco_evidence import NativeOrderFact, _instant, _topology
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
from .paper_order_contract import decode_broker_json

_ORIGIN = "INJECTED_TEST_CALLBACK_NOT_BROKER_PROOF"
_ERRORS = {
    "TIMEOUT",
    "CONNECTION_ERROR",
    "OS_ERROR",
    "CALLBACK_ERROR",
    "INVALID_RESPONSE",
}


@dataclass(frozen=True, repr=False)
class NativeOcoCallbackResponse:
    status: int
    body: bytes


class _RecordedConflict(Exception):
    """Verified identical conflict bytes already exist under the write lock."""


def _command(
    group_id: str, attempt_id: str, decision_at: datetime, started_at: datetime
) -> dict[str, str]:
    if _utc(started_at) < _utc(decision_at):
        raise JournalError("native test precall predates decision")
    return {
        "group_id": _text(group_id),
        "attempt_id": _text(attempt_id),
        "decision_at": decision_at.isoformat(),
        "started_at": started_at.isoformat(),
    }


def _checked_command(value: object) -> dict[str, str]:
    row = _object(value, {"group_id", "attempt_id", "decision_at", "started_at"})
    result = _command(
        row["group_id"],
        row["attempt_id"],
        _clock(row["decision_at"]),
        _clock(row["started_at"]),
    )
    if row != result:
        raise JournalError("native test command is not canonical")
    return result


def _correlate(
    precall: dict[str, Any],
    outcome: dict[str, Any],
    prior: dict[str, dict[str, str]],
    group_key: str,
) -> dict[str, Any]:
    """Only exact reported fields; no fill census, ownership or ACK inference."""
    issues: list[str] = []
    orders: tuple[NativeOrderFact, ...] = ()
    if outcome["result_kind"] != "HTTP_RESPONSE":
        issues.append("CALLBACK_OUTCOME_UNKNOWN")
    elif not 200 <= outcome["http_status"] < 300:
        issues.append("NON_2XX_RESPONSE")
    else:
        raw = base64.b64decode(outcome["body_base64"], validate=True)
        try:
            document = decode_broker_json(raw)
            if not isinstance(document, dict) or type(document.get("id")) is not str:
                raise ValueError
            orders, topology_issues = _topology(raw, document["id"])
            issues.extend(topology_issues)
        except (ValueError, ArithmeticError):
            issues.append("RESPONSE_TOPOLOGY_UNQUALIFIED")
    identity = None
    if len(orders) == 2:
        root, stop = orders
        body = json.loads(precall["request_body"])
        quantity = Fraction(body["qty"])
        checks = (
            (
                root.client_order_id == body["client_order_id"],
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
        issues.extend(code for valid, code in checks if not valid)
        if root.filled_quantity or stop.filled_quantity:
            issues.append("RESPONSE_EXECUTION_CENSUS_NOT_OBSERVED")
        elif stop.quantity != quantity:
            issues.append("RESPONSE_QUANTITY_RELATION_UNQUALIFIED")
        if root.filled_quantity + stop.filled_quantity > quantity:
            issues.append("REPORTED_EXIT_TOTAL_EXCEEDS_PREPARED_QUANTITY")
        if any(
            order.replaces is not None or order.replaced_by is not None
            for order in orders
        ):
            issues.append("REPLACEMENT_HISTORY_UNRESOLVED")
        if any(
            _instant(order.created_at) < _instant(precall["command"]["started_at"])
            for order in orders
        ):
            issues.append("ORDER_CREATION_PRECEDES_PRECALL_OR_CLOCK_UNQUALIFIED")
        if any(
            _instant(stamp) > _instant(outcome["completed_at"])
            for order in orders
            for stamp in (order.created_at, order.updated_at, order.submitted_at)
            if stamp is not None
        ):
            issues.append("FUTURE_RESPONSE_ORDER_CLOCK")
        identity = {
            "root_order_id": root.order_id,
            "stop_order_id": stop.order_id,
            "broker_group_id": root.group_id,
        }
        for key, previous in prior.items():
            if key != group_key and (
                {previous["root_order_id"], previous["stop_order_id"]}
                & {root.order_id, stop.order_id}
                or previous["broker_group_id"] == root.group_id
            ):
                issues.append("RESPONSE_BROKER_IDENTITY_REUSED")
    return {
        "status": "REPORTED_FIELDS_MATCH" if not issues else "UNQUALIFIED",
        "orders": [order.to_dict() for order in orders],
        "candidate_identity": identity,
        "issues": list(dict.fromkeys(issues)),
    }


class NativeOcoAttempts:
    def __init__(self, requests: NativeOcoRequests) -> None:
        if type(requests) is not NativeOcoRequests:
            raise JournalError("canonical native preparation owner required")
        self.requests, self.journal = requests, requests.journal
        self.binding: dict[str, Any] = _copy(requests.binding)
        self.state_key = (
            "native_oco_attempt:"
            + _json([self.binding["account_ref"], self.binding["entry_scope"]]).decode()
        )
        self.view()

    @staticmethod
    def _key(binding: dict[str, Any], group: str, phase: str) -> str:
        return _json([binding["entry_scope"], group, phase]).decode()

    def _derive(self, command: dict[str, str], horizon: int) -> dict[str, Any]:
        prefix = self.requests.read_verified_at(horizon, _clock(command["decision_at"]))
        if prefix is None or prefix["conflict_groups"]:
            raise JournalError("clean historical native preparation required")
        prepared = next(
            (
                row
                for row in prefix["requests"]
                if row["receipt"]["group_id"] == command["group_id"]
            ),
            None,
        )
        if prepared is None:
            raise JournalError("native prepared group missing")
        return {
            "schema": 1,
            "classification": "DURABLE_PRECALL_INTENT_NOT_TRANSMISSION_PROOF",
            "origin": _ORIGIN,
            "binding": _copy(self.binding),
            "command": command,
            "source_horizon": horizon,
            "prepared_ref": prepared["request_ref"],
            "prepared_receipt": prepared["receipt"],
            "request_body": prepared["receipt"]["request_body"],
            "request_sha256": prepared["receipt"]["request_sha256"],
            "authority": dict(_AUTHORITY),
            "send_proven": False,
            "ack_proven": False,
        }

    def _item(self, event: Any, raw: bytes) -> dict[str, Any]:
        item = _object(
            json.loads(raw),
            {
                "schema",
                "binding",
                "kind",
                "dedupe_key",
                "command",
                "precall",
                "outcome",
            },
        )
        binding = self.requests._binding(item["binding"])
        command = _checked_command(item["command"])
        phase = "outcome" if item["kind"] == "FAKE_OUTCOME" else "precall"
        if (
            type(item["schema"]) is not int
            or item["schema"] != 1
            or event.source_id != "ALPACA_NATIVE_OCO_ATTEMPT"
            or event.feed != "paper"
            or event.event_kind
            != ("FAKE_OUTCOME" if item["kind"] == "FAKE_OUTCOME" else "PRECALL_INTENT")
            or event.external_clock_conflict
            or _json(item) != raw
            or item["kind"]
            not in {"PRECALL_INTENT", "FAKE_OUTCOME", "ATTEMPT_IDENTITY_CONFLICT"}
            or item["dedupe_key"] != self._key(binding, command["group_id"], phase)
            or event.available_at < _clock(command["started_at"])
        ):
            raise JournalError("native attempt immutable metadata differs")
        precall = _object(
            item["precall"],
            {
                "schema",
                "classification",
                "origin",
                "binding",
                "command",
                "source_horizon",
                "prepared_ref",
                "prepared_receipt",
                "request_body",
                "request_sha256",
                "authority",
                "send_proven",
                "ack_proven",
            },
        )
        original_command = _checked_command(precall["command"])
        ref = _object(precall["prepared_ref"], {"sequence", "hash"})
        horizon = precall["source_horizon"]
        if (
            type(precall["schema"]) is not int
            or precall["schema"] != 1
            or precall["classification"]
            != "DURABLE_PRECALL_INTENT_NOT_TRANSMISSION_PROOF"
            or precall["origin"] != _ORIGIN
            or precall["binding"] != binding
            or original_command["group_id"] != command["group_id"]
            or type(horizon) is not int
            or not 0 <= horizon < event.sequence
            or type(ref["sequence"]) is not int
            or not 0 < ref["sequence"] <= horizon
            or _json(precall["authority"]) != _json(_AUTHORITY)
            or precall["send_proven"] is not False
            or precall["ack_proven"] is not False
            or event.available_at < _clock(original_command["started_at"])
        ):
            raise JournalError("native precall identity differs")
        prepared = self.journal.read_event(ref["sequence"])
        if (
            prepared is None
            or prepared[0].payload_ref != ref["hash"]
            or not prepared[0].visible_at(_clock(original_command["decision_at"]))
        ):
            raise JournalError("native precall preparation source unavailable")
        prepared_item = self.requests._validate_item(*prepared)
        receipt = prepared_item["receipt"]
        if (
            prepared_item["outcome"] != "PREPARED_NOT_BROKER_VALIDATED"
            or receipt != precall["prepared_receipt"]
            or receipt["binding"] != binding
            or receipt["group_id"] != original_command["group_id"]
            or precall["request_body"] != receipt["request_body"]
            or precall["request_sha256"] != receipt["request_sha256"]
        ):
            raise JournalError("native precall rewrites prepared bytes")
        if item["kind"] != "ATTEMPT_IDENTITY_CONFLICT" and command != original_command:
            raise JournalError("native attempt command rewrites original")
        if item["kind"] == "PRECALL_INTENT" and (
            event.available_at != _clock(command["started_at"])
            or item["outcome"] is not None
        ):
            raise JournalError("native precall chronology differs")
        if item["kind"] == "ATTEMPT_IDENTITY_CONFLICT" and item["outcome"] is not None:
            raise JournalError("native conflict cannot replace raw outcome")
        if item["kind"] == "FAKE_OUTCOME":
            self._validate_outcome(item["outcome"], event.available_at)
        return item

    @staticmethod
    def _validate_outcome(value: object, received: datetime) -> dict[str, Any]:
        row = _object(
            value,
            {
                "schema",
                "classification",
                "precall_ref",
                "completed_at",
                "result_kind",
                "http_status",
                "body_base64",
                "error_category",
                "correlation",
                "authority",
                "send_proven",
                "ack_proven",
            },
        )
        if (
            type(row["schema"]) is not int
            or row["schema"] != 1
            or row["classification"] != _ORIGIN
            or _clock(row["completed_at"]) != received
            or _json(row["authority"]) != _json(_AUTHORITY)
            or row["send_proven"] is not False
            or row["ack_proven"] is not False
        ):
            raise JournalError("native fake outcome identity differs")
        ref = _object(row["precall_ref"], {"sequence", "hash"})
        if (
            type(ref["sequence"]) is not int
            or ref["sequence"] < 1
            or type(ref["hash"]) is not str
        ):
            raise JournalError("native outcome precall reference differs")
        if row["result_kind"] == "HTTP_RESPONSE":
            if (
                type(row["http_status"]) is not int
                or not 100 <= row["http_status"] <= 599
                or type(row["body_base64"]) is not str
                or row["error_category"] is not None
            ):
                raise JournalError("native raw response schema differs")
            try:
                raw = base64.b64decode(row["body_base64"], validate=True)
            except (ValueError, binascii.Error):
                raise JournalError("native raw response base64 differs") from None
            if base64.b64encode(raw).decode() != row["body_base64"]:
                raise JournalError("native raw response base64 not canonical")
        elif (
            row["result_kind"] != "CALLBACK_UNKNOWN"
            or row["http_status"] is not None
            or row["body_base64"] is not None
            or row["error_category"] not in _ERRORS
        ):
            raise JournalError("native unknown outcome schema differs")
        return row

    def _projection(
        self,
        attempts: dict[str, dict[str, Any]],
        conflicts: set[str],
        watermark: int,
    ) -> dict[str, Any] | None:
        if not attempts:
            return None
        return {
            "schema": 1,
            "binding": self.binding,
            "attempts": [attempts[key] for key in sorted(attempts)],
            "conflict_groups": sorted(conflicts),
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
        dict[str, set[str]],
        dict[str, dict[str, str]],
    ]:
        self.requests._bound()
        self.requests.view()
        if self.requests.binding != self.binding:
            raise JournalError("native test attempt binding changed")
        originals: dict[str, dict[str, Any]] = {}
        attempts: dict[str, dict[str, Any]] = {}
        seen: dict[str, set[str]] = {}
        attempt_ids: dict[str, set[str]] = {}
        identities: dict[str, dict[str, str]] = {}
        expected: dict[str, object] = {}
        conflicts: set[str] = set()
        watermark = 0
        for event, raw in self.journal.read_native_oco_attempt_events():
            item = self._item(event, raw)
            binding, command, precall = (
                item["binding"],
                item["command"],
                item["precall"],
            )
            group = command["group_id"]
            group_key = self._key(binding, group, "precall")
            key, digest = item["dedupe_key"], event.payload_ref
            if digest in seen.get(key, set()):
                raise JournalError("duplicate immutable native test attempt")
            seen.setdefault(key, set()).add(digest)
            own = binding == self.binding
            prior = originals.get(group_key)
            if item["kind"] == "PRECALL_INTENT":
                if prior is not None or attempt_ids.get(
                    command["attempt_id"], set()
                ) - {group_key}:
                    raise JournalError("native precall or attempt ID reused")
                if own and _json(
                    self._derive(command, precall["source_horizon"])
                ) != _json(precall):
                    raise JournalError("native precall source replay differs")
                original = {
                    "precall_receipt": precall,
                    "attempt_ref": {"sequence": event.sequence, "hash": digest},
                    "outcome": None,
                    "outcome_ref": None,
                }
                originals[group_key] = original
                if own:
                    attempts[group] = original
                    expected[key] = {"hash": digest, "sequence": event.sequence}
            elif prior is None or precall != prior["precall_receipt"]:
                raise JournalError("native attempt original missing or changed")
            elif item["kind"] == "FAKE_OUTCOME":
                outcome = item["outcome"]
                if (
                    prior["outcome"] is not None
                    or outcome["precall_ref"] != prior["attempt_ref"]
                    or event.available_at < _clock(command["started_at"])
                ):
                    raise JournalError("native raw outcome rewrites original")
                correlation = _correlate(precall, outcome, identities, group_key)
                if _json(correlation) != _json(outcome["correlation"]):
                    raise JournalError("native response field replay differs")
                prior.update(
                    outcome=outcome,
                    outcome_ref={"sequence": event.sequence, "hash": digest},
                )
                if correlation["status"] == "REPORTED_FIELDS_MATCH":
                    identities[group_key] = correlation["candidate_identity"]
                if own:
                    expected[key] = {"hash": digest, "sequence": event.sequence}
                    if "RESPONSE_BROKER_IDENTITY_REUSED" in correlation["issues"]:
                        conflicts.add(group)
            else:
                if command == precall["command"]:
                    raise JournalError("native identity conflict has original command")
                if own:
                    conflicts.add(group)
            if attempt_ids.get(command["attempt_id"], set()) - {group_key}:
                raise JournalError("native test attempt identity collision")
            attempt_ids.setdefault(command["attempt_id"], set()).add(group_key)
            if own:
                watermark = event.sequence
        scoped = {}
        for key, value in self.journal.read_native_oco_attempt_evidence_cache(
            self.binding["account_ref"]
        ).items():
            identity = json.loads(key)
            if (
                type(identity) is not list
                or len(identity) != 3
                or any(type(part) is not str for part in identity)
                or _json(identity).decode() != key
            ):
                raise JournalError("native attempt dedupe identity differs")
            if identity[0] == self.binding["entry_scope"]:
                scoped[key] = value
        state = self._projection(attempts, conflicts, watermark)
        if _json(state) != _json(self.journal.read_state(self.state_key)) or _json(
            scoped
        ) != _json(expected):
            raise JournalError("native attempt projection/deduplication differs")
        return state, attempts, seen, attempt_ids, identities

    def _result(
        self, state: dict[str, Any], original: dict[str, Any]
    ) -> dict[str, Any]:
        issues = ["ATTEMPT_IDENTITY_CONFLICT"] if state["conflict_groups"] else []
        current = self.requests.view()
        if current is not None and current["conflict_groups"]:
            issues.append("PREPARATION_IDENTITY_CONFLICT")
        outcome = original["outcome"]
        if outcome is not None:
            issues.extend(outcome["correlation"]["issues"])
        result = _copy(
            {
                "schema": 1,
                "status": "CURRENT_CONFLICT"
                if state["conflict_groups"]
                else "PRECALL_CONSUMED_OUTCOME_UNKNOWN"
                if outcome is None
                else "INJECTED_CALLBACK_OUTCOME_RECORDED",
                **original,
                "issues": issues,
                "authority": dict(_AUTHORITY),
                "send_proven": False,
                "ack_proven": False,
            }
        )
        assert isinstance(result, dict)
        return result

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
                    ["ATTEMPT_IDENTITY_CONFLICT"] if state["conflict_groups"] else []
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

    def _record_pre(self, command: dict[str, str]) -> tuple[dict[str, Any], bool]:
        group = command["group_id"]
        key = self._key(self.binding, group, "precall")
        item: dict[str, Any] = {
            "schema": 1,
            "binding": self.binding,
            "kind": "PRECALL_INTENT",
            "dedupe_key": key,
            "command": command,
            "precall": None,
            "outcome": None,
        }
        result = None

        def prepare(_: bytes) -> bytes:
            nonlocal result
            state, originals, seen, ids, _identities = self._verified_replay()
            original = originals.get(group)
            if ids.get(command["attempt_id"], set()) - {key}:
                raise JournalError("native test attempt ID collision")
            if original is None:
                current = self.requests.view()
                if (
                    state is not None
                    and state["conflict_groups"]
                    or current is not None
                    and current["conflict_groups"]
                ):
                    raise JournalError("native conflict blocks new test callback")
                item["precall"] = self._derive(
                    command, self.journal.read_evidence_sequence()
                )
            else:
                item["precall"] = original["precall_receipt"]
                if _clock(command["started_at"]) < _clock(
                    item["precall"]["command"]["started_at"]
                ):
                    raise JournalError("native retry predates original precall")
                if command != item["precall"]["command"]:
                    item["kind"] = "ATTEMPT_IDENTITY_CONFLICT"
                assert state is not None
                result = self._result(state, original)
            encoded = _json(item)
            if item["kind"] == "ATTEMPT_IDENTITY_CONFLICT" and _digest(
                encoded
            ) in seen.get(key, set()):
                raise _RecordedConflict
            return encoded

        def reduce(old: object, conflict: bool, sequence: int) -> object:
            nonlocal result
            state = _copy(old)
            attempts = (
                {}
                if state is None
                else {
                    row["precall_receipt"]["command"]["group_id"]: row
                    for row in state["attempts"]
                }
            )
            conflicts = set(() if state is None else state["conflict_groups"])
            if conflict:
                if group not in attempts or item["kind"] != "ATTEMPT_IDENTITY_CONFLICT":
                    raise JournalError("native test attempt conflict lacks original")
                conflicts.add(group)
            else:
                attempts[group] = {
                    "precall_receipt": item["precall"],
                    "attempt_ref": {"sequence": sequence, "hash": _digest(_json(item))},
                    "outcome": None,
                    "outcome_ref": None,
                }
            updated = self._projection(attempts, conflicts, sequence)
            assert updated is not None
            result = self._result(updated, attempts[group])
            return updated

        event = None
        try:
            event = self.journal.apply_native_oco_attempt_event(
                account_ref=self.binding["account_ref"],
                state_key=self.state_key,
                dedupe_key=key,
                event_kind="PRECALL_INTENT",
                payload=b"",
                received_at=_clock(command["started_at"]),
                prepare_payload=prepare,
                reduce=reduce,
            )
        except _RecordedConflict:
            pass
        if result is None:
            raise JournalError("native precall did not commit")
        # Conflict payloads retain the PRECALL_INTENT event namespace; only the
        # original payload kind can trigger a callback after its first commit.
        return result, event is not None and item["kind"] == "PRECALL_INTENT"

    def _record_outcome(
        self,
        original: dict[str, Any],
        completed: datetime,
        status: int | None,
        body: bytes | None,
        error: str | None,
    ) -> dict[str, Any]:
        if self.journal._db.in_transaction:
            raise JournalError("native outcome refuses callback-owned transaction")
        command = original["precall_receipt"]["command"]
        if _utc(completed) < _clock(command["started_at"]):
            raise JournalError("native callback outcome clock precedes precall")
        group = command["group_id"]
        key = self._key(self.binding, group, "outcome")
        outcome: dict[str, Any] = {
            "schema": 1,
            "classification": _ORIGIN,
            "precall_ref": original["attempt_ref"],
            "completed_at": completed.isoformat(),
            "result_kind": "HTTP_RESPONSE" if error is None else "CALLBACK_UNKNOWN",
            "http_status": status,
            "body_base64": None if body is None else base64.b64encode(body).decode(),
            "error_category": error,
            "correlation": None,
            "authority": dict(_AUTHORITY),
            "send_proven": False,
            "ack_proven": False,
        }
        item = {
            "schema": 1,
            "binding": self.binding,
            "kind": "FAKE_OUTCOME",
            "dedupe_key": key,
            "command": command,
            "precall": original["precall_receipt"],
            "outcome": outcome,
        }
        result = None

        def prepare(_: bytes) -> bytes:
            state, originals, _seen, _ids, identities = self._verified_replay()
            current = originals.get(group)
            if (
                current is None
                or current["attempt_ref"] != original["attempt_ref"]
                or current["outcome"] is not None
            ):
                raise JournalError(
                    "native outcome requires original uncompleted precall"
                )
            outcome["correlation"] = _correlate(
                item["precall"],
                outcome,
                identities,
                self._key(self.binding, group, "precall"),
            )
            self._validate_outcome(outcome, completed)
            return _json(item)

        def reduce(old: object, conflict: bool, sequence: int) -> object:
            nonlocal result
            if conflict or not isinstance(old, dict):
                raise JournalError("native raw outcome identity already consumed")
            attempts = {
                row["precall_receipt"]["command"]["group_id"]: row
                for row in _copy(old)["attempts"]
            }
            attempts[group].update(
                outcome=outcome,
                outcome_ref={"sequence": sequence, "hash": _digest(_json(item))},
            )
            conflicts = set(old["conflict_groups"])
            if "RESPONSE_BROKER_IDENTITY_REUSED" in outcome["correlation"]["issues"]:
                conflicts.add(group)
            updated = self._projection(attempts, conflicts, sequence)
            assert updated is not None
            result = self._result(updated, attempts[group])
            return updated

        self.journal.apply_native_oco_attempt_event(
            account_ref=self.binding["account_ref"],
            state_key=self.state_key,
            dedupe_key=key,
            event_kind="FAKE_OUTCOME",
            payload=b"",
            received_at=completed,
            prepare_payload=prepare,
            reduce=reduce,
        )
        if result is None:
            raise JournalError("native raw outcome did not commit")
        return result

    def run_fake_once(
        self,
        group_id: str,
        attempt_id: str,
        *,
        decision_at: datetime,
        started_at: datetime,
        callback: Callable[[bytes], NativeOcoCallbackResponse],
        clock: Callable[[], datetime],
    ) -> dict[str, Any]:
        """Exercise one injected callback, only after a committed local fence.

        Exact retries return original evidence without calling again. Any crash
        or failed outcome write leaves that same fence permanently consumed.
        Caller-owned transactions are rejected before writes/callbacks.
        """
        if self.journal._db.in_transaction:
            raise JournalError("native test callback refuses caller-owned transaction")
        if not callable(callback) or not callable(clock):
            raise JournalError("explicit test callback and clock required")
        command = _command(group_id, attempt_id, decision_at, started_at)
        result, newly_committed = self._record_pre(command)
        if not newly_committed:
            return result
        status = None
        body = None
        error = None
        try:
            response = callback(result["precall_receipt"]["request_body"].encode())
            if (
                type(response) is not NativeOcoCallbackResponse
                or type(response.status) is not int
                or not 100 <= response.status <= 599
                or type(response.body) is not bytes
            ):
                error = "INVALID_RESPONSE"
            else:
                status, body = response.status, response.body
        except TimeoutError:
            error = "TIMEOUT"
        except ConnectionError:
            error = "CONNECTION_ERROR"
        except OSError:
            error = "OS_ERROR"
        except Exception:
            error = "CALLBACK_ERROR"
        try:
            completed = _utc(clock())
        except Exception:
            raise JournalError(
                "native callback outcome UTC clock unavailable"
            ) from None
        return self._record_outcome(result, completed, status, body, error)
