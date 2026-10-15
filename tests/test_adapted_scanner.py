from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import alpaca_ross_bot.adapted_scanner as scanner
from alpaca_ross_bot.adapted_scanner import (
    SESSION_SCOPE,
    ClockedPrice,
    ScreenContext,
    ScreenInput,
    evaluate_all,
    evaluate_one,
)
from alpaca_ross_bot.scanner_contract import load_accepted_scanner_contract_file
from alpaca_ross_bot.scanner_input_audit import DatedDailyVolume, VolumeContract

NY = ZoneInfo("America/New_York")


def fixture(
    today: date = date(2026, 10, 15), symbol: str = "QS"
) -> tuple[ScreenContext, ScreenInput]:
    days = tuple(
        today - timedelta(days=n)
        for n in range(50, -1, -1)
        if (today - timedelta(days=n)).weekday() < 5
    )
    decision = datetime.combine(today, time(12), NY).astimezone(UTC)
    contract = VolumeContract("alpaca", symbol, "iex", "raw", SESSION_SCOPE)

    def bar(day: date, volume: int, completed: bool = True) -> DatedDailyVolume:
        event = datetime.combine(day, time.min, NY).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), time.min, NY).astimezone(UTC)
        return DatedDailyVolume(
            contract, day, event, end if completed else None, volume, decision, decision
        )

    history = tuple(bar(day, 100) for day in days if day < today)[-30:]
    price = ClockedPrice(
        symbol, Decimal("10"), today, decision, decision, "alpaca", "iex", "raw"
    )
    prior_day = history[-1].session_date
    prior = replace(
        price,
        value=Decimal("8"),
        session_date=prior_day,
        event_at=datetime.combine(prior_day, time(16), NY).astimezone(UTC),
    )
    return ScreenContext(
        decision,
        days,
        decision,
        (symbol,),
        decision,
        load_accepted_scanner_contract_file(
            Path("evidence/general-iex-daily30-screen-v1.json")
        ),
    ), ScreenInput(
        symbol, price, prior, bar(today, 500, False), history, decision, True
    )


def test_exact_threshold_and_optional_unknowns() -> None:
    context, item = fixture()
    assert item.current_volume is not None and item.prior_close is not None
    result = evaluate_one(context, item)
    assert result.status == "SCREEN_MATCH"
    assert (result.rvol_numerator, result.rvol_denominator) == (15000, 3000)
    assert result.percent_change == 25
    assert result.news_preference == result.float_preference == "UNAVAILABLE"
    assert result.classification == "USER_ACCEPTED_ADAPTATION_NOT_ROSS_REPLICA"
    assert not result.order_authority and not result.full_market_coverage
    assert result.strategy_eligibility == "BLOCKED_RESEARCH"


@pytest.mark.parametrize(
    ("price", "status"),
    [
        ("1", "SCREEN_MATCH"),
        ("19.999", "SCREEN_MATCH"),
        ("20", "RULE_NOT_MET"),
        ("0.99", "RULE_NOT_MET"),
    ],
)
def test_price_boundaries(price: str, status: str) -> None:
    context, item = fixture()
    assert item.current_volume is not None and item.prior_close is not None
    assert (
        evaluate_one(
            context,
            replace(
                item, current_price=replace(item.current_price, value=Decimal(price))
            ),
        ).status
        == status
    )


