"""Real journal-shaped source conversion; no broker or formal enrollment."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import alpaca_ross_bot.paper_execution_adapter as adapter
from alpaca_ross_bot.paper_execution_adapter import inspect_engineering_executions
from test_account_risk_owner import account_sources, money
from test_paper_ledger_sources import complete, inspect
from test_risk_ledger_audit import NOW, POLICY, REF


def convert(source: Any, selection: Any, at: Any, **changes: Any) -> Any:
    return inspect_engineering_executions(
        source.journal,
        selection.capture_sequence,
        **(
            {
                "journal_scope": "engineering-source",
                "account_ref": REF,
                "decision_at": at,
                "through_sequence": source.journal.read_evidence_sequence(),
                "max_age": timedelta(seconds=30),
                "fee_policy": POLICY,
            }
            | changes
        ),
    )


def test_exact_rows_and_provenance_are_read_only_and_nonformal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection, at, _ = complete(source, owner, nanos=True)
        source.journal._db.execute("BEGIN")
        before = tuple(source.journal._db.iterdump())
        result = convert(source, selection, at)
        assert source.journal._db.in_transaction
        assert tuple(source.journal._db.iterdump()) == before
        assert len(result.executions) == 3
        assert not result.unconverted_observations
        for execution, raw in zip(result.executions, result.observations, strict=True):
            assert execution.execution_id == raw.activity_id
            assert execution.quantity == raw.quantity
            assert execution.price == raw.price
            assert execution.original_timestamp == raw.transaction_time
            assert execution.submicrosecond_remainder == raw.submicrosecond_remainder
            assert execution.canonical_row == raw.canonical_row
            assert execution.canonical_row_hash == (
                "sha256:" + hashlib.sha256(raw.canonical_row).hexdigest()
            )
            assert execution.activity_source == raw.source
            assert execution.occurrences == raw.occurrences
            assert execution.ownership_sources
            assert execution.origin == "ENGINEERING_TEST"
        assert not result.formal_sample_eligible
        assert not result.runtime_authorized
        assert not result.dispatch_authorized
        assert not result.journal_scope_authenticated
        assert not result.activity_finality_accepted
        assert not result.account_day_census_accepted
        source.journal._db.rollback()


def test_supplied_raw_order_is_not_sorted_or_inferred_from_order_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        _, at, cash = complete(source, owner)
        source.fills.reverse()
        selection = source.selection(at, cash=money(cash))
        owner.observe("reverse-source-order", selection, at)
        result = convert(source, selection, at)
        assert [row.side for row in result.executions] == ["sell", "buy"]
        assert not result.supplied_order_chronological
        assert "SOURCE_EXECUTION_ORDER_NOT_CHRONOLOGICAL" in result.issues


def test_variants_postings_and_unowned_rows_remain_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        at = NOW
        fill: dict[str, object] = {
            "id": "foreign",
            "activity_type": "FILL",
            "order_id": "foreign-order",
            "qty": "1",
            "price": "10",
            "symbol": "AAPL",
            "side": "buy",
            "transaction_time": at.isoformat(),
        }
        source.fills.extend(
            [
                fill,
                fill | {"price": "10.01"},
                {
                    "id": "fee",
                    "activity_type": "FEE",
                    "net_amount": "-0.01",
                    "order_id": "nontrade",
                },
            ]
        )
        selection = source.selection(at)
        result = convert(source, selection, at)
        assert len(result.observations) == 3
        assert not result.executions
        assert result.unconverted_observations == result.observations
        assert "EXECUTION_ACTIVITY_VARIANTS_UNRESOLVED" in result.issues
        assert "NTA_ATTRIBUTION_UNQUALIFIED" in result.issues


def test_fixed_prefix_is_stable_and_scope_remains_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection, at, cash = complete(source, owner)
        horizon = source.journal.read_evidence_sequence()
        original = convert(source, selection, at, through_sequence=horizon)
        complete(source, owner, index=1, cash=cash)
        assert convert(source, selection, at, through_sequence=horizon) == original
        other = convert(
            source, selection, at, through_sequence=horizon, journal_scope="other"
        )
        assert (
            other.executions[0].activity_source
            != original.executions[0].activity_source
        )
        assert other.journal_scope_authenticated is False


def test_duplicate_ownership_assignment_is_defensively_not_converted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection, at, _ = complete(source, owner)
        observed = inspect(source, selection, at)
        duplicated = replace(
            observed,
            observed_roundtrips=observed.observed_roundtrips * 2,
        )
        # Exercise the adapter's defensive boundary, not an alternate public API.
        monkeypatch.setattr(
            adapter, "inspect_paper_ledger_sources", lambda *a, **k: duplicated
        )
        result = convert(source, selection, at)
        assert not result.executions
        assert result.unconverted_observations == observed.activities
        assert "EXECUTION_ASSIGNMENT_CONFLICT" in result.issues
