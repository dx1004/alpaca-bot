"""Raw-bound IEX observations; measured clocks never grant strategy authority."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from alpaca_ross_bot.journal import Journal

_STAMP = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _datetime_ns(value: datetime) -> int:
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("decision time must be UTC")
    delta = value - _EPOCH
    return (
        delta.days * 86400 + delta.seconds
    ) * 1_000_000_000 + delta.microseconds * 1000


def _timestamp_ns(value: object) -> int:
    if not isinstance(value, str) or (match := _STAMP.fullmatch(value)) is None:
        raise ValueError("timestamp must be RFC3339 UTC with at most nine decimals")
    base = datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    return _datetime_ns(base) + int((match[2] or "").ljust(9, "0"))


def _positive(value: object) -> Decimal:
    if not isinstance(value, (Decimal, int, str)) or isinstance(value, bool):
        raise ValueError("positive exact number required")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid number") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("positive finite number required")
    return number


def _identity(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _condition_identity(value: object) -> bool:
    # Alpaca trade metadata tapes A/B define U+0020 as "Regular Sale".
    # Preserve that exact code; do not trim or generalize whitespace identifiers.
    return value == " " or _identity(value)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous duplicate raw JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class MarketInputAssessment:
    sequence: int
    record_index: int
    payload_ref: str
    kind: str | None
    symbol: str | None
    schema_valid: bool
    measured_fresh: bool
    event_age_ns: int | None
    receipt_age_ns: int
    conditions: tuple[tuple[str, str | None], ...]
    revision_observed: bool
    reasons: tuple[str, ...]

    @property
    def runtime_eligible(self) -> bool:
        """No accepted condition policy or provider clock qualification exists here."""
        return False

    @property
    def coverage_label(self) -> str:
        return "NOT_SIP_EQUIVALENT"


def assess_market_record(
    journal: Journal,
    *,
    sequence: int,
    record_index: int,
    payload_ref: str,
    decision_at: datetime,
    condition_names: Mapping[str, str] | None = None,
) -> MarketInputAssessment:
    """Inspect exactly one persisted raw-list element, with no network or writes.

    The one-second age bound is research policy, not an Alpaca latency promise.
    Condition names are descriptive observations, never a condition whitelist.
    Corrections, cancel/errors and updated bars are explicit revision evidence;
    they cannot masquerade as an unrevised current trade or quote.
    """
    decision_ns = _datetime_ns(decision_at)
    if type(sequence) is not int or sequence < 1:
        raise ValueError("positive journal sequence required")
    if type(record_index) is not int or record_index < 0:
        raise ValueError("nonnegative raw record index required")
    stored = journal.read_event(sequence)
    if stored is None:
        raise ValueError("raw event missing")
    event, raw = stored
    if (event.source_id, event.feed, event.event_kind) != (
        "alpaca_market_data",
        "iex",
        "RAW_FRAME",
    ) or event.payload_ref != payload_ref:
        raise ValueError("raw evidence identity mismatch")
    if not event.visible_at(decision_at):
        raise ValueError("raw evidence unavailable at decision")
    records = json.loads(
        raw, parse_float=Decimal, parse_int=int, object_pairs_hook=_object
    )
    if not isinstance(records, list) or record_index >= len(records):
        raise ValueError("raw record index unavailable")
    record = records[record_index]
    if not isinstance(record, dict):
        raise ValueError("raw record must be an object")
    kind = record.get("T")
    symbol = record.get("S")
    kind = kind if isinstance(kind, str) else None
    symbol = symbol if isinstance(symbol, str) else None
    revision = kind in {"c", "x", "u"}
    reasons: list[str] = []
    receipt_age = decision_ns - _datetime_ns(event.system_first_seen_at)
    age: int | None = None
    schema_valid = True
    codes: list[str] = []
    try:
        if not _identity(symbol):
            raise ValueError("symbol")
        age = decision_ns - _timestamp_ns(record.get("t"))
        if kind not in {"t", "q"}:
            raise ValueError("not current trade or quote")
        conditions = record.get("c")
        if (
            not isinstance(conditions, list)
            or not conditions
            or any(not _condition_identity(code) for code in conditions)
        ):
            raise ValueError("conditions")
        codes = conditions
        if not _identity(record.get("z")):
            raise ValueError("tape")
        if kind == "t":
            _positive(record.get("p"))
            _positive(record.get("s"))
            if (
                not _identity(record.get("x"))
                or type(record.get("i")) is not int
                or record["i"] < 0
            ):
                raise ValueError("trade identity")
        else:
            bid, ask = _positive(record.get("bp")), _positive(record.get("ap"))
            _positive(record.get("bs"))
            _positive(record.get("as"))
            if bid > ask or not all(_identity(record.get(k)) for k in ("bx", "ax")):
                raise ValueError("quote identity or crossed prices")
    except ValueError:
        schema_valid = False
        reasons.append("INVALID_TRADE_QUOTE_SCHEMA")
    fresh = (
        age is not None
        and 0 <= age <= 1_000_000_000
        and 0 <= receipt_age <= 1_000_000_000
    )
    if not fresh:
        reasons.append("EVENT_OR_RECEIPT_AGE_OUT_OF_BOUNDS")
    if revision:
        reasons.append("REVISION_REQUIRES_ORDERED_RECONSTRUCTION")
    names = condition_names or {}
    mapped = tuple((code, names.get(code)) for code in codes)
    if any(name is None for _, name in mapped):
        reasons.append("UNMAPPED_CONDITION_CODE")
    reasons.extend(("CONDITION_POLICY_UNQUALIFIED", "PROVIDER_CLOCK_UNQUALIFIED"))
    return MarketInputAssessment(
        sequence,
        record_index,
        payload_ref,
        kind,
        symbol,
        schema_valid,
        fresh,
        age,
        receipt_age,
        mapped,
        revision,
        tuple(reasons),
    )
