"""Declared synthetic ledgers; these tests never prove a formal study or source."""

from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction as F
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from alpaca_ross_bot.equity_fees import ExecutedFeeLot, daily_fees
from alpaca_ross_bot.paper_performance import (
    BrokerCensus,
    BrokerPosting,
    DayCensus,
    EquitySample,
    Execution,
    FlatCashSnapshot,
    PerformanceDescription,
    PerformanceWindow,
    ScopeKey,
    describe_paper_performance,
)

POLICY = Path(__file__).parents[1] / "evidence/alpaca-equity-fees-20261012.json"
DAY = date(2026, 10, 26)
START = datetime(2026, 10, 26, 13, 30, tzinfo=UTC)
END = START + timedelta(hours=6, minutes=30)
SCOPE = ScopeKey(
    "declared-account",
    "formal-namespace",
    "declared-manifest",
    "declared-study",
    "FORMAL_PAPER",
)
WINDOW = PerformanceWindow(SCOPE, START, END, (DAY,))
FILLS = (
    Execution(
        "buy",
        SCOPE,
        "round-1",
        "AAPL",
        "buy",
        F(10),
        F(10),
        START + timedelta(seconds=1),
        "declared-owned-buy",
    ),
    Execution(
        "sell",
        SCOPE,
        "round-1",
        "AAPL",
        "sell",
        F(10),
        F(11),
        START + timedelta(seconds=2),
        "declared-owned-sell",
    ),
)


def calculate(
    *,
    fills: tuple[Execution, ...] = FILLS,
    postings: tuple[BrokerPosting, ...] = (),
    ending: F = F(510),
    window: PerformanceWindow = WINDOW,
    days: tuple[DayCensus, ...] | None = None,
    broker_census: BrokerCensus | None = None,
    curve: tuple[EquitySample, ...] = (),
    order_ref: str | None = None,
    opening_flat_ref: str | None = "declared-initial-flat",
) -> PerformanceDescription:
    if days is None:
        days = tuple(
            DayCensus(
                day,
                tuple(
                    dict.fromkeys(
                        fill.execution_id
                        for fill in fills
                        if fill.at.astimezone(ZoneInfo("America/New_York")).date()
                        == day
                    )
                ),
                "declared-full-account-day",
            )
            for day in window.expected_fee_days
        )
    if broker_census is None:
        broker_census = BrokerCensus(
            tuple(dict.fromkeys(row.activity_id for row in postings)),
            "declared-scope-attribution",
        )
    return describe_paper_performance(
        window,
        fills,
        postings,
        day_censuses=days,
        broker_census=broker_census,
        opening=FlatCashSnapshot(window.scope, window.start, F(500), opening_flat_ref),
        closing=FlatCashSnapshot(
            window.scope, window.end, ending, "declared-final-flat"
        ),
        fee_policy=POLICY,
        execution_order_ref=order_ref,
        equity_curve=curve,
    )


def posting(identity: str, kind: str, amount: F) -> BrokerPosting:
    return BrokerPosting(
        identity,
        SCOPE,
        kind,
        amount,
        END,
        "declared-identified-attribution:" + identity,
    )


def sample(
    identity: str, at: datetime, equity: F, basis_kind: str = "OBSERVED_ACCOUNT_EQUITY"
) -> EquitySample:
    return EquitySample(
        identity,
        SCOPE,
        at,
        equity,
        "declared-broker-equity-basis",
        "declared-source:" + identity,
        basis_kind,
    )


def test_paper_zero_posted_fees_preserves_raw_gross_and_separate_modelled_net() -> None:
    result = calculate()
    assert result.executions == FILLS and result.broker_postings == ()
    assert result.gross_pnl == result.broker_net_pnl == 10
    assert (
        result.posted_regulatory_cost
        == result.other_posted_cost
        == result.signed_funding
        == 0
    )
    assert result.modelled_regulatory_cost == F(1, 50)
    assert result.adjusted_net_pnl == F(499, 50)
    assert len(result.roundtrips) == 1 and result.roundtrips[0].gross == 10
    assert result.roundtrips[0].execution_ids == ("buy", "sell")
    assert result.issues == () and result.fee_document_ref
    assert not result.source_evidence_verified and not result.runtime_authorized
    assert not any(
        "verdict" in field.name or "win" in field.name for field in fields(result)
    )
    assert not any("net" in field.name for field in fields(result.roundtrips[0]))


