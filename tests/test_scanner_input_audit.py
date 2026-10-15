import hashlib
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from alpaca_ross_bot.scanner_input_audit import (
    DatedDailyVolume,
    DirectoryKind,
    ScannerInputAuditError,
    VolumeContract,
    VolumeHistoryAudit,
    audit_daily_volume_history,
    audit_eodhd_reference_capacity,
    parse_nasdaq_directory,
)

NASDAQ_HEADER = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|"
    "Round Lot Size|ETF|NextShares"
)
OTHER_HEADER = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|"
    "Test Issue|NASDAQ Symbol"
)
TRAILER = "File Creation Time: 1014202621:31"
CONTRACT = VolumeContract("alpaca_stock_bars", "ABC", "iex", "raw", "1day_all_eligible")
DAYS = (date(2026, 10, 12), date(2026, 10, 13))
DECISION = datetime(2026, 10, 14, 14, tzinfo=UTC)


def _raw(header: str, *rows: str, trailer: str = TRAILER + "|||||||") -> bytes:
    return ("\n".join((header, *rows, trailer)) + "\n").encode()


def _bar(day: date, volume: int = 100) -> DatedDailyVolume:
    event = datetime(
        day.year, day.month, day.day, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(UTC)
    completed = event + timedelta(hours=20)
    return DatedDailyVolume(
        CONTRACT,
        day,
        event,
        completed,
        volume,
        completed + timedelta(seconds=1),
        completed + timedelta(seconds=2),
    )


def _current(volume: int = 55) -> DatedDailyVolume:
    return replace(
        _bar(date(2026, 10, 14), volume),
        session_completed_at=None,
        first_seen_at=DECISION - timedelta(seconds=1),
        processed_at=DECISION,
    )


def _audit(
    bars: tuple[DatedDailyVolume, ...],
    *,
    exhausted: bool = True,
    current: DatedDailyVolume | None = None,
) -> VolumeHistoryAudit:
    return audit_daily_volume_history(
        bars,
        expected_sessions=DAYS,
        contract=CONTRACT,
        first_seen_at=DECISION - timedelta(seconds=1),
        processed_at=DECISION,
        decision_at=DECISION,
        pagination_exhausted=exhausted,
        current=current,
    )


def test_nasdaq_preserves_test_issues_unknown_codes_and_raw_hash() -> None:
    raw = _raw(NASDAQ_HEADER, "Z.TEST|Test company|Z|Y|? |100|Y|N")
    parsed = parse_nasdaq_directory(raw, kind="nasdaqlisted", source_id="nasdaq")
    assert parsed.rows == (("Z.TEST", "Test company", "Z", "Y", "? ", "100", "Y", "N"),)
    assert parsed.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert parsed.source_created_at == datetime(2026, 10, 14, 21, 31)
    assert parsed.source_created_at.tzinfo is None
    assert parsed.source_timezone == "UNKNOWN"


def test_otherlisted_preserves_alias_punctuation_and_short_trailer() -> None:
    raw = _raw(
        OTHER_HEADER,
        "ABC.A|Company|F|ABC.A|N|100|N|ABC$A",
        trailer=TRAILER + "||||||",
    )
    parsed = parse_nasdaq_directory(raw, kind="otherlisted", source_id="nasdaq")
    assert parsed.rows[0][0] == "ABC.A"
    assert parsed.rows[0][2:4] == ("F", "ABC.A")
    assert parsed.rows[0][-1] == "ABC$A"


def test_alias_collisions_are_reported_only_within_original_namespace() -> None:
    raw = _raw(
        OTHER_HEADER,
        "ABC.A|Company A|F|SHARED|N|100|N|ABC$A",
        "ABC.B|Company B|F|SHARED|N|100|N|ABC$B",
        "SHARED|Company C|F|OTHER|N|100|N|SHARED",
    )
    parsed = parse_nasdaq_directory(raw, kind="otherlisted", source_id="n")
    assert len(parsed.alias_collisions) == 1
    collision = parsed.alias_collisions[0]
    assert collision.namespace == "CQS Symbol"
    assert collision.alias == "SHARED"
    assert collision.primary_symbols == ("ABC.A", "ABC.B")
    assert parsed.rows[2][0] == "SHARED"


@pytest.mark.parametrize("flag_index", [3, 6, 7])
def test_invalid_nasdaq_flags_are_rejected(flag_index: int) -> None:
    row = ["ABC", "Company", "Q", "N", "N", "100", "N", "N"]
    row[flag_index] = "?"
    with pytest.raises(ScannerInputAuditError, match="flag"):
        parse_nasdaq_directory(
            _raw(NASDAQ_HEADER, "|".join(row)),
            kind="nasdaqlisted",
            source_id="n",
        )


@pytest.mark.parametrize("flag_index", [4, 6])
def test_invalid_otherlisted_flags_are_rejected(flag_index: int) -> None:
    row = ["ABC", "Company", "F", "ABC", "N", "100", "N", "ABC"]
    row[flag_index] = "?"
    with pytest.raises(ScannerInputAuditError, match="flag"):
        parse_nasdaq_directory(
            _raw(OTHER_HEADER, "|".join(row)),
            kind="otherlisted",
            source_id="n",
        )


@pytest.mark.parametrize("lot", ["0", "-1", "1.5", "True", ""])
def test_invalid_lot_size_is_rejected(lot: str) -> None:
    with pytest.raises(ScannerInputAuditError, match="lot"):
        parse_nasdaq_directory(
            _raw(OTHER_HEADER, f"ABC|Company|N|ABC|N|{lot}|N|ABC"),
            kind="otherlisted",
            source_id="n",
        )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (_raw(NASDAQ_HEADER + "|Extra"), "header"),
        (_raw(NASDAQ_HEADER, "ABC|Company"), "shape"),
        (_raw(NASDAQ_HEADER, ""), "shape"),
        (_raw(NASDAQ_HEADER, TRAILER + "|||||||"), "terminal"),
        (_raw(NASDAQ_HEADER, trailer="File Creation Time: 1014202625:31"), "datetime"),
        (_raw(NASDAQ_HEADER, trailer=TRAILER + "|unexpected"), "trailer"),
        ((NASDAQ_HEADER + "\nABC|Company|Q|N|N|100|N|N\n").encode(), "required"),
        (_raw(NASDAQ_HEADER, TRAILER + "|||||||", "ABC|Co|Q|N|N|100|N|N"), "terminal"),
    ],
)
def test_malformed_directory_is_rejected(raw: bytes, message: str) -> None:
    with pytest.raises(ScannerInputAuditError, match=message):
        parse_nasdaq_directory(raw, kind="nasdaqlisted", source_id="n")


