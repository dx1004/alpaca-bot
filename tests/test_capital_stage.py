"""Synthetic source facts cannot activate a leveraged strategy or broker orders."""

import json
import sqlite3
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import localcontext
from pathlib import Path
from threading import Barrier

import pytest

from alpaca_ross_bot import risk_baseline
from alpaca_ross_bot.capital_stage import POLICY, VERSIONS, CapitalStageOwner
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.risk_account_inputs import KIND, SOURCE
from test_risk_baseline import NOW, REF, freeze, setup
from test_risk_baseline_rollover import FLAT, append_flat_capture


def sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, baseline: bool = True
) -> tuple[Journal, int, int]:
    journal, account, calendar = setup(tmp_path, account=FLAT)
    if baseline:
        monkeypatch.setattr(risk_baseline, "_now", lambda: NOW)
        freeze(journal, account, calendar)
    return journal, account, calendar


def reported(
    journal: Journal,
    at: datetime,
    equity: str,
    **fields: object,
) -> tuple[int, int]:
    account, calendar = append_flat_capture(journal, at, equity=equity)
    if not fields:
        return account, calendar
    saved = journal.read_event(account)
    assert saved is not None
    data = json.loads(saved[1])
    for request in data["requests"]:
        if request["path"] == "/v2/account":
            body = json.loads(request["body"])
            body.update(fields)
            request["body"] = json.dumps(body)
    changed = journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(data).encode(),
        system_first_seen_at=at,
        processed_at=at,
    )
    return changed.sequence, calendar


def assess(
    owner: CapitalStageOwner,
    account: int,
    calendar: int,
    at: datetime = NOW,
    key: str = "first",
) -> dict[str, object]:
    return owner.assess(
        key,
        account_sequence=account,
        calendar_sequence=calendar,
        decision_at=at,
        max_age=timedelta(seconds=30),
    )


def test_initial_source_bound_stage_retains_separate_versions_and_no_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar = sources(tmp_path, monkeypatch)
    try:
        owner = CapitalStageOwner(journal, REF)
        state = assess(owner, account, calendar)
        assert state["versions"] == VERSIONS
        observation = state["observation"]
        assert isinstance(observation, dict)
        assert observation["capital_stage"] == "NO_BORROWING_RESEARCH"
        assert observation["current_equity_threshold_reached"] is False
        assert observation["e0_threshold_reached"] is False
        assert observation["reported_multiplier"] == "1"
        assert observation["risk_gate"] == "BLOCK_NEW_EXPOSURE"
        assert not observation["borrowed_leverage_authorized"]
        assert not observation["strategy_switch_authorized"]
        assert not state["runtime_authorized"] and owner.verify_rebuild()
        assert len(journal.read_capital_stage_events()) == 1
        assert assess(owner, account, calendar) == state
        assert len(journal.read_capital_stage_events()) == 1
        for seq, digest in observation["source_refs"]:
            source = journal.read_event(seq)
            assert source is not None and source[0].payload_ref == digest
        journal.close()
        root = tmp_path / "private"
        journal = Journal.open_private(root / "events.sqlite3", root)
        assert CapitalStageOwner(journal, REF).view() == state
        public = json.dumps(CapitalStageOwner(journal, REF).public_summary())
        assert REF not in public and '"500"' not in public
    finally:
        journal.close()


