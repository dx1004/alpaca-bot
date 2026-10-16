from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from alpaca_ross_bot.strategy_features import ema


def test_entry_risk_and_durable_attempt_integrate_without_broker_authority(
    tmp_path: Path,
) -> None:
    from alpaca_ross_bot.entry_strategy import (
        EntryState,
        InputHealth,
        Quote,
        Trade,
        evaluate_entry,
    )
    from alpaca_ross_bot.journal import Journal
    from alpaca_ross_bot.paper_order_contract import paper_account_ref
    from alpaca_ross_bot.strategy_attempts import ResearchAttempts
    from alpaca_ross_bot.strategy_features import (
        Bar,
        Session,
        Slot,
        Trend,
        latest_entry_bar,
    )
    from alpaca_ross_bot.strategy_risk import (
        DailyRiskState,
        FeeBound,
        FeeBounds,
        RiskAccount,
        RiskLedger,
        size_entry,
    )

    opened = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
    day = date(2026, 10, 16)
    session = Session(
        day, opened, opened + timedelta(hours=6, minutes=30), opened, "test"
    )
    trend = Trend(
        "TEST",
        session,
        Fraction(10),
        Fraction(9),
        Fraction(8),
        Fraction(10),
        Fraction(9),
        ("offline-fixture",),
    )
    state = EntryState("TEST", day)
    minute = Bar(
        "TEST",
        Slot(opened, opened + timedelta(minutes=1)),
        Fraction(11),
        Fraction(11),
        Fraction(10),
        Fraction(10),
        2,
        opened + timedelta(minutes=1),
        0,
        "provider-minute-fixture",
    )
    for identity, seconds, price in (
        ("first", 0, "11"),
        ("arm", 30, "10"),
        ("cross", 61, "11.01"),
    ):
        at = opened + timedelta(seconds=seconds)
        trade = Trade(
            identity, "TEST", at, at, Fraction(price), "test-conditions", True
        )
        quote = Quote(
            "TEST",
            at,
            at,
            Fraction(price),
            Fraction(price),
            1,
            1,
            "test-conditions",
            True,
        )
        health = InputHealth(
            "offline-test-only",
            "test-conditions",
            "first",
            True,
            True,
            True,
            True,
            False,
            False,
            False,
            Fraction(11 if seconds == 0 else 10),
        )
        prior = (
            None
            if seconds < 60
            else latest_entry_bar("TEST", session, (minute,), at, at)
        )
        decision = evaluate_entry(state, trend, trade, quote, prior, health, at)
        state = decision.state
    assert decision.signal is not None and not state.attempted
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    raw = b'{"id":"11111111-1111-4111-8111-111111111111"}'
    account_ref = paper_account_ref(raw)
    journal.record_paper_account_verification(account_ref, raw, opened)
    account = RiskAccount(
        Decimal("500"),
        Decimal("500"),
        day,
        account_ref,
        equity_verified=True,
        funds_verified=True,
        account_eligible=True,
        symbol_tradable=True,
        one_x_verified=True,
        baseline_verified=True,
    )
    ledger = RiskLedger(
        account_ref,
        day,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        verified=True,
        marks_verified=True,
        cashflows_verified=True,
    )
    daily = DailyRiskState(day, account_ref, Decimal("500"))
    fees = FeeBounds(
        tuple(FeeBound(q, Decimal("0.01"), Decimal("0.01")) for q in range(1, 46)),
        "explicit-test-fees",
        True,
    )
    sized = size_entry(
        account,
        ledger,
        fees,
        minute.high,
        decision.signal.low_since_open,
        trade.price,
        quote.ask,
        daily_state=daily,
    )
    assert sized.quantity == 2 and sized.limit == Fraction(1101, 100)
    consumed = ResearchAttempts(journal, Path(__file__).resolve().parents[1]).reserve(
        decision,
        sized,
        reserved_at=at,
        daily_state=daily,
    )
    assert consumed.attempted and consumed.durable_intent_ref is not None
    assert not journal.has_order_send_attempt(consumed.durable_intent_ref)
    assert not decision.order_authority and not sized.runtime_authorized
    journal.close()


@pytest.mark.parametrize("period", [9, 21, 50])
def test_ema_matches_independent_closed_form_under_low_decimal_precision(
    period: int,
) -> None:
    closes = tuple(Fraction(1000 + (i * 37) % 211, 100) for i in range(100))
    alpha = Fraction(2, period + 1)
    beta = 1 - alpha
    # Closed form is deliberately independent of the production recurrence.
    seed = sum(closes[:period], Fraction(0)) / period
    expected = seed * beta ** (100 - period) + sum(
        (alpha * closes[i] * beta ** (99 - i) for i in range(period, 100)),
        Fraction(0),
    )
    with localcontext() as context:
        context.prec = 1
        assert ema(closes, period) == expected
