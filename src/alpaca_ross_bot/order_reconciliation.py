"""Durable, replayable order lifecycle and conservative account comparisons."""

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from uuid import uuid4

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.order_lifecycle import (
    LifecycleState,
    OrderObservation,
    OrderProjection,
    parse_order_observation,
    reduce_order,
    signed_positions,
    timestamp_ns,
)
from alpaca_ross_bot.paper_order_contract import (
    PaperContractError,
    decode_broker_json,
    validate_order_snapshot,
)
from alpaca_ross_bot.paper_order_http import PaperHttpResponse


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    ).encode()


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("quantity is invalid")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("quantity is not finite")
    return result


def _sum(values: list[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = (
            sum(
                len(value.as_tuple().digits) + abs(int(value.as_tuple().exponent))
                for value in values
            )
            + 20
        )
        return sum(values, Decimal(0))


@dataclass(frozen=True)
class ReconciliationPolicy:
    max_snapshot_age: timedelta
    max_capture_span: timedelta
    max_pages: int

    def __post_init__(self) -> None:
        if (
            self.max_snapshot_age <= timedelta(0)
            or self.max_capture_span <= timedelta(0)
            or type(self.max_pages) is not int
            or self.max_pages < 1
        ):
            raise ValueError("reconciliation limits must be explicit and positive")


@dataclass(frozen=True)
class RestBatch:
    account_ref: str
    started_at: datetime
    completed_at: datetime
    local_revision: int
    open_orders: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]
    lookups: dict[str, PaperHttpResponse]
    activities: dict[str, tuple[dict[str, object], ...]]
    complete: bool
    stable: bool
    issues: tuple[str, ...] = ()
    captures: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "account_ref": self.account_ref,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "local_revision": self.local_revision,
            "open_orders": self.open_orders,
            "positions": self.positions,
            "lookups": {
                key: {
                    "status": response.status,
                    "body": base64.b64encode(response.body).decode(),
                }
                for key, response in self.lookups.items()
            },
            "activities": self.activities,
            "complete": self.complete,
            "stable": self.stable,
            "issues": self.issues,
            "captures": self.captures,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    status: str
    differences: tuple[dict[str, str], ...]
    expected_positions: dict[str, str]
    observed_positions: dict[str, str]
    watermark: int
    snapshots_atomic: bool = False
    trading_authorized: bool = False


class OrderReconciler:
    """Own one account's evidence projection; contains no order-write operation."""

    def __init__(
        self,
        journal: Journal,
        account_ref: str,
        *,
        clock: Callable[[], datetime] | None = None,
        record_start: bool = True,
    ) -> None:
        self.journal = journal
        self.account_ref = account_ref
        self.clock = clock or (lambda: datetime.now(UTC))
        self.state_key = "order_lifecycle:" + account_ref
        if journal.read_paper_account_ref() != account_ref:
            raise JournalError("reconciliation account is not bound")
        if not self.verify_rebuild():
            raise JournalError("lifecycle projection does not match durable evidence")
        if type(record_start) is not bool:
            raise ValueError("record_start must be bool")
        if record_start:
            self._record({"kind": "START"}, self.clock(), key="start:" + str(uuid4()))

    @classmethod
    def read_evidence_at(
        cls, journal: Journal, account_ref: str, decision_at: datetime
    ) -> tuple[dict[str, object], tuple[int, ...]]:
        """Rebuild only the visible immutable prefix, without a START mutation.

        This is observation provenance, never a submission or recovery grant.
        Current cache integrity is checked separately from point-in-time replay.
        """
        if type(decision_at) is not datetime or decision_at.utcoffset() != timedelta(0):
            raise ValueError("UTC lifecycle observation clock required")
        reader = cls(
            journal, account_ref, clock=lambda: decision_at, record_start=False
        )
        projected: object | None = None
        seen: dict[str, str] = {}
        refs: list[int] = []
        unavailable_prefix = False
        for event, payload in journal.read_order_events():
            if not event.visible_at(decision_at):
                unavailable_prefix = True
                continue
            if unavailable_prefix or event.external_clock_conflict:
                raise JournalError("lifecycle evidence prefix is unavailable")
            item = decode_broker_json(payload)
            if (
                not isinstance(item, dict)
                or item.get("account_ref") != account_ref
                or event.feed != "paper"
                or event.event_kind != item.get("kind")
            ):
                raise JournalError("lifecycle evidence identity differs")
            if item["kind"] == "BASELINE":
                basis = journal.read_event(int(str(item["evidence_sequence"])))
                if (
                    basis is None
                    or not basis[0].visible_at(event.available_at)
                    or basis[0].external_clock_conflict
                ):
                    raise JournalError("lifecycle baseline source is unavailable")
                refs.append(basis[0].sequence)
            key, fingerprint = str(item["dedupe_key"]), str(item["identity_hash"])
            conflict = key in seen and seen[key] != fingerprint
            seen.setdefault(key, fingerprint)
            projected = reader._reduce(
                projected, item, event.system_first_seen_at, conflict, event.sequence
            )
            refs.append(event.sequence)
        state = reader._initial() if projected is None else reader._state(projected)
        return state, tuple(dict.fromkeys(refs))

    def view(self) -> dict[str, object]:
        raw = self.journal.read_state(self.state_key)
        return self._initial() if raw is None else self._state(raw)

    def _initial(self) -> dict[str, object]:
        return {
            "schema": 1,
            "account_ref": self.account_ref,
            "orders": {},
            "stream_status": "DISCONNECTED",
            "session_id": "",
            "stream_gap": True,
            "issues": [],
            "watermark": 0,
            "last_report": None,
            "baseline": None,
            "stream_position_differences": {},
        }

    def _state(self, value: object) -> dict[str, object]:
        if (
            not isinstance(value, dict)
            or value.get("schema") != 1
            or value.get("account_ref") != self.account_ref
        ):
            raise JournalError("lifecycle projection schema/account is invalid")
        return dict(value)

    def _orders(self, state: dict[str, object]) -> dict[str, OrderProjection]:
        raw = state["orders"]
        if not isinstance(raw, dict):
            raise JournalError("order projection map is invalid")
        return {
            str(key): OrderProjection.from_dict(value) for key, value in raw.items()
        }

    def _intents(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for cid in self.journal.read_order_intent_ids():
            intent = self.journal.read_order_intent(cid)
            result[cid] = {
                "intent": intent,
                "sent": self.journal.has_order_send_attempt(cid),
                "http_history": [
                    {
                        "status": row.http_status,
                        "body": base64.b64encode(row.body).decode(),
                    }
                    for row in self.journal.read_order_http_observations(cid)
                    if row.body is not None and row.http_status in (200, 201)
                ],
            }
        return result

    def _record(
        self,
        item: dict[str, object],
        at: datetime,
        *,
        key: str | None = None,
        identity: bytes | None = None,
    ) -> None:
        item = dict(item) | {"account_ref": self.account_ref}
        payload = _json(item)
        identity = payload if identity is None else identity
        key = (
            key
            or "content:"
            + hashlib.sha256(payload + at.isoformat().encode()).hexdigest()
        )
        item["dedupe_key"] = key
        item["identity_hash"] = hashlib.sha256(identity).hexdigest()
        payload = _json(item)
        canonical_item = json.loads(payload)

        def prepare_locked(raw: bytes) -> bytes:
            nonlocal canonical_item
            canonical_item = json.loads(raw)
            if canonical_item["kind"] == "REST_BATCH":
                current = self._intents()
                if _json(current) != _json(canonical_item["intents"]):
                    canonical_item["batch"]["local_inputs_changed"] = True
                canonical_item["intents"] = current
            return _json(canonical_item)

        def reduce_locked(old: object | None, conflict: bool, sequence: int) -> object:
            if canonical_item["kind"] == "BASELINE":
                if self.journal.read_order_intent_ids():
                    raise JournalError(
                        "position baseline must precede all local intents"
                    )
                if (
                    old is None
                    or self._state(old)["watermark"] != canonical_item["basis_revision"]
                ):
                    raise JournalError("position baseline capture is no longer current")
            return self._reduce(old, canonical_item, at, conflict, sequence)

        self.journal.apply_order_event(
            account_ref=self.account_ref,
            state_key=self.state_key,
            dedupe_key=key,
            event_kind=str(item["kind"]),
            payload=payload,
            received_at=at,
            identity_payload=identity,
            reduce=reduce_locked,
            prepare_payload=prepare_locked,
        )

    def stream_health(self, status: str, at: datetime, session_id: str) -> None:
        if (
            status
            not in {
                "CONNECTING",
                "AUTHENTICATED",
                "SUBSCRIBED",
                "DISCONNECTED",
                "ERROR",
            }
            or not session_id
        ):
            raise ValueError("stream health status/session is invalid")
        self._record({"kind": "HEALTH", "status": status, "session_id": session_id}, at)

    def register_position_baseline(
        self, event_sequence: int, policy: ReconciliationPolicy
    ) -> None:
        """Derive a one-time inventory basis from a recent bound recovery batch."""
        if self.journal.read_order_intent_ids() or self.view()["baseline"] is not None:
            raise JournalError("position baseline must precede all local intents")
        evidence = self.journal.read_event(event_sequence)
        if (
            evidence is None
            or evidence[0].source_id != "ALPACA_ORDER_LIFECYCLE"
            or evidence[0].feed != "paper"
            or evidence[0].event_kind != "REST_BATCH"
        ):
            raise JournalError("position baseline requires a bound recovery batch")
        item = decode_broker_json(evidence[1])
        batch = item.get("batch") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or item.get("account_ref") != self.account_ref
            or item.get("intents") != {}
            or not isinstance(batch, dict)
            or batch.get("account_ref") != self.account_ref
            or batch.get("complete") is not True
            or batch.get("stable") is not True
            or batch.get("issues") != []
            or batch.get("local_inputs_changed") is True
            or batch.get("open_orders") != []
            or batch.get("lookups") != {}
            or batch.get("activities") != {}
        ):
            raise JournalError("position baseline recovery evidence is incomplete")
        report = self.view()["last_report"]
        allowed = {
            "BASELINE_NOT_REGISTERED",
            "POSITION_MISMATCH",
            "STREAM_NOT_SUBSCRIBED",
        }
        if (
            not isinstance(report, dict)
            or report.get("watermark") != event_sequence
            or any(row.get("code") not in allowed for row in report["differences"])
        ):
            raise JournalError("position baseline recovery has unresolved differences")
        now = self.clock()
        try:
            started = datetime.fromisoformat(str(batch["started_at"]))
            completed = datetime.fromisoformat(str(batch["completed_at"]))
            if (
                any(
                    value.tzinfo is None or value.utcoffset() != timedelta(0)
                    for value in (now, started, completed)
                )
                or not started <= completed <= now
                or now - started > policy.max_snapshot_age
                or completed - started > policy.max_capture_span
            ):
                raise ValueError
        except (ValueError, KeyError):
            raise JournalError("position baseline capture is not fresh") from None
        positions = batch.get("positions")
        if not isinstance(positions, list):
            raise JournalError("position baseline snapshot is invalid")
        basis: dict[str, str] = {}
        for row in positions:
            if (
                not isinstance(row, dict)
                or row.get("asset_class") != "us_equity"
                or row.get("side") != "long"
                or not isinstance(row.get("symbol"), str)
            ):
                raise JournalError("position baseline interpretation is unsupported")
            try:
                quantity = _decimal(row.get("qty"))
            except (ValueError, InvalidOperation):
                raise JournalError("position baseline quantity is invalid") from None
            if quantity <= 0 or row["symbol"] in basis:
                raise JournalError("position baseline quantity is invalid")
            basis[row["symbol"]] = str(quantity)
        self._record(
            {
                "kind": "BASELINE",
                "positions": basis,
                "evidence_ref": evidence[0].payload_ref,
                "evidence_sequence": event_sequence,
                "basis_revision": event_sequence,
            },
            now,
        )

    def ingest_frame(self, raw: bytes, at: datetime, session_id: str) -> None:
        request: object = None
        key = "ws_raw:" + hashlib.sha256(raw).hexdigest()
        identity = raw
        try:
            decoded = decode_broker_json(raw)
            identity = _json(decoded)
            if isinstance(decoded, dict) and decoded.get("stream") == "trade_updates":
                data = decoded.get("data")
                if isinstance(data, dict):
                    order = data.get("order")
                    if isinstance(order, dict) and isinstance(
                        order.get("client_order_id"), str
                    ):
                        request = self.journal.read_order_intent(
                            order["client_order_id"]
                        )
                    execution = data.get("execution_id")
                    if isinstance(execution, str) and execution:
                        key = "ws_execution:" + execution + ":" + str(data.get("event"))
        except PaperContractError:
            pass
        self._record(
            {
                "kind": "FRAME",
                "raw": base64.b64encode(raw).decode(),
                "session_id": session_id,
                "intent": request,
            },
            at,
            key=key,
            identity=identity,
        )

    def apply_rest_batch(
        self,
        batch: RestBatch,
        policy: ReconciliationPolicy,
        *,
        at: datetime | None = None,
    ) -> ReconciliationReport:
        at = self.clock() if at is None else at
        self._record(
            {
                "kind": "REST_BATCH",
                "batch": batch.to_dict(),
                "intents": self._intents(),
                "max_age": str(policy.max_snapshot_age.total_seconds()),
                "max_span": str(policy.max_capture_span.total_seconds()),
            },
            at,
            key="rest_batch:" + str(uuid4()),
        )
        report = self.view()["last_report"]
        if not isinstance(report, dict):
            raise JournalError("reconciliation report was not persisted")
        return ReconciliationReport(
            status=str(report["status"]),
            differences=tuple(report["differences"]),
            expected_positions=dict(report["expected_positions"]),
            observed_positions=dict(report["observed_positions"]),
            watermark=int(report["watermark"]),
        )

    def _reduce(
        self,
        old: object | None,
        item: dict[str, object],
        at: datetime,
        conflict: bool,
        sequence: int,
    ) -> dict[str, object]:
        state = self._initial() if old is None else self._state(old)
        orders = self._orders(state)
        raw_issues = state["issues"]
        if not isinstance(raw_issues, list):
            raise JournalError("lifecycle issue list is invalid")
        issues = set(str(value) for value in raw_issues)
        kind = item["kind"]
        if conflict:
            issues.add("EVENT_ID_CONFLICT")
            state["stream_gap"] = True
        if kind == "START":
            state.update(
                stream_status="DISCONNECTED",
                session_id="",
                stream_gap=True,
                last_report=None,
            )
        elif kind == "BASELINE":
            if state["baseline"] is not None or orders:
                raise JournalError("position baseline is immutable")
            evidence = self.journal.read_event(int(str(item["evidence_sequence"])))
            if evidence is None or evidence[0].payload_ref != item["evidence_ref"]:
                raise JournalError("position baseline evidence is missing")
            state["baseline"] = item["positions"]
        elif kind == "HEALTH":
            status = str(item["status"])
            session = str(item["session_id"])
            if status == "CONNECTING":
                state.update(
                    session_id=session,
                    stream_status=status,
                    stream_gap=True,
                    last_report=None,
                )
            elif state["session_id"] == session:
                state["stream_status"] = status
                if status in {"DISCONNECTED", "ERROR"}:
                    state.update(stream_gap=True, last_report=None)
        elif kind == "FRAME":
            self._apply_frame(state, orders, issues, item, at)
            state["last_report"] = None
        elif kind == "REST_BATCH":
            self._apply_batch(state, orders, issues, item, at, sequence)
        else:
            raise JournalError("unknown lifecycle evidence kind")
        state["orders"] = {
            cid: value.to_dict() for cid, value in sorted(orders.items())
        }
        state["issues"] = sorted(issues)
        state["watermark"] = sequence
        return state

    def _apply_frame(
        self,
        state: dict[str, object],
        orders: dict[str, OrderProjection],
        issues: set[str],
        item: dict[str, object],
        at: datetime,
    ) -> None:
        raw = base64.b64decode(str(item["raw"]), validate=True)
        try:
            frame = decode_broker_json(raw)
            if not isinstance(frame, dict):
                raise ValueError
            stream = frame.get("stream")
            if stream in {"authorization", "listening"}:
                return
            if stream != "trade_updates" or not isinstance(frame.get("data"), dict):
                raise ValueError
            if (
                state["session_id"] != item["session_id"]
                or state["stream_status"] != "SUBSCRIBED"
            ):
                state["stream_gap"] = True
                return
            data = frame["data"]
            if data.get("event") not in {
                "new",
                "fill",
                "partial_fill",
                "canceled",
                "expired",
                "done_for_day",
                "replaced",
                "accepted",
                "rejected",
                "pending_new",
                "stopped",
                "pending_cancel",
                "pending_replace",
                "calculated",
                "suspended",
                "order_replace_rejected",
                "order_cancel_rejected",
            }:
                issues.add("UNSUPPORTED_TRADE_EVENT")
                return
            intent = item.get("intent")
            if (
                not isinstance(intent, dict)
                or intent.get("paper_account_ref") != self.account_ref
                or not isinstance(intent.get("request_body"), str)
            ):
                issues.add("UNOWNED_STREAM_ORDER")
                return
            observation = parse_order_observation(
                intent["request_body"],
                _json(data.get("order")),
                self.account_ref,
                "WS",
                "sha256:" + hashlib.sha256(raw).hexdigest(),
                broker_timestamp=data.get("timestamp"),
            )
            cid = observation.snapshot.client_order_id
            if any(
                other.client_order_id != cid
                and other.broker_order_id == observation.snapshot.broker_order_id
                for other in orders.values()
            ):
                issues.add("OWNED_BROKER_ID_REUSED")
                state["stream_gap"] = True
                return
            orders[cid] = reduce_order(orders.get(cid), observation)
            # Receipt datetimes represent microseconds; retain up to 999ns of
            # representation uncertainty without discarding provider precision.
            if observation.broker_time_ns > timestamp_ns(at.isoformat()) + 999:
                issues.add("PROVIDER_TIMESTAMP_FUTURE")
                state["stream_gap"] = True
            if data.get("position_qty") is not None:
                observed = _decimal(data["position_qty"])
                expected = signed_positions(tuple(orders.values())).get(
                    observation.symbol, Decimal(0)
                )
                baseline = state["baseline"]
                if isinstance(baseline, dict):
                    expected = _sum(
                        [expected, _decimal(baseline.get(observation.symbol, "0"))]
                    )
                discrepancy = state["stream_position_differences"]
                if not isinstance(discrepancy, dict):
                    raise ValueError
                discrepancy = dict(discrepancy)
                if observed != expected or baseline is None:
                    discrepancy[observation.symbol] = {
                        "expected": str(expected),
                        "observed": str(observed),
                        "evidence_ref": observation.evidence_ref,
                    }
                else:
                    discrepancy.pop(observation.symbol, None)
                state["stream_position_differences"] = discrepancy
        except (ValueError, TypeError, KeyError, InvalidOperation, PaperContractError):
            issues.add("MALFORMED_TRADE_UPDATE")
            state["stream_gap"] = True

    def _restore_http_history(
        self,
        orders: dict[str, OrderProjection],
        issues: set[str],
        cid: str,
        request_body: str,
        history: object,
    ) -> list[tuple[str, object, object]]:
        """Restore known exposure even when the current lookup is unavailable."""
        differences: list[tuple[str, object, object]] = []
        if not isinstance(history, list):
            return [("HISTORICAL_ORDER_EVIDENCE_INVALID", "", "")]
        for row in history:
            if not isinstance(row, dict) or not isinstance(row.get("body"), str):
                differences.append(("HISTORICAL_ORDER_EVIDENCE_INVALID", "", ""))
                continue
            try:
                body = base64.b64decode(row["body"], validate=True)
                snapshot = validate_order_snapshot(request_body, body)
            except (ValueError, PaperContractError):
                continue
            if any(
                other.client_order_id != cid
                and other.broker_order_id == snapshot.broker_order_id
                for other in orders.values()
            ):
                issues.add("OWNED_BROKER_ID_REUSED")
                differences.append(
                    ("OWNED_BROKER_ID_REUSED", "", snapshot.broker_order_id)
                )
                continue
            previous = orders.get(cid)
            if (
                previous is not None
                and previous.broker_order_id != snapshot.broker_order_id
            ):
                issues.add("BROKER_ID_CONFLICT:" + cid)
                differences.append(
                    (
                        "BROKER_ID_CONFLICT",
                        previous.broker_order_id,
                        snapshot.broker_order_id,
                    )
                )
            reference = "sha256:" + hashlib.sha256(body).hexdigest()
            try:
                observation = parse_order_observation(
                    request_body, body, self.account_ref, "REST", reference
                )
            except (ValueError, PaperContractError):
                differences.append(
                    ("HISTORICAL_ORDER_TIME_UNVERIFIED", "", snapshot.filled_quantity)
                )
                raw_order = decode_broker_json(body)
                assert isinstance(raw_order, dict)
                if previous is None:
                    # Zero is an explicit unknown-time sentinel, never a
                    # reconstructed broker timestamp; the issue blocks acceptance.
                    orders[cid] = OrderProjection(
                        self.account_ref,
                        cid,
                        snapshot.broker_order_id,
                        str(raw_order["symbol"]),
                        str(raw_order["side"]),
                        snapshot.quantity,
                        snapshot.filled_quantity,
                        LifecycleState.UNKNOWN,
                        snapshot.status,
                        0,
                        reference,
                        ("historical_time_unverified",),
                    )
                else:
                    identity_issue = (
                        {"identity_conflict:broker_order_id"}
                        if previous.broker_order_id != snapshot.broker_order_id
                        else set()
                    )
                    orders[cid] = replace(
                        previous,
                        filled_quantity=(
                            previous.filled_quantity
                            if identity_issue
                            else max(previous.filled_quantity, snapshot.filled_quantity)
                        ),
                        state=LifecycleState.UNKNOWN,
                        issues=tuple(
                            sorted(
                                set(previous.issues)
                                | identity_issue
                                | {"historical_time_unverified"}
                            )
                        ),
                    )
            else:
                orders[cid] = reduce_order(previous, observation)
        return differences

    def _apply_batch(
        self,
        state: dict[str, object],
        orders: dict[str, OrderProjection],
        issues: set[str],
        item: dict[str, object],
        at: datetime,
        sequence: int,
    ) -> None:
        batch = item["batch"]
        intents = item["intents"]
        if not isinstance(batch, dict) or not isinstance(intents, dict):
            raise JournalError("REST recovery evidence is invalid")
        differences: list[dict[str, str]] = []
        evidence = "journal:" + str(sequence)

        def difference(
            code: str,
            subject: str = "account",
            expected: object = "",
            observed: object = "",
        ) -> None:
            differences.append(
                {
                    "code": code,
                    "subject": subject,
                    "expected": str(expected),
                    "observed": str(observed),
                    "evidence_ref": evidence,
                }
            )

        incomplete = False
        if batch.get("account_ref") != self.account_ref:
            difference("ACCOUNT_MISMATCH")
            expected = signed_positions(tuple(orders.values()))
            baseline = state["baseline"]
            if isinstance(baseline, dict):
                for symbol, quantity in baseline.items():
                    expected[str(symbol)] = _sum(
                        [expected.get(str(symbol), Decimal(0)), _decimal(quantity)]
                    )
            state["stream_gap"] = True
            state["last_report"] = {
                "status": "INCOMPLETE",
                "differences": differences,
                "expected_positions": {
                    key: str(value) for key, value in expected.items() if value
                },
                "observed_positions": {},
                "watermark": sequence,
                "snapshots_atomic": False,
                "trading_authorized": False,
            }
            return
        try:
            started = datetime.fromisoformat(str(batch["started_at"]))
            completed = datetime.fromisoformat(str(batch["completed_at"]))
            if any(
                value.tzinfo is None or value.utcoffset() != timedelta(0)
                for value in (started, completed, at)
            ):
                raise ValueError
            if (
                not started <= completed <= at
                or (at - started).total_seconds() > float(str(item["max_age"]))
                or (completed - started).total_seconds() > float(str(item["max_span"]))
            ):
                difference("SNAPSHOT_CLOCK_OR_FRESHNESS")
                incomplete = True
        except (ValueError, KeyError):
            difference("SNAPSHOT_CLOCK_INVALID")
            incomplete = True
        if batch.get("complete") is not True or batch.get("stable") is not True:
            difference("INCOMPLETE_OR_UNSTABLE_REST")
            incomplete = True
        if batch.get("local_revision") != state["watermark"]:
            difference(
                "LOCAL_CHANGED_DURING_CAPTURE",
                expected=batch.get("local_revision"),
                observed=state["watermark"],
            )
            incomplete = True
        for code in batch.get("issues", []):
            difference(str(code))
            incomplete = True
        if batch.get("local_inputs_changed") is True:
            difference("LOCAL_INPUTS_CHANGED_DURING_CAPTURE")
            incomplete = True
        lookups = batch.get("lookups")
        activities = batch.get("activities")
        if not isinstance(lookups, dict) or not isinstance(activities, dict):
            difference("REST_RESPONSE_SHAPE")
            incomplete = True
            lookups, activities = {}, {}
        parsed: dict[str, OrderObservation] = {}
        for cid, record in sorted(intents.items()):
            if not isinstance(record, dict) or not isinstance(
                record.get("intent"), dict
            ):
                difference("LOCAL_INTENT_INVALID", str(cid))
                continue
            intent = record["intent"]
            if intent.get("paper_account_ref") != self.account_ref or not isinstance(
                intent.get("request_body"), str
            ):
                difference("LOCAL_INTENT_IDENTITY", str(cid))
                continue
            if record.get("sent") is not True:
                difference("INTENT_NOT_SENT", str(cid))
                continue
            for code, expected_value, observed_value in self._restore_http_history(
                orders,
                issues,
                str(cid),
                intent["request_body"],
                record.get("http_history", []),
            ):
                difference(code, str(cid), expected_value, observed_value)
            result = lookups.get(cid)
            if (
                not isinstance(result, dict)
                or result.get("status") != 200
                or not isinstance(result.get("body"), str)
            ):
                difference("LOOKUP_UNKNOWN", str(cid))
                previous = orders.get(str(cid))
                if previous is not None:
                    orders[str(cid)] = replace(
                        previous,
                        state=LifecycleState.UNKNOWN,
                        issues=tuple(sorted(set(previous.issues) | {"lookup_unknown"})),
                    )
                continue
            try:
                body = base64.b64decode(result["body"], validate=True)
                observation = parse_order_observation(
                    intent["request_body"],
                    body,
                    self.account_ref,
                    "REST",
                    "sha256:" + hashlib.sha256(body).hexdigest(),
                )
                if observation.snapshot.client_order_id != cid:
                    raise ValueError
                if any(
                    other.client_order_id != cid
                    and other.broker_order_id == observation.snapshot.broker_order_id
                    for other in orders.values()
                ) or any(
                    other.snapshot.broker_order_id
                    == observation.snapshot.broker_order_id
                    for other in parsed.values()
                ):
                    difference("OWNED_BROKER_ID_REUSED", str(cid))
                    issues.add("OWNED_BROKER_ID_REUSED")
                    continue
                if (
                    observation.broker_time_ns
                    > timestamp_ns(str(batch["completed_at"])) + 999
                ):
                    difference("PROVIDER_TIMESTAMP_FUTURE", str(cid))
                    incomplete = True
                previous = orders.get(str(cid))
                if (
                    previous is not None
                    and previous.broker_order_id != observation.snapshot.broker_order_id
                ):
                    difference(
                        "BROKER_ID_CONFLICT",
                        str(cid),
                        previous.broker_order_id,
                        observation.snapshot.broker_order_id,
                    )
                    issues.add("BROKER_ID_CONFLICT:" + str(cid))
                parsed[str(cid)] = observation
            except (ValueError, TypeError, KeyError, PaperContractError):
                difference("ORDER_RESPONSE_INVALID", str(cid))
        # Only a completely covered round may repair a recoverable status conflict.
        candidates = dict(orders)
        for cid, observation in parsed.items():
            candidates[cid] = reduce_order(
                orders.get(cid), observation, authoritative=not incomplete
            )

        activity_owners: dict[str, str] = {}
        for cid, projection in candidates.items():
            if cid not in intents:
                difference("LOCAL_PROJECTION_WITHOUT_INTENT", cid)
            if projection.issues:
                difference(
                    "ORDER_STATE_UNRESOLVED",
                    cid,
                    "coherent broker state",
                    ",".join(projection.issues),
                )
            if projection.state.value == "UNKNOWN":
                difference("ORDER_STATE_UNKNOWN", cid, observed=projection.raw_status)
            rows = activities.get(projection.broker_order_id)
            if not isinstance(rows, list):
                difference("FILL_ACTIVITY_COVERAGE", cid)
                continue
            total = Decimal(0)
            seen: set[str] = set()
            try:
                quantities = []
                for row in rows:
                    if (
                        not isinstance(row, dict)
                        or row.get("activity_type") != "FILL"
                        or row.get("order_id") != projection.broker_order_id
                        or row.get("symbol") != projection.symbol
                        or row.get("side") != projection.side
                    ):
                        raise ValueError
                    identity = row.get("id")
                    if (
                        not isinstance(identity, str)
                        or not identity
                        or identity in seen
                    ):
                        raise ValueError
                    seen.add(identity)
                    if (
                        identity in activity_owners
                        and activity_owners[identity] != projection.broker_order_id
                    ):
                        difference(
                            "FILL_ACTIVITY_ID_CONFLICT",
                            cid,
                            activity_owners[identity],
                            projection.broker_order_id,
                        )
                    activity_owners[identity] = projection.broker_order_id
                    quantity = _decimal(row.get("qty"))
                    if quantity <= 0:
                        raise ValueError
                    transaction_time = row.get("transaction_time")
                    if (
                        not isinstance(transaction_time, str)
                        or timestamp_ns(transaction_time)
                        > timestamp_ns(str(batch["completed_at"])) + 999
                    ):
                        raise ValueError
                    if (
                        row.get("cum_qty") is not None
                        and not 0 <= _decimal(row["cum_qty"]) <= projection.quantity
                    ):
                        raise ValueError
                    if (
                        row.get("leaves_qty") is not None
                        and not 0 <= _decimal(row["leaves_qty"]) <= projection.quantity
                    ):
                        raise ValueError
                    quantities.append(quantity)
                    if row.get("price") is not None and _decimal(row["price"]) <= 0:
                        raise ValueError
                # Exact sum: no dependence on the ambient Decimal context.
                if quantities:
                    total = _sum(quantities)
                if total != projection.filled_quantity:
                    difference(
                        "FILL_ACTIVITY_MISMATCH", cid, projection.filled_quantity, total
                    )
            except (ValueError, TypeError, InvalidOperation):
                difference("FILL_ACTIVITY_INVALID", cid)

        raw_open = batch.get("open_orders")
        open_ids: set[str] = set()
        if not isinstance(raw_open, list):
            difference("OPEN_ORDER_RESPONSE_INVALID")
        else:
            for row in raw_open:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not isinstance(row.get("client_order_id"), str)
                ):
                    difference("OPEN_ORDER_RESPONSE_INVALID")
                    continue
                cid = row["client_order_id"]
                if cid in open_ids:
                    difference("DUPLICATE_OPEN_ORDER", cid)
                open_ids.add(cid)
                open_projection = candidates.get(cid)
                if open_projection is None:
                    difference("EXTERNAL_ORDER", cid)
                elif row["id"] != open_projection.broker_order_id:
                    difference("BROKER_ID_CONFLICT", cid)
                elif cid not in parsed:
                    difference("OPEN_ORDER_LOOKUP_UNVERIFIED", cid)
                elif row.get("status") != open_projection.raw_status or _json(
                    row
                ) != _json(decode_broker_json(base64.b64decode(lookups[cid]["body"]))):
                    # Equality includes request fields and cumulative quantities.
                    difference("OPEN_ORDER_LOOKUP_DISAGREEMENT", cid)
        for cid, projection in candidates.items():
            terminal = projection.state.value in {
                "FILLED",
                "CANCELED",
                "EXPIRED",
                "REJECTED",
            }
            if not terminal and cid not in open_ids:
                difference("LOCAL_ORDER_MISSING_FROM_OPEN", cid)
            if terminal and cid in open_ids:
                difference("TERMINAL_ORDER_STILL_OPEN", cid)

        expected = signed_positions(tuple(candidates.values()))
        baseline = state["baseline"]
        if not isinstance(baseline, dict):
            difference("BASELINE_NOT_REGISTERED")
            incomplete = True
        else:
            for symbol, quantity in baseline.items():
                expected[str(symbol)] = _sum(
                    [expected.get(str(symbol), Decimal(0)), _decimal(quantity)]
                )
        observed: dict[str, Decimal] = {}
        raw_positions = batch.get("positions")
        if not isinstance(raw_positions, list):
            difference("POSITION_RESPONSE_INVALID")
        else:
            for row in raw_positions:
                try:
                    if (
                        not isinstance(row, dict)
                        or row.get("asset_class") != "us_equity"
                        or not isinstance(row.get("symbol"), str)
                    ):
                        raise ValueError
                    symbol = row["symbol"]
                    quantity = _decimal(row.get("qty"))
                    if symbol in observed:
                        raise ValueError
                    if row.get("side") == "long" and quantity > 0:
                        observed[symbol] = quantity
                    elif row.get("side") == "short":
                        # Do not infer REST sign conventions from position_qty.
                        difference(
                            "POSITION_SHORT_SIGN_UNVERIFIED", symbol, observed=quantity
                        )
                    else:
                        raise ValueError
                except (ValueError, TypeError, InvalidOperation):
                    difference("POSITION_RESPONSE_INVALID")
        for symbol in sorted(set(expected) | set(observed)):
            if expected.get(symbol, Decimal(0)) != observed.get(symbol, Decimal(0)):
                difference(
                    "POSITION_MISMATCH",
                    symbol,
                    expected.get(symbol, Decimal(0)),
                    observed.get(symbol, Decimal(0)),
                )
        recoverable = {"MALFORMED_TRADE_UPDATE", "PROVIDER_TIMESTAMP_FUTURE"}
        for code in sorted(issues - recoverable):
            difference(code)
        if state["stream_status"] != "SUBSCRIBED":
            difference("STREAM_NOT_SUBSCRIBED")
            incomplete = True
        if differences:
            # Retain newly seen cumulative exposure, but don't clear conflict states.
            for cid, observation in parsed.items():
                orders[cid] = reduce_order(orders.get(cid), observation)
        else:
            orders.clear()
            orders.update(candidates)
            issues.difference_update(recoverable)
            state["stream_gap"] = False
            state["stream_position_differences"] = {}
        state["last_report"] = {
            "status": "INCOMPLETE"
            if incomplete
            else "DIFFERENCES"
            if differences
            else "MATCHED",
            "differences": differences,
            "expected_positions": {
                key: str(value) for key, value in expected.items() if value
            },
            "observed_positions": {
                key: str(value) for key, value in observed.items() if value
            },
            "watermark": sequence,
            "snapshots_atomic": False,
            "trading_authorized": False,
        }

    def verify_rebuild(self) -> bool:
        raw = self.journal.read_state(self.state_key)
        events = self.journal.read_order_events()
        if raw is None:
            return not events and not self.journal.read_order_evidence_cache(
                self.account_ref
            )
        state = self._state(raw)
        if not events or int(str(state["watermark"])) != events[-1][0].sequence:
            return False
        projected: object | None = None
        seen: dict[str, str] = {}
        expected_cache: dict[str, object] = {}
        for event, payload in events:
            item = json.loads(payload)
            if item.get("account_ref") != self.account_ref:
                continue
            key, fingerprint = item["dedupe_key"], item["identity_hash"]
            conflict = key in seen and seen[key] != fingerprint
            seen.setdefault(key, fingerprint)
            expected_cache.setdefault(
                key, {"hash": "sha256:" + fingerprint, "sequence": event.sequence}
            )
            projected = self._reduce(
                projected, item, event.system_first_seen_at, conflict, event.sequence
            )
        return (
            projected == state
            and expected_cache
            == self.journal.read_order_evidence_cache(self.account_ref)
        )