@pytest.mark.parametrize("kind", ["nasdaqlisted", "otherlisted"])
def test_duplicate_symbols_are_rejected(kind: DirectoryKind) -> None:
    header = NASDAQ_HEADER if kind == "nasdaqlisted" else OTHER_HEADER
    row = (
        "ABC|Company|Q|N|N|100|N|N"
        if kind == "nasdaqlisted"
        else ("ABC|Company|N|ABC|N|100|N|ABC")
    )
    with pytest.raises(ScannerInputAuditError, match="duplicate"):
        parse_nasdaq_directory(_raw(header, row, row), kind=kind, source_id="n")


def test_missing_bar_is_not_zero_and_no_last_sessions_are_selected() -> None:
    result = _audit((_bar(DAYS[0], 0), _bar(date(2026, 10, 9))))
    assert "MISSING_SESSION:2026-10-13" in result.reasons
    assert "UNEXPECTED_SESSION:2026-10-09" in result.reasons
    assert result.mean_daily_volume is None
    assert not result.consistent
    assert _audit(tuple(_bar(day, 0) for day in DAYS)).reasons == (
        "ZERO_HISTORY_VOLUME",
    )


def test_duplicate_daily_session_blocks_derivation() -> None:
    result = _audit((_bar(DAYS[0]), _bar(DAYS[0]), _bar(DAYS[1])))
    assert "DUPLICATE_SESSION:2026-10-12" in result.reasons
    assert result.mean_daily_volume is None


def test_day_start_event_does_not_prove_session_completion() -> None:
    result = _audit((replace(_bar(DAYS[0]), session_completed_at=None), _bar(DAYS[1])))
    assert "SESSION_COMPLETION_UNPROVEN:2026-10-12" in result.reasons


@pytest.mark.parametrize("field", ["event_at", "first_seen_at", "processed_at"])
def test_future_bar_clocks_block_point_in_time_inputs(field: str) -> None:
    future = DECISION + timedelta(seconds=1)
    original = _bar(DAYS[0])
    if field == "event_at":
        bar = replace(original, event_at=future)
    elif field == "first_seen_at":
        bar = replace(original, first_seen_at=future)
    else:
        bar = replace(original, processed_at=future)
    assert f"FUTURE_{field.upper()}:2026-10-12" in _audit((bar, _bar(DAYS[1]))).reasons


