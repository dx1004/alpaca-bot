"""Daily-risk observation preserves offline provenance and historical cutoffs."""

import json
from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.risk_journal import RiskJournal
from test_risk_journal import ACCOUNT, AT, REFS, inputs, setup


def test_historical_read_retains_latch_and_has_no_side_effects(tmp_path: Path) -> None:
    journal, owner, facts = setup(tmp_path)
    owner.observe("initial", REFS, AT)
    facts[0] = replace(inputs(), ledger=replace(inputs().ledger, realized=Decimal(-5)))
    owner.observe("loss", REFS, AT + timedelta(seconds=1))
    facts[0] = inputs()
    owner.observe("later", REFS, AT + timedelta(seconds=2))
    events, current = journal.read_strategy_risk_events(), owner.view()
    writes = journal._db.total_changes
    state, refs = RiskJournal.read_evidence_at(journal, ACCOUNT, AT)
    assert state is not None and isinstance(state["daily"], dict)
    assert state["daily"]["latched"] is False
    assert refs == (1, events[0][0].sequence)
    latched, refs = RiskJournal.read_evidence_at(
        journal, ACCOUNT, AT + timedelta(seconds=1)
    )
    assert latched is not None and isinstance(latched["daily"], dict)
    assert latched["daily"]["latched"] is True
    assert latched["qualification"] == "OFFLINE_FIXTURE_ONLY"
    assert latched["runtime_authorized"] is False
    assert refs == (1, events[0][0].sequence, events[1][0].sequence)
    assert journal._db.total_changes == writes
    assert owner.view() == current and journal.read_strategy_risk_events() == events


def test_empty_historical_prefix_returns_no_state(tmp_path: Path) -> None:
    journal, owner, _ = setup(tmp_path)
    owner.observe("future", REFS, AT)
    assert RiskJournal.read_evidence_at(
        journal, ACCOUNT, AT - timedelta(seconds=1)
    ) == (
        None,
        (),
    )


def test_nonprefix_availability_is_rejected(tmp_path: Path) -> None:
    journal, owner, _ = setup(tmp_path)
    owner.observe("future", REFS, AT + timedelta(seconds=2))
    owner.observe("earlier", REFS, AT + timedelta(seconds=1))
    with pytest.raises(JournalError, match="prefix"):
        RiskJournal.read_evidence_at(journal, ACCOUNT, AT + timedelta(seconds=1))


@pytest.mark.parametrize(
    "kind", ["projection", "dedupe", "payload", "missing", "future", "external"]
)
def test_corruption_and_unavailable_sources_are_rejected(
    tmp_path: Path, kind: str
) -> None:
    journal, owner, _ = setup(tmp_path)
    owner.observe("initial", REFS, AT)
    if kind == "projection":
        journal._db.execute("DELETE FROM state WHERE key=?", (owner.state_key,))
    elif kind == "dedupe":
        journal._db.execute(
            "DELETE FROM state WHERE key LIKE 'strategy_risk_evidence:%'"
        )
    else:
        journal._db.execute("DROP TRIGGER events_no_update")
        if kind == "payload":
            journal._db.execute(
                "UPDATE events SET payload=? WHERE sequence=1", (b"bad",)
            )
        elif kind == "missing":
            journal._db.execute("DROP TRIGGER events_no_delete")
            journal._db.execute("DELETE FROM events WHERE sequence=1")
        elif kind == "future":
            future = (AT + timedelta(seconds=1)).isoformat()
            journal._db.execute(
                "UPDATE events SET available_at=? WHERE sequence=1", (future,)
            )
        else:
            journal._db.execute(
                "UPDATE events SET source_event_at=? WHERE sequence=1",
                ((AT + timedelta(seconds=1)).isoformat(),),
            )
    journal._db.commit()
    writes = journal._db.total_changes
    with pytest.raises(JournalError):
        RiskJournal.read_evidence_at(journal, ACCOUNT, AT)
    assert journal._db.total_changes == writes


def test_account_and_non_utc_clocks_rejected(tmp_path: Path) -> None:
    journal, _, _ = setup(tmp_path)
    with pytest.raises(JournalError, match="bound"):
        RiskJournal.read_evidence_at(journal, "foreign", AT)
    for clock in (AT.replace(tzinfo=None), AT.astimezone(timezone(timedelta(hours=1)))):
        with pytest.raises(ValueError, match="UTC"):
            RiskJournal.read_evidence_at(journal, ACCOUNT, clock)


@pytest.mark.parametrize(
    "field", ["version", "account_ref", "feed", "event_kind", "external_clock"]
)
def test_risk_event_identity_or_clock_conflict_is_rejected(
    tmp_path: Path, field: str
) -> None:
    journal, owner, _ = setup(tmp_path)
    owner.observe("initial", REFS, AT)
    _, payload = journal.read_strategy_risk_events()[0]
    item = json.loads(payload)
    if field in ("version", "account_ref"):
        item[field] = "foreign"
    journal.append_next(
        source_id="ALPACA_STRATEGY_RISK",
        feed="foreign" if field == "feed" else "paper",
        event_kind="foreign" if field == "event_kind" else "RISK_INPUT",
        payload=json.dumps(item, sort_keys=True, separators=(",", ":")).encode(),
        source_event_at=AT + timedelta(seconds=1)
        if field == "external_clock"
        else None,
        system_first_seen_at=AT,
        processed_at=AT,
    )
    with pytest.raises(JournalError):
        RiskJournal.read_evidence_at(journal, ACCOUNT, AT)