@pytest.mark.parametrize(
    "equity, threshold",
    [
        ("1999.9999999999999999", False),
        ("2000", True),
        ("2000.0000000000000001", True),
        ("5000", True),
    ],
)
@pytest.mark.parametrize("multiplier", ["1", "2", "4", "6"])
def test_threshold_is_review_only_and_never_infers_entitlement_or_changes_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    equity: str,
    threshold: bool,
    multiplier: str,
) -> None:
    journal, _, _ = sources(tmp_path, monkeypatch)
    try:
        account, calendar = reported(
            journal, NOW, equity, multiplier=multiplier, buying_power="30000"
        )
        with localcontext() as context:
            context.prec = 1
            state = assess(CapitalStageOwner(journal, REF), account, calendar)
        observation = state["observation"]
        assert isinstance(observation, dict)
        assert observation["current_equity_threshold_reached"] is threshold
        assert observation["e0_threshold_reached"] is False  # independent E0
        assert observation["reported_multiplier"] == multiplier
        assert observation["reported_buying_power"] == "30000"
        assert observation["broker_entitlement"] == "UNVERIFIED"
        assert observation["higher_stage"] == "BLOCKED_PENDING_SEPARATE_ACCEPTANCE"
        assert observation["separate_leveraged_paper_acceptance"] == "UNACCEPTED"
        assert not observation["borrowed_leverage_authorized"]
        assert not observation["strategy_switch_authorized"]
        assert state["versions"] == VERSIONS and not state["runtime_authorized"]
        assert ("REPORTED_MULTIPLIER_UNQUALIFIED" in observation["reasons"]) is (
            multiplier == "6"
        )
    finally:
        journal.close()


def test_downcrossing_and_history_survive_restart_without_strategy_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, _, _ = sources(tmp_path, monkeypatch)
    try:
        owner = CapitalStageOwner(journal, REF)
        for index, (equity, count) in enumerate(
            [
                ("2500", 0),
                ("1500", 1),
                ("2200", 1),
                ("1800", 2),
            ]
        ):
            at = NOW + timedelta(seconds=index)
            account, calendar = reported(journal, at, equity)
            state = assess(owner, account, calendar, at, str(index))
            assert state["downgrade_count"] == count
            assert state["review_withdrawn"] is (equity in {"1500", "1800"})
            assert state["versions"] == VERSIONS and not state["runtime_authorized"]
        assert len(journal.read_capital_stage_events()) == 4
        journal.close()
        root = tmp_path / "private"
        journal = Journal.open_private(root / "events.sqlite3", root)
        assert CapitalStageOwner(journal, REF).view() == state
    finally:
        journal.close()


def test_missing_e0_does_not_hide_reported_threshold_or_fabricate_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, _, _ = sources(tmp_path, monkeypatch, baseline=False)
    try:
        account, calendar = reported(journal, NOW, "2500", multiplier="2")
        state = assess(CapitalStageOwner(journal, REF), account, calendar)
        observation = state["observation"]
        assert isinstance(observation, dict)
        assert observation["current_equity_threshold_reached"] is True
        assert observation["e0_threshold_reached"] is None
        assert "PRESESSION_E0_UNAVAILABLE" in observation["reasons"]
        assert not state["runtime_authorized"]
        assert not journal.read_risk_baseline_events()
    finally:
        journal.close()


@pytest.mark.parametrize(
    "field", ["trading_blocked", "account_blocked", "trade_suspended_by_user"]
)
def test_blocked_account_has_unqualified_stage_but_retains_independent_source_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    journal, _, _ = sources(tmp_path, monkeypatch)
    try:
        account, calendar = reported(journal, NOW, "2500", **{field: True})
        state = assess(CapitalStageOwner(journal, REF), account, calendar)
        observation = state["observation"]
        assert isinstance(observation, dict)
        assert observation["capital_stage"] == "UNQUALIFIED"
        assert observation["current_equity_threshold_reached"] is True
        assert "ACCOUNT_FLAGS_UNQUALIFIED" in observation["reasons"]
        assert observation["higher_stage"] == "BLOCKED_PENDING_SEPARATE_ACCEPTANCE"
    finally:
        journal.close()


def test_later_same_clock_e0_cannot_rewrite_old_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar = sources(tmp_path, monkeypatch, baseline=False)
    try:
        owner = CapitalStageOwner(journal, REF)
        before = assess(owner, account, calendar)
        monkeypatch.setattr(risk_baseline, "_now", lambda: NOW)
        freeze(journal, account, calendar)
        assert owner.verify_rebuild() and owner.view() == before
        assert assess(owner, account, calendar) == before
        assert len(journal.read_capital_stage_events()) == 1
    finally:
        journal.close()


