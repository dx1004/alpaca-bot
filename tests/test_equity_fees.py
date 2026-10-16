from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from alpaca_ross_bot.equity_fees import (
    CAT,
    SEC,
    ExecutedFeeLot,
    daily_fees,
    planned_charge_bounds,
    verify_fee_policy,
)

POLICY = (
    Path(__file__).resolve().parents[1] / "evidence/alpaca-equity-fees-20261012.json"
)


def test_daily_rounding_is_per_type_not_per_fill_or_order() -> None:
    buys = tuple(
        ExecutedFeeLot(f"b{i}", "buy", Fraction(1), Fraction(10)) for i in range(100)
    )
    sells = tuple(
        ExecutedFeeLot(f"s{i}", "sell", Fraction(1), Fraction(10)) for i in range(100)
    )
    result = daily_fees(POLICY, buys + sells)
    assert result.sec == Fraction(3, 100)
    assert result.cat == Fraction(1, 100)
    assert result.taf == 0 and result.total == Fraction(4, 100)
    assert result.runtime_authorized is False


def test_exact_fractional_execution_quantity_and_idempotent_duplicate() -> None:
    fill = ExecutedFeeLot("one", "sell", Fraction(1, 2), Fraction(10))
    result = daily_fees(POLICY, (fill, fill))
    assert result.sec == result.cat == Fraction(1, 100)
    with pytest.raises(ValueError, match="identity conflict"):
        daily_fees(POLICY, (fill, replace(fill, price=Fraction(11))))


def test_fee_policy_and_empty_census_are_explicit(tmp_path: Path) -> None:
    assert len(verify_fee_policy(POLICY)) == 64
    assert daily_fees(POLICY, ()).total == 0
    changed = tmp_path / "changed.json"
    changed.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(ValueError, match="version"):
        daily_fees(changed, ())


def test_planned_bounds_are_exact_even_under_low_decimal_precision() -> None:
    with localcontext() as ctx:
        ctx.prec = 1
        entry, exit_fee = planned_charge_bounds(POLICY, 100, Fraction(10))
        assert (entry, exit_fee) == (Decimal("0.01"), Decimal("0.04"))


def test_increment_bound_for_arbitrary_existing_daily_fee_fractions() -> None:
    def ceil_cent(value: Fraction) -> Fraction:
        return Fraction((value * 100).__ceil__(), 100)

    for q in range(1, 101):
        entry, stop = planned_charge_bounds(POLICY, q, Fraction(12345, 100))
        for a in (Fraction(0), Fraction(1, 1000), Fraction(999, 100000)):
            assert ceil_cent(a + CAT * q) - ceil_cent(a) <= Fraction(entry)
            increment = ceil_cent(a + SEC * q * Fraction(12345, 100)) - ceil_cent(a)
            increment += ceil_cent(a + CAT * q) - ceil_cent(a)
            assert increment <= Fraction(stop)


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_invalid_order_quantity_is_rejected(quantity: int) -> None:
    with pytest.raises(ValueError):
        planned_charge_bounds(POLICY, quantity, Fraction(10))