def test_model_replacement_preserves_other_costs_without_double_charging() -> None:
    rows = (
        posting("sec", "SEC_FEE", -F(3, 100)),
        posting("cat", "CAT_FEE", -F(2, 100)),
        posting("other", "OTHER_COST", -F(1, 2)),
    )
    result = calculate(postings=rows, ending=F(10189, 20))
    assert result.gross_pnl == 10 and result.broker_net_pnl == F(189, 20)
    assert result.posted_regulatory_cost == F(1, 20) and result.other_posted_cost == F(
        1, 2
    )
    assert result.adjusted_net_pnl == F(237, 25)
    assert result.broker_postings == rows
    assert (
        result.adjusted_net_pnl
        == result.broker_net_pnl + result.posted_regulatory_cost - F(1, 50)
    )


def test_explicit_attributable_fee_refund_is_signed_and_still_replaced_once() -> None:
    credit = posting("refund", "SEC_FEE", F(1, 100))
    result = calculate(postings=(credit,), ending=F(51001, 100))
    assert result.posted_regulatory_cost == -F(1, 100)
    assert result.broker_net_pnl == F(1001, 100) and result.adjusted_net_pnl == F(
        499, 50
    )
    assert result.broker_postings == (credit,)


@pytest.mark.parametrize("amount", [F(100), -F(100)])
def test_funding_is_removed_from_pnl_but_cannot_accept_the_fixed_capital_study(
    amount: F,
) -> None:
    result = calculate(
        postings=(posting("fund", "FUNDING", amount),), ending=F(510) + amount
    )
    assert result.signed_funding == amount and result.broker_cash_delta == 10 + amount
    assert result.broker_net_pnl == 10 and result.adjusted_net_pnl == F(499, 50)
    assert "FUNDING_REQUIRES_STUDY_REVIEW" in result.issues
    assert not result.source_evidence_verified and not result.runtime_authorized


@pytest.mark.parametrize("kind", ["FEE", "UNKNOWN", "CSD", "DIV"])
def test_generic_or_unknown_broker_activity_never_becomes_a_guessed_regulatory_fee(
    kind: str,
) -> None:
    result = calculate(
        postings=(posting("unknown", kind, -F(1, 10)),), ending=F(5099, 10)
    )
    assert result.broker_cash_delta == F(99, 10)
    assert result.posted_regulatory_cost is None and result.signed_funding is None
    assert result.broker_net_pnl is None and result.adjusted_net_pnl is None
    assert "BROKER_POSTING_CLASSIFICATION_OR_ATTRIBUTION_UNKNOWN" in result.issues


@pytest.mark.parametrize("kind", ["SEC_FEE", "OTHER_COST", "FUNDING"])
def test_missing_attribution_blocks_fee_credit_cost_or_funding_claims(
    kind: str,
) -> None:
    row = replace(posting("unattributed", kind, F(1)), attribution_ref=None)
    result = calculate(postings=(row,), ending=F(511))
    assert result.gross_pnl is None and result.adjusted_net_pnl is None
    assert result.broker_postings == (row,)


@pytest.mark.parametrize(
    "census",
    [
        BrokerCensus(None, "ref"),
        BrokerCensus((), None),
        BrokerCensus(("missing-post",), "ref"),
    ],
)
def test_unknown_or_mismatched_posting_census_does_not_mean_zero_costs(
    census: BrokerCensus,
) -> None:
    result = calculate(broker_census=census)
    assert result.posted_regulatory_cost is None and result.broker_net_pnl is None
    assert "COMPLETE_BROKER_POSTING_CENSUS_MISSING" in result.issues


@pytest.mark.parametrize(
    "census",
    [
        DayCensus(DAY, None, "ref"),
        DayCensus(DAY, (), "ref"),
        DayCensus(DAY, ("buy", "sell"), None),
    ],
)
def test_incomplete_account_day_execution_census_blocks_complete_model_fee_claim(
    census: DayCensus,
) -> None:
    result = calculate(days=(census,))
    assert result.modelled_regulatory_cost is None and result.adjusted_net_pnl is None
    assert "COMPLETE_ACCOUNT_DAY_EXECUTION_CENSUS_MISSING" in result.issues


