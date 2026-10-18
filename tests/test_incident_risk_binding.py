"""Incident gates consume immutable source references, never acceptance flags."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.incident_journal import IncidentJournal
from alpaca_ross_bot.incident_risk import IncidentRiskRequest
from alpaca_ross_bot.incident_state import IncidentObservation, IncidentPolicy
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, SOURCE
from alpaca_ross_bot.risk_journal import RiskJournal
from alpaca_ross_bot.strategy_risk import VERSION
from test_incident_journal import _observation
from test_risk_account_inputs import REF
from test_risk_input_evidence import AT, evidence, mark_reference
from test_risk_journal import inputs


def calendar(journal: Journal) -> int:
    day = AT.date().isoformat()
    payload = {
        "account_ref": REF,
        "endpoint": ENDPOINT,
        "request": {
            "method": "GET",
            "path": f"/v2/calendar?start={day}&end={day}",
            "status": 200,
            "started_at": AT.isoformat(),
            "completed_at": AT.isoformat(),
            "body": json.dumps([{"date": day, "open": "09:30", "close": "16:00"}]),
        },
    }
    return journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind="CALENDAR_INPUT_CAPTURE",
        payload=json.dumps(payload).encode(),
        system_first_seen_at=AT,
        processed_at=AT,
    ).sequence


def policy() -> IncidentPolicy:
    return IncidentPolicy(
        REF,
        VERSION,
        "risk-binding-scope",
        "AAPL",
        ("P11", "P12", "P13"),
        "sha256:" + "b" * 64,
    )


def request(
    journal: Journal, capture: int, *, nonflat: bool = False
) -> IncidentRiskRequest:
    return IncidentRiskRequest(
        capture,
        calendar(journal),
        timedelta(seconds=30),
        timedelta(days=1),
        (mark_reference(journal),) if nonflat else (),
    )


def flat_observation(watermark: int = 1) -> IncidentObservation:
    old = _observation(watermark=watermark)
    return replace(
        old,
        account_ref=REF,
        policy_version=VERSION,
        scope_id=policy().scope_id,
        orders=(),
        position_quantity=old.baseline_quantity,
    )


def test_source_qualified_flat_inputs_cannot_self_grant_runtime(tmp_path: Path) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        owner.observe(flat_observation(), AT)
        result = owner.observe_risk("risk-1", request(journal, capture), AT)
        assert result["reported_inputs_qualified"] is True
        assert result["daily_state_present"] is False
        assert result["runtime_authorized"] is False
        state = owner.view()
        assert state is not None and not state.new_exposure_allowed
        assert "BOUND_RISK_DAILY_STATE_MISSING" in state.reasons
        assert owner.verify_rebuild()
        wrapper = journal.read_state(owner.state_key)
        assert isinstance(wrapper, dict) and wrapper["schema"] == 2


def test_actual_inventory_disagreement_cannot_be_hidden_by_caller_flags(
    tmp_path: Path,
) -> None:
    with evidence(tmp_path) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        owner.observe(flat_observation(), AT)  # caller says flat; source says two
        result = owner.observe_risk(
            "risk-1", request(journal, capture, nonflat=True), AT
        )
        assert result["ownership_verified"] is True
        assert result["bid_valuation_qualified"] is True
        state = owner.view()
        assert state is not None and "BOUND_RISK_POSITION_MISMATCH" in state.reasons


def test_missing_bid_or_invalid_source_produces_durable_veto(tmp_path: Path) -> None:
    with evidence(tmp_path) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        owner.observe(flat_observation(), AT)
        result = owner.observe_risk("no-bid", request(journal, capture), AT)
        assert result["bid_valuation_qualified"] is False
        state = owner.view()
        assert state is not None
        assert "BOUND_RISK_REPORTED_INPUTS_UNQUALIFIED" in state.reasons
        bad = replace(request(journal, capture), capture_sequence=99999)
        result = owner.observe_risk("missing-source", bad, AT)
        reasons = result["reasons"]
        assert isinstance(reasons, list)
        assert "BOUND_RISK_SOURCE_INVALID_OR_UNAVAILABLE" in reasons
        assert owner.verify_rebuild()


def test_fixture_daily_stop_is_preserved_without_production_qualification(
    tmp_path: Path,
) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        base = inputs()
        fixture = replace(
            base,
            account=replace(base.account, account_ref=REF, session=AT.date()),
            ledger=replace(
                base.ledger,
                account_ref=REF,
                session=AT.date(),
                realized=-base.account.equity,
            ),
            observed_at=AT,
            presession_equity_at=AT.replace(hour=12, minute=0, second=0),
            session_open=AT.replace(hour=13, minute=30, second=0),
            input_event_refs=(capture,),
        )
        risk = RiskJournal(journal, REF, lambda refs: fixture)
        risk.observe("loss", (capture,), AT)
        owner = IncidentJournal(journal, policy())
        result = owner.observe_risk("risk-1", request(journal, capture), AT)
        assert result["daily_stop_observed"] is True
        assert result["daily_stop_latched"] is True
        reasons = result["reasons"]
        assert isinstance(reasons, list)
        assert "BOUND_RISK_DAILY_STATE_OFFLINE_FIXTURE_ONLY" in reasons
        state = owner.observe(flat_observation(), AT)
        assert state.daily_stop_latched and not state.new_exposure_allowed
        assert "BOUND_RISK_REFRESH_REQUIRED" in state.reasons
        restarted = IncidentJournal(journal, policy())
        restarted_state = restarted.view()
        assert restarted_state is not None and restarted_state.daily_stop_latched
        assert restarted.verify_rebuild()


def test_new_observation_cannot_clear_bound_risk_reasons(tmp_path: Path) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        owner.observe_risk("risk-1", request(journal, capture), AT)
        state = owner.observe(replace(flat_observation(), recovery_verified=True), AT)
        assert "BOUND_RISK_RUNTIME_OWNER_UNQUALIFIED" in state.reasons
        assert not state.new_exposure_allowed


def test_bound_risk_conflict_before_first_observation_is_sticky(tmp_path: Path) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        selected = request(journal, capture)
        owner.observe_risk("same-id", selected, AT)
        owner.observe_risk("same-id", replace(selected, capture_sequence=99999), AT)
        owner.observe_risk("next-id", selected, AT)
        state = owner.observe(flat_observation(), AT)
        assert "EVENT_ID_CONFLICT" in state.reasons
        assert owner.verify_rebuild()


def test_bound_commit_failure_does_not_publish_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        selected = request(journal, capture)
        before = journal.read_state(owner.state_key)

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("synthetic commit failure")

        monkeypatch.setattr(journal, "apply_incident_event", fail)
        with pytest.raises(OSError):
            owner.observe_risk("risk-1", selected, AT)
        assert journal.read_state(owner.state_key) == before


def test_bound_risk_wrapper_tamper_rejected(tmp_path: Path) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        owner.observe_risk("risk-1", request(journal, capture), AT)
        state = journal.read_state(owner.state_key)
        assert isinstance(state, dict) and isinstance(state["risk"], dict)
        state["risk"]["runtime_authorized"] = True
        journal.append_next(
            source_id="synthetic-corruption",
            feed="paper",
            event_kind="TEST",
            payload=b"{}",
            system_first_seen_at=AT,
            processed_at=AT,
            state_updates={owner.state_key: state},
        )
        with pytest.raises(JournalError, match="evidence"):
            IncidentJournal(journal, policy())


def test_failed_risk_evidence_preserves_existing_protection(tmp_path: Path) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        old = _observation()
        observation = replace(
            old,
            account_ref=REF,
            policy_version=VERSION,
            scope_id=policy().scope_id,
            orders=tuple(
                replace(row, order=replace(row.order, account_ref=REF))
                for row in old.orders
            ),
        )
        before = owner.observe(observation, AT)
        selected = replace(request(journal, capture), capture_sequence=99999)
        owner.observe_risk("failed-inputs", selected, AT)
        after = owner.view()
        assert after is not None
        assert (
            after.preserve_protection_ids
            == before.preserve_protection_ids
            == ("stop-1",)
        )
        assert after.expected_quantity == before.expected_quantity
        assert not after.new_exposure_allowed


def test_new_intent_does_not_rewrite_history_but_refresh_detects_it(
    tmp_path: Path,
) -> None:
    with evidence(tmp_path, quantity=None) as (journal, capture):
        owner = IncidentJournal(journal, policy())
        selected = request(journal, capture)
        original = owner.observe_risk("initial", selected, AT)
        assert original["reported_inputs_qualified"] is True
        journal.reserve_order_intent("later-new-entry", {"paper_account_ref": REF})
        assert owner.verify_rebuild()
        refreshed = owner.observe_risk("refresh", selected, AT)
        assert refreshed["reported_inputs_qualified"] is False
        assert owner.verify_rebuild()
