"""Owned execution/cash/bid conservation with real-shaped immutable fixtures."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import localcontext
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest

import test_order_reconciliation as lifecycle
from alpaca_ross_bot import risk_baseline
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.order_reconciliation import RestBatch
from alpaca_ross_bot.owned_equity_ledger import OwnedEquityAudit, audit_owned_equity
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.risk_account_inputs import KIND, SOURCE
from test_risk_ledger_audit import ACCOUNT, BEFORE, NOW, POLICY, REF, calendar, capture
from test_risk_marks import QUOTE, URL, put

AT = NOW + timedelta(seconds=10)


@contextmanager
def sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cash: str = "498",
    freeze: bool = True,
    missing_fill: bool = False,
    foreign: bool = False,
    nta: bool = False,
    stale_mark: bool = False,
    price_disagreement: bool = False,
    oversized_cumulative_field: bool = False,
    newer_intent: bool = False,
    intent_ids: tuple[str, ...] | None = None,
) -> Iterator[OwnedEquityAudit]:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), BEFORE
        )
        oldcal = calendar(journal, BEFORE)
        old = capture(journal, BEFORE, [])
        if freeze:
            monkeypatch.setattr(risk_baseline, "_now", lambda: BEFORE)
            risk_baseline.freeze_baseline(
                journal,
                account_ref=REF,
                account_sequence=old,
                calendar_sequence=oldcal,
                session_day=BEFORE.date(),
                max_age=timedelta(seconds=30),
            )
        monkeypatch.setattr(lifecycle, "NOW", BEFORE)
        reconciler = lifecycle._with_baseline(journal)
        monkeypatch.setattr(lifecycle, "NOW", NOW)
        lifecycle._reserve(journal)
        lifecycle._connect(reconciler)
        order = lifecycle._order(filled="2")
        result = reconciler.apply_rest_batch(
            lifecycle._batch(reconciler, order), lifecycle.POLICY, at=AT
        )
        assert result.status == "MATCHED"
        rows = [] if missing_fill else [lifecycle._activity("2")]
        if price_disagreement:
            rows[0]["price"] = "2.00"
        if oversized_cumulative_field:
            rows[0]["cum_qty"] = "99999"
        if foreign:
            rows[0]["order_id"] = "33333333-3333-4333-8333-333333333333"
        if nta:
            rows.append(
                {
                    "id": "fee-1",
                    "activity_type": "FEE",
                    "net_amount": "-1",
                    "date": AT.date().isoformat(),
                }
            )
        position = cast(
            dict[str, str], lifecycle._position("2") | {"market_value": "9000"}
        )
        seq = capture(journal, AT, rows, cash=cash, positions=[position])
        event = journal.read_event(seq)
        assert event is not None
        data = json.loads(event[1])
        for req in data["requests"]:
            if req["path"] == "/v2/account":
                body = json.loads(req["body"])
                body.update(
                    multiplier="1",
                    buying_power="490",
                    non_marginable_buying_power="490",
                )
                req["body"] = json.dumps(body)
            elif req["path"].startswith("/v2/orders?"):
                req["body"] = json.dumps([order])
        current = journal.append_next(
            source_id=SOURCE,
            feed="paper",
            event_kind=KIND,
            payload=json.dumps(data).encode(),
            system_first_seen_at=AT,
            processed_at=AT,
        ).sequence
        cal = calendar(journal, AT)
        quote = QUOTE | {
            "S": "AAPL",
            "bp": "1.25",
            "ap": "1.26",
            "t": ((AT - timedelta(seconds=2)) if stale_mark else AT)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        qseq = put(journal, [quote], at=AT)
        mseq = put(
            journal,
            {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
            metadata=True,
            at=AT,
        )
        if newer_intent:
            lifecycle._reserve(journal, "later-intent", sent=False)
        yield audit_owned_equity(
            journal,
            current,
            cal,
            account_ref=REF,
            decision_at=AT,
            max_age=timedelta(seconds=30),
            fee_policy=POLICY,
            mark_refs=(("AAPL", qseq, 0, mseq),),
            max_metadata_age=timedelta(days=1),
            _intent_ids=intent_ids,
        )
    finally:
        journal.close()


def test_owned_nonflat_cash_and_bid_equity_ignore_provider_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sources(tmp_path, monkeypatch) as audit:
        assert audit.reported_conservation_verified
        assert audit.cash_matches is True and audit.quantity_matches
        assert audit.observed_equity_at_bid == Fraction(1001, 2)
        assert audit.gross_since_baseline == Fraction(1, 2)
        assert audit.modelled_fee_delta is not None
        assert (
            audit.modelled_net_since_baseline
            == Fraction(1, 2) - audit.modelled_fee_delta
        )
        assert audit.source_refs and not audit.runtime_authorized


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing_fill", "FILL_CUMULATIVE_QUANTITY_MISMATCH"),
        ("foreign", "FILL_OWNERSHIP_MISMATCH"),
        ("nta", "NEW_NTA_REQUIRES_ATTRIBUTION"),
    ],
)
def test_unexplained_activities_veto_equity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, reason: str
) -> None:
    with sources(
        tmp_path,
        monkeypatch,
        missing_fill=case == "missing_fill",
        foreign=case == "foreign",
        nta=case == "nta",
    ) as audit:
        assert reason in audit.issues
        assert not audit.reported_conservation_verified
        assert audit.modelled_net_since_baseline is None


def test_cash_mismatch_is_not_pnl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sources(tmp_path, monkeypatch, cash="499") as audit:
        assert audit.cash_matches is False
        assert "OBSERVED_CASH_CONSERVATION_MISMATCH" in audit.issues
        assert audit.gross_since_baseline is None


def test_missing_actual_e0_cannot_be_substituted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sources(tmp_path, monkeypatch, freeze=False) as audit:
        assert not audit.baseline_present and audit.cash_matches is None
        assert "PRESESSION_E0_MISSING" in audit.issues
        assert audit.gross_since_baseline is None


def test_stale_bid_does_not_become_provider_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError):
        with sources(tmp_path, monkeypatch, stale_mark=True):
            pass


def test_public_summary_contains_no_money_or_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sources(tmp_path, monkeypatch) as audit:
        public = json.dumps(audit.public_summary()) + repr(audit)
        for value in (REF, "AAPL", "498", "500.5", "9000"):
            assert value not in public


def test_disagreeing_execution_prices_block_even_if_cash_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sources(tmp_path, monkeypatch, price_disagreement=True, cash="496") as audit:
        assert "FILL_SOURCE_DISAGREEMENT" in audit.issues
        assert audit.gross_since_baseline is None


def test_cumulative_quantity_field_is_not_summed_as_execution_quantity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sources(tmp_path, monkeypatch, oversized_cumulative_field=True) as audit:
        assert audit.reported_conservation_verified
        assert audit.gross_since_baseline == Fraction(1, 2)


def test_low_decimal_precision_cannot_change_equity_conservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with localcontext() as context:
        context.prec = 1
        with sources(tmp_path, monkeypatch) as audit:
            assert audit.reported_conservation_verified
            assert audit.observed_equity_at_bid == Fraction(1001, 2)


@pytest.mark.parametrize("locked_census", [False, True])
def test_historical_locked_census_is_forwarded_without_weakening_ordinary_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked_census: bool
) -> None:
    if not locked_census:
        with pytest.raises(ValueError, match="outside source-owned inventory"):
            with sources(tmp_path, monkeypatch, newer_intent=True):
                pass
        return
    with sources(
        tmp_path,
        monkeypatch,
        newer_intent=True,
        intent_ids=(lifecycle.CID,),
    ) as audit:
        assert audit.reported_conservation_verified
        assert "REPORTED_INPUTS_UNQUALIFIED" not in audit.issues
        assert not audit.runtime_authorized


def _profile_capture(
    journal: Journal,
    when: datetime,
    rows: list[dict[str, object]],
    *,
    orders: tuple[dict[str, object], ...] = (),
    cash: str = "500",
    position: dict[str, object] | None = None,
) -> int:
    seq = capture(
        journal,
        when,
        rows,
        cash=cash,
        positions=cast(list[dict[str, str]], [position] if position else []),
    )
    stored = journal.read_event(seq)
    assert stored is not None
    data = json.loads(stored[1])
    for req in data["requests"]:
        if req["path"] == "/v2/account":
            body = json.loads(req["body"])
            body.update(
                multiplier="1", buying_power=cash, non_marginable_buying_power=cash
            )
            req["body"] = json.dumps(body)
        elif req["path"].startswith("/v2/orders?"):
            req["body"] = json.dumps(orders)
    return journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(data).encode(),
        system_first_seen_at=when,
        processed_at=when,
    ).sequence


@contextmanager
def retained_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    external_prior: bool = False,
    changed: bool = False,
    missing: bool = False,
    foreign_new: bool = False,
    late_new: bool = False,
) -> Iterator[OwnedEquityAudit]:
    """Preserve an earlier owned roundtrip, then freeze a genuinely flat next E0."""
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    previous = BEFORE - timedelta(days=1)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), previous
        )
        monkeypatch.setattr(lifecycle, "NOW", previous)
        reconciler = lifecycle._with_baseline(journal)
        lifecycle._reserve(journal, "old-buy")
        lifecycle._connect(reconciler)
        buy = lifecycle._order(
            "filled", "10", cid="old-buy", broker_id=lifecycle.OTHER_BROKER_ID
        )
        buy_fill = lifecycle._activity(
            "10", "old-buy-fill", broker_id=lifecycle.OTHER_BROKER_ID
        )

        def batch(
            orders: tuple[dict[str, object], ...],
            rows: dict[str, tuple[dict[str, object], ...]],
            when: datetime,
            position: dict[str, object] | None = None,
        ) -> RestBatch:
            return RestBatch(
                account_ref=REF,
                started_at=when,
                completed_at=when,
                local_revision=cast(int, reconciler.view()["watermark"]),
                open_orders=tuple(
                    order for order in orders if order["status"] == "partially_filled"
                ),
                positions=(position,) if position else (),
                lookups={
                    str(order["client_order_id"]): PaperHttpResponse(
                        200, json.dumps(order).encode()
                    )
                    for order in orders
                },
                activities=rows,
                complete=True,
                stable=True,
            )

        report = reconciler.apply_rest_batch(
            batch(
                (buy,),
                {lifecycle.OTHER_BROKER_ID: (buy_fill,)},
                previous + timedelta(seconds=10),
                lifecycle._position("10"),
            ),
            lifecycle.POLICY,
            at=previous + timedelta(seconds=10),
        )
        assert report.status == "MATCHED"
        monkeypatch.setattr(lifecycle, "NOW", previous + timedelta(seconds=11))
        lifecycle._reserve(journal, "old-sell", side="sell")
        sell = lifecycle._order(
            "filled",
            "10",
            cid="old-sell",
            broker_id="44444444-4444-4444-8444-444444444444",
            side="sell",
        )
        sell_fill = lifecycle._activity(
            "10", "old-sell-fill", broker_id=str(sell["id"]), side="sell"
        )
        history: dict[str, tuple[dict[str, object], ...]] = {
            str(buy["id"]): (buy_fill,),
            str(sell["id"]): (sell_fill,),
        }
        report = reconciler.apply_rest_batch(
            batch((buy, sell), history, previous + timedelta(seconds=21)),
            lifecycle.POLICY,
            at=previous + timedelta(seconds=21),
        )
        assert report.status == "MATCHED"
        report = reconciler.apply_rest_batch(
            batch((buy, sell), history, BEFORE - timedelta(seconds=1)),
            lifecycle.POLICY,
            at=BEFORE - timedelta(seconds=1),
        )
        assert report.status == "MATCHED"
        prior = [buy_fill, sell_fill]
        if external_prior:
            prior += [
                buy_fill | {"id": "external-old-buy", "order_id": "external-buy-order"},
                sell_fill
                | {"id": "external-old-sell", "order_id": "external-sell-order"},
            ]
        oldcal = calendar(journal, BEFORE)
        old = _profile_capture(journal, BEFORE, prior)
        monkeypatch.setattr(risk_baseline, "_now", lambda: BEFORE)
        risk_baseline.freeze_baseline(
            journal,
            account_ref=REF,
            account_sequence=old,
            calendar_sequence=oldcal,
            session_day=BEFORE.date(),
            max_age=timedelta(seconds=30),
        )
        monkeypatch.setattr(lifecycle, "NOW", NOW)
        lifecycle._reserve(journal)
        order = lifecycle._order(filled="2")
        current_fill = lifecycle._activity("2")
        report = reconciler.apply_rest_batch(
            batch(
                (buy, sell, order),
                history | {lifecycle.BROKER_ID: (current_fill,)},
                AT,
                lifecycle._position("2"),
            ),
            lifecycle.POLICY,
            at=AT,
        )
        assert report.status == "MATCHED"
        rows = prior + [current_fill]
        if changed:
            rows[0] = rows[0] | {"price": "2.00"}
        if missing:
            rows = rows[1:]
        if foreign_new or late_new:
            rows.append(
                current_fill
                | {
                    "id": "unknown-new-fill",
                    "order_id": "unknown-new-order",
                    "transaction_time": (
                        previous if late_new else NOW + timedelta(seconds=6)
                    ).isoformat(),
                }
            )
        current = _profile_capture(
            journal,
            AT,
            rows,
            orders=(order,),
            cash="498",
            position=lifecycle._position("2") | {"market_value": "9000"},
        )
        cal = calendar(journal, AT)
        qseq = put(
            journal,
            [
                QUOTE
                | {
                    "S": "AAPL",
                    "bp": "1.25",
                    "ap": "1.26",
                    "t": AT.isoformat().replace("+00:00", "Z"),
                }
            ],
            at=AT,
        )
        mseq = put(
            journal,
            {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
            metadata=True,
            at=AT,
        )
        yield audit_owned_equity(
            journal,
            current,
            cal,
            account_ref=REF,
            decision_at=AT,
            max_age=timedelta(seconds=30),
            fee_policy=POLICY,
            mark_refs=(("AAPL", qseq, 0, mseq),),
            max_metadata_age=timedelta(days=1),
            _intent_ids=("old-buy", "old-sell", lifecycle.CID),
        )
    finally:
        journal.close()


@pytest.mark.parametrize("external_prior", [False, True])
def test_prior_owned_roundtrip_is_lifetime_census_not_new_session_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external_prior: bool
) -> None:
    with retained_history(
        tmp_path, monkeypatch, external_prior=external_prior
    ) as audit:
        assert audit.reported_conservation_verified
        assert audit.cash_matches and audit.quantity_matches
        assert audit.observed_equity_at_bid == Fraction(1001, 2)
        assert audit.gross_since_baseline == Fraction(1, 2)
        assert not audit.runtime_authorized


@pytest.mark.parametrize("mutation", ["changed", "missing"])
def test_retained_baseline_history_cannot_change_or_disappear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    with retained_history(
        tmp_path,
        monkeypatch,
        changed=mutation == "changed",
        missing=mutation == "missing",
    ) as audit:
        assert "BASELINE_CENSUS_CHANGED" in audit.issues
        assert "FILL_SOURCE_DISAGREEMENT" in audit.issues
        assert not audit.reported_conservation_verified
        assert audit.gross_since_baseline is None


@pytest.mark.parametrize("late", [False, True])
def test_new_unknown_or_late_historical_fills_still_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, late: bool
) -> None:
    with retained_history(
        tmp_path, monkeypatch, foreign_new=not late, late_new=late
    ) as audit:
        assert "FILL_OWNERSHIP_MISMATCH" in audit.issues
        assert ("LATE_BASELINE_FILL" in audit.issues) is late
        assert not audit.reported_conservation_verified
        assert audit.modelled_net_since_baseline is None