@pytest.mark.parametrize("fault", ["foreign", "stale", "changed_retry", "backwards"])
def test_invalid_source_or_retry_cannot_mutate_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    journal, account, calendar = sources(tmp_path, monkeypatch)
    try:
        if fault == "foreign":
            with pytest.raises(JournalError, match="not bound"):
                CapitalStageOwner(journal, "foreign")
            return
        owner = CapitalStageOwner(journal, REF)
        old = assess(owner, account, calendar)
        if fault == "stale":
            with pytest.raises(ValueError, match="freshness|stale"):
                assess(owner, account, calendar, NOW + timedelta(seconds=60), "stale")
        else:
            new_account, new_calendar = reported(
                journal, NOW + timedelta(seconds=1), "2500"
            )
            if fault == "changed_retry":
                with pytest.raises(JournalError, match="retry command"):
                    assess(owner, new_account, new_calendar, NOW + timedelta(seconds=1))
            else:
                assess(
                    owner, new_account, new_calendar, NOW + timedelta(seconds=1), "new"
                )
                current_state = owner.view()
                assert current_state is not None
                old = current_state
                with pytest.raises(JournalError, match="backwards"):
                    assess(owner, account, calendar, NOW, "backwards")
        assert owner.view() == old and owner.verify_rebuild()
    finally:
        journal.close()


@pytest.mark.parametrize("component", ["projection", "dedupe"])
def test_capital_projection_and_deduplication_tampering_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str
) -> None:
    journal, account, calendar = sources(tmp_path, monkeypatch)
    try:
        owner = CapitalStageOwner(journal, REF)
        state = assess(owner, account, calendar)
        changed = dict(state) | {"downgrade_count": 999}
        with sqlite3.connect(tmp_path / "private" / "events.sqlite3") as db:
            db.execute(
                "UPDATE state SET value_json=? WHERE key=?",
                (
                    json.dumps(changed) if component == "projection" else "{}",
                    f"capital_stage:{REF}:{POLICY}"
                    if component == "projection"
                    else f"capital_stage_evidence:{REF}:first",
                ),
            )
        assert not owner.verify_rebuild()
        with pytest.raises(JournalError, match="immutable evidence"):
            CapitalStageOwner(journal, REF)
    finally:
        journal.close()


def test_concurrent_retry_has_one_atomic_source_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar = sources(tmp_path, monkeypatch)
    journal.close()
    barrier = Barrier(2)
    root = tmp_path / "private"

    def observe(_: int) -> dict[str, object]:
        connected = Journal.open_private(root / "events.sqlite3", root)
        try:
            owner = CapitalStageOwner(connected, REF)
            barrier.wait(timeout=5)
            return assess(owner, account, calendar)
        finally:
            connected.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(observe, (1, 2)))
    journal = Journal.open_private(root / "events.sqlite3", root)
    try:
        assert results[0] == results[1] == CapitalStageOwner(journal, REF).view()
        assert len(journal.read_capital_stage_events()) == 1
    finally:
        journal.close()


def test_failed_commit_rolls_back_history_projection_and_dedupe_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar = sources(tmp_path, monkeypatch)
    try:
        owner = CapitalStageOwner(journal, REF)
        original = journal._append_locked

        def fail(
            event: EventEnvelope, payload: bytes, updates: Mapping[str, object] | None
        ) -> None:
            original(event, payload, updates)
            raise RuntimeError("synthetic commit failure")

        monkeypatch.setattr(journal, "_append_locked", fail)
        with pytest.raises(RuntimeError, match="commit failure"):
            assess(owner, account, calendar)
        assert owner.view() is None and owner.verify_rebuild()
        assert not journal.read_capital_stage_events()
        assert not journal.read_capital_stage_evidence_cache(REF)
        monkeypatch.setattr(journal, "_append_locked", original)
        assert assess(owner, account, calendar)
        assert owner.verify_rebuild()
    finally:
        journal.close()
