"""Actual source-owner incident propagation and historical E0 availability."""

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


def reasons(result: dict[str, object]) -> list[str]:
    value = result["reasons"]
    assert isinstance(value, list)
    return value


def test_incident_reads_actual_owner_without_granting_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        selected = source.selection(NOW)
        risk.observe("actual", selected, NOW)
        incident = IncidentJournal(source.journal, policy())
        result = incident.observe_risk(
            "actual-risk", selected, NOW, account_risk_fee_policy=POLICY
        )
        assert result["daily_state_source"] == "SOURCE_BOUND_ACCOUNT_RISK"
        assert result["reported_inputs_qualified"]
        assert result["daily_state_present"] and not result["daily_stop_observed"]
        assert "BOUND_RISK_ACCOUNTING_UNQUALIFIED" not in reasons(result)
        assert "BOUND_RISK_RUNTIME_RELEASE_UNACCEPTED" in reasons(result)
        assert "BOUND_RISK_DATA_CONTRACT_UNQUALIFIED" in reasons(result)
        assert not result["runtime_authorized"]
        later = NOW + timedelta(seconds=1)
        risk.observe("later", source.selection(later), later)
        assert IncidentJournal(source.journal, policy()).verify_rebuild()


def test_actual_daily_stop_propagates_and_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        risk.reserve("entry", source.selection(NOW), NOW, PLAN)
        at = NOW + timedelta(seconds=1)
        source.intent(PLAN.client_order_id, at)
        source.order(PLAN.client_order_id, at, "filled", 4)
        source.fill(PLAN.client_order_id, at, 4, "10")
        selected = source.selection(at, cash="460", position=4, bid="8.50")
        risk.observe("drawdown", selected, at)
        incident = IncidentJournal(source.journal, policy())
        result = incident.observe_risk(
            "loss", selected, at, account_risk_fee_policy=POLICY
        )
        assert result["daily_stop_observed"] and result["daily_stop_latched"]
        assert "BOUND_RISK_DAILY_STOP_OBSERVED" in reasons(result)
        assert not result["runtime_authorized"]
        assert IncidentJournal(source.journal, policy()).verify_rebuild()


def test_exit_binding_does_not_refresh_underlying_account_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        risk.reserve("entry", source.selection(NOW), NOW, PLAN)
        at = NOW + timedelta(seconds=31)
        source.intent("stop", at, side="sell", order_type="stop", price="9.50")
        risk.bind_exit("stop-binding", at, "stop", "STOP")
        source.order("stop", at, "canceled", 0)
        selected = source.selection(at)
        incident = IncidentJournal(source.journal, policy())
        result = incident.observe_risk(
            "stale", selected, at, account_risk_fee_policy=POLICY
        )
        assert "BOUND_RISK_DAILY_STATE_STALE" in reasons(result)
        assert "BOUND_RISK_OWNER_VETO" in reasons(result)
        assert not result["runtime_authorized"]
        assert incident.verify_rebuild()


@pytest.mark.parametrize("complete", (False, True))
def test_future_e0_pending_or_completed_cannot_rewrite_prior_missing_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, complete: bool
) -> None:
    with account_sources(tmp_path, monkeypatch, freeze=False) as (source, risk):
        selected = source.selection(BEFORE)
        risk.observe("before-e0", selected, BEFORE)
        original = view(risk)
        assert original["issues"] == ["PRESESSION_E0_MISSING"]
        future = BEFORE + timedelta(seconds=1)
        monkeypatch.setattr(risk_baseline, "_now", lambda: future)
        original_append = source.journal.append_next_if_state_missing

        def crash_before_receipt(**kwargs: Any) -> object:
            if kwargs["event_kind"] == "E0_DURABILITY_OBSERVED":
                raise OSError("synthetic crash before receipt")
            return original_append(**kwargs)

        if not complete:
            monkeypatch.setattr(
                source.journal, "append_next_if_state_missing", crash_before_receipt
            )
        if complete:
            risk_baseline.freeze_baseline(
                source.journal,
                account_ref=REF,
                account_sequence=selected.capture_sequence,
                calendar_sequence=selected.calendar_sequence,
                session_day=BEFORE.date(),
                max_age=timedelta(seconds=30),
            )
        else:
            with pytest.raises(OSError, match="before receipt"):
                risk_baseline.freeze_baseline(
                    source.journal,
                    account_ref=REF,
                    account_sequence=selected.capture_sequence,
                    calendar_sequence=selected.calendar_sequence,
                    session_day=BEFORE.date(),
                    max_age=timedelta(seconds=30),
                )
        assert risk.verify_rebuild() and view(risk) == original
        assert (
            risk_baseline.read_baseline(
                source.journal,
                account_ref=REF,
                session_day=BEFORE.date(),
                decision_at=BEFORE,
            )
            is None
        )
        if not complete:
            with pytest.raises(ValueError, match="durability receipt"):
                risk_baseline.read_baseline(
                    source.journal, account_ref=REF, session_day=BEFORE.date()
                )


def test_missing_source_owner_cannot_fall_back_to_fixture_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, _):
        selected = source.selection(NOW)
        incident = IncidentJournal(source.journal, policy())
        result = incident.observe_risk(
            "missing",
            replace(selected, max_age=timedelta(seconds=10)),
            NOW,
            account_risk_fee_policy=POLICY,
        )
        assert not result["daily_state_present"]
        assert "BOUND_RISK_DAILY_STATE_MISSING" in reasons(result)
        assert "BOUND_RISK_ACCOUNTING_UNQUALIFIED" in reasons(result)
        assert not result["runtime_authorized"] and incident.verify_rebuild()