def test_below_rvol_threshold() -> None:
    context, item = fixture()
    assert item.current_volume is not None and item.prior_close is not None
    result = evaluate_one(
        context, replace(item, current_volume=replace(item.current_volume, volume=499))
    )
    assert result.status == "RULE_NOT_MET" and result.reasons == ("RVOL_BELOW_5",)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "zero",
        "feed",
        "raw",
        "scope",
        "future",
        "day",
        "early_close",
        "pagination",
        "calendar",
        "catalog",
        "price_future",
        "prior_day",
    ],
)
def test_unavailable_evidence(kind: str) -> None:
    context, item = fixture()
    assert item.current_volume is not None and item.prior_close is not None
    volume, prior = item.current_volume, item.prior_close
    assert volume is not None and prior is not None
    if kind == "missing":
        item = replace(item, history=item.history[1:])
    elif kind == "zero":
        item = replace(item, history=tuple(replace(b, volume=0) for b in item.history))
    elif kind in ("feed", "raw", "scope"):
        field = {"feed": "feed", "raw": "adjustment", "scope": "session_scope"}[kind]
        item = replace(
            item,
            current_volume=replace(
                volume,
                contract=replace(volume.contract, **{field: "wrong"}),
            ),
        )
    elif kind == "future":
        item = replace(
            item, history_available_at=context.decision_at + timedelta(seconds=1)
        )
    elif kind == "day":
        item = replace(
            item,
            current_volume=replace(volume, session_date=item.history[-1].session_date),
        )
    elif kind == "early_close":
        item = replace(
            item,
            history=(
                replace(
                    item.history[0],
                    session_completed_at=item.history[0].event_at + timedelta(hours=13),
                ),
                *item.history[1:],
            ),
        )
    elif kind == "pagination":
        item = replace(item, pagination_exhausted=False)
    elif kind == "calendar":
        context = replace(
            context, calendar_available_at=context.decision_at + timedelta(seconds=1)
        )
    elif kind == "catalog":
        context = replace(context, catalog_symbols=("OTHER",))
    elif kind == "price_future":
        item = replace(
            item,
            current_price=replace(
                item.current_price,
                available_at=context.decision_at + timedelta(seconds=1),
            ),
        )
    else:
        item = replace(
            item,
            prior_close=replace(prior, session_date=item.history[-2].session_date),
        )
    assert evaluate_one(context, item).status == "INPUT_UNAVAILABLE"


@pytest.mark.parametrize(
    "today", [date(2026, 3, 9), date(2026, 11, 2), date(2026, 11, 30)]
)
def test_dst_and_caller_calendar(today: date) -> None:
    context, item = fixture(today)
    assert evaluate_one(context, item).status == "SCREEN_MATCH"


def test_preferences_clocked_non_veto() -> None:
    context, item = fixture()
    assert item.current_volume is not None and item.prior_close is not None
    result = evaluate_one(
        context,
        replace(
            item,
            news_available_at=context.decision_at,
            float_available_at=context.decision_at + timedelta(seconds=1),
        ),
    )
    assert result.status == "SCREEN_MATCH"
    assert result.news_preference == "AVAILABLE_NOT_QUALIFIED"
    assert result.float_preference == "UNAVAILABLE"


def test_rank_and_missing_catalog_observations() -> None:
    context, first = fixture(symbol="BBB")
    _, second = fixture(symbol="AAA")
    context = replace(context, catalog_symbols=("BBB", "AAA", "MISSING"))
    results = evaluate_all(context, (first, second))
    assert tuple(r.symbol for r in results) == ("AAA", "BBB", "MISSING")
    assert results[-1].reasons == ("OBSERVATION_MISSING",)
    higher = replace(
        first, current_price=replace(first.current_price, value=Decimal("11"))
    )
    assert evaluate_all(context, (second, higher))[0].symbol == "BBB"
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_all(context, (first, first))


def test_no_gain_threshold_or_top_five_inherited() -> None:
    context, item = fixture()
    assert item.current_volume is not None and item.prior_close is not None
    assert (
        evaluate_one(
            context,
            replace(
                item, current_price=replace(item.current_price, value=Decimal("7"))
            ),
        ).status
        == "SCREEN_MATCH"
    )


def test_invalid_context_rejected() -> None:
    context, item = fixture()
    assert item.current_volume is not None and item.prior_close is not None
    with pytest.raises(ValueError, match="catalog"):
        evaluate_one(replace(context, catalog_symbols=("QS", "QS")), item)
    with pytest.raises(ValueError, match="UTC"):
        evaluate_one(
            replace(context, decision_at=context.decision_at.replace(tzinfo=None)), item
        )