def test_fee_type_ceiling_is_daily_not_per_execution_or_per_roundtrip() -> None:
    fills = tuple(
        replace(
            FILLS[0],
            execution_id="b" + str(i),
            quantity=F(1),
            at=START + timedelta(seconds=i + 1),
        )
        for i in range(10)
    ) + tuple(
        replace(
            FILLS[1],
            execution_id="s" + str(i),
            quantity=F(1),
            at=START + timedelta(seconds=i + 11),
        )
        for i in range(10)
    )
    result = calculate(fills=fills)
    expected = daily_fees(
        POLICY,
        tuple(
            ExecutedFeeLot(fill.execution_id, fill.side, fill.quantity, fill.price)
            for fill in fills
        ),
    )
    assert result.modelled_regulatory_cost == expected.total == F(1, 50)
    assert result.modelled_days[0].fees_on_declared_census == expected
    assert len(result.executions) == 20 and len(result.roundtrips) == 1


def test_fee_aggregation_keeps_new_york_account_days_separate() -> None:
    next_day = DAY + timedelta(days=1)
    window = replace(
        WINDOW, end=END + timedelta(days=1), expected_fee_days=(DAY, next_day)
    )
    fills = FILLS + tuple(
        replace(
            fill,
            execution_id=fill.execution_id + "2",
            roundtrip_id="round-2",
            at=fill.at + timedelta(days=1),
        )
        for fill in FILLS
    )
    result = calculate(window=window, fills=fills, ending=F(520))
    assert result.gross_pnl == 20 and result.modelled_regulatory_cost == F(1, 25)
    assert len(result.modelled_days) == len(result.roundtrips) == 2
    assert result.adjusted_net_pnl == F(499, 25)


def test_duplicate_execution_and_posting_identities_are_exactly_idempotent() -> None:
    row = posting("sec", "SEC_FEE", -F(1, 100))
    result = calculate(
        fills=(FILLS[0], FILLS[0], FILLS[1], FILLS[1]),
        postings=(row, row),
        ending=F(50999, 100),
    )
    assert result.executions == FILLS and result.broker_postings == (row,)
    assert result.gross_pnl == 10 and result.adjusted_net_pnl == F(499, 50)
    with pytest.raises(ValueError, match="execution identity conflict"):
        calculate(fills=(FILLS[0], replace(FILLS[0], price=F(9)), FILLS[1]))
    with pytest.raises(ValueError, match="posting identity conflict"):
        calculate(postings=(row, replace(row, signed_cash=-F(2, 100))))


@pytest.mark.parametrize("origin", ["ENGINEERING_TEST", "RESEARCH_EXPOSED"])
def test_nonformal_scopes_cannot_be_reported_as_formal_performance(origin: str) -> None:
    scope = replace(SCOPE, origin=origin)
    with pytest.raises(ValueError, match="formal-paper"):
        calculate(window=replace(WINDOW, scope=scope))


@pytest.mark.parametrize(
    "scope",
    [
        replace(SCOPE, account_ref="foreign"),
        replace(SCOPE, state_namespace="engineering"),
        replace(SCOPE, manifest_ref="different"),
        replace(SCOPE, origin="ENGINEERING_TEST"),
    ],
)
def test_foreign_accounts_namespaces_releases_and_engineering_rows_cannot_mix(
    scope: ScopeKey,
) -> None:
    with pytest.raises(ValueError, match="scopes cannot mix"):
        calculate(fills=(replace(FILLS[0], scope=scope), FILLS[1]))
    with pytest.raises(ValueError, match="scopes cannot mix"):
        calculate(
            postings=(replace(posting("foreign", "OTHER_COST", F(0)), scope=scope),)
        )


