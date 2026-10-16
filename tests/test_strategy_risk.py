from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from alpaca_ross_bot.strategy_risk import (
    COVERAGE,
    VERSION,
    DailyRiskState,
    FeeBound,
    FeeBounds,
    Reservation,
    RiskAccount,
    RiskLedger,
    SizeDecision,
    capacity,
    closing_disposition,
    size_entry,
    target_price,
    update_daily_stop,
)

D = Decimal
DAY = date(2026, 10, 16)


def account() -> RiskAccount:
    return RiskAccount(
        D(500),
        D(500),
        DAY,
        "synthetic",
        equity_verified=True,
        funds_verified=True,
        account_eligible=True,
        symbol_tradable=True,
        one_x_verified=True,
        baseline_verified=True,
    )


def ledger() -> RiskLedger:
    return RiskLedger(
        "synthetic",
        DAY,
        D(0),
        D(0),
        D(0),
        verified=True,
        marks_verified=True,
        cashflows_verified=True,
    )


def fees(entry: str = "0", exit_fee: str = "0") -> FeeBounds:
    return FeeBounds(
        tuple(FeeBound(q, D(entry), D(exit_fee)) for q in range(1, 51)),
        "synthetic-complete-bounds",
        True,
    )


def size(
    a: RiskAccount | None = None,
    book: RiskLedger | None = None,
    f: FeeBounds | None = None,
) -> SizeDecision:
    return size_entry(
        a or account(),
        book or ledger(),
        f or fees(),
        D("9.99"),
        D("9.5"),
        D(10),
        D(10),
        daily_state=DailyRiskState(
            (a or account()).session,
            (a or account()).account_ref,
            (a or account()).equity,
        ),
    )


def test_exact_boundary_maximality_and_no_runtime_authority() -> None:
    result = size()
    assert (result.quantity, result.limit, result.stop, result.capacity) == (
        5,
        Fraction(10),
        Fraction(19, 2),
        Fraction(5),
    )
    assert result.version == VERSION and result.coverage == COVERAGE
    assert not result.runtime_authorized
    assert 6 * (result.limit - result.stop) > Fraction(5, 2)
    assert size(f=fees("0.01", "0.01")).quantity == 4
    assert size(a=replace(account(), spendable=D("19.99"))).quantity == 1


def test_nonmonotone_fees_enumerate_all_quantities() -> None:
    rows = tuple(FeeBound(q, D(100) if q == 4 else D(0), D(0)) for q in range(1, 51))
    assert size(f=FeeBounds(rows, "verified-synthetic", True)).quantity == 5


@pytest.mark.parametrize(
    "field",
    [
        "funds_verified",
        "account_eligible",
        "symbol_tradable",
        "one_x_verified",
        "baseline_verified",
    ],
)
def test_account_gates(field: str) -> None:
    changed = replace(account(), **{field: False})  # type: ignore[arg-type]
    assert size(a=changed).reason == "ACCOUNT_UNAVAILABLE"


def test_fees_unavailable_or_incomplete() -> None:
    assert size(f=replace(fees(), verified=False)).reason == "FEES_UNAVAILABLE"
    assert size(f=replace(fees(), rows=fees().rows[:-1])).reason == "FEES_INCOMPLETE"
    with pytest.raises(ValueError):
        size(f=replace(fees(), rows=(FeeBound(1, D(-1), D(0)),)))


@pytest.mark.parametrize("field", ["verified", "marks_verified", "cashflows_verified"])
def test_unknown_ledger_blocks(field: str) -> None:
    with pytest.raises(ValueError):
        changed = replace(ledger(), **{field: False})  # type: ignore[arg-type]
        size(book=changed)


def reservation() -> Reservation:
    return Reservation(
        "one", 10, 6, 4, D(10), D("9.5"), D("9.8"), D("0.1"), D("0.2"), D("0.3"), True
    )


