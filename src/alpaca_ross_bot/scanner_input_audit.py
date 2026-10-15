"""Pure input-contract checks; never float validation or strategy qualification."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

type DirectoryKind = Literal["nasdaqlisted", "otherlisted"]
_NEW_YORK = ZoneInfo("America/New_York")
_HEADERS: dict[DirectoryKind, tuple[str, ...]] = {
    "nasdaqlisted": (
        "Symbol",
        "Security Name",
        "Market Category",
        "Test Issue",
        "Financial Status",
        "Round Lot Size",
        "ETF",
        "NextShares",
    ),
    "otherlisted": (
        "ACT Symbol",
        "Security Name",
        "Exchange",
        "CQS Symbol",
        "ETF",
        "Round Lot Size",
        "Test Issue",
        "NASDAQ Symbol",
    ),
}


class ScannerInputAuditError(ValueError):
    """An input cannot express a valid, explicit audit contract."""


@dataclass(frozen=True)
class ReferenceCapacityAudit:
    required_identities: tuple[str, ...]
    required_identity_count: int
    required_units: int
    reported_remaining_units: int | None
    reasons: tuple[str, ...]
    recurring_daily_limit_units: int = 20
    units_per_queried_identity: int = 1
    cost_basis: str = "ONE_LOOKUP_PER_SUPPLIED_IDENTITY"
    remaining_units_status: str = "CALLER_REPORTED_NOT_VERIFIED_BALANCE"
    scope: str = "REPORTED_INPUT_CAPACITY_CONSISTENCY_ONLY"
    strategy_eligibility: str = "BLOCKED_RESEARCH"

    @property
    def consistent(self) -> bool:
        return not self.reasons


def audit_eodhd_reference_capacity(
    required_identities: tuple[str, ...], *, reported_remaining_units: int | None
) -> ReferenceCapacityAudit:
    """Compare supplied premises with recurring free ticker-unit capacity only.

    One lookup per supplied identity consumes one unit, regardless of batching.
    Additional refreshes/retries incur extra units not modeled by this set audit.
    The recurring daily limit is 20 ticker units; a reported welcome balance
    above 20 cannot expand it. This is independent of per-minute request limits.
    The balance is caller-reported and unverified. Empty input demands no units;
    an unknown balance remains explicitly unknown even with no demand. Nothing
    selects identities, credits caching, schedules queries or admits live work.
    """
    if not isinstance(required_identities, tuple) or any(
        not isinstance(symbol, str)
        or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]*\.US", symbol) is None
        for symbol in required_identities
    ):
        raise ScannerInputAuditError("required EODHD US identities are invalid")
    if len(set(required_identities)) != len(required_identities):
        raise ScannerInputAuditError("required EODHD US identities must be unique")
    if reported_remaining_units is not None and (
        type(reported_remaining_units) is not int or reported_remaining_units < 0
    ):
        raise ScannerInputAuditError(
            "reported remaining units must be nonnegative integer or unknown"
        )
    required = len(required_identities)
    reasons: list[str] = []
    if required > 20:
        reasons.append("REQUIRED_SET_EXCEEDS_RECURRING_DAILY_LIMIT")
    if reported_remaining_units is None:
        reasons.append("REMAINING_UNITS_UNKNOWN")
    elif required > reported_remaining_units:
        reasons.append("INSUFFICIENT_REPORTED_REMAINING_UNITS")
    return ReferenceCapacityAudit(
        required_identities=tuple(required_identities),
        required_identity_count=required,
        required_units=required,
        reported_remaining_units=reported_remaining_units,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class DirectoryAliasCollision:
    namespace: str
    alias: str
    primary_symbols: tuple[str, ...]


@dataclass(frozen=True)
class DirectoryInput:
    kind: DirectoryKind
    source_id: str
    raw_sha256: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    source_created_at: datetime
    alias_collisions: tuple[DirectoryAliasCollision, ...]
    source_timezone: str = "UNKNOWN"
    scope: str = "SUPPLIED_DIRECTORY_BYTES_ONLY"


def parse_nasdaq_directory(
    raw: bytes, *, kind: DirectoryKind, source_id: str
) -> DirectoryInput:
    """Retain raw field values, including aliases, test issues and unknown codes.

    Creation time is a naive source label, not a receipt/availability clock.
    No punctuation normalization, ETF exclusion or coverage claim is made.
    """
    if kind not in _HEADERS or not source_id.strip():
        raise ScannerInputAuditError("directory kind and source are required")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ScannerInputAuditError("directory must be UTF-8") from exc
    headers = _HEADERS[kind]
    if len(lines) < 2 or tuple(lines[0].split("|")) != headers:
        raise ScannerInputAuditError("directory header mismatch")
    rows: list[tuple[str, ...]] = []
    symbols: set[str] = set()
    created_at: datetime | None = None
    for index, line in enumerate(lines[1:], start=1):
        fields = tuple(line.split("|"))
        if fields[0].startswith("File Creation Time"):
            if index != len(lines) - 1 or created_at is not None:
                raise ScannerInputAuditError("creation trailer must be terminal")
            match = re.fullmatch(r"File Creation Time: ([0-9]{10}:[0-9]{2})", fields[0])
            if match is None or any(fields[1:]):
                raise ScannerInputAuditError("malformed creation trailer")
            try:
                created_at = datetime.strptime(match[1], "%m%d%Y%H:%M")
            except ValueError as exc:
                raise ScannerInputAuditError("invalid creation datetime") from exc
            continue  # otherlisted trailers may have fewer columns than data rows
        if len(fields) != len(headers) or not fields[0].strip():
            raise ScannerInputAuditError("directory row shape mismatch")
        if fields[0] in symbols:
            raise ScannerInputAuditError("duplicate directory symbol")
        symbols.add(fields[0])
        flags = (3, 6, 7) if kind == "nasdaqlisted" else (4, 6)
        if any(fields[column] not in ("Y", "N") for column in flags):
            raise ScannerInputAuditError("directory flag must be Y or N")
        if re.fullmatch(r"[0-9]+", fields[5]) is None or int(fields[5]) <= 0:
            raise ScannerInputAuditError("round lot size must be positive")
        rows.append(fields)
    if created_at is None:
        raise ScannerInputAuditError("creation trailer is required")
    alias_owners: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        for column in (0,) if kind == "nasdaqlisted" else (0, 3, 7):
            if row[column]:
                alias_owners.setdefault((headers[column], row[column]), []).append(
                    row[0]
                )
    collisions = tuple(
        DirectoryAliasCollision(namespace, alias, tuple(owners))
        for (namespace, alias), owners in sorted(alias_owners.items())
        if len(owners) > 1
    )
    return DirectoryInput(
        kind,
        source_id,
        hashlib.sha256(raw).hexdigest(),
        headers,
        tuple(rows),
        created_at,
        collisions,
    )


@dataclass(frozen=True)
class VolumeContract:
    source_id: str
    symbol: str
    feed: str
    adjustment: str
    session_scope: str


@dataclass(frozen=True)
class DatedDailyVolume:
    contract: VolumeContract
    session_date: date
    event_at: datetime
    session_completed_at: datetime | None
    volume: int
    first_seen_at: datetime
    processed_at: datetime


@dataclass(frozen=True)
class VolumeHistoryAudit:
    reasons: tuple[str, ...]
    mean_daily_volume: Decimal | None
    session_volume_ratio: Decimal | None
    scope: str = "INPUT_CONTRACT_CONSISTENCY_ONLY"
    coverage_label: str = "NOT_SIP_EQUIVALENT"
    strategy_eligibility: str = "BLOCKED_RESEARCH"

    @property
    def consistent(self) -> bool:
        return not self.reasons


def audit_daily_volume_history(
    bars: tuple[DatedDailyVolume, ...],
    *,
    expected_sessions: tuple[date, ...],
    contract: VolumeContract,
    first_seen_at: datetime,
    processed_at: datetime,
    decision_at: datetime,
    pagination_exhausted: bool,
    current: DatedDailyVolume | None = None,
) -> VolumeHistoryAudit:
    """Audit the exact caller-declared completed sessions without gap filling.

    The caller supplies the calendar, final-session completion clocks and scope.
    This checks consistency, not those declarations' external truth. A day-start
    bar event timestamp alone never establishes session completion. Ratios use
    Decimal arithmetic and remain descriptive, not Ross/time-normalized RVOL.
    The cumulative current bar may omit a completion claim; it still requires
    daily event and acquisition clocks. All history must precede its session.
    """
    _validate_contract(contract)
    if (
        not expected_sessions
        or any(type(day) is not date for day in expected_sessions)
        or tuple(sorted(set(expected_sessions))) != expected_sessions
    ):
        raise ScannerInputAuditError("expected sessions must be unique sorted dates")
    for field, stamp in (
        ("first_seen_at", first_seen_at),
        ("processed_at", processed_at),
        ("decision_at", decision_at),
    ):
        _require_utc(stamp, field)
    reasons: set[str] = set()
    if contract.feed != "iex":
        reasons.add("CONTRACT_FEED_NOT_IEX")
    if type(pagination_exhausted) is not bool:
        raise ScannerInputAuditError("pagination exhaustion must be explicit")
    if not pagination_exhausted:
        reasons.add("PAGINATION_NOT_EXHAUSTED")
    if first_seen_at > decision_at or processed_at > decision_at:
        reasons.add("HISTORY_UNAVAILABLE_AT_DECISION")
    if processed_at < first_seen_at:
        reasons.add("HISTORY_PROCESSING_PRECEDES_FIRST_SEEN")
    observed: set[date] = set()
    for bar in bars:
        reasons.update(_bar_findings(bar, contract, decision_at, completed=True))
        day = bar.session_date.isoformat()
        if bar.session_date in observed:
            reasons.add(f"DUPLICATE_SESSION:{day}")
        observed.add(bar.session_date)
        if bar.session_date not in expected_sessions:
            reasons.add(f"UNEXPECTED_SESSION:{day}")
        if bar.first_seen_at > first_seen_at or bar.processed_at > processed_at:
            reasons.add(f"BAR_UNAVAILABLE_AT_HISTORY_CAPTURE:{day}")
    for missing in set(expected_sessions) - observed:
        reasons.add(f"MISSING_SESSION:{missing.isoformat()}")
    if current is not None:
        reasons.update(
            f"CURRENT_{reason}"
            for reason in _bar_findings(current, contract, decision_at, completed=False)
        )
        if any(
            day >= current.session_date for day in set(expected_sessions) | observed
        ):
            reasons.add("HISTORY_NOT_STRICTLY_BEFORE_CURRENT_SESSION")
    if reasons:
        return VolumeHistoryAudit(tuple(sorted(reasons)), None, None)
    total = sum(bar.volume for bar in bars)
    if total == 0:
        return VolumeHistoryAudit(("ZERO_HISTORY_VOLUME",), None, None)
    count = len(expected_sessions)
    mean = Decimal(total) / Decimal(count)
    ratio = (
        None
        if current is None
        else Decimal(current.volume) * Decimal(count) / Decimal(total)
    )
    return VolumeHistoryAudit((), mean, ratio)


def _bar_findings(
    bar: DatedDailyVolume,
    contract: VolumeContract,
    decision_at: datetime,
    *,
    completed: bool,
) -> set[str]:
    _validate_contract(bar.contract)
    if type(bar.session_date) is not date:
        raise ScannerInputAuditError("bar session date must be a date")
    day = bar.session_date.isoformat()
    reasons: set[str] = set()
    if bar.session_date > decision_at.astimezone(_NEW_YORK).date():
        reasons.add(f"FUTURE_SESSION_DATE:{day}")
    for field in ("source_id", "symbol", "feed", "adjustment", "session_scope"):
        if getattr(bar.contract, field) != getattr(contract, field):
            reasons.add(f"MISMATCHED_{field.upper()}:{day}")
    for field in ("event_at", "first_seen_at", "processed_at"):
        stamp = getattr(bar, field)
        _require_utc(stamp, field)
        if stamp > decision_at:
            reasons.add(f"FUTURE_{field.upper()}:{day}")
    daily_start = datetime.combine(bar.session_date, time.min, _NEW_YORK)
    if bar.event_at != daily_start.astimezone(UTC):
        reasons.add(f"DAILY_EVENT_SESSION_MISMATCH:{day}")
    if bar.first_seen_at < bar.event_at or bar.processed_at < bar.first_seen_at:
        reasons.add(f"BAR_CLOCK_ORDER_INVALID:{day}")
    if bar.session_completed_at is None:
        if completed:
            reasons.add(f"SESSION_COMPLETION_UNPROVEN:{day}")
    else:
        _require_utc(bar.session_completed_at, "session_completed_at")
        if bar.session_completed_at > decision_at:
            reasons.add(f"INCOMPLETE_SESSION:{day}")
        if (
            bar.event_at >= bar.session_completed_at
            or bar.first_seen_at < bar.session_completed_at
        ):
            reasons.add(f"COMPLETION_CLOCK_ORDER_INVALID:{day}")
    if type(bar.volume) is not int or bar.volume < 0:
        reasons.add(f"INVALID_VOLUME:{day}")
    return reasons


def _validate_contract(contract: VolumeContract) -> None:
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (
            contract.source_id,
            contract.symbol,
            contract.feed,
            contract.adjustment,
            contract.session_scope,
        )
    ):
        raise ScannerInputAuditError("source, identity and volume scope are required")


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ScannerInputAuditError(f"{field} must be timezone-aware UTC")
