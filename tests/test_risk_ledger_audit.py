"""Synthetic baseline/current posted ledger reconciliation."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from alpaca_ross_bot import risk_baseline
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, KIND, SCHEMA, SOURCE
from alpaca_ross_bot.risk_ledger_audit import RiskLedgerAudit, audit_risk_ledger

BEFORE = datetime(2026, 10, 16, 13, tzinfo=UTC)
NOW = BEFORE + timedelta(hours=1)
POLICY = Path(__file__).parents[1] / "evidence/alpaca-equity-fees-20261012.json"
ACCOUNT = {
    "id": "11111111-1111-4111-8111-111111111111",
    "cash": "500",
    "equity": "500",
    "currency": "USD",
    "status": "ACTIVE",
    "trading_blocked": False,
    "account_blocked": False,
    "trade_suspended_by_user": False,
}
REF = paper_account_ref(json.dumps(ACCOUNT).encode())


def request(path: str, body: object, when: datetime) -> dict[str, object]:
    return {
        "method": "GET",
        "path": path,
        "status": 200,
        "started_at": when.isoformat(),
        "completed_at": when.isoformat(),
        "body": json.dumps(body),
    }


def calendar(journal: Journal, when: datetime) -> int:
    return journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind="CALENDAR_INPUT_CAPTURE",
        payload=json.dumps(
            {
                "account_ref": REF,
                "endpoint": ENDPOINT,
                "request": request(
                    "/v2/calendar?start=2026-10-16&end=2026-10-16",
                    [{"date": "2026-10-16", "open": "09:30", "close": "16:00"}],
                    when,
                ),
            }
        ).encode(),
        system_first_seen_at=when,
        processed_at=when,
    ).sequence


def capture(
    journal: Journal,
    when: datetime,
    rows: list[dict[str, object]],
    *,
    cash: str = "500",
    positions: list[dict[str, str]] | None = None,
) -> int:
    paths = [
        request("/v2/account", ACCOUNT | {"cash": cash, "equity": cash}, when),
        request("/v2/positions", positions or [], when),
        request("/v2/orders?status=open&limit=500&nested=false", [], when),
        request("/v2/account/activities?direction=desc&page_size=100", rows, when),
    ]
    if rows:
        paths.append(
            request(
                "/v2/account/activities?direction=desc&page_size=100&page_token="
                + str(rows[-1]["id"]),
                [],
                when,
            )
        )
    return journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(
            {
                "schema": SCHEMA,
                "account_ref": REF,
                "endpoint": ENDPOINT,
                "requests": paths,
            }
        ).encode(),
        system_first_seen_at=when,
        processed_at=when,
    ).sequence


def fill(identity: str, side: str, price: str, **changes: object) -> dict[str, object]:
    return {
        "id": identity,
        "activity_type": "FILL",
        "side": side,
        "order_id": "order-" + identity,
        "symbol": "XYZ",
        "qty": "1",
        "price": price,
        "transaction_time": (
            NOW - timedelta(minutes=2 if side == "buy" else 1)
        ).isoformat(),
    } | changes


def run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
    *,
    prior: list[dict[str, object]] | None = None,
    cash: str = "500",
    freeze: bool = True,
    positions: list[dict[str, str]] | None = None,
) -> RiskLedgerAudit:
    root = tmp_path / "private"
    j = Journal.open_private(root / "journal.sqlite3", root)
    try:
        j.record_paper_account_verification(REF, json.dumps(ACCOUNT).encode(), BEFORE)
        oldcal = calendar(j, BEFORE)
        old = capture(j, BEFORE, prior or [])
        if freeze:
            monkeypatch.setattr(risk_baseline, "_now", lambda: BEFORE)
            risk_baseline.freeze_baseline(
                j,
                account_ref=REF,
                account_sequence=old,
                calendar_sequence=oldcal,
                session_day=BEFORE.date(),
                max_age=timedelta(seconds=30),
            )
        cal = calendar(j, NOW)
        current = capture(j, NOW, rows, cash=cash, positions=positions)
        return audit_risk_ledger(
            j,
            current,
            cal,
            account_ref=REF,
            decision_at=NOW,
            max_age=timedelta(seconds=30),
            fee_policy=POLICY,
        )
    finally:
        j.close()


def test_missing_baseline_never_synthetic_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tmp_path, monkeypatch, [], freeze=False)
    assert not result.baseline_present and result.gross_pnl is None
    assert "PRESESSION_E0_MISSING" in result.issues


def test_flat_unused_snapshot_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tmp_path, monkeypatch, [])
    assert result.cash_matches and result.gross_pnl == result.cost_adjusted_net == 0
    assert not result.runtime_authorized
    assert "500" not in json.dumps(result.public_summary())
    assert "11111111" not in repr(result)
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    try:
        receipt_ref = journal.read_risk_baseline_receipt_events()[0][0].payload_ref
        assert receipt_ref in result.source_refs
    finally:
        journal.close()


def test_flat_roundtrip_exact_cash_and_model_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with localcontext() as ctx:
        ctx.prec = 1
        result = run(
            tmp_path,
            monkeypatch,
            [fill("sell", "sell", "11"), fill("buy", "buy", "10")],
            cash="501",
        )
    assert result.cash_matches and result.gross_pnl == 1
    assert result.modelled_fee_delta == Fraction(2, 100)
    assert result.cost_adjusted_net == Fraction(98, 100)
    assert "FOREIGN_FILLS_IN_UNUSED_JOURNAL" in result.issues


def test_cash_mismatch_has_no_accepted_pnl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tmp_path, monkeypatch, [], cash="501")
    assert result.cash_matches is False and result.gross_pnl is None
    assert "POSTED_CASH_EQUATION_MISMATCH" in result.issues


@pytest.mark.parametrize("kind", ["FEE", "DIV", "SPLIT"])
def test_nta_charge_unknown_no_double_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    result = run(
        tmp_path,
        monkeypatch,
        [{"id": "nta", "activity_type": kind, "net_amount": "-0.01"}],
        cash="499.99",
    )
    assert result.cash_matches is None and result.cost_adjusted_net is None
    assert result.raw_fee_delta == (Fraction(-1, 100) if kind == "FEE" else 0)
    assert "NEW_NTA_CHARGE_CLASSIFICATION_UNQUALIFIED" in result.issues


def test_signed_funding_observed_not_pnl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(
        tmp_path,
        monkeypatch,
        [{"id": "fund", "activity_type": "CSD", "net_amount": "5"}],
        cash="505",
    )
    assert (
        result.cash_matches
        and result.signed_funding_delta == 5
        and result.gross_pnl == 0
    )
    assert "NTA_INTRADAY_ATTRIBUTION_UNQUALIFIED" in result.issues


@pytest.mark.parametrize("changed", [False, True])
def test_old_census_cannot_change_or_disappear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    prior: list[dict[str, object]] = [
        {"id": "old", "activity_type": "CSD", "net_amount": "500"}
    ]
    rows = [prior[0] | {"net_amount": "499"}] if changed else []
    result = run(tmp_path, monkeypatch, rows, prior=prior)
    assert result.cash_matches is None
    assert "BASELINE_ACTIVITY_CENSUS_CHANGED" in result.issues


def test_late_historical_fill_not_intraday_gain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(
        tmp_path,
        monkeypatch,
        [
            fill(
                "a",
                "buy",
                "10",
                transaction_time=(BEFORE - timedelta(days=1)).isoformat(),
            )
        ],
    )
    assert "LATE_BASELINE_FILL_POSTING" in result.issues and result.gross_pnl is None


def test_fill_one_nanosecond_after_baseline_not_mislabeled_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(
        tmp_path,
        monkeypatch,
        [fill("a", "buy", "10", transaction_time="2026-10-16T13:00:00.000000001Z")],
    )
    assert "LATE_BASELINE_FILL_POSTING" not in result.issues


def test_short_prefix_is_unqualified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(
        tmp_path,
        monkeypatch,
        [
            fill(
                "a",
                "sell",
                "11",
                transaction_time=(NOW - timedelta(minutes=3)).isoformat(),
            ),
            fill("b", "buy", "10"),
        ],
        cash="501",
    )
    assert (
        "SHORT_SELL_PREFIX_UNQUALIFIED" in result.issues and result.cash_matches is None
    )


def test_nonflat_is_not_flat_cash_equation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tmp_path, monkeypatch, [], positions=[{"market_value": "1"}])
    assert "NONFLAT_SNAPSHOT_RECONCILIATION_UNQUALIFIED" in result.issues
    assert result.cash_matches is None


def test_duplicate_fill_not_double_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buy, sell = fill("a", "buy", "10"), fill("b", "sell", "11")
    result = run(tmp_path, monkeypatch, [sell, buy, buy], cash="501")
    assert result.new_fill_count == 2 and result.posted_buy_delta == 10


def test_capture_hash_corruption_rejected(tmp_path: Path) -> None:
    root = tmp_path / "private"
    j = Journal.open_private(root / "journal.sqlite3", root)
    try:
        j.record_paper_account_verification(REF, json.dumps(ACCOUNT).encode(), NOW)
        cal = calendar(j, NOW)
        current = capture(j, NOW, [])
        with sqlite3.connect(root / "journal.sqlite3") as db:
            db.execute("DROP TRIGGER events_no_update")
            db.execute("UPDATE events SET payload=? WHERE sequence=?", (b"{}", current))
        with pytest.raises(ValueError, match="integrity"):
            audit_risk_ledger(
                j,
                current,
                cal,
                account_ref=REF,
                decision_at=NOW,
                max_age=timedelta(seconds=30),
                fee_policy=POLICY,
            )
    finally:
        j.close()


def test_future_baseline_receipt_cannot_support_past_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "private"
    j = Journal.open_private(root / "journal.sqlite3", root)
    try:
        j.record_paper_account_verification(REF, json.dumps(ACCOUNT).encode(), BEFORE)
        oldcal = calendar(j, BEFORE)
        old = capture(j, BEFORE, [])
        decision = BEFORE + timedelta(seconds=1)
        cal = calendar(j, decision)
        current = capture(j, decision, [])
        clocks = iter([BEFORE] * 4 + [BEFORE + timedelta(seconds=2)])
        monkeypatch.setattr(risk_baseline, "_now", lambda: next(clocks))
        risk_baseline.freeze_baseline(
            j,
            account_ref=REF,
            account_sequence=old,
            calendar_sequence=oldcal,
            session_day=BEFORE.date(),
            max_age=timedelta(seconds=30),
        )
        with pytest.raises(ValueError, match="baseline unavailable"):
            audit_risk_ledger(
                j,
                current,
                cal,
                account_ref=REF,
                decision_at=decision,
                max_age=timedelta(seconds=30),
                fee_policy=POLICY,
            )
    finally:
        j.close()
