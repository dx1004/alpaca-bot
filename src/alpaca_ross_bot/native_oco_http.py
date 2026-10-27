"""Single native OCO HTTP attempt with durable provenance, never protection proof.

No CLI or strategy wiring exists. Callers still need component experiment
authority, qualified inputs and supported cleanup before invoking this transport.
Injected exchange evidence is permanently distinguished from built-in HTTPS.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ._causal_risk_memo import record_path_resolution
from .journal import Journal, JournalError
from .native_oco_requests import (
    _AUTHORITY,
    NativeOcoRequests,
    _clock,
    _copy,
    _digest,
    _json,
    _text,
    _utc,
)
from .paper_engineering import EngineeringHarness, EngineeringScope
from .paper_order_contract import paper_account_ref, require_submission_eligible_account
from .paper_order_http import PaperOrderHttp, PaperOrderHttpError

SOURCE = "ALPACA_NATIVE_OCO_HTTP"


def _verification(journal: Journal) -> dict[str, str]:
    row = journal._db.execute(
        "SELECT verified_at,body FROM paper_account_verifications "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise JournalError("fresh native HTTP account verification missing")
    raw = bytes(row[1])
    return {
        "verified_at": str(row[0]),
        "body_base64": base64.b64encode(raw).decode(),
        "body_sha256": _digest(raw),
    }


def _check_verification(
    proof: object, account: str, started: datetime, *, submission: bool
) -> None:
    if type(proof) is not dict or set(proof) != {
        "verified_at",
        "body_base64",
        "body_sha256",
    }:
        raise JournalError("native HTTP account proof schema differs")
    try:
        raw = base64.b64decode(proof["body_base64"], validate=True)
        if (
            base64.b64encode(raw).decode() != proof["body_base64"]
            or _digest(raw) != proof["body_sha256"]
            or paper_account_ref(raw) != account
            or _clock(proof["verified_at"]) > started
        ):
            raise ValueError
        if submission:
            require_submission_eligible_account(raw)
    except (ValueError, TypeError, KeyError) as exc:
        raise JournalError("native HTTP account proof is unqualified") from exc


def _check_scope_proof(journal: Journal, pre: dict[str, Any]) -> None:
    try:
        scope_data = json.loads(pre["scope_payload"])
        scope = EngineeringScope(
            experiment_id=scope_data["experiment_id"],
            account_ref=scope_data["account_ref"],
            formal_account_ref=scope_data["formal_account_ref"],
            formal_runtime_root=Path(scope_data["formal_runtime_root"]),
        )
        formal_target = scope.formal_runtime_root.resolve()
        engineering_target = journal._runtime_root.resolve()
        scope.validate(journal._runtime_root)
        events = journal.read_engineering_events()
        scopes = [(event, raw) for event, raw in events if event.event_kind == "SCOPE"]
        if (
            len(scopes) != 1
            or scopes[0] != events[0]
            or scopes[0][0].feed != "paper"
            or not scopes[0][0].visible_at(_clock(pre["started_at"]))
            or scopes[0][1] != scope.payload()
            or scopes[0][0].payload_ref != _digest(scope.payload())
            or journal.read_state("engineering_scope")
            != _digest(scope.payload()).removeprefix("sha256:")
            or scope.payload().decode() != pre["scope_payload"]
            or scope.account_ref != pre["binding"]["account_ref"]
            or _clock(pre["started_at"]) < _clock(pre["decision_at"])
        ):
            raise ValueError
        _check_verification(
            pre["account_verification"],
            scope.account_ref,
            _clock(pre["started_at"]),
            submission=True,
        )
        _check_verification(
            pre["formal_verification"],
            scope.formal_account_ref,
            _clock(pre["started_at"]),
            submission=False,
        )
        record_path_resolution(journal, scope.formal_runtime_root, formal_target)
        record_path_resolution(journal, journal._runtime_root, engineering_target)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise JournalError("native HTTP authenticated scope proof differs") from exc


def _events(journal: Journal) -> tuple[Any, ...]:
    return journal._read_private_events(SOURCE, None)


def require_no_http_group(journal: Journal, scope: str, group: str) -> None:
    """Called inside the existing shared write lock by the fake attempt owner."""
    for row in native_http_inventory(journal):
        item = row["precall"]
        if item["binding"]["entry_scope"] == scope and item["group_id"] == group:
            raise JournalError("native group already claimed by HTTP transport")


def native_http_inventory(journal: Journal) -> list[dict[str, Any]]:
    """Read immutable transport facts only; no ACK adoption or retry permission."""
    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        return _inventory(journal)
    finally:
        if owns:
            journal._db.rollback()


def _inventory(journal: Journal) -> list[dict[str, Any]]:
    journal.require_private_order_state()
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for event, raw in _events(journal):
        try:
            item = json.loads(raw)
            common = {"schema", "binding", "kind", "group_id", "origin", "authority"}
            fields = (
                {
                    "decision_at",
                    "started_at",
                    "source_horizon",
                    "prepared_ref",
                    "request_body",
                    "request_sha256",
                    "scope_payload",
                    "account_verification",
                    "formal_verification",
                    "formal_origin",
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
                or item["schema"] != 1
                or type(item["schema"]) is not int
                or item["binding"]["account_ref"] != journal.read_paper_account_ref()
                or event.feed != "paper"
                or event.event_kind != item["kind"]
                or event.payload_ref != _digest(raw)
                or item["origin"] not in {"BUILTIN_PAPER_HTTPS", "INJECTED_EXCHANGE"}
                or _json(item["authority"]) != _json(_AUTHORITY)
            ):
                raise ValueError
            key = (item["binding"]["entry_scope"], item["group_id"])
            ref = {"sequence": event.sequence, "hash": event.payload_ref}
            stamp = _clock(
                item["started_at"]
                if item["kind"] == "PRECALL"
                else item["completed_at"]
            )
            if (
                event.system_first_seen_at != stamp
                or event.processed_at != stamp
                or event.source_event_at is not None
                or event.provider_received_at is not None
            ):
                raise ValueError
            if item["kind"] == "PRECALL":
                if key in rows or event.available_at != _clock(item["started_at"]):
                    raise ValueError
                _check_scope_proof(journal, item)
                if item["formal_origin"] not in {
                    "BUILTIN_PAPER_HTTPS",
                    "INJECTED_EXCHANGE",
                } or (
                    item["origin"] == "BUILTIN_PAPER_HTTPS"
                    and item["formal_origin"] != "BUILTIN_PAPER_HTTPS"
                ):
                    raise ValueError
                prepared_ref = item["prepared_ref"]
                if (
                    type(prepared_ref) is not dict
                    or set(prepared_ref) != {"sequence", "hash"}
                    or type(prepared_ref["sequence"]) is not int
                    or type(item["source_horizon"]) is not int
                    or not prepared_ref["sequence"]
                    <= item["source_horizon"]
                    < event.sequence
                ):
                    raise ValueError
                saved = journal.read_event(prepared_ref["sequence"])
                if saved is None:
                    raise ValueError
                prepared_event, prepared_raw = saved
                prepared = json.loads(prepared_raw)
                receipt = prepared["receipt"]
                if (
                    prepared_event.source_id != "ALPACA_NATIVE_OCO_REQUEST"
                    or prepared_event.event_kind != "PREPARE_NATIVE_OCO"
                    or prepared_event.feed != "paper"
                    or prepared_event.payload_ref != prepared_ref["hash"]
                    or prepared_event.payload_ref != _digest(prepared_raw)
                    or not prepared_event.visible_at(_clock(item["decision_at"]))
                    or prepared["outcome"] != "PREPARED_NOT_BROKER_VALIDATED"
                    or prepared["binding"] != item["binding"]
                    or receipt["binding"] != item["binding"]
                    or receipt["group_id"] != item["group_id"]
                    or receipt["request_body"] != item["request_body"]
                    or receipt["request_sha256"] != item["request_sha256"]
                    or _digest(item["request_body"].encode()) != item["request_sha256"]
                ):
                    raise ValueError
                rows[key] = {
                    "precall": item,
                    "attempt_ref": ref,
                    "outcome": None,
                    "outcome_ref": None,
                }
            elif item["kind"] == "OUTCOME":
                row = rows[key]
                if (
                    row["outcome"] is not None
                    or item["attempt_ref"] != row["attempt_ref"]
                    or item["binding"] != row["precall"]["binding"]
                    or item["origin"] != row["precall"]["origin"]
                    or event.available_at != _clock(item["completed_at"])
                    or _clock(item["completed_at"])
                    < _clock(row["precall"]["started_at"])
                ):
                    raise ValueError
                if item["result_kind"] == "HTTP_RESPONSE":
                    if (
                        type(item["http_status"]) is not int
                        or not 100 <= item["http_status"] <= 599
                    ):
                        raise ValueError
                    encoded = item["body_base64"]
                    if (
                        type(encoded) is not str
                        or base64.b64encode(
                            base64.b64decode(encoded, validate=True)
                        ).decode()
                        != encoded
                    ):
                        raise ValueError
                elif (
                    item["result_kind"] != "TRANSPORT_UNKNOWN"
                    or item["http_status"] is not None
                    or item["body_base64"] is not None
                ):
                    raise ValueError
                row.update(outcome=item, outcome_ref=ref)
            else:
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise JournalError("native HTTP provenance is malformed") from exc
    expected_projections: dict[str, list[dict[str, Any]]] = {}
    expected_dedupe: dict[str, Any] = {}
    for row in rows.values():
        pre = row["precall"]
        projection_key = "native_oco_http:" + _json(pre["binding"]).decode()
        expected_projections.setdefault(projection_key, []).append(row)
        for kind, ref in (
            ("PRECALL", row["attempt_ref"]),
            ("OUTCOME", row["outcome_ref"]),
        ):
            if ref is not None:
                key = (
                    "native_oco_http_evidence:"
                    + pre["binding"]["account_ref"]
                    + ":"
                    + _json(
                        [pre["binding"]["entry_scope"], pre["group_id"], kind]
                    ).decode()
                )
                expected_dedupe[key] = {
                    "sequence": ref["sequence"],
                    "hash": ref["hash"],
                }
    for prefix, expected in (
        ("native_oco_http:", expected_projections),
        ("native_oco_http_evidence:", expected_dedupe),
    ):
        try:
            actual = {
                key: json.loads(raw)
                for key, raw in journal._db.execute(
                    "SELECT key,value_json FROM state WHERE substr(key,1,?)=?",
                    (len(prefix), prefix),
                )
            }
        except (ValueError, TypeError) as exc:
            raise JournalError(
                "native HTTP malformed projection/deduplication map"
            ) from exc
        if actual != expected:
            raise JournalError(
                "native HTTP projection/deduplication map differs from evidence"
            )
    return list(rows.values())


class NativeOcoHttp:
    def __init__(self, requests: NativeOcoRequests) -> None:
        if type(requests) is not NativeOcoRequests:
            raise JournalError("canonical native preparation required")
        self.requests, self.journal = requests, requests.journal
        self.binding = _copy(requests.binding)
        self.state_key = "native_oco_http:" + _json(self.binding).decode()
        self.view()

    def _prepared(self, group: str, horizon: int, decision: datetime) -> dict[str, Any]:
        state = self.requests.read_verified_at(horizon, decision)
        if state is None or state["conflict_groups"]:
            raise JournalError("clean historical native preparation required")
        found = [r for r in state["requests"] if r["receipt"]["group_id"] == group]
        if len(found) != 1:
            raise JournalError("native prepared group missing")
        return cast(dict[str, Any], found[0])

    def view(self) -> list[dict[str, Any]]:
        owns = not self.journal._db.in_transaction
        if owns:
            self.journal._db.execute("BEGIN")
        try:
            return self._view()
        finally:
            if owns:
                self.journal._db.rollback()

    def _view(self) -> list[dict[str, Any]]:
        """Rebuild exact send records; historical evidence never enables replay."""
        self.requests._bound()
        rows = [
            row
            for row in native_http_inventory(self.journal)
            if row["precall"]["binding"] == self.binding
        ]
        for row in rows:
            pre = row["precall"]
            prepared = self._prepared(
                pre["group_id"], pre["source_horizon"], _clock(pre["decision_at"])
            )
            if (
                pre["prepared_ref"] != prepared["request_ref"]
                or pre["request_body"] != prepared["receipt"]["request_body"]
                or pre["request_sha256"] != _digest(pre["request_body"].encode())
                or pre["source_horizon"] >= row["attempt_ref"]["sequence"]
                or _clock(pre["started_at"]) < _clock(pre["decision_at"])
                or pre["origin"] not in {"BUILTIN_PAPER_HTTPS", "INJECTED_EXCHANGE"}
            ):
                raise JournalError("native HTTP precall differs from preparation")
            out = row["outcome"]
            if out is not None:
                if (
                    out["origin"] != pre["origin"]
                    or _clock(out["completed_at"]) < _clock(pre["started_at"])
                    or out["binding"] != self.binding
                ):
                    raise JournalError("native HTTP outcome identity differs")
                if out["result_kind"] == "HTTP_RESPONSE":
                    if (
                        type(out["http_status"]) is not int
                        or not 100 <= out["http_status"] <= 599
                    ):
                        raise JournalError("native HTTP status invalid")
                    base64.b64decode(out["body_base64"], validate=True)
                elif out["result_kind"] != "TRANSPORT_UNKNOWN":
                    raise JournalError("native HTTP result invalid")
        cached = self.journal.read_state(self.state_key)
        if (rows and cached != rows) or (not rows and cached not in (None, [])):
            raise JournalError("native HTTP projection differs from immutable evidence")
        for row in rows:
            for kind, ref in (
                ("PRECALL", row["attempt_ref"]),
                ("OUTCOME", row["outcome_ref"]),
            ):
                if ref is not None:
                    key = (
                        "native_oco_http_evidence:"
                        + self.binding["account_ref"]
                        + ":"
                        + _json(
                            [
                                self.binding["entry_scope"],
                                row["precall"]["group_id"],
                                kind,
                            ]
                        ).decode()
                    )
                    if self.journal.read_state(key) != {
                        "hash": ref["hash"],
                        "sequence": ref["sequence"],
                    }:
                        raise JournalError(
                            "native HTTP deduplication differs from evidence"
                        )
        return cast(list[dict[str, Any]], _copy(rows))

    def verify_rebuild(self) -> bool:
        self.view()
        return True

    def _append(
        self, item: dict[str, Any], at: datetime, prepare: Callable[[], None]
    ) -> bool:
        def locked(_: bytes) -> bytes:
            prepare()
            return _json(item)

        def reduce(_old: object, conflict: bool, sequence: int) -> object:
            if conflict:
                raise JournalError("native HTTP event identity already consumed")
            rows = self.view()
            ref = {"sequence": sequence, "hash": _digest(_json(item))}
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
                    r for r in rows if r["precall"]["group_id"] == item["group_id"]
                )
                row.update(outcome=item, outcome_ref=ref)
            return rows

        event = self.journal._apply_private_event(
            account_ref=self.binding["account_ref"],
            state_key=self.state_key,
            dedupe_key=_json(
                [self.binding["entry_scope"], item["group_id"], item["kind"]]
            ).decode(),
            event_kind=item["kind"],
            payload=b"",
            received_at=at,
            reduce=reduce,
            identity_payload=None,
            namespace="native_oco_http",
            source_id=SOURCE,
            dedupe_namespace="native_oco_http_evidence",
            prepare_payload=locked,
        )
        return event is not None

    def post_once(
        self,
        api: PaperOrderHttp,
        harness: EngineeringHarness,
        *,
        formal_api: PaperOrderHttp,
        group_id: str,
        decision_at: datetime,
        started_at: datetime,
        clock: Callable[[], datetime],
    ) -> dict[str, Any]:
        """Perform one bounded real transport operation, with no automatic retries.

        This low-level engineering primitive does not assert experiment approval,
        source admission, safe cleanup, ACK ownership or protective coverage.
        """
        if self.journal._db.in_transaction:
            raise JournalError("native HTTP refuses caller-owned transaction")
        if type(api) is not PaperOrderHttp or type(formal_api) is not PaperOrderHttp:
            raise JournalError("canonical paper transports required")
        if (
            type(harness) is not EngineeringHarness
            or harness.journal is not self.journal
        ):
            raise JournalError("native HTTP requires its actual engineering harness")
        harness.verify_scope()
        if harness.scope.account_ref != self.binding["account_ref"]:
            raise JournalError("native HTTP engineering account differs")
        group = _text(group_id)
        decision, started = _utc(decision_at), _utc(started_at)
        if started < decision or _utc(clock()) < started:
            raise JournalError("native HTTP requires current explicit UTC clock")
        for row in self.view():
            if row["precall"]["group_id"] == group:
                if row["precall"]["decision_at"] != decision.isoformat():
                    raise JournalError("native HTTP retry identity differs")
                return row
        harness.verify_formal_account(formal_api)
        api.verify_account(
            self.journal,
            for_submission=True,
            expected_reference=self.binding["account_ref"],
        )
        account_proof = _verification(self.journal)
        proof_journal = Journal.open_private(
            harness.root / "formal-account-proof.sqlite3", harness.root
        )
        try:
            formal_proof = _verification(proof_journal)
        finally:
            proof_journal.close()
        # Account verification can take time: the durable precall uses the actual
        # post-verification clock, never a caller's pre-network timestamp.
        started = _utc(clock())
        if started < decision:
            raise JournalError("native HTTP account verification clock regressed")
        item: dict[str, Any] = {
            "schema": 1,
            "binding": self.binding,
            "kind": "PRECALL",
            "group_id": group,
            "decision_at": decision.isoformat(),
            "started_at": started.isoformat(),
            "authority": dict(_AUTHORITY),
            "origin": api.native_http_origin,
            "formal_origin": formal_api.native_http_origin,
            "scope_payload": harness.scope.payload().decode(),
            "account_verification": account_proof,
            "formal_verification": formal_proof,
        }
        if (
            item["origin"] == "BUILTIN_PAPER_HTTPS"
            and item["formal_origin"] != "BUILTIN_PAPER_HTTPS"
        ):
            raise JournalError(
                "builtin native HTTP requires builtin formal authentication"
            )
        _check_verification(
            account_proof, harness.scope.account_ref, started, submission=True
        )
        _check_verification(
            formal_proof, harness.scope.formal_account_ref, started, submission=False
        )

        def prepare() -> None:
            if any(r["precall"]["group_id"] == group for r in self.view()):
                raise JournalError("native HTTP attempt already consumed")
            from .native_oco_attempts import NativeOcoAttempts

            fake = NativeOcoAttempts(self.requests).view()
            if fake is not None and any(
                row["precall_receipt"]["command"]["group_id"] == group
                for row in fake["attempts"]
            ):
                raise JournalError("native group already claimed by fake attempt")
            horizon = self.journal.read_evidence_sequence()
            prepared = self._prepared(group, horizon, started)
            body = prepared["receipt"]["request_body"]
            fresh = self.requests._derive(
                {
                    "group_id": group,
                    "client_order_id": prepared["receipt"]["client_order_id"],
                    "decision_at": started.isoformat(),
                },
                horizon,
                started,
            )
            if fresh["request_body"] != body:
                raise JournalError("native HTTP preparation is no longer current")
            api.validate_native_http_body(body)
            item.update(
                source_horizon=horizon,
                prepared_ref=prepared["request_ref"],
                request_body=body,
                request_sha256=_digest(body.encode()),
            )

        self._append(item, started, prepare)
        row = next(r for r in self.view() if r["precall"]["group_id"] == group)
        status = body_bytes = None
        result_kind = "TRANSPORT_UNKNOWN"
        try:
            response = api._request("POST", "/v2/orders", item["request_body"].encode())
            status, body_bytes = response.status, response.body
            result_kind = "HTTP_RESPONSE"
        except PaperOrderHttpError:
            pass
        out = {
            "schema": 1,
            "binding": self.binding,
            "kind": "OUTCOME",
            "group_id": group,
            "attempt_ref": row["attempt_ref"],
            "origin": item["origin"],
            "completed_at": _utc(clock()).isoformat(),
            "result_kind": result_kind,
            "http_status": status,
            "body_base64": None
            if body_bytes is None
            else base64.b64encode(body_bytes).decode(),
            "authority": dict(_AUTHORITY),
        }
        if _clock(out["completed_at"]) < started:
            raise JournalError(
                "native HTTP completion clock regressed; attempt consumed"
            )

        def outcome_prepare() -> None:
            current = next(r for r in self.view() if r["precall"]["group_id"] == group)
            if (
                current["outcome"] is not None
                or current["attempt_ref"] != row["attempt_ref"]
            ):
                raise JournalError("native HTTP outcome already consumed")

        self._append(out, _clock(out["completed_at"]), outcome_prepare)
        return next(r for r in self.view() if r["precall"]["group_id"] == group)
