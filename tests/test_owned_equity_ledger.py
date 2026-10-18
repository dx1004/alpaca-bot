"""Owned execution/cash/bid conservation with real-shaped immutable fixtures."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import localcontext
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest

import test_order_reconciliation as lifecycle
from alpaca_ross_bot import risk_baseline
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.owned_equity_ledger import OwnedEquityAudit, audit_owned_equity
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