def test_future_session_label_is_not_made_completed_by_past_clocks() -> None:
    bar = replace(_bar(DAYS[0]), session_date=date(2050, 1, 1))
    assert "FUTURE_SESSION_DATE:2050-01-01" in _audit((bar, _bar(DAYS[1]))).reasons


def test_past_forged_session_label_is_rejected_by_the_daily_event_binding() -> None:
    bar = replace(_bar(DAYS[0]), session_date=DAYS[1])
    result = _audit((bar,))
    assert "DAILY_EVENT_SESSION_MISMATCH:2026-10-13" in result.reasons
    assert result.session_volume_ratio is None


@pytest.mark.parametrize(("day", "utc_hour"), [(date(2026, 1, 12), 5), (DAYS[0], 4)])
def test_daily_start_binding_tracks_new_york_daylight_saving(
    day: date, utc_hour: int
) -> None:
    bar = _bar(day)
    assert bar.event_at == datetime(day.year, day.month, day.day, utc_hour, tzinfo=UTC)
    wrong = replace(bar, event_at=bar.event_at + timedelta(seconds=1))
    result = audit_daily_volume_history(
        (wrong,),
        expected_sessions=(day,),
        contract=CONTRACT,
        first_seen_at=DECISION,
        processed_at=DECISION,
        decision_at=DECISION,
        pagination_exhausted=True,
    )
    assert f"DAILY_EVENT_SESSION_MISMATCH:{day.isoformat()}" in result.reasons


@pytest.mark.parametrize("field", ["event_at", "first_seen_at", "processed_at"])
def test_future_current_numerator_clock_prevents_a_ratio(field: str) -> None:
    current = _current()
    future = DECISION + timedelta(seconds=1)
    if field == "event_at":
        current = replace(current, event_at=future)
    elif field == "first_seen_at":
        current = replace(current, first_seen_at=future)
    else:
        current = replace(current, processed_at=future)
    result = _audit(tuple(_bar(day) for day in DAYS), current=current)
    assert f"CURRENT_FUTURE_{field.upper()}:2026-10-14" in result.reasons
    assert result.session_volume_ratio is None


def test_history_cannot_include_the_current_numerator_session() -> None:
    current = replace(_current(), session_date=DAYS[1], event_at=_bar(DAYS[1]).event_at)
    result = _audit(tuple(_bar(day) for day in DAYS), current=current)
    assert "HISTORY_NOT_STRICTLY_BEFORE_CURRENT_SESSION" in result.reasons
    assert result.session_volume_ratio is None


def test_zero_history_denominator_is_explicit_with_or_without_numerator() -> None:
    bars = tuple(_bar(day, 0) for day in DAYS)
    for current in (None, _current()):
        result = _audit(bars, current=current)
        assert not result.consistent
        assert result.reasons == ("ZERO_HISTORY_VOLUME",)
        assert result.mean_daily_volume is None
        assert result.session_volume_ratio is None


def test_incomplete_session_and_unexhausted_pages_block_mean() -> None:
    future = replace(_bar(DAYS[0]), session_completed_at=DECISION + timedelta(hours=1))
    result = _audit((future, _bar(DAYS[1])), exhausted=False)
    assert "INCOMPLETE_SESSION:2026-10-12" in result.reasons
    assert "PAGINATION_NOT_EXHAUSTED" in result.reasons
    assert result.mean_daily_volume is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("feed", "sip"),
        ("session_scope", "regular_hours"),
        ("adjustment", "split"),
        ("symbol", "OTHER"),
        ("source_id", "other_provider"),
    ],
)
def test_mixed_volume_contracts_are_rejected(field: str, value: str) -> None:
    bar = replace(_bar(DAYS[0]), contract=replace(CONTRACT, **{field: value}))
    assert (
        f"MISMATCHED_{field.upper()}:2026-10-12" in _audit((bar, _bar(DAYS[1]))).reasons
    )


