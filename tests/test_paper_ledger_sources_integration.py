"""Independent source-shaped ledger checks; no broker or formal sample."""

from datetime import timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.paper_ledger_sources import (
    PaperLedgerSources,
    inspect_paper_ledger_sources,
)
from test_account_risk_owner import PLAN, account_sources
from test_risk_ledger_audit import NOW, POLICY, REF


def test_unbound_flat_roundtrip_never_becomes_attributed_performance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        bought = NOW + timedelta(seconds=1)
        source.intent("unbound-buy", bought)
        source.order("unbound-buy", bought, "filled", 4)
        source.fill("unbound-buy", bought, 4, "10")
        sold = NOW + timedelta(seconds=2)
        source.intent("unbound-sell", sold, side="sell")
        source.order("unbound-sell", sold, "filled", 4)
        source.fill("unbound-sell", sold, 4, "10")
        selection = source.selection(sold)
        owner.observe("unbound-observation", selection, sold)
        horizon = source.journal.read_evidence_sequence()
        before = source.journal._db.total_changes
        result = inspect_paper_ledger_sources(
            source.journal,
            selection.capture_sequence,
            account_ref=REF,
            journal_scope="INDEPENDENT_ENGINEERING_FIXTURE",
            decision_at=sold,
            through_sequence=horizon,
            max_age=timedelta(seconds=30),
            fee_policy=POLICY,
        )
        assert len(result.activities) == 2
        assert not result.observed_roundtrips
        assert result.issues
        assert source.journal.read_evidence_sequence() == horizon
        assert source.journal._db.total_changes == before


def test_historical_completed_roundtrip_does_not_adopt_later_unbound_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        owner.reserve("reserve", source.selection(NOW), NOW, PLAN)
        bought = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, bought)
        source.order(PLAN.client_order_id, bought, "filled", 4)
        source.fill(PLAN.client_order_id, bought, 4, "10")
        owner.observe(
            "bought", source.selection(bought, cash="460", position=4), bought
        )
        sold = NOW + timedelta(seconds=2)
        source.intent("bound-stop", sold, side="sell", order_type="stop", price="9.50")
        owner.bind_exit("bind-stop", sold, "bound-stop", "STOP")
        source.order("bound-stop", sold, "filled", 4)
        source.fill("bound-stop", sold, 4, "9.50")
        selection = source.selection(sold, cash="498")
        owner.observe("flat", selection, sold)
        horizon = source.journal.read_evidence_sequence()

        def historical() -> PaperLedgerSources:
            return inspect_paper_ledger_sources(
                source.journal,
                selection.capture_sequence,
                account_ref=REF,
                journal_scope="INDEPENDENT_ENGINEERING_FIXTURE",
                decision_at=sold,
                through_sequence=horizon,
                max_age=timedelta(seconds=30),
                fee_policy=POLICY,
            )

        original = historical()
        assert len(original.observed_roundtrips) == 1
        later = NOW + timedelta(seconds=3)
        source.intent("later-unbound", later, qty=1)
        source.order("later-unbound", later, "filled", 1)
        source.fill("later-unbound", later, 1, "10")
        current_selection = source.selection(later, cash="488", position=1)
        owner.observe("unbound-later", current_selection, later)
        assert historical() == original
        current_horizon = source.journal.read_evidence_sequence()
        before = source.journal._db.total_changes
        current = inspect_paper_ledger_sources(
            source.journal,
            current_selection.capture_sequence,
            account_ref=REF,
            journal_scope="INDEPENDENT_ENGINEERING_FIXTURE",
            decision_at=later,
            through_sequence=current_horizon,
            max_age=timedelta(seconds=30),
            fee_policy=POLICY,
        )
        assert len(current.activities) == 3
        assert not current.observed_roundtrips
        assert current.issues
        assert source.journal.read_evidence_sequence() == current_horizon
        assert source.journal._db.total_changes == before
