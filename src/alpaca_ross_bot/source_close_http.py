"""Authenticated synthetic source-CLOSE transport; builtin dispatch is disabled.

Only the actual PaperOrderHttp injected-exchange path is accepted by this schema.
It supplies compatible transport provenance, never broker ownership or release.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import datetime
from fractions import Fraction
from typing import Any, Never, cast

from ._causal_risk_memo import causal_risk_verification_scope
from .crossed_stop_attempts import (
    HTTP_ORIGIN,
    HTTP_SOURCE,
    _fresh,
    _scope,
    source_close_fences,
)
from .crossed_stop_attempts import (
    SOURCE as CALLBACK_SOURCE,
)
from .crossed_stop_capture import AUTHORITY as AUTHORITY
from .crossed_stop_preparation import derive_source_close_precall
from .events import EventEnvelope
from .journal import Journal, JournalError
from .native_oco_http import _check_scope_proof, _check_verification, _verification
from .native_oco_requests import _clock, _copy, _digest, _json, _text, _utc
from .oco_journal import OcoJournal, _causal_oco_reader
from .oco_research import OcoResearchState, load_oco_research_contract
from .paper_engineering import EngineeringHarness
from .paper_order_contract import validate_saved_request
from .paper_order_http import PaperOrderHttp

SOURCE = HTTP_SOURCE
ORIGIN = HTTP_ORIGIN
NAMESPACE = "source_close_http"
DEDUPE = "source_close_http_evidence"
CLASSIFICATION = "SIMULATED_SOURCE_HTTP_NOT_BROKER_ACCEPTANCE"


class _Retry(Exception):
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row


def _command(
    attempt_id: str, decision: datetime, started: datetime, preparation: dict[str, Any]
) -> dict[str, Any]:
    if type(preparation) is not dict or started < decision:
        raise JournalError("exact source HTTP preparation and chronology required")
    return {
        "attempt_id": _text(attempt_id),
        "decision_at": _utc(decision).isoformat(),
        "started_at": _utc(started).isoformat(),
        "preparation": _copy(preparation),
    }


def _initial(journal: Journal, binding: dict[str, Any]) -> OcoResearchState:
    initial = OcoResearchState(
        load_oco_research_contract(),
        binding["account_ref"],
        binding["symbol"],
        binding["entry_scope"],
        Fraction(binding["limit"]),
        Fraction(binding["stop"]),
    )
    if _json(_causal_oco_reader(journal, initial).binding) != _json(binding):
        raise JournalError("source HTTP canonical initial binding differs")
    return initial


def _state_key(binding: dict[str, Any]) -> str:
    return NAMESPACE + ":" + _json(binding).decode()


def _dedupe_key(item: dict[str, Any]) -> str:
    return _json([item["entry_scope"], item["client_order_id"], item["kind"]]).decode()


def _limits(api: PaperOrderHttp) -> dict[str, int]:
    return {
        "max_request_bytes": api._max_request_bytes,
        "max_response_bytes": api._max_response_bytes,
    }


def _check_limits(limits: object) -> dict[str, int]:
    if (
        type(limits) is not dict
        or set(limits) != {"max_request_bytes", "max_response_bytes"}
        or any(type(value) is not int or value < 1 for value in limits.values())
    ):
        raise JournalError("source HTTP explicit transport budgets required")
    return limits


def source_http_inventory(journal: Journal) -> list[dict[str, Any]]:
    """Current raw transport/cache integrity only; no risk/prequalification replay."""
    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            return _inventory(journal, exclusion=True)
    finally:
        if owns:
            journal._db.rollback()


def _inventory(journal: Journal, *, exclusion: bool) -> list[dict[str, Any]]:
    journal.require_private_order_state()
    account_ref = _text(journal.read_paper_account_ref())
    if exclusion:
        source_close_fences(journal)
    rows: dict[str, dict[str, Any]] = {}
    common = {
        "schema",
        "classification",
        "kind",
        "binding",
        "account_ref",
        "entry_scope",
        "client_order_id",
        "origin",
        "authority",
        "recorded_at",
    }
    for event, raw in journal._read_private_events(SOURCE, None):
        try:
            item = json.loads(raw)
            fields = (
                {
                    "command",
                    "decision_at",
                    "started_at",
                    "qualified_at",
                    "source_horizon",
                    "qualification",
                    "request_body",
                    "request_sha256",
                    "scope_payload",
                    "account_verification",
                    "formal_verification",
                    "formal_origin",
                    "limits",
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
                or item["classification"] != CLASSIFICATION
                or item["origin"] != ORIGIN
                or item["account_ref"] != journal.read_paper_account_ref()
                or item["binding"]["account_ref"] != item["account_ref"]
                or item["binding"]["entry_scope"] != item["entry_scope"]
                or _json(item["authority"]) != _json(AUTHORITY)
                or any(value is not False for value in item["authority"].values())
                or event.feed != "paper"
                or event.event_kind != item["kind"]
                or event.payload_ref != _digest(raw)
                or event.source_event_at is not None
                or event.provider_received_at is not None
                or event.external_clock_conflict
            ):
                raise ValueError
            _initial(journal, item["binding"])
            cid = _text(item["client_order_id"])
            recorded = _clock(item["recorded_at"])
            if (
                event.system_first_seen_at != recorded
                or event.processed_at != recorded
                or event.available_at != recorded
            ):
                raise ValueError
            ref = {"sequence": event.sequence, "hash": event.payload_ref}
            if item["kind"] == "PRECALL":
                command = item["command"]
                if type(command) is not dict or set(command) != {
                    "attempt_id",
                    "decision_at",
                    "started_at",
                    "preparation",
                }:
                    raise ValueError
                if _json(
                    _command(
                        command["attempt_id"],
                        _clock(command["decision_at"]),
                        _clock(command["started_at"]),
                        command["preparation"],
                    )
                ) != _json(command):
                    raise ValueError
                limits = _check_limits(item["limits"])
                request = validate_saved_request(item["request_body"])
                if (
                    cid in rows
                    or item["formal_origin"] != ORIGIN
                    or item["decision_at"] != command["decision_at"]
                    or item["started_at"] != item["qualified_at"]
                    or not _clock(command["started_at"])
                    <= _clock(item["qualified_at"])
                    <= recorded
                    or type(item["source_horizon"]) is not int
                    or not 0 <= item["source_horizon"] < event.sequence
                    or _digest(item["request_body"].encode()) != item["request_sha256"]
                    or not 0
                    < len(item["request_body"].encode())
                    <= limits["max_request_bytes"]
                    or request["client_order_id"] != cid
                    or request["symbol"] != item["binding"]["symbol"]
                    or request["type"] != "market"
                    or request["side"] != "sell"
                ):
                    raise ValueError
                _scope(journal, item["scope_payload"], _clock(item["started_at"]))
                _check_scope_proof(journal, item)
                _fresh(item["qualification"], recorded)
                rows[cid] = {
                    "precall": item,
                    "attempt_ref": ref,
                    "outcome": None,
                    "outcome_ref": None,
                }
            elif item["kind"] == "OUTCOME":
                row = rows[cid]
                pre = row["precall"]
                completed = _clock(item["completed_at"])
                if (
                    row["outcome"] is not None
                    or item["attempt_ref"] != row["attempt_ref"]
                    or item["binding"] != pre["binding"]
                    or not _clock(pre["recorded_at"]) <= completed <= recorded
                ):
                    raise ValueError
                if item["result_kind"] == "HTTP_RESPONSE":
                    body = base64.b64decode(item["body_base64"], validate=True)
                    if (
                        type(item["http_status"]) is not int
                        or not 100 <= item["http_status"] <= 599
                        or base64.b64encode(body).decode() != item["body_base64"]
                        or len(body) > pre["limits"]["max_response_bytes"]
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
        except JournalError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise JournalError(
                "source HTTP raw transport provenance is malformed"
            ) from exc
    projections: dict[str, list[dict[str, Any]]] = {}
    dedupe: dict[str, dict[str, Any]] = {}
    for row in rows.values():
        pre = row["precall"]
        projections.setdefault(_state_key(pre["binding"]), []).append(row)
        for kind, ref in (
            ("PRECALL", row["attempt_ref"]),
            ("OUTCOME", row["outcome_ref"]),
        ):
            if ref is not None:
                key = _json([pre["entry_scope"], pre["client_order_id"], kind]).decode()
                dedupe[key] = ref
    try:
        actual = {
            key: json.loads(raw)
            for key, raw in journal._db.execute(
                "SELECT key,value_json FROM state WHERE substr(key,1,?)=?",
                (len(NAMESPACE) + 1, NAMESPACE + ":"),
            )
        }
        if _json(actual) != _json(projections) or _json(
            journal._read_evidence_cache(DEDUPE, account_ref)
        ) != _json(dedupe):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise JournalError(
            "source HTTP projection or first-reference map differs"
        ) from exc
    return cast(list[dict[str, Any]], _copy(list(rows.values())))


class SourceCloseHttp:
    def __init__(self, owner: OcoJournal) -> None:
        if type(owner) is not OcoJournal:
            raise JournalError("canonical current source OCO owner required")
        self._initialize(owner.journal, owner.initial)
        self.owner = owner
        self.view()

    def _initialize(self, journal: Journal, initial: OcoResearchState) -> None:
        self.journal, self.initial = journal, initial
        self.owner = _causal_oco_reader(journal, initial)
        self.binding = cast(dict[str, Any], _copy(self.owner.binding))
        self.state_key = _state_key(self.binding)

    def view(self) -> list[dict[str, Any]]:
        return self._read(None, None)

    def read_verified_at(
        self, through_sequence: int, decision_at: datetime
    ) -> list[dict[str, Any]]:
        if (
            type(through_sequence) is not int
            or not 0 <= through_sequence <= self.journal.read_evidence_sequence()
        ):
            raise JournalError("bounded source HTTP horizon required")
        return self._read(through_sequence, _utc(decision_at))

    def _read(self, horizon: int | None, at: datetime | None) -> list[dict[str, Any]]:
        owns = not self.journal._db.in_transaction
        if owns:
            self.journal._db.execute("BEGIN")
        try:
            with causal_risk_verification_scope(self.journal):
                rows = _inventory(self.journal, exclusion=horizon is None)
                source_close_fences(
                    self.journal, through_sequence=horizon, decision_at=at
                )
                result = []
                for row in rows:
                    pre = row["precall"]
                    if horizon is not None and row["attempt_ref"]["sequence"] > horizon:
                        continue
                    if at is not None and _clock(pre["recorded_at"]) > at:
                        continue
                    initial = _initial(self.journal, pre["binding"])
                    if (
                        initial.entry_scope == self.initial.entry_scope
                        and pre["binding"] != self.binding
                    ):
                        raise JournalError("source HTTP requested initial differs")
                    qualification = derive_source_close_precall(
                        _causal_oco_reader(self.journal, initial),
                        client_order_id=pre["client_order_id"],
                        horizon=pre["source_horizon"],
                        at=_clock(pre["qualified_at"]),
                        request=pre["command"]["preparation"],
                    )
                    if (
                        qualification != pre["qualification"]
                        or qualification["handoff"]["intent"]["request_body"]
                        != pre["request_body"]
                    ):
                        raise JournalError(
                            "source HTTP original prequalification differs"
                        )
                    _fresh(qualification, _clock(pre["recorded_at"]))
                    if row["outcome_ref"] is not None and (
                        (
                            horizon is not None
                            and row["outcome_ref"]["sequence"] > horizon
                        )
                        or (
                            at is not None
                            and _clock(row["outcome"]["recorded_at"]) > at
                        )
                    ):
                        row.update(outcome=None, outcome_ref=None)
                    result.append(row)
                return cast(list[dict[str, Any]], _copy(result))
        except JournalError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise JournalError("source HTTP full source proof is malformed") from exc
        finally:
            if owns:
                self.journal._db.rollback()

    def _append(
        self,
        item: dict[str, Any],
        clock: Callable[[], datetime],
        prepare: Callable[[], None],
    ) -> None:
        self.journal.require_private_order_state()
        if self.journal._db.in_transaction:
            raise JournalError("source HTTP writer refuses caller transaction")
        try:
            self.journal._db.execute("BEGIN IMMEDIATE")
            with causal_risk_verification_scope(self.journal):
                rows = _inventory(self.journal, exclusion=True)
                prepare()
                recorded = _utc(clock())
                if item["kind"] == "PRECALL":
                    if recorded < _clock(item["qualified_at"]):
                        raise JournalError(
                            "source HTTP qualification recording clock regressed"
                        )
                    _fresh(item["qualification"], recorded)
                elif recorded < _clock(item["completed_at"]):
                    raise JournalError("source HTTP outcome recording clock regressed")
                item["recorded_at"] = recorded.isoformat()
                raw = _json(item)
                sequence = self.journal.read_evidence_sequence() + 1
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
                        r
                        for r in rows
                        if r["precall"]["client_order_id"] == item["client_order_id"]
                    )
                    row.update(outcome=item, outcome_ref=ref)
                key = DEDUPE + ":" + item["account_ref"] + ":" + _dedupe_key(item)
                if self.journal.read_state(key) is not None:
                    raise JournalError("source HTTP fence or outcome already consumed")
                projection = [
                    row for row in rows if row["precall"]["binding"] == self.binding
                ]
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
            self.journal._append_locked(
                event, raw, {self.state_key: projection, key: ref}
            )
            self.journal._db.commit()
        except BaseException:
            if self.journal._db.in_transaction:
                self.journal._db.rollback()
            raise

    def post_once(
        self,
        api: PaperOrderHttp,
        harness: EngineeringHarness,
        *,
        formal_api: PaperOrderHttp,
        client_order_id: str,
        attempt_id: str,
        preparation: dict[str, Any],
        decision_at: datetime,
        started_at: datetime,
        clock: Callable[[], datetime],
    ) -> dict[str, Any]:
        if self.journal._db.in_transaction:
            raise JournalError("source HTTP refuses caller transaction")
        try:
            return self._post_once(
                api,
                harness,
                formal_api=formal_api,
                client_order_id=client_order_id,
                attempt_id=attempt_id,
                preparation=preparation,
                decision_at=decision_at,
                started_at=started_at,
                clock=clock,
            )
        except BaseException:
            if self.journal._db.in_transaction:
                self.journal._db.rollback()
            raise

    def _post_once(
        self,
        api: PaperOrderHttp,
        harness: EngineeringHarness,
        *,
        formal_api: PaperOrderHttp,
        client_order_id: str,
        attempt_id: str,
        preparation: dict[str, Any],
        decision_at: datetime,
        started_at: datetime,
        clock: Callable[[], datetime],
    ) -> dict[str, Any]:
        if self.journal._db.in_transaction:
            raise JournalError("source HTTP refuses caller transaction")
        if (
            type(self.owner) is not OcoJournal
            or type(api) is not PaperOrderHttp
            or type(formal_api) is not PaperOrderHttp
            or type(harness) is not EngineeringHarness
            or harness.journal is not self.journal
        ):
            raise JournalError("canonical source HTTP engineering inputs required")
        if api.native_http_origin != ORIGIN or formal_api.native_http_origin != ORIGIN:
            raise JournalError(
                "builtin source HTTP dispatch and mixed authentication remain disabled"
            )
        harness.verify_scope()
        _scope(self.journal, harness.scope.payload().decode(), _utc(clock()))
        if harness.scope.account_ref != self.initial.account_ref:
            raise JournalError("source HTTP engineering account differs")
        cid = _text(client_order_id)
        command = _command(attempt_id, _utc(decision_at), _utc(started_at), preparation)
        limits = _check_limits(_limits(api))
        if not callable(clock) or _utc(clock()) < started_at:
            raise JournalError("current explicit source HTTP clock required")

        def retry(row: dict[str, Any]) -> dict[str, Any]:
            pre = row["precall"]
            if (
                pre["command"] != command
                or pre["binding"] != self.binding
                or pre["limits"] != limits
                or pre["origin"] != api.native_http_origin
                or pre["formal_origin"] != formal_api.native_http_origin
                or pre["scope_payload"] != harness.scope.payload().decode()
            ):
                raise JournalError("source HTTP exact retry identity differs")
            return row

        for row in self.view():
            if row["precall"]["client_order_id"] == cid:
                return retry(row)
        if source_close_fences(self.journal).get(cid) == CALLBACK_SOURCE:
            raise JournalError("source CID already fenced by injected callback")
        harness.verify_formal_account(formal_api)
        api.verify_account(
            self.journal,
            for_submission=True,
            expected_reference=self.initial.account_ref,
        )
        account_proof = _verification(self.journal)
        formal_journal = Journal.open_private(
            harness.root / "formal-account-proof.sqlite3", harness.root
        )
        try:
            formal_proof = _verification(formal_journal)
        finally:
            formal_journal.close()
        item: dict[str, Any] = {}

        def prepare() -> None:
            current = _inventory(self.journal, exclusion=True)
            for row in current:
                if row["precall"]["client_order_id"] == cid:
                    raise _Retry(retry(row))
            if source_close_fences(self.journal).get(cid) == CALLBACK_SOURCE:
                raise JournalError("source CID already fenced by injected callback")
            if (
                api.native_http_origin != ORIGIN
                or formal_api.native_http_origin != ORIGIN
            ):
                raise JournalError("source HTTP origin changed during authentication")
            qualified = _utc(clock())
            if qualified < started_at:
                raise JournalError("source HTTP post-auth clock regressed")
            _check_verification(
                account_proof, self.initial.account_ref, qualified, submission=True
            )
            _check_verification(
                formal_proof,
                harness.scope.formal_account_ref,
                qualified,
                submission=False,
            )
            horizon = self.journal.read_evidence_sequence()
            qualification = derive_source_close_precall(
                self.owner,
                client_order_id=cid,
                horizon=horizon,
                at=qualified,
                request=command["preparation"],
                _require_current_census=True,
            )
            body = qualification["handoff"]["intent"]["request_body"]
            api.validate_source_close_http_body(body)
            item.update(
                schema=1,
                classification=CLASSIFICATION,
                kind="PRECALL",
                binding=self.binding,
                account_ref=self.initial.account_ref,
                entry_scope=self.initial.entry_scope,
                client_order_id=cid,
                origin=api.native_http_origin,
                authority=dict(AUTHORITY),
                command=command,
                decision_at=command["decision_at"],
                started_at=qualified.isoformat(),
                qualified_at=qualified.isoformat(),
                source_horizon=horizon,
                qualification=qualification,
                request_body=body,
                request_sha256=_digest(body.encode()),
                scope_payload=harness.scope.payload().decode(),
                account_verification=account_proof,
                formal_verification=formal_proof,
                formal_origin=formal_api.native_http_origin,
                limits=limits,
            )
            _check_scope_proof(self.journal, item)

        try:
            self._append(item, clock, prepare)
        except _Retry as repeated:
            return repeated.row
        row = next(
            row
            for row in source_http_inventory(self.journal)
            if row["precall"]["client_order_id"] == cid
        )
        kind, status, encoded = "TRANSPORT_UNKNOWN", None, None
        api.validate_source_close_http_body(item["request_body"])
        invocation = _utc(clock())
        try:
            if (
                api.native_http_origin != item["origin"]
                or formal_api.native_http_origin != item["formal_origin"]
                or _limits(api) != limits
                or invocation < _clock(item["recorded_at"])
            ):
                raise JournalError("source HTTP dispatch identity or clock changed")
            _fresh(item["qualification"], invocation)
        except (ValueError, TypeError):
            kind = "LOCAL_NOT_INVOKED"
        if kind != "LOCAL_NOT_INVOKED":
            try:
                response = api._request(
                    "POST", "/v2/orders", item["request_body"].encode()
                )
                if len(response.body) > limits["max_response_bytes"]:
                    raise JournalError(
                        "source HTTP response exceeds frozen PRECALL budget"
                    )
                status, encoded = (
                    response.status,
                    base64.b64encode(response.body).decode(),
                )
                kind = "HTTP_RESPONSE"
            except Exception:
                pass
        completed = _utc(clock())
        if completed < max(invocation, _clock(item["recorded_at"])):
            raise JournalError("source HTTP completion clock regressed; fence consumed")
        out = {
            key: item[key]
            for key in (
                "schema",
                "classification",
                "binding",
                "account_ref",
                "entry_scope",
                "client_order_id",
                "origin",
                "authority",
            )
        }
        out.update(
            kind="OUTCOME",
            attempt_ref=row["attempt_ref"],
            completed_at=completed.isoformat(),
            result_kind=kind,
            http_status=status,
            body_base64=encoded,
        )

        def outcome_prepare() -> None:
            current = next(
                row
                for row in _inventory(self.journal, exclusion=True)
                if row["precall"]["client_order_id"] == cid
            )
            if (
                current["outcome"] is not None
                or current["attempt_ref"] != row["attempt_ref"]
            ):
                raise JournalError("source HTTP outcome already consumed")

        self._append(out, clock, outcome_prepare)
        return next(
            row
            for row in source_http_inventory(self.journal)
            if row["precall"]["client_order_id"] == cid
        )


class _ReadOnlySourceHttp(SourceCloseHttp):
    def post_once(self, *args: Any, **kwargs: Any) -> Never:
        raise JournalError("historical source HTTP reader cannot dispatch")

    def _append(self, *args: Any, **kwargs: Any) -> Never:
        raise JournalError("historical source HTTP reader cannot write")

    def view(self) -> Never:
        raise JournalError("historical source HTTP reader requires explicit horizon")


def read_source_close_http_at(
    journal: Journal,
    initial: OcoResearchState,
    *,
    through_sequence: int,
    decision_at: datetime,
) -> list[dict[str, Any]]:
    reader = object.__new__(_ReadOnlySourceHttp)
    reader._initialize(journal, initial)
    return reader.read_verified_at(through_sequence, decision_at)
