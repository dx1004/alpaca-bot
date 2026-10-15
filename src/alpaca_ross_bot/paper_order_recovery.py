"""Bounded GET-only recovery capture; observations never grant order authority."""

import asyncio
import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.order_lifecycle import (
    OrderObservation,
    parse_order_observation,
    timestamp_ns,
)
from alpaca_ross_bot.order_reconciliation import ReconciliationPolicy, RestBatch
from alpaca_ross_bot.paper_order_contract import PaperContractError, decode_broker_json
from alpaca_ross_bot.paper_order_http import (
    PaperHttpResponse,
    PaperOrderAuthenticationError,
    PaperOrderHttp,
    PaperOrderHttpError,
)


class RecoveryContext(Protocol):
    @property
    def journal(self) -> Journal: ...

    @property
    def account_ref(self) -> str: ...

    def view(self) -> dict[str, object]: ...


def collect_rest_batch(
    api: PaperOrderHttp,
    reconciler: RecoveryContext,
    policy: ReconciliationPolicy,
    *,
    clock: Callable[[], datetime],
) -> RestBatch:
    """Capture two bounded account views, retaining incomplete response evidence.

    Account bodies remain in canonical paper_account_verifications. Every
    explicit page, position, and targeted lookup response is retained here.
    Storage and authentication failures propagate without being reclassified.
    """
    return _Collector(api, reconciler, policy, clock).collect()


async def collect_rest_batch_async(
    api: PaperOrderHttp,
    runtime_root: Path,
    account_ref: str,
    policy: ReconciliationPolicy,
    *,
    clock: Callable[[], datetime],
) -> RestBatch:
    """Leave stream callbacks responsive while a worker owns its SQLite handle.

    The caller must not use ``api`` concurrently during capture. Cancellation
    does not interrupt a blocking HTTP request; the worker still closes its own
    journal in ``finally`` when collection ends.
    """
    return await asyncio.to_thread(
        _collect_existing, api, runtime_root, account_ref, policy, clock
    )


@dataclass(frozen=True)
class _JournalRecoveryContext:
    journal: Journal
    account_ref: str

    def view(self) -> dict[str, object]:
        if self.journal.read_paper_account_ref() != self.account_ref:
            raise JournalError("recovery account is not bound to this journal")
        state = self.journal.read_state("order_lifecycle:" + self.account_ref)
        if (
            not isinstance(state, dict)
            or state.get("schema") != 1
            or state.get("account_ref") != self.account_ref
            or type(state.get("watermark")) is not int
            or state["watermark"] < 0
        ):
            raise JournalError("existing recovery lifecycle state is invalid")
        return state


def _collect_existing(
    api: PaperOrderHttp,
    runtime_root: Path,
    account_ref: str,
    policy: ReconciliationPolicy,
    clock: Callable[[], datetime],
) -> RestBatch:
    if not isinstance(account_ref, str) or not account_ref.strip():
        raise JournalError("recovery account reference is missing")
    root = runtime_root.resolve()
    path = root / "journal.sqlite3"
    if not path.is_file():
        raise JournalError("recovery requires an existing private journal")
    journal = Journal.open_private(path, root)
    try:
        return collect_rest_batch(
            api, _JournalRecoveryContext(journal, account_ref), policy, clock=clock
        )
    finally:
        journal.close()


