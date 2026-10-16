"""Source-only joins of synthetic account, lifecycle and IEX evidence."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.risk_account_inputs import KIND, SOURCE
from alpaca_ross_bot.risk_input_evidence import RiskInputEvidence, inspect_risk_inputs
from test_order_reconciliation import (
    POLICY,
    _at,
    _batch,
    _connect,
    _order,
    _position,
    _reserve,
    _with_baseline,
)
from test_risk_account_inputs import ACCOUNT, REF, flat_capture
from test_risk_marks import QUOTE, URL, put

AT = _at(10)


@contextmanager
def evidence(
    tmp_path: Path,
    *,
    quantity: str | None = "2",
    foreign: bool = False,
    unowned: bool = False,
) -> Iterator[tuple[Journal, int]]:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "j.sqlite3", root)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), _at()
        )
        order = _order(filled=quantity or "0")
        if quantity is not None and not unowned:
            reconciler = _with_baseline(journal)
            _reserve(journal)
            _connect(reconciler)
            report = reconciler.apply_rest_batch(
                _batch(reconciler, order), POLICY, at=AT
            )
            assert report.status == "MATCHED"
        payload = flat_capture()
        requests = payload["requests"]
        assert isinstance(requests, list)
        if quantity is not None:
            requests[1]["body"] = json.dumps([_position("3" if foreign else quantity)])
            requests[2]["body"] = json.dumps([] if unowned else [order])
        for request in requests:
            request["started_at"] = request["completed_at"] = AT.isoformat()
        event = journal.append_next(
            source_id=SOURCE,
            feed="paper",
            event_kind=KIND,
            payload=json.dumps(payload).encode(),
            system_first_seen_at=AT,
            processed_at=AT,
        )
        yield journal, event.sequence
    finally:
        journal.close()


def mark_reference(
    journal: Journal,
    *,
    symbol: str = "AAPL",
    timestamp: str | None = None,
    feed: str = "iex",
) -> tuple[str, int, int, int]:
    quote = QUOTE | {
        "S": symbol,
        "t": (timestamp or AT.isoformat()).replace("+00:00", "Z"),
    }
    sequence = put(journal, [quote], at=AT, feed=feed)
    metadata = put(
        journal,
        {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
        metadata=True,
        at=AT,
    )
    return symbol, sequence, 0, metadata


def inspect(
    journal: Journal,
    capture: int,
    refs: tuple[tuple[str, int, int, int], ...] = (),
) -> RiskInputEvidence:
    return inspect_risk_inputs(
        journal,
        capture,
        account_ref=REF,
        decision_at=AT,
        session_open=_at(1000),
        max_age=timedelta(seconds=30),
        mark_refs=refs,
        max_metadata_age=timedelta(days=1),
    )


def test_genuine_nonflat_join_and_exact_low_precision_total(tmp_path: Path) -> None:
    with evidence(tmp_path) as (journal, capture):
        refs = (mark_reference(journal),)
        with localcontext() as context:
            context.prec = 1
            facts = inspect(journal, capture, refs)
        assert facts.reported_inputs_qualified
        assert facts.total_bid_value == Fraction(200002, 1000)
        assert len(facts.marks) == 1
        assert {capture, refs[0][1], refs[0][3]} <= {
            sequence for sequence, _ in facts.source_refs
        }
        assert all(
            reference.startswith("sha256:") for _, reference in facts.source_refs
        )
        assert not facts.runtime_authorized
        assert (
            "E0_AND_ACCOUNTING_LEDGER_REQUIRE_SEPARATE_ACCEPTANCE" in facts.limitations
        )


def test_flat_source_verified_zero_requires_no_fake_quote(tmp_path: Path) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        facts = inspect(journal, capture)
        assert facts.reported_inputs_qualified
        assert facts.total_bid_value == 0 and facts.marks == ()
        assert len(facts.source_refs) == 1
        assert not facts.runtime_authorized


@pytest.mark.parametrize("unowned", [False, True])
def test_inventory_without_ownership_cannot_be_valued(
    tmp_path: Path, unowned: bool
) -> None:
    with evidence(tmp_path, foreign=not unowned, unowned=unowned) as (journal, capture):
        facts = inspect(journal, capture)
        assert not facts.reported_inputs_qualified
        assert not facts.ownership.ownership_verified
        assert facts.total_bid_value is None


def test_unowned_inventory_cannot_be_laundered_by_quote(tmp_path: Path) -> None:
    with evidence(tmp_path, unowned=True) as (journal, capture):
        with pytest.raises(ValueError, match="outside source-owned"):
            inspect(journal, capture, (mark_reference(journal),))


def test_missing_owned_mark_blocks_valuation(tmp_path: Path) -> None:
    with evidence(tmp_path) as (journal, capture):
        facts = inspect(journal, capture)
        assert not facts.reported_inputs_qualified and facts.total_bid_value is None
        assert "OWNED_POSITION_BID_EVIDENCE_MISSING" in facts.issues


@pytest.mark.parametrize(
    "case", ["foreign_symbol", "foreign_feed", "duplicate", "stale", "future"]
)
def test_invalid_quote_reference_rejected(tmp_path: Path, case: str) -> None:
    with evidence(tmp_path) as (journal, capture):
        reference = mark_reference(
            journal,
            symbol="SPY" if case == "foreign_symbol" else "AAPL",
            feed="sip" if case == "foreign_feed" else "iex",
            timestamp=(AT - timedelta(seconds=2)).isoformat()
            if case == "stale"
            else (AT + timedelta(microseconds=1)).isoformat()
            if case == "future"
            else None,
        )
        with pytest.raises(ValueError):
            inspect(
                journal,
                capture,
                (reference, reference) if case == "duplicate" else (reference,),
            )


def test_nonwhole_position_is_not_valued(tmp_path: Path) -> None:
    with evidence(tmp_path, quantity="0.5") as (journal, capture):
        facts = inspect(journal, capture, (mark_reference(journal),))
        assert not facts.reported_inputs_qualified and facts.total_bid_value is None
        assert "NONWHOLE_OWNED_POSITION_UNQUALIFIED" in facts.issues


def test_public_summary_redacts_inventory_money_and_identity(tmp_path: Path) -> None:
    with evidence(tmp_path) as (journal, capture):
        facts = inspect(journal, capture, (mark_reference(journal),))
        public = json.dumps(facts.public_summary()) + repr(facts)
        for private in ("AAPL", "100.001", "200.002", "500", "11111111", REF):
            assert private not in public
        assert facts.public_summary()["runtime_authorized"] is False