@pytest.mark.parametrize(
    "fault",
    [
        "short_prefix",
        "open_position",
        "multiple_episodes",
        "mixed_symbol",
        "ownership_unknown",
    ],
)
def test_roundtrip_assignment_must_prove_one_declared_owned_long_to_flat_episode(
    fault: str,
) -> None:
    fills: tuple[Execution, ...]
    if fault == "short_prefix":
        fills = (replace(FILLS[1], at=FILLS[0].at), replace(FILLS[0], at=FILLS[1].at))
    elif fault == "open_position":
        fills = FILLS[:1]
    elif fault == "multiple_episodes":
        fills = FILLS + tuple(
            replace(
                fill,
                execution_id=fill.execution_id + "2",
                at=fill.at + timedelta(seconds=5),
            )
            for fill in FILLS
        )
    elif fault == "mixed_symbol":
        fills = (FILLS[0], replace(FILLS[1], symbol="OTHER"))
    else:
        fills = (replace(FILLS[0], ownership_ref=None), FILLS[1])
    result = calculate(fills=fills)
    assert result.gross_pnl is None and result.broker_net_pnl is None
    assert result.issues


def test_simultaneous_opposite_fills_need_declared_order_not_order_id_sorting() -> None:
    fills = (FILLS[0], replace(FILLS[1], at=FILLS[0].at))
    unknown = calculate(fills=fills)
    assert (
        unknown.broker_net_pnl is None
        and "OPPOSITE_SIDE_ORDERING_UNKNOWN" in unknown.issues
    )
    declared = calculate(fills=fills, order_ref="declared-source-order")
    assert declared.broker_net_pnl == 10 and not declared.source_evidence_verified


def test_exact_fractional_partial_fills_of_a_whole_order_are_supported() -> None:
    fills = (
        replace(FILLS[0], quantity=F(1, 2)),
        replace(
            FILLS[0],
            execution_id="buy-rest",
            quantity=F(19, 2),
            at=START + timedelta(seconds=2),
        ),
        replace(FILLS[1], quantity=F(1, 4), at=START + timedelta(seconds=3)),
        replace(
            FILLS[1],
            execution_id="sell-rest",
            quantity=F(39, 4),
            at=START + timedelta(seconds=4),
        ),
    )
    result = calculate(fills=fills)
    assert result.gross_pnl == result.broker_net_pnl == 10
    assert len(result.roundtrips) == 1 and result.adjusted_net_pnl == F(499, 50)


def test_nonpositive_and_nonexact_execution_values_are_refused() -> None:
    with pytest.raises(ValueError, match="exact Fraction"):
        calculate(fills=(replace(FILLS[0], quantity=F(0)), FILLS[1]))
    with pytest.raises(ValueError, match="exact Fraction"):
        calculate(fills=(replace(FILLS[0], price=cast(F, 10.0)), FILLS[1]))


def test_cash_observations_cannot_silently_bracket_unobserved_boundary_trades() -> None:
    with pytest.raises(ValueError, match="exact declared window endpoints"):
        describe_paper_performance(
            WINDOW,
            FILLS,
            (),
            day_censuses=(DayCensus(DAY, ("buy", "sell"), "day"),),
            broker_census=BrokerCensus((), "zero-postings"),
            opening=FlatCashSnapshot(
                SCOPE, START - timedelta(seconds=1), F(500), "flat"
            ),
            closing=FlatCashSnapshot(SCOPE, END + timedelta(seconds=1), F(510), "flat"),
            fee_policy=POLICY,
        )


def test_cash_conservation_mismatch_is_not_imputed_to_fees_or_funding() -> None:
    result = calculate(ending=F(509))
    assert result.broker_cash_delta == 9 and result.gross_pnl is None
    assert result.broker_net_pnl is None and result.adjusted_net_pnl is None
    assert result.signed_funding == result.posted_regulatory_cost == 0
    assert "DECLARED_CASH_CONSERVATION_MISMATCH" in result.issues


def test_missing_flat_boundary_cannot_be_repaired_by_balanced_fill_totals() -> None:
    result = calculate(opening_flat_ref=None)
    assert result.gross_pnl is None and result.adjusted_net_pnl is None
    assert "FLAT_CASH_BOUNDARY_UNQUALIFIED" in result.issues


def test_no_equity_curve_is_unknown_not_cash_principal_drawdown_or_zero() -> None:
    result = calculate()
    assert (
        result.drawdown.maximum_absolute is None
        and result.drawdown.maximum_fraction is None
    )
    assert (
        result.drawdown.sample_count == 0
        and not result.drawdown.covers_declared_endpoints
    )