class _Collector:
    def __init__(
        self,
        api: PaperOrderHttp,
        reconciler: RecoveryContext,
        policy: ReconciliationPolicy,
        clock: Callable[[], datetime],
    ) -> None:
        self.api = api
        self.reconciler = reconciler
        self.policy = policy
        self.clock = clock
        reconciler.journal.require_private_order_state()
        revision = reconciler.view()["watermark"]
        if type(revision) is not int:
            raise ValueError("lifecycle revision is invalid")
        self.revision = revision
        self.started = self.now()
        self.issues: set[str] = set()
        self.captures: list[dict[str, object]] = []
        self.sent = self.sent_intents()
        self.requests: dict[str, str] = {}
        for cid in self.sent:
            intent = reconciler.journal.read_order_intent(cid)
            if isinstance(intent, dict) and isinstance(intent.get("request_body"), str):
                self.requests[cid] = intent["request_body"]
            else:
                self.issues.add("LOCAL_INTENT_INVALID:" + cid)

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("recovery clock must return an aware UTC timestamp")
        return value

    def sent_intents(self) -> tuple[str, ...]:
        journal = self.reconciler.journal
        return tuple(
            cid
            for cid in journal.read_order_intent_ids()
            if journal.has_order_send_attempt(cid)
        )

    def verify_account(self) -> bool:
        try:
            reference = self.api.verify_account(
                self.reconciler.journal,
                expected_reference=self.reconciler.account_ref,
            )
        except PaperOrderAuthenticationError:
            raise
        except PaperOrderHttpError:
            self.issues.add("ACCOUNT_VERIFICATION_FAILED")
            self.captures.append(
                {
                    "path": "/v2/account",
                    "observed_at": self.now().isoformat(),
                    "error": "ACCOUNT_VERIFICATION_FAILED",
                }
            )
            return False
        self.captures.append(
            {
                "path": "/v2/account",
                "account_ref": reference,
                "observed_at": self.now().isoformat(),
                "evidence": "paper_account_verifications",
            }
        )
        if reference != self.reconciler.account_ref:
            self.issues.add("ACCOUNT_VERIFICATION_FAILED")
            return False
        return True

    def read(
        self, label: str, path: str, request: Callable[[], PaperHttpResponse]
    ) -> PaperHttpResponse | None:
        try:
            response = request()
        except PaperOrderAuthenticationError:
            raise
        except PaperOrderHttpError:
            self.captures.append(
                {
                    "path": path,
                    "observed_at": self.now().isoformat(),
                    "status": None,
                    "body_base64": None,
                    "error": "REQUEST_FAILED",
                }
            )
            self.issues.add("REQUEST_FAILED:" + label)
            return None
        self.captures.append(
            {
                "path": path,
                "observed_at": self.now().isoformat(),
                "status": response.status,
                "body_base64": base64.b64encode(response.body).decode("ascii"),
            }
        )
        if response.status in (401, 403):
            raise PaperOrderAuthenticationError("paper recovery authentication failed")
        if response.status != 200:
            self.issues.add("HTTP_STATUS:" + label + ":" + str(response.status))
        return response

    def rows(
        self, response: PaperHttpResponse | None, label: str
    ) -> tuple[dict[str, object], ...] | None:
        if response is None or response.status != 200:
            return None
        try:
            decoded = decode_broker_json(response.body)
        except PaperContractError:
            self.issues.add("INVALID_JSON:" + label)
            return None
        if not isinstance(decoded, list) or any(
            not isinstance(item, dict) for item in decoded
        ):
            self.issues.add("INVALID_LIST:" + label)
            return None
        return tuple(decoded)

    def paged(
        self,
        label: str,
        size: int,
        path: Callable[[str | None], str],
        request: Callable[[str | None], PaperHttpResponse],
        *,
        order_ids: bool = False,
    ) -> tuple[dict[str, object], ...] | None:
        cursor: str | None = None
        used_cursors: set[str] = set()
        identities: set[str] = set()
        result: list[dict[str, object]] = []
        for _ in range(self.policy.max_pages):
            page = self.rows(
                self.read(label, path(cursor), partial(request, cursor)), label
            )
            if page is None:
                return tuple(result) if result else None
            if len(page) > size:
                self.issues.add("OVERSIZED_PAGE:" + label)
                return tuple(result + list(page))
            if len(page) == size:
                try:
                    last = (
                        _uuid(page[-1].get("id"))
                        if order_ids
                        else _text(page[-1].get("id"))
                    )
                    if last in used_cursors:
                        self.issues.add("CURSOR_LOOP:" + label)
                except (ValueError, TypeError, AttributeError):
                    self.issues.add("INVALID_PAGE_ID:" + label)
                    return tuple(result + list(page))
            for row in page:
                try:
                    identity = (
                        _uuid(row.get("id")) if order_ids else _text(row.get("id"))
                    )
                except (ValueError, TypeError, AttributeError):
                    self.issues.add("INVALID_PAGE_ID:" + label)
                    return tuple(result + list(page))
                if identity in identities:
                    self.issues.add("DUPLICATE_PAGE_ID:" + label)
                    return tuple(result + list(page))
                identities.add(identity)
            result.extend(page)
            if len(page) < size:
                return tuple(result)
            identity = _uuid(page[-1]["id"]) if order_ids else _text(page[-1]["id"])
            if identity in used_cursors:
                self.issues.add("CURSOR_LOOP:" + label)
                return tuple(result)
            used_cursors.add(identity)
            cursor = identity
        self.issues.add("PAGE_LIMIT_REACHED:" + label)
        return tuple(result)

    def open_orders(self, label: str) -> tuple[dict[str, object], ...] | None:
        return self.paged(
            label,
            500,
            _open_path,
            lambda cursor: self.api.read_open_orders_page(after_order_id=cursor),
            order_ids=True,
        )

    def positions(self, label: str) -> tuple[dict[str, object], ...] | None:
        return self.rows(
            self.read(label, "/v2/positions", self.api.read_positions), label
        )

    def lookups(
        self, label: str
    ) -> tuple[dict[str, PaperHttpResponse], dict[str, OrderObservation]]:
        responses: dict[str, PaperHttpResponse] = {}
        observations: dict[str, OrderObservation] = {}
        for cid in self.sent:
            path = "/v2/orders:by_client_order_id?" + urlencode(
                {"client_order_id": cid}
            )
            response = self.read(
                label + ":" + cid,
                path,
                partial(self.api.get_order_by_client_id, self.reconciler.journal, cid),
            )
            if response is None:
                continue
            responses[cid] = response
            if response.status != 200 or cid not in self.requests:
                continue
            try:
                observation = parse_order_observation(
                    self.requests[cid],
                    response.body,
                    self.reconciler.account_ref,
                    "REST",
                    "sha256:" + hashlib.sha256(response.body).hexdigest(),
                )
                if observation.snapshot.client_order_id != cid:
                    raise ValueError
                observations[cid] = observation
            except (ValueError, TypeError, KeyError):
                self.issues.add("LOOKUP_INVALID:" + cid)
        return responses, observations

    def activities(
        self, observations: dict[str, OrderObservation]
    ) -> dict[str, tuple[dict[str, object], ...]]:
        result: dict[str, tuple[dict[str, object], ...]] = {}
        for observation in observations.values():
            identity = observation.snapshot.broker_order_id
            if identity in result:
                self.issues.add("OWNED_BROKER_ID_REUSED:" + identity)
                continue
            label = "FILL_ACTIVITIES:" + identity
            rows = self.paged(
                label,
                100,
                partial(_activity_path, identity),
                partial(self.fill_page, identity),
            )
            if rows is None:
                continue
            result[identity] = rows
            for row in rows:
                try:
                    if (
                        row.get("activity_type") != "FILL"
                        or _uuid(row.get("order_id")) != identity
                        or row.get("symbol") != observation.symbol
                        or row.get("side") != observation.side
                        or _number(row.get("qty")) <= 0
                        or _number(row.get("price")) <= 0
                    ):
                        raise ValueError
                    timestamp_ns(_text(row.get("transaction_time")))
                except (ValueError, TypeError, AttributeError, InvalidOperation):
                    self.issues.add("FILL_ACTIVITY_INVALID:" + identity)
        return result

    def fill_page(self, identity: str, cursor: str | None) -> PaperHttpResponse:
        return self.api.read_fill_activities_page(identity, page_token=cursor)

    def compare(
        self,
        first: tuple[dict[str, object], ...] | None,
        second: tuple[dict[str, object], ...] | None,
        label: str,
        signature: Callable[[dict[str, object]], tuple[object, ...]],
        key: str,
    ) -> None:
        if first is None or second is None:
            self.issues.add("COMPARISON_INCOMPLETE:" + label)
            return
        try:

            def normalize(
                rows: tuple[dict[str, object], ...],
            ) -> dict[str, tuple[object, ...]]:
                result = {}
                for row in rows:
                    identity = _text(row.get(key))
                    if identity in result:
                        raise ValueError
                    result[identity] = signature(row)
                return result

            if normalize(first) != normalize(second):
                self.issues.add("SNAPSHOT_CHANGED:" + label)
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
            self.issues.add("SNAPSHOT_INVALID:" + label)

    def collect(self) -> RestBatch:
        if not self.verify_account():
            return self.finish((), (), {}, {})
        first_open = self.open_orders("OPEN_ORDERS_FIRST")
        first_positions = self.positions("POSITIONS_FIRST")
        first_lookups, observations = self.lookups("LOOKUPS_FIRST")
        activities = self.activities(observations)
        second_open = self.open_orders("OPEN_ORDERS_SECOND")
        second_positions = self.positions("POSITIONS_SECOND")
        second_lookups, second_observations = self.lookups("LOOKUPS_SECOND")
        self.verify_account()
        self.compare(first_open, second_open, "OPEN_ORDERS", _order_signature, "id")
        self.compare(
            first_positions,
            second_positions,
            "POSITIONS",
            _position_signature,
            "symbol",
        )
        for cid in self.sent:
            first = first_lookups.get(cid)
            second = second_lookups.get(cid)
            if cid not in observations or cid not in second_observations:
                self.issues.add("LOOKUP_COMPARISON_INCOMPLETE:" + cid)
                continue
            assert first is not None and second is not None
            try:
                left, right = (
                    decode_broker_json(first.body),
                    decode_broker_json(second.body),
                )
                assert isinstance(left, dict) and isinstance(right, dict)
                if _order_signature(left) != _order_signature(right):
                    self.issues.add("LOOKUP_CHANGED:" + cid)
            except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
                self.issues.add("LOOKUP_INVALID:" + cid)
        return self.finish(
            second_open if second_open is not None else first_open or (),
            second_positions if second_positions is not None else first_positions or (),
            first_lookups | second_lookups,
            activities,
        )

    def finish(
        self,
        orders: tuple[dict[str, object], ...],
        positions: tuple[dict[str, object], ...],
        lookups: dict[str, PaperHttpResponse],
        activities: dict[str, tuple[dict[str, object], ...]],
    ) -> RestBatch:
        completed = self.now()
        if (
            completed < self.started
            or completed - self.started > self.policy.max_capture_span
        ):
            self.issues.add("CAPTURE_CLOCK_OR_SPAN")
        if self.reconciler.view()["watermark"] != self.revision:
            self.issues.add("LOCAL_REVISION_CHANGED")
        if self.sent_intents() != self.sent:
            self.issues.add("LOCAL_SENT_INTENTS_CHANGED")
        return RestBatch(
            account_ref=self.reconciler.account_ref,
            started_at=self.started,
            completed_at=completed,
            local_revision=self.revision,
            open_orders=orders,
            positions=positions,
            lookups=lookups,
            activities=activities,
            complete=not self.issues,
            stable=not self.issues,
            issues=tuple(sorted(self.issues)),
            captures=tuple(self.captures),
        )


