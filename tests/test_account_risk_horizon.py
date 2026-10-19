"""Immutable sequence horizons and raw-source age; no broker requests."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot import risk_baseline
from alpaca_ross_bot.incident_journal import IncidentJournal
from test_account_risk_owner import PLAN, account_sources, view
from test_incident_risk_binding import policy
from test_risk_ledger_audit import BEFORE, NOW, POLICY, REF


def test_late_evaluation_does_not_refresh_accounting_source_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        old_capture = source.selection(NOW)
        evaluated = NOW + timedelta(seconds=20)
        owner.observe("late-evaluation", old_capture, evaluated)
        assert view(owner)["source_snapshot_qualified"]

        at = evaluated + timedelta(milliseconds=1)
        fresh_capture = replace(source.selection(at), max_age=timedelta(seconds=1))
        incident = IncidentJournal(source.journal, policy())
        result = incident.observe_risk(
            "strict-accounting-age",
            fresh_capture,
            at,
            account_risk_fee_policy=POLICY,
        )
        assert result["reported_inputs_qualified"]
        reasons = result["reasons"]
        assert isinstance(reasons, list)
        assert "BOUND_RISK_DAILY_STATE_STALE" in reasons
        assert not result["runtime_authorized"]
        assert incident.verify_rebuild()


def test_later_same_clock_lifecycle_and_quote_do_not_rewrite_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        owner.reserve("entry", source.selection(NOW), NOW, PLAN)
        at = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, at)
        source.order(PLAN.client_order_id, at, "partially_filled", 2)
        source.fill(PLAN.client_order_id, at, 2, "10")
        original_capture = source.selection(
            at, cash="480", power="460", position=2, bid="10"
        )
        owner.observe("partial", original_capture, at)
        original = view(owner)
        assert original["source_snapshot_qualified"]
        owner_events = source.journal.read_account_risk_events()

        # These later sequence numbers share the original observation clock.
        source.selection(at, cash="480", power="460", position=2, bid="8.50")
        assert owner.verify_rebuild()
        assert view(owner) == original
        assert source.journal.read_account_risk_events() == owner_events


def test_incident_ignores_later_same_clock_account_owner_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected = source.selection(NOW)
        owner.observe("original", selected, NOW)
        incident = IncidentJournal(source.journal, policy())
        original = incident.observe_risk(
            "bound-owner", selected, NOW, account_risk_fee_policy=POLICY
        )
        cached = source.journal.read_state(incident.state_key)
        owner.reserve("later-same-clock", selected, NOW, PLAN)
        later, _ = source.journal.read_account_risk_events()[-1]
        refs = original["source_refs"]
        assert isinstance(refs, list)
        assert all(row[0] != later.sequence for row in refs)
        assert incident.verify_rebuild()
        assert source.journal.read_state(incident.state_key) == cached


@pytest.mark.parametrize("complete", (False, True))
def test_later_same_clock_e0_cannot_rewrite_prior_missing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, complete: bool
) -> None:
    with account_sources(tmp_path, monkeypatch, freeze=False) as (source, owner):
        selected = source.selection(BEFORE)
        owner.observe("missing-e0", selected, BEFORE)
        original = view(owner)
        assert original["issues"] == ["PRESESSION_E0_MISSING"]
        monkeypatch.setattr(risk_baseline, "_now", lambda: BEFORE)
        original_append = source.journal.append_next_if_state_missing

        def crash_before_receipt(**kwargs: Any) -> object:
            if kwargs["event_kind"] == "E0_DURABILITY_OBSERVED":
                raise OSError("synthetic receipt crash")
            return original_append(**kwargs)

        if not complete:
            monkeypatch.setattr(
                source.journal, "append_next_if_state_missing", crash_before_receipt
            )

        def freeze() -> None:
            risk_baseline.freeze_baseline(
                source.journal,
                account_ref=REF,
                account_sequence=selected.capture_sequence,
                calendar_sequence=selected.calendar_sequence,
                session_day=BEFORE.date(),
                max_age=timedelta(seconds=30),
            )

        if complete:
            freeze()
        else:
            with pytest.raises(OSError, match="receipt crash"):
                freeze()
        assert owner.verify_rebuild()
        assert view(owner) == original
