"""Bounded injected source-CLOSE GET evidence, never ownership or broker proof.

The callback label describes provenance, not proof that its implementation is
network-free. This module supplies no HTTP client, credentials, CLI or sender.
Page exhaustion is a capture-local component, not provider posting finality.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Protocol, cast
from urllib.parse import urlencode

from .crossed_stop_attempts import ORIGIN, SourceCloseAttempts, _source_attempt_reader
from .journal import Journal, JournalError
from .market_input_checks import _datetime_ns, _timestamp_ns
from .native_oco_requests import _clock, _copy, _digest, _json, _utc
from .oco_research import OcoResearchState
from .paper_order_contract import (
    decode_broker_json,
    paper_account_ref,
    validate_order_snapshot,
    validate_saved_request,
)

SOURCE = "ALPACA_SOURCE_CLOSE_INJECTED_GET"
KIND = "SOURCE_CLOSE_RAW_CAPTURE"
GET_ORIGIN = "INJECTED_GET_CALLBACK_NOT_BROKER_PROOF"
AUTHORITY = dict.fromkeys(
    (
        "source_admission_accepted",
        "source_fill_ownership_accepted",
        "local_ownership_accepted",
        "protection_accepted",
        "risk_release_authorized",
        "runtime_authorized",
        "dispatch_authorized",
        "formal_paper_authorized",
    ),
    False,
)


@dataclass(frozen=True, repr=False)
class SourceCloseGetResponse:
    status: int
    body: bytes


def _stamp(value: object) -> int:
    # Preserve nanoseconds; datetime.fromisoformat would silently truncate them.
    if isinstance(value, str) and value.endswith("+00:00"):
        value = value[:-6] + "Z"
    return _timestamp_ns(value)


def _quantity(value: object, *, positive: bool = False) -> Fraction:
    if type(value) is not str or not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ValueError("exact decimal quantity required")
    result = Fraction(value)
    if positive and result <= 0:
        raise ValueError("positive quantity required")
    return result


def _snapshot(pin: dict[str, Any], body: bytes, available: datetime) -> dict[str, Any]:
    parsed = decode_broker_json(body)
    if type(parsed) is not dict:
        raise ValueError("order snapshot object required")
    order = cast(dict[str, Any], parsed)
    for field in ("qty", "filled_qty"):
        value = order.get(field)
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError("exact order quantity required")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("exact order quantity required") from exc
        if not number.is_finite() or number < 0:
            raise ValueError("finite nonnegative order quantity required")
        # Bound expansion by the existing caller-supplied raw-body resource cap,
        # not by an invented provider precision or trading quantity policy.
        parts = number.as_tuple()
        if not number.is_zero() and (
            len(parts.digits) + abs(int(parts.exponent)) + 1 > pin["max_body_bytes"]
        ):
            raise ValueError("order quantity expansion exceeds capture body budget")
    snapshot = validate_order_snapshot(pin["request_body"], body)
    created, submitted, updated = (
        _stamp(order.get(key)) for key in ("created_at", "submitted_at", "updated_at")
    )
    if not (
        _datetime_ns(_clock(pin["precall_recorded_at"]))
        <= created
        <= submitted
        <= updated
        <= _datetime_ns(available)
    ):
        raise ValueError("order timestamp chronology differs")
    for key in ("filled_at", "canceled_at", "expired_at", "failed_at", "replaced_at"):
        if order.get(key) is not None:
            if not submitted <= _stamp(order[key]) <= updated:
                raise ValueError("order lifecycle timestamp differs")
    if snapshot.filled_quantity > 0 and order.get("filled_at") is None:
        raise ValueError("positive fill needs timestamp")
    return {
        "order_id": snapshot.broker_order_id,
        "status": snapshot.status,
        "quantity": str(snapshot.quantity),
        "filled_quantity": "0"
        if snapshot.filled_quantity.is_zero()
        else str(snapshot.filled_quantity),
        "submitted_at_ns": submitted,
        "updated_at_ns": updated,
    }


def _body(row: dict[str, Any]) -> bytes | None:
    if row["error"] is not None or row["status"] != 200:
        return None
    return base64.b64decode(row["body_base64"], validate=True)


def _walk(
    pin: dict[str, Any],
    read: Callable[[str], dict[str, Any]],
    *,
    http_source: bool = False,
) -> dict[str, Any]:
    """Shared deterministic GET plan: malformed identity never becomes a path."""
    result: dict[str, Any] = {
        "association": "UNESTABLISHED",
        "account_consistency": False,
        "post_reported_order_id": None,
        "get_order_id": None,
        "order_snapshot": None,
        "fill_pages_exhausted": False,
        "fill_component_consistent": False,
        "fill_quantity": "0",
        "matching_fills": [],
        "issues": [],
        "authority": dict(AUTHORITY),
    }
    issues = result["issues"]
    posted = None
    if pin["outcome_kind"] == (
        "HTTP_RESPONSE" if http_source else "INJECTED_RESPONSE"
    ) and pin["outcome_status"] in (
        200,
        201,
    ):
        try:
            posted = _snapshot(
                pin,
                base64.b64decode(pin["outcome_body_base64"], validate=True),
                _clock(pin["outcome_completed_at"]),
            )
            result["post_reported_order_id"] = posted["order_id"]
        except (ValueError, KeyError, TypeError):
            issues.append("POST_REPORTED_SNAPSHOT_INVALID")
    account = read("/v2/account")
    try:
        raw_account = _body(account)
        if raw_account is None or paper_account_ref(raw_account) != pin["account_ref"]:
            raise ValueError
    except (ValueError, TypeError):
        issues.append("ACCOUNT_NOT_MATCHED")
        return result
    result["account_consistency"] = True
    lookup = read(
        "/v2/orders:by_client_order_id?"
        + urlencode({"client_order_id": pin["client_order_id"]})
    )
    try:
        raw_lookup = _body(lookup)
        if raw_lookup is None:
            raise ValueError
        found = _snapshot(pin, raw_lookup, _clock(lookup["available_at"]))
    except (ValueError, KeyError, TypeError):
        issues.append("CID_LOOKUP_NOT_MATCHED")
        return result
    result["get_order_id"] = found["order_id"]
    if posted is not None and posted["order_id"] != found["order_id"]:
        issues.append("POST_GET_UUID_CONFLICT")
        return result
    result["association"] = (
        (
            "HTTP_POST_GET_IDENTITY_CONSISTENT"
            if http_source
            else "INJECTED_POST_GET_IDENTITY_CONSISTENT"
        )
        if posted is not None
        else "LOCAL_NOT_INVOKED_GET_CANDIDATE"
        if pin["outcome_kind"] == "LOCAL_NOT_INVOKED"
        else "GET_CANDIDATE_ONLY"
    )
    detail = read(f"/v2/orders/{found['order_id']}?nested=true")
    try:
        raw_detail = _body(detail)
        if raw_detail is None:
            raise ValueError
        order = _snapshot(pin, raw_detail, _clock(detail["available_at"]))
        if (
            order["order_id"] != found["order_id"]
            or order["updated_at_ns"] < found["updated_at_ns"]
            or Fraction(order["filled_quantity"]) < Fraction(found["filled_quantity"])
            or (
                posted is not None
                and (
                    order["updated_at_ns"] < posted["updated_at_ns"]
                    or Fraction(order["filled_quantity"])
                    < Fraction(posted["filled_quantity"])
                )
            )
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        issues.append("ORDER_DETAIL_NOT_MATCHED")
        return result
    result["order_snapshot"] = order
    request = validate_saved_request(pin["request_body"])
    seen_cursors: set[str] = set()
    seen_fills: dict[str, dict[str, Any]] = {}
    total = Fraction(0)
    cursor = None
    last_time = order["submitted_at_ns"]
    for _ in range(pin["max_fill_pages"]):
        query = [
            ("activity_types", "FILL"),
            ("order_id", order["order_id"]),
            ("direction", "asc"),
            ("page_size", str(pin["page_size"])),
        ]
        if cursor is not None:
            query.append(("page_token", cursor))
        page = read("/v2/account/activities?" + urlencode(query))
        try:
            raw_page = _body(page)
            rows = None if raw_page is None else decode_broker_json(raw_page)
            if type(rows) is not list or len(rows) > pin["page_size"]:
                raise ValueError
        except (ValueError, TypeError):
            issues.append("FILL_PAGE_INVALID_OR_FAILED")
            break
        if not rows:
            result["fill_pages_exhausted"] = True
            break
        for fill in rows:
            try:
                if type(fill) is not dict:
                    raise ValueError
                identity = fill.get("id")
                if (
                    type(identity) is not str
                    or not identity
                    or identity != identity.strip()
                ):
                    raise ValueError
                if identity in seen_fills:
                    issues.append(
                        "DUPLICATE_FILL_ID"
                        if seen_fills[identity] == fill
                        else "CONFLICTING_FILL_ID"
                    )
                    continue
                seen_fills[identity] = fill
                if fill.get("order_id") != order["order_id"]:
                    raise ValueError
                quantity = _quantity(fill.get("qty"), positive=True)
                cumulative = _quantity(fill.get("cum_qty"))
                leaves = _quantity(fill.get("leaves_qty"))
                stamp = _stamp(fill.get("transaction_time"))
                if (
                    fill.get("activity_type") != "FILL"
                    or fill.get("type") != "fill"
                    or fill.get("symbol") != request["symbol"]
                    or fill.get("side") != request["side"]
                    or _quantity(fill.get("price"), positive=True) <= 0
                    or not last_time
                    <= stamp
                    <= _datetime_ns(_clock(detail["available_at"]))
                    <= _datetime_ns(_clock(page["available_at"]))
                    or cumulative != total + quantity
                    or cumulative + leaves != Fraction(order["quantity"])
                ):
                    raise ValueError
                total += quantity
                last_time = stamp
                result["matching_fills"].append(
                    {
                        key: fill[key]
                        for key in (
                            "id",
                            "order_id",
                            "activity_type",
                            "type",
                            "symbol",
                            "side",
                            "qty",
                            "cum_qty",
                            "leaves_qty",
                            "price",
                            "transaction_time",
                        )
                    }
                )
            except (ValueError, TypeError, KeyError, ZeroDivisionError):
                issues.append("FILL_ROW_INCONSISTENT")
        last = rows[-1]
        next_cursor = last.get("id") if type(last) is dict else None
        if (
            type(next_cursor) is not str
            or not next_cursor
            or next_cursor != next_cursor.strip()
            or next_cursor in seen_cursors
        ):
            issues.append("FILL_CURSOR_INVALID_OR_LOOP")
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        issues.append("FILL_PAGE_BUDGET_TRUNCATED")
    result["fill_quantity"] = str(total)
    result["fill_component_consistent"] = (
        result["fill_pages_exhausted"]
        and not issues
        and total == Fraction(order["filled_quantity"])
    )
    if total != Fraction(order["filled_quantity"]):
        issues.append("FILL_SUM_DIFFERS_FROM_SNAPSHOT")
    return result


class _CaptureAttemptReader(Protocol):
    @property
    def journal(self) -> Journal: ...

    def read_verified_at(
        self, through_sequence: int, decision_at: datetime, /
    ) -> list[dict[str, Any]]: ...


class _SourceCloseCaptureCore:
    """Private raw-capture mechanics; public adapters fix the provenance profile."""

    _source = SOURCE
    _kind = KIND
    _origin = GET_ORIGIN
    _attempt_origin = ORIGIN

    def __init__(self, attempts: _CaptureAttemptReader) -> None:
        self.attempts, self.journal = attempts, attempts.journal

    def _walk(
        self, pin: dict[str, Any], read: Callable[[str], dict[str, Any]]
    ) -> dict[str, Any]:
        return _walk(pin, read)

    def _pin(
        self,
        attempt_ref: dict[str, Any],
        outcome_ref: dict[str, Any] | None,
        horizon: int,
        decision: datetime,
        max_body_bytes: int,
        max_fill_pages: int,
        page_size: int,
    ) -> dict[str, Any]:
        for value, limit in (
            (max_body_bytes, 1_000_000),
            (max_fill_pages, 100),
            (page_size, 100),
        ):
            if type(value) is not int or not 0 < value <= limit:
                raise JournalError("bounded source GET budgets required")
        for ref in (attempt_ref, outcome_ref):
            if ref is not None and (
                type(ref) is not dict
                or set(ref) != {"sequence", "hash"}
                or type(ref["sequence"]) is not int
                or ref["sequence"] < 1
                or type(ref["hash"]) is not str
            ):
                raise JournalError("exact source evidence reference required")
        rows = [
            row
            for row in self.attempts.read_verified_at(horizon, decision)
            if row["attempt_ref"] == attempt_ref
        ]
        if len(rows) != 1:
            raise JournalError("verified source attempt reference required")
        row = rows[0]
        pre = row["precall"]
        intent = validate_saved_request(pre["request_body"])
        if (
            pre["origin"] != self._attempt_origin
            or intent["type"] != "market"
            or intent["side"] != "sell"
            or pre["account_ref"] != self.journal.read_paper_account_ref()
            or type(horizon) is not int
            or not attempt_ref["sequence"] <= horizon
            or _clock(pre["recorded_at"]) > decision
        ):
            raise JournalError("source GET precall binding differs")
        out = None
        if outcome_ref is not None:
            if row["outcome_ref"] != outcome_ref or outcome_ref["sequence"] > horizon:
                raise JournalError("verified source outcome reference required")
            out = row["outcome"]
            if _clock(out["recorded_at"]) > decision:
                raise JournalError("source outcome unavailable at capture decision")
        elif (
            row["outcome_ref"] is not None and row["outcome_ref"]["sequence"] <= horizon
        ):
            raise JournalError("available source outcome must be pinned")
        return {
            "attempt_ref": _copy(attempt_ref),
            "outcome_ref": _copy(outcome_ref),
            "source_horizon": horizon,
            "source_attempt_horizon": pre["source_horizon"],
            "account_ref": pre["account_ref"],
            "entry_scope": pre["entry_scope"],
            "client_order_id": pre["client_order_id"],
            "attempt_origin": self._attempt_origin,
            "request_body": pre["request_body"],
            "request_sha256": _digest(pre["request_body"].encode()),
            "scope_sha256": _digest(pre["scope_payload"].encode()),
            "precall_recorded_at": pre["recorded_at"],
            "outcome_kind": None if out is None else out["result_kind"],
            "outcome_status": None if out is None else out["http_status"],
            "outcome_body_base64": None if out is None else out["body_base64"],
            "outcome_completed_at": None if out is None else out["completed_at"],
            "decision_at": decision.isoformat(),
            "max_body_bytes": max_body_bytes,
            "max_fill_pages": max_fill_pages,
            "page_size": page_size,
        }

    def _capture(
        self,
        *,
        attempt_ref: dict[str, Any],
        decision_at: datetime,
        get: Callable[[str], SourceCloseGetResponse],
        clock: Callable[[], datetime],
        max_body_bytes: int,
        max_fill_pages: int,
        page_size: int,
        before_read: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if self.journal._db.in_transaction or not callable(get) or not callable(clock):
            raise JournalError(
                "explicit injected GET and clock outside transaction required"
            )
        self.journal.require_private_order_state()
        decision, started = _utc(decision_at), _utc(clock())
        if started < decision:
            raise JournalError("source GET clock regressed")
        horizon = self.journal.read_evidence_sequence()
        rows = [
            r
            for r in self.attempts.read_verified_at(horizon, decision)
            if r["attempt_ref"] == attempt_ref
        ]
        if len(rows) != 1:
            raise JournalError("verified source attempt reference required")
        pin = self._pin(
            attempt_ref,
            rows[0]["outcome_ref"],
            horizon,
            decision,
            max_body_bytes,
            max_fill_pages,
            page_size,
        )
        previous = started
        requests: list[dict[str, Any]] = []

        def read(path: str) -> dict[str, Any]:
            nonlocal previous
            began = _utc(clock())
            if began < previous:
                raise JournalError("source GET clock rewound")
            status = encoded = error = size = None
            if before_read is not None:
                before_read()
            try:
                response = get(path)
                if (
                    type(response) is not SourceCloseGetResponse
                    or type(response.status) is not int
                    or not 100 <= response.status <= 599
                    or type(response.body) is not bytes
                ):
                    error = "INVALID_CALLBACK_RESPONSE"
                else:
                    status, size = response.status, len(response.body)
                    encoded = base64.b64encode(response.body[:max_body_bytes]).decode()
                    if size > max_body_bytes:
                        error = "BODY_LIMIT_EXCEEDED_PREFIX_ONLY"
            except Exception:
                error = "CALLBACK_EXCEPTION"
            if before_read is not None:
                before_read()
            completed = _utc(clock())
            if completed < began:
                raise JournalError("source GET clock rewound")
            previous = completed
            row = {
                "method": "GET",
                "path": path,
                "status": status,
                "body_base64": encoded,
                "body_size": size,
                "error": error,
                "started_at": began.isoformat(),
                "completed_at": completed.isoformat(),
                "available_at": completed.isoformat(),
            }
            requests.append(row)
            return row

        assessment = self._walk(pin, read)
        completed = _utc(clock())
        if completed < previous:
            raise JournalError("source GET completion clock rewound")
        # A callback cannot replace canonical provenance while this collector runs.
        if (
            self._pin(
                attempt_ref,
                pin["outcome_ref"],
                horizon,
                decision,
                max_body_bytes,
                max_fill_pages,
                page_size,
            )
            != pin
        ):
            raise JournalError("source GET provenance changed")
        payload = {
            "schema": 1,
            "origin": self._origin,
            "authority": dict(AUTHORITY),
            "pin": pin,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "requests": requests,
            "assessment": assessment,
        }
        event = self.journal.append_next(
            source_id=self._source,
            feed="paper",
            event_kind=self._kind,
            payload=_json(payload),
            system_first_seen_at=started,
            processed_at=completed,
        )
        return {
            "capture_ref": {"sequence": event.sequence, "hash": event.payload_ref},
            **_copy(payload),
        }

    def view(self) -> list[dict[str, Any]]:
        return self._view(None, None)

    def read_verified_at(
        self,
        horizon: int,
        decision_at: datetime,
    ) -> list[dict[str, Any]]:
        if (
            type(horizon) is not int
            or not 0 <= horizon <= self.journal.read_evidence_sequence()
        ):
            raise JournalError("bounded capture evidence horizon required")
        return self._view(horizon, _utc(decision_at))

    def _view(
        self,
        horizon: int | None,
        decision_at: datetime | None,
    ) -> list[dict[str, Any]]:
        result = []
        for event, raw in self.journal._read_private_events(self._source, horizon):
            if decision_at is not None and not event.visible_at(decision_at):
                continue
            try:
                item = decode_broker_json(raw)
                if type(item) is not dict:
                    raise ValueError
                pin = item["pin"]
                started, completed = (
                    _clock(item["started_at"]),
                    _clock(item["completed_at"]),
                )
                if (
                    set(item)
                    != {
                        "schema",
                        "origin",
                        "authority",
                        "pin",
                        "started_at",
                        "completed_at",
                        "requests",
                        "assessment",
                    }
                    or type(item["schema"]) is not int
                    or item["schema"] != 1
                    or item["origin"] != self._origin
                    or item["authority"] != AUTHORITY
                    or any(v is not False for v in item["authority"].values())
                    or _json(item) != raw
                    or event.payload_ref != _digest(raw)
                    or event.feed != "paper"
                    or event.event_kind != self._kind
                    or event.source_event_at is not None
                    or event.provider_received_at is not None
                    or event.system_first_seen_at != started
                    or event.processed_at != completed
                    or event.available_at != completed
                    or completed < started
                    or not pin["source_horizon"] < event.sequence
                    or started < _clock(pin["decision_at"])
                    or self._pin(
                        pin["attempt_ref"],
                        pin["outcome_ref"],
                        pin["source_horizon"],
                        _clock(pin["decision_at"]),
                        pin["max_body_bytes"],
                        pin["max_fill_pages"],
                        pin["page_size"],
                    )
                    != pin
                    or type(item["requests"]) is not list
                ):
                    raise ValueError
                iterator = [iter(item["requests"])]
                previous = started

                def read(
                    path: str,
                    *,
                    pin: dict[str, Any] = pin,
                    completed: datetime = completed,
                    cursors: list[Any] = iterator,
                ) -> dict[str, Any]:
                    nonlocal previous
                    row = next(cursors[0])
                    began, ended = (
                        _clock(row["started_at"]),
                        _clock(row["completed_at"]),
                    )
                    if (
                        set(row)
                        != {
                            "method",
                            "path",
                            "status",
                            "body_base64",
                            "body_size",
                            "error",
                            "started_at",
                            "completed_at",
                            "available_at",
                        }
                        or row["method"] != "GET"
                        or row["path"] != path
                        or not previous <= began <= ended <= completed
                        or row["available_at"] != row["completed_at"]
                    ):
                        raise ValueError
                    previous = ended
                    if row["error"] in (
                        "CALLBACK_EXCEPTION",
                        "INVALID_CALLBACK_RESPONSE",
                    ):
                        if any(
                            row[k] is not None
                            for k in ("status", "body_base64", "body_size")
                        ):
                            raise ValueError
                    else:
                        body = base64.b64decode(row["body_base64"], validate=True)
                        if (
                            type(row["status"]) is not int
                            or not 100 <= row["status"] <= 599
                            or base64.b64encode(body).decode() != row["body_base64"]
                            or type(row["body_size"]) is not int
                            or row["body_size"] < 0
                            or len(body) > pin["max_body_bytes"]
                            or (row["error"] is None and len(body) != row["body_size"])
                            or (
                                row["error"] == "BODY_LIMIT_EXCEEDED_PREFIX_ONLY"
                                and not (
                                    row["body_size"]
                                    > pin["max_body_bytes"]
                                    == len(body)
                                )
                            )
                            or row["error"]
                            not in (None, "BODY_LIMIT_EXCEEDED_PREFIX_ONLY")
                        ):
                            raise ValueError
                    return cast(dict[str, Any], row)

                # Validate every envelope outside the semantic diagnostic catcher.
                # Otherwise malformed metadata could be downgraded into a GET error.
                for envelope in item["requests"]:
                    read(envelope["path"])
                iterator[0] = iter(item["requests"])
                previous = started
                rebuilt = self._walk(pin, read)
                if next(iterator[0], None) is not None or _json(rebuilt) != _json(
                    item["assessment"]
                ):
                    raise ValueError
                result.append(
                    {
                        "capture_ref": {
                            "sequence": event.sequence,
                            "hash": event.payload_ref,
                        },
                        **item,
                    }
                )
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                StopIteration,
            ) as exc:
                raise JournalError("source GET capture provenance malformed") from exc
        return cast(list[dict[str, Any]], _copy(result))


class SourceCloseCaptures(_SourceCloseCaptureCore):
    """Append injected callback observations; preserve their original profile."""

    def __init__(self, attempts: SourceCloseAttempts) -> None:
        if type(attempts) is not SourceCloseAttempts:
            raise JournalError("canonical source attempts required")
        super().__init__(attempts)

    def capture(
        self,
        *,
        attempt_ref: dict[str, Any],
        decision_at: datetime,
        get: Callable[[str], SourceCloseGetResponse],
        clock: Callable[[], datetime],
        max_body_bytes: int,
        max_fill_pages: int,
        page_size: int,
    ) -> dict[str, Any]:
        return self._capture(
            attempt_ref=attempt_ref,
            decision_at=decision_at,
            get=get,
            clock=clock,
            max_body_bytes=max_body_bytes,
            max_fill_pages=max_fill_pages,
            page_size=page_size,
        )


def read_source_close_captures_at(
    journal: Journal,
    initial: OcoResearchState,
    *,
    through_sequence: int,
    decision_at: datetime,
) -> list[dict[str, Any]]:
    """Return verified packets using only a local constructor-free read handle."""
    reader = object.__new__(SourceCloseCaptures)
    reader.attempts = _source_attempt_reader(journal, initial)
    reader.journal = journal
    return reader.read_verified_at(through_sequence, decision_at)
