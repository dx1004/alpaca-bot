"""P04 exact, as-observed IEX research features; never runtime data authority."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from .observation_admission import CANDIDATE_VERSION, ObservationContract

NY = ZoneInfo("America/New_York")
VERSION = "HYBRID_500_IEX_INTRADAY_RESEARCH_V1"
COVERAGE = "NOT_SIP_EQUIVALENT"


def research_candidate_version(
    observation_contract: ObservationContract | None = None,
) -> str:
    """Opt into a pinned research identity; this never admits observed inputs."""
    if observation_contract is None:
        return VERSION
    if (
        type(observation_contract) is not ObservationContract
        or observation_contract != ObservationContract()
        or observation_contract.runtime_authorized is not False
        or type(observation_contract.max_age_seconds) is not int
    ):
        raise FeatureUnavailable("pinned observation-age research contract required")
    return CANDIDATE_VERSION


class FeatureUnavailable(ValueError):
    """Required decision-time evidence is missing or malformed."""


def utc(value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise FeatureUnavailable("P04/P13: explicit UTC clock required")


def positive(value: Fraction) -> None:
    if type(value) is not Fraction or value <= 0:
        raise FeatureUnavailable("P04/P13: exact positive Fraction required")


@dataclass(frozen=True)
class Session:
    day: date
    open_at: datetime
    close_at: datetime
    available_at: datetime
    calendar_ref: str

    def __post_init__(self) -> None:
        for stamp in (self.open_at, self.close_at, self.available_at):
            utc(stamp)
        if (
            type(self.day) is not date
            or not self.calendar_ref.strip()
            or self.open_at >= self.close_at
            or self.open_at.astimezone(NY).date() != self.day
            or self.close_at.astimezone(NY).date() != self.day
            or self.open_at.second
            or self.open_at.microsecond
            or self.close_at.second
            or self.close_at.microsecond
        ):
            raise FeatureUnavailable("P04/P07: invalid supplied session calendar")


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        utc(self.start)
        utc(self.end)
        if self.start >= self.end:
            raise FeatureUnavailable("P04: nonpositive bar interval")


@dataclass(frozen=True)
class Bar:
    symbol: str
    slot: Slot
    open: Fraction
    high: Fraction
    low: Fraction
    close: Fraction
    volume: int
    available_at: datetime
    revision: int
    evidence_ref: str
    feed: str = "iex"
    adjustment: str = "raw"

    def __post_init__(self) -> None:
        utc(self.available_at)
        for value in (self.open, self.high, self.low, self.close):
            positive(value)
        if (
            not self.symbol.strip()
            or not self.evidence_ref.strip()
            or type(self.volume) is not int
            or self.volume < 0
            or type(self.revision) is not int
            or self.revision < 0
            or not self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
            or self.feed != "iex"
            or self.adjustment != "raw"
            or self.available_at < self.slot.end
        ):
            raise FeatureUnavailable("P04: invalid raw IEX completed bar")


def session_slots(
    session: Session, minutes: int, decision_at: datetime
) -> tuple[Slot, ...]:
    """Expected slots come from the supplied calendar, including truncated close."""
    utc(decision_at)
    if type(minutes) is not int or minutes not in (1, 5, 60):
        raise FeatureUnavailable("P04: unsupported RTH interval")
    if session.available_at > decision_at:
        raise FeatureUnavailable("P04: calendar unavailable at decision")
    result = []
    start = session.open_at
    while start < session.close_at:
        end = min(start + timedelta(minutes=minutes), session.close_at)
        result.append(Slot(start, end))
        start = end
    return tuple(result)


def observed_bar(
    symbol: str, slot: Slot, observations: tuple[Bar, ...], at: datetime
) -> Bar:
    """Select only revisions actually available by the decision clock."""
    utc(at)
    if slot.end > at:
        raise FeatureUnavailable("P04: bar not completed")
    candidates = [
        bar
        for bar in observations
        if bar.symbol == symbol and bar.slot == slot and bar.available_at <= at
    ]
    if not candidates:
        raise FeatureUnavailable("P04: expected bar missing")
    revisions: dict[int, Bar] = {}
    for bar in candidates:
        old = revisions.get(bar.revision)
        if old is not None and old != bar:
            raise FeatureUnavailable("P04: conflicting revision identity")
        revisions[bar.revision] = bar
    newest = max(candidates, key=lambda bar: (bar.available_at, bar.revision))
    if newest.revision != max(revisions):
        raise FeatureUnavailable("P04: revision clock conflict")
    return newest


def aggregate_minutes(
    symbol: str, slot: Slot, observations: tuple[Bar, ...], at: datetime
) -> Bar:
    """No zero-filled minutes: every expected provider minute must exist."""
    if (
        slot.start.second
        or slot.start.microsecond
        or slot.end.second
        or slot.end.microsecond
    ):
        raise FeatureUnavailable("P04: minute-aligned bucket required")
    relevant = tuple(
        bar
        for bar in observations
        if bar.symbol == symbol and slot.start <= bar.slot.start < slot.end
    )
    minutes = []
    start = slot.start
    while start < slot.end:
        end = start + timedelta(minutes=1)
        minutes.append(observed_bar(symbol, Slot(start, end), relevant, at))
        start = end
    return Bar(
        symbol,
        slot,
        minutes[0].open,
        max(bar.high for bar in minutes),
        min(bar.low for bar in minutes),
        minutes[-1].close,
        sum(bar.volume for bar in minutes),
        max(bar.available_at for bar in minutes),
        max(bar.revision for bar in minutes),
        "P04:aggregate:" + ",".join(bar.evidence_ref for bar in minutes),
    )


def ema(closes: tuple[Fraction, ...], periods: int) -> Fraction:
    """Exactly 100 expected closes, first N mean, then exact rational recurrence."""
    if len(closes) != 100 or type(periods) is not int or periods not in (9, 21, 50):
        raise FeatureUnavailable("P04: exactly 100 closes and supported EMA required")
    for value in closes:
        positive(value)
    result = sum(closes[:periods], Fraction(0)) / periods
    alpha = Fraction(2, periods + 1)
    for value in closes[periods:]:
        result = alpha * value + (1 - alpha) * result
    return result


def series(
    symbol: str, expected: tuple[Slot, ...], observations: tuple[Bar, ...], at: datetime
) -> tuple[Bar, ...]:
    """Caller supplies the audited consecutive expected calendar, not sparse data."""
    if (
        len(expected) != 100
        or tuple(sorted(set(expected), key=lambda s: s.start)) != expected
    ):
        raise FeatureUnavailable("P04: 100 distinct ordered expected slots required")
    if any(
        left.end > right.start
        for left, right in zip(expected, expected[1:], strict=False)
    ):
        raise FeatureUnavailable("P04: overlapping expected slots")
    return tuple(observed_bar(symbol, slot, observations, at) for slot in expected)


@dataclass(frozen=True)
class Trend:
    symbol: str
    session: Session
    prior_close: Fraction
    daily21: Fraction
    daily50: Fraction
    hourly9: Fraction
    hourly21: Fraction
    evidence_refs: tuple[str, ...]
    version: str = VERSION


def expected_history(
    sessions: tuple[Session, ...], minutes: int | None, at: datetime
) -> tuple[Slot, ...]:
    """Derive expected bars from an audited calendar, never from sparse observations."""
    utc(at)
    if (
        not sessions
        or tuple(sorted(sessions, key=lambda s: s.day)) != sessions
        or len({session.day for session in sessions}) != len(sessions)
        or any(session.available_at > at for session in sessions)
    ):
        raise FeatureUnavailable("P04: unavailable or unordered history calendar")
    expected: list[Slot] = []
    for session in sessions:
        if minutes is None:
            left = datetime.combine(session.day, datetime.min.time(), NY)
            right = datetime.combine(
                session.day + timedelta(days=1), datetime.min.time(), NY
            )
            slot = Slot(left.astimezone(at.tzinfo), right.astimezone(at.tzinfo))
            expected.extend((slot,) if slot.end <= at else ())
        else:
            expected.extend(
                slot for slot in session_slots(session, minutes, at) if slot.end <= at
            )
    result = tuple(expected[-100:])
    if len(result) != 100:
        raise FeatureUnavailable("P04: fewer than 100 expected completed bars")
    return result


def build_trend(
    symbol: str,
    session: Session,
    history_sessions: tuple[Session, ...],
    daily_bars: tuple[Bar, ...],
    minutes: tuple[Bar, ...],
    *,
    observation_contract: ObservationContract | None = None,
) -> Trend:
    """Freeze at open: 100 full-NY-day bars and 100 RTH hourly minute aggregates."""
    version = research_candidate_version(observation_contract)
    if session.available_at > session.open_at:
        raise FeatureUnavailable("P04/P05: calendar unknown at open")
    daily_expected = expected_history(history_sessions, None, session.open_at)
    hourly_expected = expected_history(history_sessions, 60, session.open_at)
    daily = series(symbol, daily_expected, daily_bars, session.open_at)
    hourly = tuple(
        aggregate_minutes(symbol, slot, minutes, session.open_at)
        for slot in hourly_expected
    )
    dc, hc = tuple(bar.close for bar in daily), tuple(bar.close for bar in hourly)
    return Trend(
        symbol,
        session,
        dc[-1],
        ema(dc, 21),
        ema(dc, 50),
        ema(hc, 9),
        ema(hc, 21),
        tuple(bar.evidence_ref for bar in daily + hourly),
        version=version,
    )


def latest_entry_bar(
    symbol: str,
    session: Session,
    minutes: tuple[Bar, ...],
    trade_at: datetime,
    decision_at: datetime,
) -> Bar:
    """P04/P07: derive the prior bar from ALL raw minutes, not a caller-filled bucket.

    The decision clock chooses timeframe; the bar must complete before the trigger
    trade, preventing a late pre-switch trade from using the just-finished5m bar.
    """
    utc(trade_at)
    utc(decision_at)
    end = min(session.open_at + timedelta(minutes=60), session.close_at)
    if not session.open_at <= trade_at <= decision_at < end:
        raise FeatureUnavailable("P07: outside entry window")
    timeframe = 1 if decision_at < session.open_at + timedelta(minutes=15) else 5
    expected = tuple(
        slot
        for slot in session_slots(session, timeframe, decision_at)
        if slot.end <= trade_at
    )
    if not expected:
        raise FeatureUnavailable("P04/P06: no completed prior entry bar")
    return aggregate_minutes(symbol, expected[-1], minutes, decision_at)