def test_partial_partition_reserves_no_double_count() -> None:
    book = replace(
        ledger(),
        unrealized_bid=D("-0.8"),
        accrued_charges=D("0.1"),
        reservations=(reservation(),),
    )
    # 5-.9 - (6*.5+.1+.2) - (4*.3+.3), floored at zero.
    assert capacity(account(), book) == 0
    book = replace(
        book,
        reservations=(
            replace(
                reservation(),
                pending_quantity=0,
                pending_entry_fees=D(0),
                pending_stop_fees=D(0),
            ),
        ),
    )
    assert capacity(account(), book) == Fraction(26, 10)
    assert size(book=book).reason == "SLOT_OCCUPIED"
    with pytest.raises(ValueError):
        capacity(
            account(),
            replace(book, reservations=(replace(reservation(), open_quantity=5),)),
        )
    with pytest.raises(ValueError):
        capacity(account(), replace(book, reservations=(reservation(), reservation())))


def test_capacity_caps_gains_and_bid_below_stop() -> None:
    assert capacity(account(), replace(ledger(), realized=D(100))) == 5
    assert capacity(account(), replace(ledger(), realized=D(-10))) == 0
    r = replace(
        reservation(),
        pending_quantity=0,
        bid=D(9),
        pending_entry_fees=D(0),
        pending_stop_fees=D(0),
    )
    assert capacity(account(), replace(ledger(), reservations=(r,))) == Fraction(47, 10)


def test_rounding_exact_independent_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 2
        result = size_entry(
            account(),
            ledger(),
            fees(),
            D("9.99999"),
            D("9.50999"),
            D(10),
            D(10),
            daily_state=DailyRiskState(DAY, "synthetic", D(500)),
        )
        assert result.limit == 10 and result.stop == Fraction(19, 2)
        result2 = size_entry(
            account(),
            ledger(),
            fees(),
            D(10),
            D("9.50999"),
            D("10.01"),
            D("10.01"),
            daily_state=DailyRiskState(DAY, "synthetic", D(500)),
        )
        assert result2.limit == Fraction(1001, 100)
        assert target_price(
            D("10.001"), D("9.5"), entry_terminal=True, fills_reconciled=True
        ).target == Fraction(1101, 100)


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), True, 1.0, "10"])
def test_strict_numeric_inputs(bad: object) -> None:
    with pytest.raises(ValueError):
        size_entry(
            account(),
            ledger(),
            fees(),
            bad,  # type: ignore[arg-type]
            D(9),
            D(10),
            D(10),
            daily_state=DailyRiskState(DAY, "synthetic", D(500)),
        )


def test_no_chase_topology_and_whole_quantity() -> None:
    assert (
        size_entry(
            account(),
            ledger(),
            fees(),
            D("9.99"),
            D(10),
            D(10),
            D(10),
            daily_state=DailyRiskState(DAY, "synthetic", D(500)),
        ).reason
        == "INVALID_STOP_TOPOLOGY"
    )
    assert (
        size_entry(
            account(),
            ledger(),
            fees(),
            D("9.99"),
            D(9),
            D("10.001"),
            D(10),
            daily_state=DailyRiskState(DAY, "synthetic", D(500)),
        ).reason
        == "NO_CHASE"
    )
    with pytest.raises(ValueError):
        size(f=replace(fees(), rows=(FeeBound(True, D(0), D(0)),)))


def test_target_terminal_reconciliation_and_incident() -> None:
    assert (
        target_price(D(10), D(9), entry_terminal=False, fills_reconciled=True).target
        is None
    )
    result = target_price(D(9), D(9), entry_terminal=True, fills_reconciled=True)
    assert result.incident_required and result.target is None


def update(state: DailyRiskState, net: str, identity: str) -> DailyRiskState:
    return update_daily_stop(
        state,
        account(),
        ledger(),
        reconciled_flat=True,
        no_orphan_orders=True,
        roundtrip_id=identity,
        roundtrip_net=D(net),
        roundtrip_reconciled=True,
    )