def _open_path(cursor: str | None) -> str:
    query = [
        ("status", "open"),
        ("limit", "500"),
        ("nested", "false"),
        ("direction", "asc"),
    ]
    if cursor is not None:
        query.append(("after_order_id", cursor))
    return "/v2/orders?" + urlencode(query)


def _activity_path(identity: str, cursor: str | None) -> str:
    query = [
        ("activity_types", "FILL"),
        ("order_id", identity),
        ("direction", "asc"),
        ("page_size", "100"),
    ]
    if cursor is not None:
        query.append(("page_token", cursor))
    return "/v2/account/activities?" + urlencode(query)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("response field must be nonempty text")
    return value


def _uuid(value: object) -> str:
    return str(UUID(_text(value)))


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("response quantity is invalid")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("response quantity is not finite")
    return number


def _order_signature(row: dict[str, object]) -> tuple[object, ...]:
    quantity = _number(row.get("qty"))
    filled = _number(row.get("filled_qty"))
    if (
        quantity <= 0
        or not 0 <= filled <= quantity
        or row.get("side") not in ("buy", "sell")
    ):
        raise ValueError("order shape is invalid")
    prices = tuple(
        None if row.get(key) is None else _number(row[key])
        for key in (
            "limit_price",
            "stop_price",
            "filled_avg_price",
            "trail_price",
            "trail_percent",
        )
    )
    if any(price is not None and price <= 0 for price in prices):
        raise ValueError("order price is invalid")
    return (
        _uuid(row.get("id")),
        _text(row.get("client_order_id")),
        _text(row.get("symbol")),
        row["side"],
        _text(row.get("status")),
        quantity,
        filled,
        prices,
        timestamp_ns(_text(row.get("updated_at"))),
        row.get("type"),
        row.get("time_in_force"),
        row.get("order_class"),
        row.get("asset_class"),
        row.get("extended_hours"),
        row.get("replaces"),
        row.get("replaced_by"),
    )


def _position_signature(row: dict[str, object]) -> tuple[object, ...]:
    quantity = _number(row.get("qty"))
    if (
        not quantity
        or row.get("side") not in ("long", "short")
        or row.get("asset_class") != "us_equity"
    ):
        raise ValueError("position shape is invalid")
    return _text(row.get("symbol")), row["side"], quantity, row["asset_class"]
