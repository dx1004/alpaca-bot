"""Private raw account evidence inspection; never a spendable-funds grant."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

from .journal import Journal
from .paper_order_contract import decode_broker_json, paper_account_ref

SCHEMA = "PAPER_RISK_INPUT_CAPTURE_V1"
SCHEMA_V2 = "PAPER_RISK_INPUT_CAPTURE_V2"
SOURCE = "ALPACA_PAPER_RISK_HTTP"
KIND = "ACCOUNT_INPUT_CAPTURE"
ENDPOINT = "https://paper-api.alpaca.markets"
MONEY_FIELDS = (
    "equity",
    "last_equity",
    "cash",
    "buying_power",
    "non_marginable_buying_power",
    "regt_buying_power",
    "daytrading_buying_power",
    "accrued_fees",
    "pending_transfer_in",
    "pending_transfer_out",
    "unsettled_funds",
)


@dataclass(frozen=True, repr=False)
class AccountInputFacts:
    account_ref: str
    evidence_ref: str
    capture_sequence: int
    observed_at: datetime
    money: tuple[tuple[str, Decimal | None], ...]
    active_usd_flags_clear: bool
    before_session_open: bool
    collection_counts: tuple[tuple[str, int], ...]
    reasons: tuple[str, ...]
    available_funds: Decimal | None = None
    available_profile: str = "UNQUALIFIED"
    settlement_status: str = "UNKNOWN"
    runtime_authorized: bool = False


def _time(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError("capture timestamp must be UTC text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("capture timestamp must be UTC")
    return parsed


def _money(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("account money must be exact decimal text")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("account money is invalid") from exc
    if not number.is_finite():
        raise ValueError("account money is not finite")
    if field != "cash" and number < 0:
        raise ValueError("account money is unexpectedly negative")
    return number


def inspect_account_inputs(
    journal: Journal,
    capture_sequence: int,
    *,
    account_ref: str,
    decision_at: datetime,
    session_open: datetime,
    max_age: timedelta,
) -> AccountInputFacts:
    """Read a fixed immutable event, preserving missing values as unknown.

    A caller cannot supply parsed account facts or completeness booleans. HTTP
    metadata and response bytes must exist together in the private journal.
    This proves capture provenance, not bank settlement, fee completeness,
    strategy equity baseline, reservation ownership or runtime eligibility.
    """
    journal.require_private_order_state()
    if journal.read_paper_account_ref() != account_ref:
        raise ValueError("account is not bound to the journal")
    if type(capture_sequence) is not int or capture_sequence < 1:
        raise ValueError("capture sequence is invalid")
    for value in (decision_at, session_open):
        if type(value) is not datetime or value.utcoffset() != timedelta(0):
            raise ValueError("UTC decision and session times required")
    if type(max_age) is not timedelta or max_age <= timedelta(0):
        raise ValueError("positive capture age required")
    stored = journal.read_event(capture_sequence)
    if stored is None:
        raise ValueError("capture evidence is missing")
    event, raw = stored
    if event.payload_ref != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise ValueError("account capture payload hash differs")
    if (event.source_id, event.feed, event.event_kind) != (SOURCE, "paper", KIND):
        raise ValueError("capture source is not qualified")
    if not event.visible_at(decision_at) or event.external_clock_conflict:
        raise ValueError("capture evidence is unavailable at decision time")
    capture = decode_broker_json(raw)
    fields = {
        "schema",
        "account_ref",
        "endpoint",
        "requests",
    }
    if not isinstance(capture, dict):
        raise ValueError("capture envelope is invalid")
    schema = capture.get("schema")
    if type(schema) is not str or schema not in {SCHEMA, SCHEMA_V2}:
        raise ValueError("capture schema is unsupported")
    if schema == SCHEMA_V2:
        fields.add("transport_origin")
        origin = capture.get("transport_origin")
        if type(origin) is not str or origin not in {
            "BUILTIN_PAPER_HTTPS",
            "INJECTED_EXCHANGE",
        }:
            raise ValueError("account capture transport origin is invalid")
    if set(capture) != fields:
        raise ValueError("capture envelope is invalid")
    if (capture["account_ref"], capture["endpoint"]) != (
        account_ref,
        ENDPOINT,
    ):
        raise ValueError("capture account or paper endpoint mismatch")
    requests = capture["requests"]
    if not isinstance(requests, list) or not requests:
        raise ValueError("capture requests are missing")
    accounts: list[dict[str, object]] = []
    counts: list[tuple[str, int]] = []
    starts: list[datetime] = []
    ends: list[datetime] = []
    for request in requests:
        if not isinstance(request, dict) or set(request) != {
            "method",
            "path",
            "status",
            "started_at",
            "completed_at",
            "body",
        }:
            raise ValueError("HTTP capture shape is invalid")
        path = request["path"]
        if type(path) is not str:
            raise ValueError("HTTP path is invalid")
        parsed = urlsplit(path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or not path.startswith("/v2/")
        ):
            raise ValueError("HTTP capture path is not local paper API")
        if (
            request["method"] != "GET"
            or type(request["status"]) is not int
            or request["status"] != 200
        ):
            raise ValueError("HTTP capture is not a successful read")
        start, end = _time(request["started_at"]), _time(request["completed_at"])
        if not start <= end <= event.system_first_seen_at or start > decision_at:
            raise ValueError("HTTP capture chronology is invalid")
        if starts and start < ends[-1]:
            raise ValueError("HTTP captures overlap or are out of order")
        if decision_at - start > max_age:
            raise ValueError("HTTP capture is stale")
        body = request["body"]
        if type(body) is not str:
            raise ValueError("raw HTTP body is missing")
        decoded = decode_broker_json(body)
        starts.append(start)
        ends.append(end)
        if path == "/v2/account":
            if paper_account_ref(body.encode()) != account_ref or not isinstance(
                decoded, dict
            ):
                raise ValueError("raw account identity differs")
            accounts.append(decoded)
        elif parsed.path in ("/v2/orders", "/v2/positions", "/v2/account/activities"):
            if not isinstance(decoded, list) or not all(
                isinstance(row, dict) for row in decoded
            ):
                raise ValueError("account collection shape is invalid")
            counts.append((path, len(decoded)))
        elif path == "/v2/account/configurations":
            if not isinstance(decoded, dict):
                raise ValueError("account configuration shape is invalid")
        else:
            raise ValueError("HTTP path is outside account risk capture")
    if len(accounts) != 1:
        raise ValueError("exactly one raw account snapshot required")
    account = accounts[0]
    amounts = tuple(
        (field, _money(account.get(field), field)) for field in MONEY_FIELDS
    )
    clear = account.get("currency") == "USD" and account.get("status") == "ACTIVE"
    clear = clear and all(
        account.get(field) is False
        for field in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
    )
    reasons = [
        "CASHFLOW_COMPLETENESS_UNVERIFIED",
        "CHARGES_COMPLETENESS_UNVERIFIED",
        "RESERVATIONS_UNVERIFIED",
        "BID_MARKS_UNVERIFIED",
        "PAGINATION_COMPLETENESS_UNVERIFIED",
    ]
    reasons.extend(
        "MISSING_" + field.upper()
        for field, value in amounts
        if value is None and field != "unsettled_funds"
    )
    if not clear:
        reasons.append("ACCOUNT_FLAGS_UNQUALIFIED")
    before_open = event.available_at < session_open
    reasons.append(
        "PRESESSION_BASELINE_UNACCEPTED" if before_open else "NO_PRESESSION_BASELINE"
    )
    if any(
        value is not None and value != 0
        for field, value in amounts
        if field.startswith("pending_") or field == "unsettled_funds"
    ):
        reasons.append("PENDING_TRANSFER_OR_UNSETTLED_FUNDS")
    if any(value is not None and value < 0 for _, value in amounts):
        reasons.append("NEGATIVE_CASH")
    money = dict(amounts)
    position_views = [
        (path, count)
        for path, count in counts
        if urlsplit(path).path == "/v2/positions"
    ]
    empty_positions = position_views == [("/v2/positions", 0)]
    empty_open_orders = False
    for path, count in counts:
        parsed = urlsplit(path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.path == "/v2/orders"
            and count == 0
            and query.get("status") == ["open"]
            and query.get("limit") == ["500"]
            and query.get("nested") == ["false"]
            and set(query) <= {"status", "limit", "nested", "direction"}
            and query.get("direction", ["asc"]) == ["asc"]
        ):
            empty_open_orders = True
    empty_open_orders = (
        empty_open_orders
        and sum(urlsplit(path).path == "/v2/orders" for path, _ in counts) == 1
    )
    # Source-scoped limited-margin multiplier=1 current availability, NOT
    # settled cash. Restrict to a fresh flat, unused journal observation so
    # no local hold is subtracted twice or silently assumed reconciled.
    cash = money["cash"]
    equal_funds = (
        cash is not None
        and cash >= 0
        and all(
            money[field] == cash
            for field in ("equity", "buying_power", "non_marginable_buying_power")
        )
    )
    profile_ok = (
        clear
        and account.get("multiplier") == "1"
        and equal_funds
        and empty_positions
        and empty_open_orders
        and not journal.read_order_intent_ids()
        and all(
            money[field] == 0
            for field in ("pending_transfer_in", "pending_transfer_out", "accrued_fees")
        )
    )
    if not profile_ok:
        reasons.append("ONE_X_FLAT_AVAILABLE_PROFILE_UNQUALIFIED")
    return AccountInputFacts(
        account_ref,
        event.payload_ref,
        capture_sequence,
        max(ends).astimezone(UTC),
        amounts,
        clear,
        before_open,
        tuple(counts),
        tuple(reasons),
        cash if profile_ok else None,
        "ONE_X_FLAT_CURRENT_AVAILABLE_NOT_SETTLED" if profile_ok else "UNQUALIFIED",
    )