def test_streak_zero_positive_and_latch() -> None:
    state = update(DailyRiskState(DAY, "synthetic", D(500)), "-1", "a")
    state = update(state, "0", "b")
    assert state.losing_streak == 1
    state = update(state, "1", "c")
    assert state.losing_streak == 0
    for i in range(3):
        state = update(state, "-1", str(i))
    assert state.latched
    assert update(state, "100", "win").latched
    with pytest.raises(ValueError):
        update(state, "-1", "0")


def test_daily_threshold_and_guarded_reset() -> None:
    state = update_daily_stop(
        DailyRiskState(DAY, "synthetic", D(500)),
        account(),
        replace(ledger(), realized=D("-4.99"), accrued_charges=D(".01")),
        reconciled_flat=True,
        no_orphan_orders=True,
    )
    assert state.latched
    tomorrow = DAY + timedelta(days=1)
    a, book = replace(account(), session=tomorrow), replace(ledger(), session=tomorrow)
    with pytest.raises(ValueError):
        update_daily_stop(state, a, book, reconciled_flat=False, no_orphan_orders=True)
    assert not update_daily_stop(
        state, a, book, reconciled_flat=True, no_orphan_orders=True
    ).latched


def test_close_threshold_keeps_protective_exits() -> None:
    close = datetime(2026, 10, 16, 20, tzinfo=UTC)
    before = closing_disposition(
        close - timedelta(minutes=5, microseconds=1), close, daily_stop_latched=True
    )
    assert not before.closing_due and before.cancel_unfilled_entries
    result = closing_disposition(
        close - timedelta(minutes=5), close, daily_stop_latched=False
    )
    assert result.closing_due and result.require_verified_close_path
    assert result.preserve_protective_exits and not result.runtime_authorized


def test_every_small_account_quantity_is_maximal() -> None:
    for cents in range(1, 101):
        funds = D(cents) / D(10)
        a = replace(account(), spendable=funds)
        schedule = FeeBounds(
            tuple(
                FeeBound(q, D(q % 3) / D(100), D((q + 1) % 4) / D(100))
                for q in range(1, 101)
            ),
            "oracle",
            True,
        )
        result = size_entry(
            a,
            ledger(),
            schedule,
            D(".99"),
            D(".50"),
            D(1),
            D(1),
            daily_state=DailyRiskState(
                (a or account()).session,
                (a or account()).account_ref,
                (a or account()).equity,
            ),
        )
        feasible = [
            q
            for q in range(1, cents // 10 + 1)
            if Fraction(q) + Fraction(schedule.rows[q - 1].entry) <= Fraction(funds)
            and Fraction(q, 2)
            + Fraction(schedule.rows[q - 1].entry)
            + Fraction(schedule.rows[q - 1].stop_exit)
            <= Fraction(5, 2)
        ]
        assert result.quantity == max(feasible, default=0)


def test_identity_date_and_flag_coercion_rejected() -> None:
    with pytest.raises(ValueError):
        size(a=replace(account(), version="ROSS_DIRECT"))
    with pytest.raises(ValueError):
        size(book=replace(ledger(), account_ref="other"))
    with pytest.raises(ValueError):
        size(a=replace(account(), equity_verified=1))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        size(a=replace(account(), equity=D("NaN")))
    with pytest.raises(ValueError):
        size(a=replace(account(), spendable=D(-1)))


def test_roundtrip_with_orphans_cannot_change_streak() -> None:
    with pytest.raises(ValueError):
        update_daily_stop(
            DailyRiskState(DAY, "synthetic", D(500)),
            account(),
            ledger(),
            reconciled_flat=True,
            no_orphan_orders=False,
            roundtrip_id="a",
            roundtrip_net=D(-1),
            roundtrip_reconciled=True,
        )


def test_fraction_entry_prices_ignore_decimal_precision() -> None:
    with localcontext() as context:
        context.prec = 1
        result = size_entry(
            account(),
            ledger(),
            fees(),
            Fraction(999999, 100000),
            Fraction(950999, 100000),
            Fraction(10),
            Fraction(10),
            daily_state=DailyRiskState(DAY, "synthetic", D(500)),
        )
        assert result.quantity == 5
        assert result.limit == Fraction(10)
        assert result.stop == Fraction(19, 2)
    # Fraction support applies only to decision prices, never account evidence.
    with pytest.raises(ValueError):
        size(a=replace(account(), equity=Fraction(500)))  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [True, 10.0])