def test_exact_decimal_ratio_remains_only_input_consistency() -> None:
    result = audit_daily_volume_history(
        (_bar(DAYS[0], 10), _bar(DAYS[1], 30)),
        expected_sessions=DAYS,
        contract=CONTRACT,
        first_seen_at=DECISION,
        processed_at=DECISION,
        decision_at=DECISION,
        pagination_exhausted=True,
        current=_current(),
    )
    assert result.consistent
    assert result.mean_daily_volume == Decimal("20")
    assert result.session_volume_ratio == Decimal("2.75")
    assert result.scope == "INPUT_CONTRACT_CONSISTENCY_ONLY"
    assert result.strategy_eligibility == "BLOCKED_RESEARCH"
    assert result.coverage_label == "NOT_SIP_EQUIVALENT"


def test_current_volume_contract_cannot_silently_differ() -> None:
    result = audit_daily_volume_history(
        tuple(_bar(day) for day in DAYS),
        expected_sessions=DAYS,
        contract=CONTRACT,
        first_seen_at=DECISION,
        processed_at=DECISION,
        decision_at=DECISION,
        pagination_exhausted=True,
        current=replace(
            _current(), contract=replace(CONTRACT, session_scope="regular_hours")
        ),
    )
    assert result.reasons == ("CURRENT_MISMATCHED_SESSION_SCOPE:2026-10-14",)
    assert result.session_volume_ratio is None


def test_history_receipt_and_processing_must_be_available_at_decision() -> None:
    result = audit_daily_volume_history(
        tuple(_bar(day) for day in DAYS),
        expected_sessions=DAYS,
        contract=CONTRACT,
        first_seen_at=DECISION + timedelta(seconds=1),
        processed_at=DECISION,
        decision_at=DECISION,
        pagination_exhausted=True,
    )
    assert result.reasons == (
        "HISTORY_PROCESSING_PRECEDES_FIRST_SEEN",
        "HISTORY_UNAVAILABLE_AT_DECISION",
    )


def test_history_contract_requires_explicit_dates_and_utc_clocks() -> None:
    with pytest.raises(ScannerInputAuditError, match="unique sorted"):
        audit_daily_volume_history(
            (),
            expected_sessions=(DAYS[0], DAYS[0]),
            contract=CONTRACT,
            first_seen_at=DECISION,
            processed_at=DECISION,
            decision_at=DECISION,
            pagination_exhausted=True,
        )
    with pytest.raises(ScannerInputAuditError, match="UTC"):
        _audit((replace(_bar(DAYS[0]), event_at=datetime(2026, 10, 12)), _bar(DAYS[1])))


@pytest.mark.parametrize("volume", [-1, True])
def test_invalid_daily_volume_does_not_enter_the_mean(volume: int) -> None:
    result = _audit((replace(_bar(DAYS[0]), volume=volume), _bar(DAYS[1])))
    assert "INVALID_VOLUME:2026-10-12" in result.reasons
    assert result.mean_daily_volume is None


def test_final_daily_bar_cannot_be_seen_before_session_completion() -> None:
    bar = _bar(DAYS[0])
    result = _audit((replace(bar, first_seen_at=bar.event_at), _bar(DAYS[1])))
    assert "COMPLETION_CLOCK_ORDER_INVALID:2026-10-12" in result.reasons


def test_bar_processing_cannot_precede_receipt_or_follow_history_capture() -> None:
    bar = _bar(DAYS[0])
    result = _audit((replace(bar, processed_at=bar.event_at), _bar(DAYS[1])))
    assert "BAR_CLOCK_ORDER_INVALID:2026-10-12" in result.reasons
    later = replace(bar, processed_at=DECISION + timedelta(seconds=1))
    assert (
        "BAR_UNAVAILABLE_AT_HISTORY_CAPTURE:2026-10-12"
        in _audit((later, _bar(DAYS[1]))).reasons
    )


def test_blank_contract_field_cannot_pass_as_declared_scope() -> None:
    bar = replace(_bar(DAYS[0]), contract=replace(CONTRACT, adjustment=""))
    with pytest.raises(ScannerInputAuditError, match="volume scope"):
        _audit((bar, _bar(DAYS[1])))


def test_bar_event_datetime_cannot_substitute_for_explicit_session_date() -> None:
    bar = replace(_bar(DAYS[0]), session_date=DECISION)
    with pytest.raises(ScannerInputAuditError, match="session date"):
        _audit((bar, _bar(DAYS[1])))


