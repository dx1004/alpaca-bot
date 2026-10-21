"""OCO research uses the existing parent entry and canonical risk slot only."""

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.entry_strategy import EntryDecision
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_research import OcoResearchContract, load_oco_research_contract
from alpaca_ross_bot.research_risk_binding import (
    OCO_EXECUTION_IDENTITY,
    _declared_research_plan,
    _research_declaration,
)
from test_account_risk_owner import account_sources, view
from test_observation_candidate_binding import AT, CID, contract, new_decision
from test_research_risk_binding import decision as legacy_decision
from test_risk_ledger_audit import POLICY, REF


@pytest.fixture(scope="module")
def candidate() -> EntryDecision:
    return new_decision()


def test_oco_opt_in_keeps_parent_signal_and_one_source_risk_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: EntryDecision
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(AT)
        args: dict[str, Any] = {
            "client_order_id": CID,
            "decision": candidate,
            "observation_contract": contract(),
            "oco_contract": load_oco_research_contract(),
        }
        result = owner.reserve_research_decision("oco", selection, AT, **args)
        assert result["quantity"] == 4
        assert result["execution_research_binding"] == OCO_EXECUTION_IDENTITY
        state = view(owner)
        declaration = state["held"]["research_signal"]
        assert candidate.signal is not None
        assert declaration["version"] == candidate.signal.version
        assert declaration["signal_id"] == candidate.signal.signal_id
        assert declaration["execution_research_binding"] == OCO_EXECUTION_IDENTITY
        assert declaration["strategy_version"] == result["strategy_version"]
        assert declaration["version"] != OCO_EXECUTION_IDENTITY["candidate_version"]
        assert state["held"]["limit"] == "10.01"
        assert state["held"]["stop"] == "9.50"
        assert source.journal.read_order_intent_ids() == ()
        assert not candidate.state.attempted
        assert result["strategy_plan_qualified"] is False
        assert result["runtime_authorized"] is False
        assert owner.reserve_research_decision("oco", selection, AT, **args) == result
        assert len(source.journal.read_account_risk_events()) == 1
        assert view(AccountRiskOwner(source.journal, REF, POLICY)) == state
        assert owner.verify_rebuild()
        second = owner.reserve_research_decision(
            "parent-other",
            selection,
            AT,
            client_order_id="second-parent",
            decision=candidate,
            observation_contract=contract(),
        )
        assert second["quantity"] == 0 and second["reason"] == "SLOT_OCCUPIED"
        assert view(owner)["held"] == state["held"]
        result["execution_research_binding"]["runtime_authorized"] = True
        assert view(owner)["held"] == state["held"]


@pytest.mark.parametrize("fault", ["missing_parent", "legacy_entry", "forged_contract"])
def test_oco_opt_in_rejects_missing_or_mixed_parent_without_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate: EntryDecision,
    fault: str,
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        with pytest.raises(JournalError):
            owner.reserve_research_decision(
                "invalid",
                source.selection(AT),
                AT,
                client_order_id=CID,
                decision=legacy_decision() if fault == "legacy_entry" else candidate,
                observation_contract=None if fault == "missing_parent" else contract(),
                oco_contract=(
                    cast(OcoResearchContract, "forged")
                    if fault == "forged_contract"
                    else load_oco_research_contract()
                ),
            )
        assert owner.view() is None
        assert not source.journal.read_account_risk_events()
        assert not source.journal.read_order_intent_ids()


@pytest.mark.parametrize(
    "key,value",
    [
        ("candidate_version", "ROSS_SMALL_ACCOUNT_CANDIDATE_V1"),
        ("entry_candidate_version", "wrong-parent"),
        ("execution_contract_sha256", "f" * 64),
        ("runtime_authorized", True),
        ("runtime_authorized", 0),
        ("entry_risk_version_binding_accepted", True),
        ("order_authority", True),
    ],
)
def test_replay_cannot_promote_or_relabel_execution_binding(
    candidate: EntryDecision, key: str, value: object
) -> None:
    declaration = _research_declaration(
        candidate,
        observation_contract=contract(),
        oco_contract=load_oco_research_contract(),
    )
    altered = deepcopy(declaration)
    altered["execution_research_binding"][key] = value
    with pytest.raises(JournalError, match="execution binding"):
        _declared_research_plan(CID, altered, AT)
    assert declaration["execution_research_binding"] == OCO_EXECUTION_IDENTITY


def test_parent_reservation_cannot_be_retroactively_upgraded_to_oco(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: EntryDecision
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selection = source.selection(AT)
        assert (
            owner.reserve_research_decision(
                "parent",
                selection,
                AT,
                client_order_id=CID,
                decision=candidate,
                observation_contract=contract(),
            )["quantity"]
            == 4
        )
        original = view(owner)["held"]
        changed = owner.reserve_research_decision(
            "parent",
            selection,
            AT,
            client_order_id=CID,
            decision=candidate,
            observation_contract=contract(),
            oco_contract=load_oco_research_contract(),
        )
        assert changed["quantity"] == 0 and changed["runtime_authorized"] is False
        assert view(owner)["held"] == original
        assert view(owner)["veto"] is True
        assert "execution_research_binding" not in original["research_signal"]
        assert len(source.journal.read_account_risk_events()) == 2
        assert owner.verify_rebuild()


def test_oco_binding_and_shared_reservation_roll_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: EntryDecision
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected = source.selection(AT)
        append = Journal._append_locked

        def fail(
            journal: Journal,
            event: EventEnvelope,
            payload: bytes,
            updates: Mapping[str, object] | None,
        ) -> None:
            append(journal, event, payload, updates)
            if event.source_id == "ALPACA_ACCOUNT_RISK":
                raise OSError("synthetic OCO binding commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(Journal, "_append_locked", fail)
            with pytest.raises(OSError, match="OCO binding commit"):
                owner.reserve_research_decision(
                    "oco",
                    selected,
                    AT,
                    client_order_id=CID,
                    decision=candidate,
                    observation_contract=contract(),
                    oco_contract=load_oco_research_contract(),
                )
        assert owner.view() is None
        assert not source.journal.read_account_risk_events()
        assert owner.verify_rebuild()
        assert source.journal.read_order_intent_ids() == ()


def test_default_parent_declaration_has_no_oco_identity(
    candidate: EntryDecision,
) -> None:
    declaration = _research_declaration(candidate, observation_contract=contract())
    assert "execution_research_binding" not in declaration
    assert "OCO_FIRST" not in json.dumps(declaration)