@pytest.mark.parametrize("slot", range(4))
def test_all_exact_price_positions_reject_coercion(bad: object, slot: int) -> None:
    inputs: list[object] = [
        Fraction(999, 100),
        Fraction(19, 2),
        Fraction(10),
        Fraction(10),
    ]
    inputs[slot] = bad
    with pytest.raises(ValueError):
        size_entry(
            account(),
            ledger(),
            fees(),
            *inputs,  # type: ignore[arg-type]
            daily_state=DailyRiskState(DAY, "synthetic", D(500)),
        )


def test_daily_state_account_and_frozen_equity_binding() -> None:
    state = DailyRiskState(DAY, "synthetic", D(500))
    with pytest.raises(ValueError, match="account mismatch"):
        update_daily_stop(
            state,
            replace(account(), account_ref="other"),
            replace(ledger(), account_ref="other"),
            reconciled_flat=True,
            no_orphan_orders=True,
        )
    with pytest.raises(ValueError, match="frozen equity mismatch"):
        update_daily_stop(
            state,
            replace(account(), equity=D(501)),
            ledger(),
            reconciled_flat=True,
            no_orphan_orders=True,
        )
    tomorrow = DAY + timedelta(days=1)
    result = update_daily_stop(
        state,
        replace(account(), session=tomorrow, equity=D(510)),
        replace(ledger(), session=tomorrow),
        reconciled_flat=True,
        no_orphan_orders=True,
    )
    assert result.account_ref == "synthetic" and result.frozen_equity == D(510)
    with pytest.raises(ValueError, match="unverified"):
        update_daily_stop(
            state,
            replace(account(), session=tomorrow, equity=D(510), equity_verified=False),
            replace(ledger(), session=tomorrow),
            reconciled_flat=True,
            no_orphan_orders=True,
        )


def test_three_loss_canonical_latch_blocks_entry_after_win() -> None:
    state = DailyRiskState(DAY, "synthetic", D(500))
    for i in range(3):
        state = update(state, "-0.01", f"loss-{i}")
    state = update(state, "1", "win")
    assert state.latched and state.losing_streak == 0
    book = replace(ledger(), daily_stop_latched=state.latched)
    assert size(book=book).reason == "DAILY_OR_OWNERSHIP_VETO"


def test_size_requires_bound_daily_state_and_enforces_streak_directly() -> None:
    state = DailyRiskState(DAY, "synthetic", D(500))
    for wrong in (
        replace(state, account_ref="other"),
        replace(state, session=DAY + timedelta(days=1)),
        replace(state, frozen_equity=D(501)),
    ):
        with pytest.raises(ValueError, match="binding"):
            size_entry(
                account(),
                ledger(),
                fees(),
                D("9.99"),
                D("9.5"),
                D(10),
                D(10),
                daily_state=wrong,
            )
    result = size_entry(
        account(),
        ledger(),
        fees(),
        D("9.99"),
        D("9.5"),
        D(10),
        D(10),
        daily_state=replace(state, losing_streak=3),
    )
    assert result.reason == "DAILY_OR_OWNERSHIP_VETO"
    assert result.account_ref == "synthetic" and result.session == DAY
    assert result.frozen_equity == Fraction(500)