def test_sampled_account_equity_drawdown_is_exact_and_separate_from_trade_returns() -> (
    None
):
    curve = (
        sample("start", START, F(500)),
        sample("low", START + timedelta(hours=1), F(400)),
        sample("end", END, F(510)),
    )
    result = calculate(curve=curve)
    assert (
        result.drawdown.maximum_absolute == 100
        and result.drawdown.maximum_fraction == F(1, 5)
    )
    assert (
        result.drawdown.sample_count == 3
        and result.drawdown.start == START
        and result.drawdown.end == END
    )
    assert (
        result.drawdown.covers_declared_endpoints and "SAMPLED" in result.drawdown.scope
    )
    assert result.broker_net_pnl == 10 and result.roundtrips[0].gross == 10


def test_sparse_or_partial_curve_claims_only_observed_samples() -> None:
    sparse = calculate(
        curve=(sample("start", START, F(500)), sample("end", END, F(510)))
    )
    assert sparse.drawdown.maximum_absolute == 0 and sparse.drawdown.sample_count == 2
    assert "NOT_CONTINUOUS_MAXIMUM" in sparse.drawdown.scope
    partial = calculate(
        curve=(
            sample("p1", START + timedelta(minutes=1), F(500)),
            sample("p2", END - timedelta(minutes=1), F(490)),
        )
    )
    assert (
        partial.drawdown.maximum_absolute == 10
        and not partial.drawdown.covers_declared_endpoints
    )
    one = calculate(curve=(sample("only", START, F(500)),))
    assert one.drawdown.maximum_absolute is None


def test_unknown_strategy_return_basis_does_not_become_account_equity_drawdown() -> (
    None
):
    result = calculate(
        curve=(
            sample("start", START, F(500), "STRATEGY_RETURN_UNKNOWN"),
            sample("end", END, F(490), "STRATEGY_RETURN_UNKNOWN"),
        )
    )
    assert result.drawdown.maximum_absolute is None
    assert "OBSERVED_ACCOUNT_EQUITY_BASIS_UNAVAILABLE" in result.drawdown.issues


def test_funding_does_not_silently_rebase_the_supplied_account_equity_curve() -> None:
    curve = (
        sample("start", START, F(500)),
        sample("peak", START + timedelta(hours=1), F(700)),
        sample("end", END, F(610)),
    )
    result = calculate(
        postings=(posting("funding", "FUNDING", F(100)),), ending=F(610), curve=curve
    )
    assert result.broker_net_pnl == 10 and result.drawdown.maximum_absolute == 90
    assert result.drawdown.maximum_fraction == F(9, 70)
    assert "FUNDING_REQUIRES_STUDY_REVIEW" in result.issues


@pytest.mark.parametrize(
    "fault", ["identity", "basis", "order", "same_instant", "scope"]
)
def test_equity_curve_conflicts_or_scope_changes_are_refused(fault: str) -> None:
    first = sample("first", START, F(500))
    last = sample("last", END, F(510))
    curve: tuple[EquitySample, ...]
    if fault == "identity":
        curve = (first, replace(first, marked_equity=F(499)), last)
    elif fault == "basis":
        curve = (first, replace(last, basis_ref="different-mark-basis"))
    elif fault == "order":
        curve = (last, first)
    elif fault == "same_instant":
        curve = (first, replace(last, at=START))
    else:
        curve = (first, replace(last, scope=replace(SCOPE, account_ref="foreign")))
    with pytest.raises(ValueError):
        calculate(curve=curve)


def test_offsetting_deposit_and_withdrawal_still_require_study_review() -> None:
    rows = (
        posting("deposit", "FUNDING", F(100)),
        posting("withdrawal", "FUNDING", -F(100)),
    )
    result = calculate(postings=rows)
    assert result.signed_funding == 0 and result.broker_net_pnl == 10
    assert "FUNDING_REQUIRES_STUDY_REVIEW" in result.issues


def test_equal_equity_at_one_instant_cannot_hide_incompatible_basis_metadata() -> None:
    first = sample("observed", START, F(500))
    unknown = replace(
        first, sample_id="unknown-basis", basis_kind="STRATEGY_RETURN_UNKNOWN"
    )
    with pytest.raises(ValueError, match="valuation bases"):
        calculate(curve=(first, unknown, sample("end", END, F(510))))
