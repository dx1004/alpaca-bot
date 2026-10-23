import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.market_input_checks import assess_market_record
from alpaca_ross_bot.reported_price_policy import (
    assess_reported_price_record,
    classify_minute_conditions,
    load_reported_price_policy,
)
from alpaca_ross_bot.strategy_features import Session

RAW = Path("evidence/alpaca-reported-intraday-price-policy-v1.json").read_bytes()
OBSERVATION = Path(
    "evidence/iex-snapshot-observation-age-research-v1.json"
).read_bytes()
OCO = Path("evidence/oco-first-execution-research-v1.json").read_bytes()
POLICY = load_reported_price_policy(RAW, observation_parent=OBSERVATION, oco_parent=OCO)
NOW = datetime(2026, 10, 22, 14, tzinfo=UTC)
SESSION = Session(
    NOW.date(),
    NOW.replace(hour=13, minute=30),
    NOW.replace(hour=20),
    NOW - timedelta(hours=1),
    "declared-session",
)
TRADE: dict[str, object] = {
    "T": "t",
    "S": "AAPL",
    "p": "10.01",
    "s": 1,
    "x": "V",
    "i": 1,
    "z": "C",
    "c": ["@"],
    "t": "2026-10-22T14:00:00Z",
}
ROWS = [
    (tape, row["code"], row["minute_open_close"], row["minute_high_low"], row["volume"])
    for row in json.loads(RAW)["minute_condition_rows"]
    for tape in row["tapes"]
]


@pytest.mark.parametrize("tape,code,opening,highlow,volume", ROWS)
def test_each_pinned_minute_field_without_code_normalization(
    tape: str, code: str, opening: bool, highlow: bool, volume: bool
) -> None:
    result = classify_minute_conditions(POLICY, tape=tape, conditions=[code])
    assert (result.minute_open_close, result.minute_high_low, result.volume) == (
        opening,
        highlow,
        volume,
    )
    assert result.issues == ()


@pytest.mark.parametrize("which", ["raw", "observation", "oco"])
def test_whole_manifest_and_parent_bytes_pinned(which: str) -> None:
    with pytest.raises(ValueError, match="digest"):
        load_reported_price_policy(
            RAW + (b" " if which == "raw" else b""),
            observation_parent=OBSERVATION + (b" " if which == "observation" else b""),
            oco_parent=OCO + (b" " if which == "oco" else b""),
        )


def test_forged_dataclass_cannot_supply_a_different_policy_matrix() -> None:
    with pytest.raises(ValueError, match="altered"):
        classify_minute_conditions(
            replace(POLICY, rows=(("C", "@", False, False, False),)),
            tape="C",
            conditions=["@"],
        )


def capture(
    tmp_path: Path,
    record: dict[str, object],
    *,
    purpose: str = "TRIGGER",
    session: Session = SESSION,
    horizon: int | None = None,
    payload_ref: str | None = None,
) -> Any:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    event = journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="RAW_FRAME",
        payload=json.dumps([record]).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    try:
        return assess_reported_price_record(
            journal,
            POLICY,
            journal_scope="declared-capture",
            through_sequence=event.sequence if horizon is None else horizon,
            sequence=event.sequence,
            record_index=0,
            payload_ref=event.payload_ref if payload_ref is None else payload_ref,
            decision_at=NOW,
            session=session,
            purpose=purpose,
        )
    finally:
        journal.close()


@pytest.mark.parametrize("purpose", POLICY.purposes)
def test_raw_bound_price_component_never_grants_later_gates(
    tmp_path: Path, purpose: str
) -> None:
    result = capture(tmp_path, TRADE, purpose=purpose)
    assert result.component_qualified and result.reported_price == Fraction(1001, 100)
    assert result.sources[0].journal_scope == "declared-capture"
    assert result.sources[0].payload_ref.startswith("sha256:")
    assert result.sources[0].record_index == 0
    assert not any(
        (
            result.source_history_qualified,
            result.whole_source_admission_qualified,
            result.strategy_eligible,
            result.runtime_authorized,
            result.order_authority,
            result.formal_paper_authorized,
        )
    )


@pytest.mark.parametrize("kind", ["c", "x", "u"])
def test_revision_record_never_becomes_new_live_price(
    tmp_path: Path, kind: str
) -> None:
    result = capture(tmp_path, {**TRADE, "T": kind})
    assert not result.component_qualified
    assert "REVISION_NOT_A_NEW_LIVE_PRINT" in result.issues
    assert result.reported_price is None


@pytest.mark.parametrize("fault", ["future_horizon", "wrong_hash"])
def test_raw_hash_and_horizon_identity_required(tmp_path: Path, fault: str) -> None:
    with pytest.raises(ValueError):
        capture(
            tmp_path,
            TRADE,
            horizon=2 if fault == "future_horizon" else None,
            payload_ref="sha256:" + "a" * 64 if fault == "wrong_hash" else None,
        )


def test_declared_calendar_must_be_visible_at_decision(tmp_path: Path) -> None:
    result = capture(
        tmp_path,
        TRADE,
        session=replace(SESSION, available_at=NOW + timedelta(seconds=1)),
    )
    assert not result.component_qualified
    assert "SESSION_NOT_VISIBLE_AT_DECISION" in result.issues


def test_volume_only_print_does_not_supply_live_price(tmp_path: Path) -> None:
    result = capture(tmp_path, {**TRADE, "c": ["I"]})
    assert result.conditions.volume is True
    assert result.conditions.minute_open_close is False
    assert not result.component_qualified


def test_existing_unqualified_baseline_is_not_changed(tmp_path: Path) -> None:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    event = journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="RAW_FRAME",
        payload=json.dumps([TRADE]).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    old = assess_market_record(
        journal,
        sequence=event.sequence,
        record_index=0,
        payload_ref=event.payload_ref,
        decision_at=NOW,
    )
    assert not old.runtime_eligible and "CONDITION_POLICY_UNQUALIFIED" in old.reasons
    journal.close()
