"""Explicit new research identity; synthetic features/account evidence, no orders."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction as F
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner, EntryRiskPlan
from alpaca_ross_bot.entry_strategy import (
    EntryDecision,
    EntryState,
    Quote,
    evaluate_entry,
)
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.observation_admission import (
    CANDIDATE_VERSION,
    CONTRACT_SHA256,
    PATH,
    ObservationContract,
    load_observation_contract,
)
from alpaca_ross_bot.research_risk_binding import (
    OBSERVATION_IDENTITY,
    RESEARCH_BINDING,
    _declared_research_plan,
    _research_declaration,
)
from alpaca_ross_bot.strategy_attempts import ResearchAttempts
from alpaca_ross_bot.strategy_features import (
    VERSION,
    Bar,
    Slot,
    Trend,
    build_trend,
    expected_history,
)
from test_account_risk_owner import account_sources, view
from test_entry_strategy import HEALTH, OPEN, prior, trade
from test_research_risk_binding import decision as original_decision
from test_risk_ledger_audit import POLICY, REF
from test_strategy_features import bar, session

ROOT = Path(__file__).resolve().parents[1]
AT = OPEN + timedelta(seconds=62)
CID = "observation-candidate-entry"


def contract() -> ObservationContract:
    return load_observation_contract((ROOT / PATH).read_bytes())


def feature_trend() -> Trend:
    current = session()
    history = tuple(session(current.day - timedelta(days=i)) for i in range(100, 0, -1))
    daily_slots = expected_history(history, None, current.open_at)
    hourly_slots = expected_history(history, 60, current.open_at)

    def scaled(item: Bar) -> Bar:
        return replace(
            item,
            symbol="AAPL",
            open=item.open / 20,
            high=item.high / 20,
            low=item.low / 20,
            close=item.close / 20,
        )

    daily = tuple(scaled(bar(slot, 100 + i)) for i, slot in enumerate(daily_slots))
    minutes = tuple(
        scaled(
            bar(
                Slot(
                    slot.start + timedelta(minutes=i),
                    slot.start + timedelta(minutes=i + 1),
                ),
                100 + n,
            )
        )
        for n, slot in enumerate(hourly_slots)
        for i in range(int((slot.end - slot.start).total_seconds() // 60))
    )
    inherited = build_trend("AAPL", current, history, daily, minutes)
    variant = build_trend(
        "AAPL", current, history, daily, minutes, observation_contract=contract()
    )
    assert variant.version == CANDIDATE_VERSION
    assert replace(variant, version=VERSION) == inherited
    assert len(variant.evidence_refs) == 200
    return variant


def new_decision(trend: Trend | None = None) -> EntryDecision:
    trend = feature_trend() if trend is None else trend
    state = EntryState("AAPL", trend.session.day, version=CANDIDATE_VERSION)
    result: EntryDecision | None = None
    for name, seconds, price in (
        ("first", 1, F(21, 2)),
        ("arm", 61, F(48, 5)),
        ("trigger", 62, F(2001, 200)),
    ):
        tick = replace(trade(name, seconds, price), symbol="AAPL")
        quote = Quote(
            "AAPL",
            tick.source_at,
            tick.received_at,
            price - F(1, 200),
            price + F(1, 200),
            10,
            10,
            "synthetic-conditions",
            True,
        )
        result = evaluate_entry(
            state,
            trend,
            tick,
            quote,
            replace(prior(), symbol="AAPL"),
            HEALTH,
            tick.received_at,
            observation_contract=contract(),
        )
        state = result.state
    assert result is not None and result.signal is not None
    assert result.outcome == "RESEARCH_SIGNAL"
    return result


def test_pinned_feature_entry_risk_link_keeps_shared_risk_profile_and_no_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = new_decision()
    assert candidate.signal is not None
    assert candidate.signal.version == CANDIDATE_VERSION
    assert candidate.signal.signal_id.startswith(CANDIDATE_VERSION + ":")
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected = source.selection(AT)
        result = owner.reserve_research_decision(
            "new-candidate",
            selected,
            AT,
            client_order_id=CID,
            decision=candidate,
            observation_contract=contract(),
        )
        assert result["quantity"] == 4 and result["reason"] == "FEASIBLE"
        assert result["strategy_version"] == CANDIDATE_VERSION
        assert result["risk_profile_version"] == VERSION
        assert result["data_contract_sha256"] == CONTRACT_SHA256
        assert result["volume_source_age"] == "UNKNOWN"
        assert result["source_admission_qualified"] is False
        assert result["strategy_plan_qualified"] is False
        assert result["runtime_authorized"] is False
        state = view(owner)
        assert state["version"] == VERSION
        assert state["held"]["limit"] == "10.01" and state["held"]["stop"] == "9.50"
        declared = state["held"]["research_signal"]
        assert declared["classification"] == RESEARCH_BINDING
        assert all(
            declared[key] == value for key, value in OBSERVATION_IDENTITY.items()
        )
        saved = json.loads(source.journal.read_account_risk_events()[-1][1])
        assert saved["research_signal"] == declared and saved["version"] == VERSION
        assert (
            not candidate.state.attempted and candidate.state.pending_signal is not None
        )
        assert source.journal.read_order_intent_ids() == ()
        assert owner.verify_rebuild()
        assert (
            owner.reserve_research_decision(
                "new-candidate",
                selected,
                AT,
                client_order_id=CID,
                decision=candidate,
                observation_contract=contract(),
            )
            == result
        )
        assert len(source.journal.read_account_risk_events()) == 1
        assert view(AccountRiskOwner(source.journal, REF, POLICY)) == state


def test_default_legacy_entry_and_risk_reject_new_candidate_without_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = new_decision()
    assert candidate.state.trend_snapshot is not None
    tick = replace(trade("later", 63, F(2001, 200)), symbol="AAPL")
    quote = Quote(
        "AAPL",
        tick.source_at,
        tick.received_at,
        F(10),
        F(1001, 100),
        10,
        10,
        "synthetic-conditions",
        True,
    )
    rejected = evaluate_entry(
        candidate.state,
        candidate.state.trend_snapshot,
        tick,
        quote,
        replace(prior(), symbol="AAPL"),
        HEALTH,
        tick.received_at,
    )
    assert rejected.signal is None and rejected.outcome == "UNAVAILABLE"
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        with pytest.raises(JournalError, match="matching pending"):
            owner.reserve_research_decision(
                "no-opt-in",
                source.selection(AT),
                AT,
                client_order_id=CID,
                decision=candidate,
            )
        assert owner.view() is None and source.journal.read_order_intent_ids() == ()


@pytest.mark.parametrize("part", ["state", "trend", "signal", "pending"])
def test_mixed_candidate_identities_never_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, part: str
) -> None:
    candidate = new_decision()
    assert candidate.signal is not None and candidate.state.trend_snapshot is not None
    state, signal = candidate.state, candidate.signal
    if part == "state":
        state = replace(state, version=VERSION)
    elif part == "trend":
        selected_trend = state.trend_snapshot
        assert selected_trend is not None
        state = replace(state, trend_snapshot=replace(selected_trend, version=VERSION))
    elif part == "signal":
        signal = replace(signal, version=VERSION)
        state = replace(state, pending_signal=signal)
    else:
        state = replace(state, pending_signal=replace(signal, version=VERSION))
    mixed = replace(candidate, state=state, signal=signal)
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        with pytest.raises(JournalError):
            owner.reserve_research_decision(
                "mixed",
                source.selection(AT),
                AT,
                client_order_id=CID,
                decision=mixed,
                observation_contract=contract(),
            )
        assert owner.view() is None and not source.journal.read_account_risk_events()


@pytest.mark.parametrize(
    "field,value",
    [
        ("data_contract_sha256", "0" * 64),
        ("volume_source_age", "QUALIFIED"),
        ("risk_profile_version", CANDIDATE_VERSION),
        ("source_admission_qualified", True),
        ("signal_id", "HYBRID_500_IEX_INTRADAY_RESEARCH_V1:wrong"),
    ],
)
def test_replay_refuses_mixed_hash_or_claimed_source_admission(
    field: str, value: object
) -> None:
    declared = _research_declaration(new_decision(), observation_contract=contract())
    declared[field] = value
    with pytest.raises(JournalError):
        _declared_research_plan(CID, declared, AT)


def test_parsed_admission_like_object_is_not_opt_in_or_risk_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = cast(
        ObservationContract,
        SimpleNamespace(
            candidate_version=CANDIDATE_VERSION,
            sha256=CONTRACT_SHA256,
            observation_age_component_qualified=True,
            runtime_authorized=True,
        ),
    )
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        with pytest.raises(ValueError, match="pinned observation-age"):
            owner.reserve_research_decision(
                "forged-admission",
                source.selection(AT),
                AT,
                client_order_id=CID,
                decision=new_decision(),
                observation_contract=fake,
            )
        assert owner.view() is None and source.journal.read_order_intent_ids() == ()


def test_shared_canonical_slot_prevents_cross_candidate_double_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected = source.selection(AT)
        assert (
            owner.reserve(
                "original",
                selected,
                AT,
                EntryRiskPlan("old-slot", "AAPL", "10.01", "9.50"),
            )["quantity"]
            == 4
        )
        old = view(owner)["held"]
        blocked = owner.reserve_research_decision(
            "new-slot",
            selected,
            AT,
            client_order_id=CID,
            decision=new_decision(),
            observation_contract=contract(),
        )
        assert blocked["quantity"] == 0 and blocked["reason"] == "SLOT_OCCUPIED"
        assert view(owner)["held"] == old
        assert owner.verify_rebuild() and source.journal.read_order_intent_ids() == ()


def test_explicit_new_opt_in_cannot_relabel_old_entry_or_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = original_decision()
    old_trend = original.state.trend_snapshot
    assert old_trend is not None
    tick = replace(trade("later", 63, F(2001, 200)), symbol="AAPL")
    quote = Quote(
        "AAPL",
        tick.source_at,
        tick.received_at,
        F(10),
        F(1001, 100),
        10,
        10,
        "synthetic-conditions",
        True,
    )
    result = evaluate_entry(
        EntryState("AAPL", old_trend.session.day),
        old_trend,
        tick,
        quote,
        replace(prior(), symbol="AAPL"),
        HEALTH,
        tick.received_at,
        observation_contract=contract(),
    )
    assert result.signal is None and result.outcome == "UNAVAILABLE"
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        with pytest.raises(JournalError, match="matching pending"):
            owner.reserve_research_decision(
                "no-relabel",
                source.selection(AT),
                AT,
                client_order_id=CID,
                decision=original,
                observation_contract=contract(),
            )
        assert owner.view() is None and source.journal.read_order_intent_ids() == ()


def test_legacy_attempt_owner_refuses_new_namespace_without_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, _):
        candidate = new_decision()
        with pytest.raises(JournalError, match="account/version/session"):
            ResearchAttempts(source.journal, ROOT).recover(candidate.state)
        assert source.journal.read_order_intent_ids() == ()
        original = _research_declaration(original_decision())
        assert original["version"] == VERSION
        assert not set(OBSERVATION_IDENTITY) & set(original)


def test_new_identity_and_slot_rollback_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, owner):
        selected, candidate = source.selection(AT), new_decision()
        original = Journal._append_locked

        def fail(
            self: Journal,
            event: EventEnvelope,
            payload: bytes,
            updates: Mapping[str, object] | None,
        ) -> None:
            original(self, event, payload, updates)
            if event.source_id == "ALPACA_ACCOUNT_RISK":
                raise OSError("synthetic new-candidate failure")

        with monkeypatch.context() as patch:
            patch.setattr(Journal, "_append_locked", fail)
            with pytest.raises(OSError, match="new-candidate"):
                owner.reserve_research_decision(
                    "rollback",
                    selected,
                    AT,
                    client_order_id=CID,
                    decision=candidate,
                    observation_contract=contract(),
                )
        assert owner.view() is None and owner.verify_rebuild()
        assert not source.journal.read_account_risk_events()
        assert source.journal.read_order_intent_ids() == ()
