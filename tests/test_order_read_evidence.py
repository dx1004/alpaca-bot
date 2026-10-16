"""Read-only lifecycle evidence must not restart state or consume future events."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.order_reconciliation import OrderReconciler
from alpaca_ross_bot.paper_order_contract import paper_account_ref

NOW = datetime(2026, 10, 16, 15, tzinfo=UTC)
RAW = json.dumps({"id": "11111111-1111-4111-8111-111111111111"}).encode()
REF = paper_account_ref(RAW)


def open_journal(tmp_path: Path) -> Journal:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "events.sqlite3", root)
    journal.record_paper_account_verification(REF, RAW, NOW)
    return journal


def test_readonly_prefix_does_not_restart_or_consume_future(tmp_path: Path) -> None:
    j = open_journal(tmp_path)
    try:
        owner = OrderReconciler(j, REF, clock=lambda: NOW)
        owner.stream_health("CONNECTING", NOW + timedelta(seconds=1), "session")
        owner.stream_health("SUBSCRIBED", NOW + timedelta(seconds=2), "session")
        original = owner.view()
        events = j.read_order_events()
        state, refs = OrderReconciler.read_evidence_at(
            j, REF, NOW + timedelta(seconds=1)
        )
        assert state["stream_status"] == "CONNECTING"
        assert refs == tuple(event.sequence for event, _ in events[:2])
        assert j.read_order_events() == events
        assert owner.view() == original
        assert original["stream_status"] == "SUBSCRIBED"
    finally:
        j.close()


def test_gap_in_visible_evidence_prefix_is_not_skipped(tmp_path: Path) -> None:
    j = open_journal(tmp_path)
    try:
        owner = OrderReconciler(j, REF, clock=lambda: NOW)
        owner.stream_health("CONNECTING", NOW + timedelta(seconds=2), "session")
        owner.stream_health("SUBSCRIBED", NOW + timedelta(seconds=1), "session")
        with pytest.raises(JournalError, match="prefix"):
            OrderReconciler.read_evidence_at(j, REF, NOW + timedelta(seconds=1))
    finally:
        j.close()


def test_naive_clock_and_foreign_account_rejected(tmp_path: Path) -> None:
    j = open_journal(tmp_path)
    try:
        with pytest.raises(ValueError):
            OrderReconciler.read_evidence_at(j, REF, NOW.replace(tzinfo=None))
        with pytest.raises(JournalError):
            OrderReconciler.read_evidence_at(j, "foreign", NOW)
    finally:
        j.close()


def test_corrupt_current_projection_cannot_bootstrap_a_read(tmp_path: Path) -> None:
    j = open_journal(tmp_path)
    try:
        owner = OrderReconciler(j, REF, clock=lambda: NOW)
        j.append_next(
            source_id="synthetic-corruption",
            feed="paper",
            event_kind="TEST",
            payload=b"{}",
            system_first_seen_at=NOW,
            processed_at=NOW,
            state_updates={owner.state_key: owner.view() | {"stream_gap": False}},
        )
        with pytest.raises(JournalError, match="projection"):
            OrderReconciler.read_evidence_at(j, REF, NOW)
    finally:
        j.close()