def test_price_only_shortcircuit() -> None:
    context, item = fixture()
    item = replace(
        item,
        prior_close=None,
        current_volume=None,
        history=(),
        current_price=replace(item.current_price, value=Decimal("20")),
    )
    assert evaluate_one(context, item).status == "RULE_NOT_MET"
    future = replace(
        item,
        current_price=replace(
            item.current_price, available_at=context.decision_at + timedelta(seconds=1)
        ),
    )
    assert evaluate_one(context, future).status == "INPUT_UNAVAILABLE"


@pytest.mark.parametrize("which", ["current_price", "prior_close"])
def test_cross_symbol_price_rejected(which: str) -> None:
    context, item = fixture()
    price = getattr(item, which)
    assert (
        evaluate_one(
            context, replace(item, **{which: replace(price, symbol="OTHER")})
        ).status
        == "INPUT_UNAVAILABLE"
    )


def test_prior_close_cannot_arrive_before_day_elapsed() -> None:
    context, item = fixture()
    assert item.prior_close is not None
    morning = datetime.combine(item.prior_close.session_date, time(10), NY).astimezone(
        UTC
    )
    prior = replace(item.prior_close, event_at=morning, available_at=morning)
    result = evaluate_one(context, replace(item, prior_close=prior))
    assert result.status == "INPUT_UNAVAILABLE"
    assert "PRIOR_CLOSE_BUCKET_NOT_ELAPSED_AT_AVAILABILITY" in result.reasons


@pytest.mark.parametrize("kind", ["naive_clock", "empty_contract", "bad_date"])
def test_malformed_symbol_does_not_abort_catalog(kind: str) -> None:
    context, good = fixture(symbol="GOOD")
    _, bad = fixture(symbol="BAD")
    assert bad.current_volume is not None
    if kind == "naive_clock":
        bad = replace(
            bad,
            current_volume=replace(
                bad.current_volume,
                first_seen_at=context.decision_at.replace(tzinfo=None),
            ),
        )
    elif kind == "empty_contract":
        bad = replace(
            bad,
            history=(
                replace(
                    bad.history[0],
                    contract=replace(bad.history[0].contract, source_id=""),
                ),
                *bad.history[1:],
            ),
        )
    else:
        malformed = replace(bad.history[0], session_date=None)  # type: ignore[arg-type]
        bad = replace(bad, history=(malformed, *bad.history[1:]))
    results = evaluate_all(
        replace(context, catalog_symbols=("GOOD", "BAD")), (bad, good)
    )
    assert results[0].symbol == "GOOD" and results[0].status == "SCREEN_MATCH"
    assert results[1].symbol == "BAD" and results[1].status == "INPUT_UNAVAILABLE"


def test_catalog_context_validated_once_per_batch() -> None:
    context, first = fixture(symbol="FIRST")
    _, second = fixture(symbol="SECOND")
    context = replace(context, catalog_symbols=("FIRST", "SECOND"))
    with patch.object(
        scanner, "_context_reasons", wraps=scanner._context_reasons
    ) as validate:
        batch = evaluate_all(context, (first, second))
        assert validate.call_count == 1
    assert batch == tuple(
        sorted(
            (evaluate_one(context, first), evaluate_one(context, second)),
            key=lambda r: r.symbol,
        )
    )


@pytest.mark.parametrize("mode", ["match", "price", "unavailable", "missing"])
def test_every_outcome_binds_contract_rules_and_inputs(mode: str) -> None:
    context, item = fixture()
    context = replace(context, input_sequence_refs=(3, 1))
    item = replace(item, input_sequence_refs=(2, 3))
    if mode == "price":
        item = replace(
            item, current_price=replace(item.current_price, value=Decimal("20"))
        )
    elif mode == "unavailable":
        item = replace(item, prior_close=None)
    results = evaluate_all(context, () if mode == "missing" else (item,))
    result = results[0]
    assert result.contract_sha256 == context.contract.sha256
    assert result.rule_ids == context.contract.rule_ids
    assert result.input_sequence_refs == ((1, 3) if mode == "missing" else (1, 2, 3))
    assert not result.order_authority
