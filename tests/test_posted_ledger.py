"""Synthetic immutable posted ledger evidence; no network or real accounts."""

import json
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.posted_ledger import PostedLedgerFacts, inspect_posted_ledger
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, KIND, SCHEMA, SOURCE

NOW = datetime(2026, 10, 16, 15, tzinfo=UTC)
OPEN = NOW.replace(hour=13, minute=30)
ACCOUNT = {"id": "11111111-1111-4111-8111-111111111111", "cash": "500"}
REF = paper_account_ref(json.dumps(ACCOUNT).encode())
POLICY = Path(__file__).parents[1] / "evidence/alpaca-equity-fees-20261012.json"


def fill(identity: str = "a", **changes: object) -> dict[str, object]:
    return {
        "id": identity,
        "activity_type": "FILL",
        "order_id": "order",
        "side": "buy",
        "qty": "0.5",
        "cum_qty": "999",
        "price": "10",
        "transaction_time": NOW.isoformat(),
    } | changes


def run(
    tmp_path: Path,
    pages: list[list[dict[str, object]]],
    *,
    paths: list[str] | None = None,
    decision_at: datetime = NOW,
    source: str = SOURCE,
    account_ref: str = REF,
) -> PostedLedgerFacts:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "ledger.sqlite3", root)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), NOW
        )
        requests = []

        def add(path: str, body: object) -> None:
            requests.append(
                {
                    "method": "GET",
                    "path": path,
                    "status": 200,
                    "started_at": NOW.isoformat(),
                    "completed_at": NOW.isoformat(),
                    "body": json.dumps(body),
                }
            )

        add("/v2/account", ACCOUNT)
        add("/v2/positions", [])
        add("/v2/orders?status=open&limit=500&nested=false", [])
        token = None
        for index, page in enumerate(pages):
            path = "/v2/account/activities?direction=desc&page_size=100"
            if token is not None:
                path += "&page_token=" + token
            add(paths[index] if paths else path, page)
            if page:
                token = str(page[-1]["id"])
        event = journal.append_next(
            source_id=source,
            feed="paper",
            event_kind=KIND,
            payload=json.dumps(
                {
                    "schema": SCHEMA,
                    "account_ref": REF,
                    "endpoint": ENDPOINT,
                    "requests": requests,
                }
            ).encode(),
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        return inspect_posted_ledger(
            journal,
            event.sequence,
            account_ref=account_ref,
            decision_at=decision_at,
            session_open=OPEN,
            max_age=timedelta(seconds=30),
            fee_policy=POLICY,
        )
    finally:
        journal.close()


def test_exact_partial_fills_not_cumulative_quantity(tmp_path: Path) -> None:
    facts = run(tmp_path, [[fill()], [fill("b", side="sell", qty="0.25")], []])
    assert facts.gross_buy_cash == 5
    assert facts.gross_sell_cash == Fraction(5, 2)
    assert facts.modelled_regulatory_fees.total == Fraction(2, 100)
    assert facts.fill_count == facts.current_day_fill_count == 2
    assert (
        facts.pagination_exhausted
        and facts.positions_count == facts.open_orders_count == 0
    )
    assert not facts.runtime_authorized
    assert "11111111" not in repr(facts)


def test_short_nonempty_page_not_exhaustion(tmp_path: Path) -> None:
    facts = run(tmp_path, [[fill()]])
    assert not facts.pagination_exhausted
    assert "ACTIVITY_PAGINATION_NOT_EXHAUSTED" in facts.issues


def test_identical_duplicate_counted_once(tmp_path: Path) -> None:
    facts = run(tmp_path, [[fill(), fill()], []])
    assert facts.activity_count == 1 and facts.gross_buy_cash == 5


def test_changed_duplicate_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="changed duplicate"):
        run(tmp_path, [[fill(), fill(price="11")], []])


@pytest.mark.parametrize("value", [True, 1, 0.1, "NaN", "Infinity", "-1", "0", "abc"])
def test_nonpositive_or_inexact_fills_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError):
        run(tmp_path, [[fill(qty=value)], []])


@pytest.mark.parametrize("field", ["id", "order_id", "transaction_time", "side"])
def test_missing_fill_identity_or_time_rejected(tmp_path: Path, field: str) -> None:
    row = fill()
    row[field] = ""
    with pytest.raises(ValueError):
        run(tmp_path, [[row], []])


def test_future_fill_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="future fill"):
        run(
            tmp_path,
            [
                [fill(transaction_time=(NOW + timedelta(microseconds=1)).isoformat())],
                [],
            ],
        )


def test_submicrosecond_future_fill_not_truncated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="future fill"):
        run(tmp_path, [[fill(transaction_time="2026-10-16T15:00:00.000000001Z")], []])


def test_historical_fill_does_not_enter_today_fee_total(tmp_path: Path) -> None:
    facts = run(
        tmp_path, [[fill(transaction_time=(NOW - timedelta(days=1)).isoformat())], []]
    )
    assert facts.historical_fill_count == 1 and facts.current_day_fill_count == 0
    assert facts.gross_buy_cash == 5 and facts.modelled_regulatory_fees.total == 0


def test_nta_signed_values_remain_separate_from_fee_model(tmp_path: Path) -> None:
    rows: list[dict[str, object]] = [
        {
            "id": "a",
            "activity_type": "FEE",
            "net_amount": "-0.03",
            "date": "2026-10-16",
        },
        {"id": "b", "activity_type": "CSD", "net_amount": "500"},
        {"id": "c", "activity_type": "CSW", "net_amount": "-20"},
        {"id": "d", "activity_type": "DIV", "net_amount": "0.1"},
    ]
    facts = run(tmp_path, [rows, []])
    assert dict(facts.signed_nta_totals) == {
        "FEE": Fraction(-3, 100),
        "CSD": 500,
        "CSW": -20,
        "DIV": Fraction(1, 10),
    }
    assert facts.modelled_regulatory_fees.total == 0
    assert "UNKNOWN_NTA_CLASSIFICATION" in facts.issues
    assert "NTA_INTRADAY_ATTRIBUTION_UNQUALIFIED" in facts.issues


@pytest.mark.parametrize(
    "path",
    [
        "/v2/account/activities?direction=asc&page_size=100",
        "/v2/account/activities?direction=desc&page_size=100&activity_types=FILL",
        "/v2/account/activities?direction=desc&page_size=100&date=2026-10-16",
        "/v2/account/activities?direction=desc&page_size=100&page_token=bad",
    ],
)
def test_filtered_first_page_rejected(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError, match="pagination"):
        run(tmp_path, [[]], paths=[path])


def test_wrong_next_cursor_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pagination"):
        run(
            tmp_path,
            [[fill()], []],
            paths=[
                "/v2/account/activities?direction=desc&page_size=100",
                "/v2/account/activities?direction=desc&page_size=100&page_token=wrong",
            ],
        )


@pytest.mark.parametrize("offset", [-1, 31])
def test_future_or_stale_source_rejected(tmp_path: Path, offset: int) -> None:
    with pytest.raises(ValueError):
        run(tmp_path, [[]], decision_at=NOW + timedelta(seconds=offset))


def test_foreign_identity_and_source_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run(tmp_path, [[]], account_ref="foreign")
    with pytest.raises(ValueError):
        run(tmp_path, [[]], source="OTHER")
