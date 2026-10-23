"""Precision integration only; fixtures declare scopes, never source acceptance."""

from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction

import pytest

from alpaca_ross_bot.paper_performance import BrokerPosting, DayCensus
from test_paper_performance import DAY, END, FILLS, SCOPE, START, WINDOW, calculate


def stamp(base: datetime, nanos: int, *, offset: bool = False) -> str:
    suffix = "+00:00" if offset else "Z"
    return base.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}" + suffix


def test_execution_one_nanosecond_after_window_end_is_not_truncated_inside() -> None:
    fills = (FILLS[0], replace(FILLS[1], at=END, original_timestamp=stamp(END, 1)))
    with pytest.raises(ValueError, match="order or declared window"):
        calculate(fills=fills)


def test_global_order_across_symbols_uses_exact_instants_before_group_accounting() -> (
    None
):
    base = START + timedelta(seconds=1)
    fills = (
        replace(FILLS[0], at=base, original_timestamp=stamp(base, 2)),
        replace(
            FILLS[0],
            execution_id="msft-buy",
            roundtrip_id="round-msft",
            symbol="MSFT",
            at=base,
            original_timestamp=stamp(base, 1),
        ),
        FILLS[1],
        replace(
            FILLS[1],
            execution_id="msft-sell",
            roundtrip_id="round-msft",
            symbol="MSFT",
            at=START + timedelta(seconds=3),
        ),
    )
    with pytest.raises(ValueError, match="execution order"):
        calculate(fills=fills, ending=Fraction(520))


@pytest.mark.parametrize("same_instant", [False, True])
def test_opposite_sides_use_exact_ties_not_python_datetime_or_timestamp_spelling(
    same_instant: bool,
) -> None:
    base = START + timedelta(seconds=1)
    buy = replace(FILLS[0], at=base, original_timestamp=stamp(base, 1))
    sell = replace(
        FILLS[1],
        at=base,
        original_timestamp=stamp(base, 1 if same_instant else 2, offset=True),
    )
    result = calculate(fills=(buy, sell))
    assert result.executions == (buy, sell)
    assert (
        result.executions[0].original_timestamp
        != result.executions[1].original_timestamp
    )
    if same_instant:
        assert "OPPOSITE_SIDE_ORDERING_UNKNOWN" in result.issues
        assert result.broker_net_pnl is None and result.adjusted_net_pnl is None
    else:
        assert "OPPOSITE_SIDE_ORDERING_UNKNOWN" not in result.issues
        assert result.gross_pnl == result.broker_net_pnl == 10
    assert result.source_evidence_verified is result.runtime_authorized is False


def test_exact_timestamp_cannot_relabel_engineering_origin_as_formal() -> None:
    engineering = replace(SCOPE, origin="ENGINEERING_TEST")
    fills = tuple(
        replace(fill, scope=engineering, original_timestamp=stamp(fill.at, 1))
        for fill in FILLS
    )
    with pytest.raises(ValueError, match="formal-paper scope"):
        calculate(fills=fills, window=replace(WINDOW, scope=engineering))


def test_precision_does_not_resolve_unknown_fee_attribution_or_day_census() -> None:
    base = START + timedelta(seconds=1)
    fills = (
        replace(FILLS[0], at=base, original_timestamp=stamp(base, 1)),
        replace(FILLS[1], at=base, original_timestamp=stamp(base, 2)),
    )
    posted = END - timedelta(seconds=1)
    unknown_fee = BrokerPosting(
        "generic-fee",
        SCOPE,
        "FEE",
        Fraction(-1, 10),
        posted,
        "declared-unclassified-posting",
        original_timestamp=stamp(posted, 3),
    )
    result = calculate(
        fills=fills,
        postings=(unknown_fee,),
        ending=Fraction(5099, 10),
        days=(DayCensus(DAY, None, None),),
    )
    assert len(result.roundtrips) == 1 and result.roundtrips[0].gross == 10
    assert "BROKER_POSTING_CLASSIFICATION_OR_ATTRIBUTION_UNKNOWN" in result.issues
    assert "COMPLETE_ACCOUNT_DAY_EXECUTION_CENSUS_MISSING" in result.issues
    assert "OPPOSITE_SIDE_ORDERING_UNKNOWN" not in result.issues
    assert result.broker_postings == (unknown_fee,)
    assert result.posted_regulatory_cost is None
    assert result.modelled_regulatory_cost is None
    assert result.adjusted_net_pnl is None
    assert result.source_evidence_verified is result.runtime_authorized is False
