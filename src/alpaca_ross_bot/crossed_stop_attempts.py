"""Durable INJECTED-only source-close provenance; never a broker sender.

A callback is an untrusted injected observation. PRECALL consumes its local
fence before invocation. Neither raw responses nor timeouts establish ownership.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from ._causal_risk_memo import causal_risk_verification_scope, record_path_resolution
from .crossed_stop_contract import MANIFEST_SHA256 as EMERGENCY_MANIFEST_SHA256
from .crossed_stop_contract import VERSION as EMERGENCY_VERSION
from .crossed_stop_preparation import derive_source_close_precall
from .events import EventEnvelope
from .journal import Journal, JournalError
from .market_input_checks import _datetime_ns
from .native_oco_requests import _clock, _copy, _digest, _json, _text, _utc
from .oco_journal import OcoJournal, _causal_oco_reader
from .oco_research import OcoResearchState, load_oco_research_contract
from .paper_engineering import EngineeringHarness, EngineeringScope

SOURCE = "ALPACA_SOURCE_CLOSE_INJECTED_ATTEMPTS"
ORIGIN = "INJECTED_TEST_CALLBACK_NOT_BROKER_PROOF"
AUTHORITY = dict.fromkeys(
    (
        "dispatch_authorized",
        "runtime_authorized",
        "formal_paper_authorized",
        "source_fill_ownership_accepted",
        "protection_accepted",
    ),
    False,
)


class _ExactRetry(Exception):
    """Leave proof scope before ending the owned transaction."""


@dataclass(frozen=True, repr=False)
class SourceCloseCallbackResponse:
    status: int
    body: bytes


def _scope(journal: Journal, payload: str, at: datetime) -> None:
    data = json.loads(payload)
    scope = EngineeringScope(
        experiment_id=data["experiment_id"],
        account_ref=data["account_ref"],
        formal_account_ref=data["formal_account_ref"],
        formal_runtime_root=Path(data["formal_runtime_root"]),
    )
    formal_target = scope.formal_runtime_root.resolve()
    engineering_target = journal._runtime_root.resolve()
    scope.validate(journal._runtime_root)
    events = journal.read_engineering_events()
    if (
        scope.payload().decode() != payload
        or scope.account_ref != journal.read_paper_account_ref()
        or not events
        or events[0][0].sequence != 1
        or events[0][0].event_kind != "SCOPE"
        or events[0][0].feed != "paper"
        or not events[0][0].visible_at(at)
        or events[0][1] != scope.payload()
        or events[0][0].payload_ref != _digest(scope.payload())
        or sum(event.event_kind == "SCOPE" for event, _ in events) != 1
        or journal.read_state("engineering_scope")
        != hashlib.sha256(scope.payload()).hexdigest()
    ):
        raise JournalError("source injected attempt engineering scope differs")
    record_path_resolution(journal, scope.formal_runtime_root, formal_target)
    record_path_resolution(journal, journal._runtime_root, engineering_target)


def _fresh(qualification: dict[str, Any], at: datetime) -> None:
    now = _datetime_ns(at)
    if (
        now < _datetime_ns(_clock(qualification["decision_at"]))
        or now > int(qualification["freshness_not_after_ns"])
        or now >= int(qualification["session_before_ns"])
    ):
        raise JournalError("source injected observations expired or clock regressed")


class SourceCloseAttempts:
    def __init__(self, owner: OcoJournal) -> None:
        if type(owner) is not OcoJournal:
            raise JournalError("canonical source OCO owner required")
        self.owner, self.journal = owner, owner.journal
        self.state_key = "source_close_attempts:" + owner.initial.entry_scope
        self.view()

    def view(self) -> list[dict[str, Any]]:
        owns = not self.journal._db.in_transaction
        if owns:
            self.journal._db.execute("BEGIN")
        try:
            with causal_risk_verification_scope(self.journal):
                return self._view()
        finally:
            if owns:
                self.journal._db.rollback()

    def read_verified_at(
        self, through_sequence: int, decision_at: datetime
    ) -> list[dict[str, Any]]:
        """Replay only the decision-visible attempt prefix, never upgrade it."""
        self.journal.require_private_order_state()
        _utc(decision_at)
        if type(through_sequence) is not int or through_sequence < 0:
            raise JournalError("invalid historical source attempt horizon")
        owns_read = not self.journal._db.in_transaction
        if owns_read:
            self.journal._db.execute("BEGIN")
        try:
            if self.journal.read_paper_account_ref() != self.owner.initial.account_ref:
                raise JournalError("historical source attempt account differs")
            if through_sequence > self.journal.read_evidence_sequence():
                raise JournalError("invalid historical source attempt horizon")
            with causal_risk_verification_scope(self.journal):
                return self._view(through_sequence, decision_at)
        finally:
            if owns_read:
                self.journal._db.rollback()

    def _historical_owner(self, item: dict[str, Any]) -> OcoJournal:
        """Construct a deterministic prefix reader, never a new BIND write."""
        binding = item["qualification"]["handoff"]["binding"]
        if binding["entry_scope"] != item["entry_scope"]:
            raise JournalError("source injected historical owner differs")
        initial = (
            self.owner.initial
            if item["entry_scope"] == self.owner.initial.entry_scope
            else OcoResearchState(
                load_oco_research_contract(),
                binding["account_ref"],
                binding["symbol"],
                binding["entry_scope"],
                Fraction(binding["limit"]),
                Fraction(binding["stop"]),
            )
        )
        reader = _causal_oco_reader(self.journal, initial)
        expected_binding = reader.binding | {
            "emergency_version": EMERGENCY_VERSION,
            "emergency_manifest_sha256": EMERGENCY_MANIFEST_SHA256,
        }
        if expected_binding != binding:
            raise JournalError("source injected historical binding differs")
        return reader

    def _view(
        self,
        through_sequence: int | None = None,
        decision_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        expected_dedupe = {}
        projections: dict[str, list[dict[str, Any]]] = {}
        for event, raw in self.journal._read_private_events(SOURCE, through_sequence):
            if decision_at is not None and (
                not event.visible_at(decision_at) or event.external_clock_conflict
            ):
                raise JournalError("source attempt prefix is not decision-visible")
            try:
                item = json.loads(raw)
                common = {
                    "schema",
                    "kind",
                    "client_order_id",
                    "entry_scope",
                    "account_ref",
                    "origin",
                    "authority",
                    "recorded_at",
                }
                fields = (
                    {
                        "command",
                        "source_horizon",
                        "qualification",
                        "request_body",
                        "scope_payload",
                        "qualified_at",
                    }
                    if item.get("kind") == "PRECALL"
                    else {
                        "attempt_ref",
                        "completed_at",
                        "result_kind",
                        "http_status",
                        "body_base64",
                    }
                )
                if (
                    set(item) != common | fields
                    or _json(item) != raw
                    or type(item["schema"]) is not int
                    or item["schema"] != 1
                    or item["origin"] != ORIGIN
                    or type(item["authority"]) is not dict
                    or set(item["authority"]) != set(AUTHORITY)
                    or any(value is not False for value in item["authority"].values())
                    or item["account_ref"] != self.journal.read_paper_account_ref()
                    or event.feed != "paper"
                    or event.event_kind != item["kind"]
                    or event.payload_ref != _digest(raw)
                    or event.source_event_at is not None
                    or event.provider_received_at is not None
                ):
                    raise ValueError
                cid = _text(item["client_order_id"])
                ref = {"sequence": event.sequence, "hash": event.payload_ref}
                if item["kind"] == "PRECALL":
                    if cid in rows:
                        raise ValueError
                    command = item["command"]
                    self._command_check(command)
                    at = _clock(item["qualified_at"])
                    if (
                        type(item["source_horizon"]) is not int
                        or not 0 <= item["source_horizon"] < event.sequence
                        or at < _clock(command["started_at"])
                    ):
                        raise ValueError
                    _scope(self.journal, item["scope_payload"], at)
                    rebuilt = derive_source_close_precall(
                        self._historical_owner(item),
                        client_order_id=cid,
                        horizon=item["source_horizon"],
                        at=at,
                        request=command["preparation"],
                    )
                    _fresh(rebuilt, _clock(item["recorded_at"]))
                    if (
                        rebuilt != item["qualification"]
                        or rebuilt["handoff"]["intent"]["request_body"]
                        != item["request_body"]
                    ):
                        raise ValueError
                    rows[cid] = {
                        "precall": item,
                        "attempt_ref": ref,
                        "outcome": None,
                        "outcome_ref": None,
                    }
                elif item["kind"] == "OUTCOME":
                    row = rows[cid]
                    at = _clock(item["completed_at"])
                    if (
                        row["outcome"] is not None
                        or item["attempt_ref"] != row["attempt_ref"]
                        or item["entry_scope"] != row["precall"]["entry_scope"]
                        or at < _clock(row["precall"]["recorded_at"])
                    ):
                        raise ValueError
                    if item["result_kind"] == "INJECTED_RESPONSE":
                        body = base64.b64decode(item["body_base64"], validate=True)
                        if (
                            base64.b64encode(body).decode() != item["body_base64"]
                            or len(body)
                            > row["precall"]["command"]["max_response_bytes"]
                            or type(item["http_status"]) is not int
                            or not 100 <= item["http_status"] <= 599
                        ):
                            raise ValueError
                    elif (
                        item["result_kind"]
                        not in {"TRANSPORT_UNKNOWN", "LOCAL_NOT_INVOKED"}
                        or item["http_status"] is not None
                        or item["body_base64"] is not None
                    ):
                        raise ValueError
                    row.update(outcome=item, outcome_ref=ref)
                else:
                    raise ValueError
                recorded = _clock(item["recorded_at"])
                if (
                    recorded < at
                    or event.system_first_seen_at != recorded
                    or event.processed_at != recorded
                    or event.available_at != recorded
                ):
                    raise ValueError
                key = (
                    "source_close_attempt_evidence:"
                    + item["account_ref"]
                    + ":"
                    + _json([item["entry_scope"], cid, item["kind"]]).decode()
                )
                expected_dedupe[key] = ref
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise JournalError(
                    "source injected attempt provenance malformed"
                ) from exc
        for row in rows.values():
            key = "source_close_attempts:" + row["precall"]["entry_scope"]
            projections.setdefault(key, []).append(row)
        for prefix, expected in (
            ("source_close_attempts:", projections),
            ("source_close_attempt_evidence:", expected_dedupe),
        ):
            if through_sequence is not None and prefix == "source_close_attempts:":
                continue
            actual = {
                key: json.loads(value)
                for key, value in self.journal._db.execute(
                    "SELECT key,value_json FROM state WHERE substr(key,1,?)=?",
                    (len(prefix), prefix),
                )
            }
            if through_sequence is not None:
                bounded = {}
                for key, reference in actual.items():
                    if (
                        type(reference) is dict
                        and type(reference.get("sequence")) is int
                        and reference["sequence"] > through_sequence
                    ):
                        continue
                    if (
                        type(reference) is not dict
                        or set(reference) != {"sequence", "hash"}
                        or type(reference["sequence"]) is not int
                        or reference["sequence"] < 1
                        or type(reference["hash"]) is not str
                    ):
                        raise JournalError("source attempt deduplication ref differs")
                    bounded[key] = reference
                actual = bounded
            if _json(actual) != _json(expected):
                raise JournalError(
                    "source injected attempt projection/deduplication differs"
                )
        return cast(list[dict[str, Any]], _copy(projections.get(self.state_key, [])))

    @staticmethod
    def _command_check(command: dict[str, Any]) -> None:
        if (
            type(command) is not dict
            or set(command)
            != {
                "attempt_id",
                "decision_at",
                "started_at",
                "preparation",
                "max_response_bytes",
            }
            or _text(command["attempt_id"]) != command["attempt_id"]
            or _clock(command["started_at"]) < _clock(command["decision_at"])
            or type(command["max_response_bytes"]) is not int
            or not 0 < command["max_response_bytes"] <= 1_000_000
            or type(command["preparation"]) is not dict
        ):
            raise JournalError("source injected exact command required")

    def _append(
        self,
        item: dict[str, Any],
        clock: Callable[[], datetime],
        prepare: Callable[[], None],
    ) -> bool:
        """Stamp after qualification under the writer lock; commit before callback."""
        journal = self.journal
        journal.require_private_order_state()
        try:
            journal._db.execute("BEGIN IMMEDIATE")
            with causal_risk_verification_scope(journal):
                if journal.read_paper_account_ref() != item["account_ref"]:
                    raise JournalError("source injected account changed")
                rows = self.view()
                prepare()
                dedupe_key = (
                    "source_close_attempt_evidence:"
                    + item["account_ref"]
                    + ":"
                    + _json(
                        [item["entry_scope"], item["client_order_id"], item["kind"]]
                    ).decode()
                )
                previous = journal.read_state(dedupe_key)
                if previous is not None:
                    if (
                        not isinstance(previous, dict)
                        or type(previous.get("sequence")) is not int
                    ):
                        raise JournalError(
                            "source injected deduplication schema differs"
                        )
                    saved = journal.read_event(previous["sequence"])
                    if saved is None or saved[1] != _json(item):
                        raise JournalError(
                            "source injected close fence already consumed"
                        )
                    raise _ExactRetry
                recorded = _utc(clock())
                if item["kind"] == "PRECALL":
                    _fresh(item["qualification"], recorded)
                elif recorded < _clock(item["completed_at"]):
                    raise JournalError(
                        "source injected outcome recording clock regressed"
                    )
                item["recorded_at"] = recorded.isoformat()
                raw = _json(item)
                sequence = journal.read_evidence_sequence() + 1
                digest = _digest(raw)
                ref = {"sequence": sequence, "hash": digest}
                if item["kind"] == "PRECALL":
                    rows.append(
                        {
                            "precall": item,
                            "attempt_ref": ref,
                            "outcome": None,
                            "outcome_ref": None,
                        }
                    )
                else:
                    row = next(
                        row
                        for row in rows
                        if row["precall"]["client_order_id"] == item["client_order_id"]
                    )
                    row.update(outcome=item, outcome_ref=ref)
                event = EventEnvelope(
                    sequence=sequence,
                    source_id=SOURCE,
                    feed="paper",
                    event_kind=item["kind"],
                    payload_ref=digest,
                    source_event_at=None,
                    provider_received_at=None,
                    system_first_seen_at=recorded,
                    processed_at=recorded,
                    available_at=recorded,
                )
            journal._append_locked(event, raw, {self.state_key: rows, dedupe_key: ref})
            journal._db.commit()
            return True
        except _ExactRetry:
            journal._db.rollback()
            return False
        except BaseException:
            journal._db.rollback()
            raise

    def invoke_once(
        self,
        harness: EngineeringHarness,
        *,
        client_order_id: str,
        attempt_id: str,
        preparation: dict[str, Any],
        decision_at: datetime,
        started_at: datetime,
        max_response_bytes: int,
        clock: Callable[[], datetime],
        callback: Callable[[bytes], SourceCloseCallbackResponse],
    ) -> dict[str, Any]:
        """Consume one local fence, then invoke the required injected callback."""
        if self.journal._db.in_transaction:
            raise JournalError("source injected attempt refuses caller transaction")
        if (
            type(harness) is not EngineeringHarness
            or harness.journal is not self.journal
        ):
            raise JournalError("source injected attempt requires its actual harness")
        harness.verify_scope()
        cid = _text(client_order_id)
        command = {
            "attempt_id": attempt_id,
            "decision_at": _utc(decision_at).isoformat(),
            "started_at": _utc(started_at).isoformat(),
            "preparation": _copy(preparation),
            "max_response_bytes": max_response_bytes,
        }
        self._command_check(command)
        if not callable(callback) or _utc(clock()) < started_at:
            raise JournalError("explicit current clock and injected callback required")
        item: dict[str, Any] = {}

        def prepare() -> None:
            prior = next(
                (r for r in self.view() if r["precall"]["client_order_id"] == cid), None
            )
            if prior is not None:
                if prior["precall"]["command"] != command:
                    raise JournalError("source injected retry command differs")
                item.update(prior["precall"])
                return
            qualified = _utc(clock())
            if qualified < started_at:
                raise JournalError("source injected qualification clock regressed")
            horizon = self.journal.read_evidence_sequence()
            qualification = derive_source_close_precall(
                self.owner,
                client_order_id=cid,
                horizon=horizon,
                at=qualified,
                request=command["preparation"],
                _require_current_census=True,
            )
            item.update(
                schema=1,
                kind="PRECALL",
                client_order_id=cid,
                entry_scope=self.owner.initial.entry_scope,
                account_ref=harness.scope.account_ref,
                origin=ORIGIN,
                authority=dict(AUTHORITY),
                command=command,
                source_horizon=horizon,
                qualification=qualification,
                request_body=qualification["handoff"]["intent"]["request_body"],
                scope_payload=harness.scope.payload().decode(),
                qualified_at=qualified.isoformat(),
            )
            _scope(self.journal, item["scope_payload"], qualified)

        # Identity fields are set before the atomic API; payload is derived only
        # inside its writer lock, including the exact retry/race decision.
        item.update(
            account_ref=harness.scope.account_ref,
            entry_scope=self.owner.initial.entry_scope,
            client_order_id=cid,
            kind="PRECALL",
        )
        created = self._append(item, clock, prepare)
        row = next(r for r in self.view() if r["precall"]["client_order_id"] == cid)
        if not created:
            return row
        status = encoded = None
        kind = "TRANSPORT_UNKNOWN"
        invocation = _utc(clock())
        try:
            _fresh(item["qualification"], invocation)
            if invocation < _clock(item["recorded_at"]):
                raise JournalError("source injected invocation clock regressed")
        except JournalError:
            kind = "LOCAL_NOT_INVOKED"
        try:
            response = (
                None
                if kind == "LOCAL_NOT_INVOKED"
                else callback(item["request_body"].encode())
            )
            if (
                type(response) is SourceCloseCallbackResponse
                and type(response.status) is int
                and 100 <= response.status <= 599
                and type(response.body) is bytes
                and len(response.body) <= max_response_bytes
            ):
                status, encoded = (
                    response.status,
                    base64.b64encode(response.body).decode(),
                )
                kind = "INJECTED_RESPONSE"
        except Exception:
            pass
        completed = _utc(clock())
        if completed < max(invocation, _clock(item["recorded_at"])):
            raise JournalError("completion clock regressed; source fence consumed")
        out = {
            key: item[key]
            for key in (
                "schema",
                "client_order_id",
                "entry_scope",
                "account_ref",
                "origin",
                "authority",
            )
        } | {
            "kind": "OUTCOME",
            "attempt_ref": row["attempt_ref"],
            "completed_at": completed.isoformat(),
            "result_kind": kind,
            "http_status": status,
            "body_base64": encoded,
        }

        def outcome_prepare() -> None:
            current = next(
                r for r in self.view() if r["precall"]["client_order_id"] == cid
            )
            if (
                current["attempt_ref"] != row["attempt_ref"]
                or current["outcome"] is not None
            ):
                raise JournalError("source injected outcome fence differs")

        self._append(out, clock, outcome_prepare)
        return next(r for r in self.view() if r["precall"]["client_order_id"] == cid)

    def verify_rebuild(self) -> bool:
        self.view()
        return True