def test_recurring_reference_capacity_boundary_counts_tickers_not_batches() -> None:
    twenty = tuple(f"TICKER{number}.US" for number in range(20))
    within = audit_eodhd_reference_capacity(twenty, reported_remaining_units=20)
    assert within.consistent and within.required_units == 20
    assert within.required_identity_count == 20
    assert within.recurring_daily_limit_units == 20
    assert within.units_per_queried_identity == 1
    assert within.cost_basis == "ONE_LOOKUP_PER_SUPPLIED_IDENTITY"
    assert within.strategy_eligibility == "BLOCKED_RESEARCH"
    assert within.scope == "REPORTED_INPUT_CAPACITY_CONSISTENCY_ONLY"
    assert within.remaining_units_status == "CALLER_REPORTED_NOT_VERIFIED_BALANCE"
    over = audit_eodhd_reference_capacity(
        (*twenty, "EXTRA.US"), reported_remaining_units=20
    )
    assert over.required_units == 21
    assert over.reasons == (
        "REQUIRED_SET_EXCEEDS_RECURRING_DAILY_LIMIT",
        "INSUFFICIENT_REPORTED_REMAINING_UNITS",
    )


def test_insufficient_reported_capacity_preserves_every_identity_and_order() -> None:
    identities = ("BRK-B.US", "BRK.B.US", *(f"T{number}.US" for number in range(18)))
    result = audit_eodhd_reference_capacity(identities, reported_remaining_units=19)
    assert result.required_identities == identities
    assert result.required_identity_count == result.required_units == 20
    assert result.reported_remaining_units == 19
    assert result.reasons == ("INSUFFICIENT_REPORTED_REMAINING_UNITS",)


def test_welcome_bonus_cannot_expand_recurring_daily_capacity() -> None:
    identities = tuple(f"T{number}.US" for number in range(21))
    result = audit_eodhd_reference_capacity(
        identities, reported_remaining_units=100_000
    )
    assert not result.consistent
    assert result.required_identities == identities
    assert result.reasons == ("REQUIRED_SET_EXCEEDS_RECURRING_DAILY_LIMIT",)
    assert result.reported_remaining_units == 100_000


def test_unknown_capacity_remains_unknown_for_even_one_required_identity() -> None:
    result = audit_eodhd_reference_capacity(("AAPL.US",), reported_remaining_units=None)
    assert not result.consistent
    assert result.reasons == ("REMAINING_UNITS_UNKNOWN",)
    assert result.reported_remaining_units is None and result.required_units == 1


def test_zero_reported_remaining_units_cannot_cover_one_identity() -> None:
    result = audit_eodhd_reference_capacity(("AAPL.US",), reported_remaining_units=0)
    assert result.reasons == ("INSUFFICIENT_REPORTED_REMAINING_UNITS",)
    assert result.required_identities == ("AAPL.US",)


def test_empty_required_set_needs_no_units_but_does_not_verify_balance() -> None:
    result = audit_eodhd_reference_capacity((), reported_remaining_units=0)
    assert result.consistent
    assert result.required_identities == ()
    assert result.required_units == result.required_identity_count == 0
    assert result.strategy_eligibility == "BLOCKED_RESEARCH"
    unknown = audit_eodhd_reference_capacity((), reported_remaining_units=None)
    assert unknown.required_units == 0
    assert unknown.reasons == ("REMAINING_UNITS_UNKNOWN",)


@pytest.mark.parametrize(
    "identities",
    [
        ("AAPL.US", "AAPL.US"),
        (None,),
        (123,),
        (True,),
        ("aapl.US",),
        ("AAPL",),
        ("AAPL.LSE",),
        (" AAPL.US",),
        ("AAPL.US?api_token=value",),
    ],
)
def test_invalid_or_duplicate_reference_identities_fail_without_normalization(
    identities: tuple[object, ...],
) -> None:
    with pytest.raises(ScannerInputAuditError):
        audit_eodhd_reference_capacity(
            cast(tuple[str, ...], identities), reported_remaining_units=20
        )


@pytest.mark.parametrize("remaining", [True, False, -1, 1.0, "20"])
def test_reported_remaining_capacity_requires_nonnegative_nonboolean_integer(
    remaining: object,
) -> None:
    with pytest.raises(ScannerInputAuditError, match="remaining units"):
        audit_eodhd_reference_capacity(
            ("AAPL.US",), reported_remaining_units=cast(int, remaining)
        )


def test_reference_capacity_report_is_immutable() -> None:
    result = audit_eodhd_reference_capacity(("AAPL.US",), reported_remaining_units=20)
    with pytest.raises(FrozenInstanceError):
        result.required_units = 0  # type: ignore[misc]
