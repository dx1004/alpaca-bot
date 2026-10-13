import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError

NOW = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
PAYLOAD = b'{"price":"2.50"}'


def envelope(sequence: int = 1) -> EventEnvelope:
    return EventEnvelope(
        sequence=sequence,
        source_id="ALPACA_IEX",
        feed="iex",
        event_kind="trade",
        payload_ref="sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
        source_event_at=NOW,
        provider_received_at=None,
        system_first_seen_at=NOW + timedelta(milliseconds=1),
        processed_at=NOW + timedelta(milliseconds=2),
        available_at=NOW + timedelta(milliseconds=3),
    )


def test_append_state_and_reopen_are_atomic(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    journal.append(envelope(), PAYLOAD, {"cursor": {"sequence": 1}})
    journal.close()

    reopened = Journal(path, tmp_path / "var")
    assert reopened.read_event(1) == (envelope(), PAYLOAD)
    assert reopened.read_state("cursor") == {"sequence": 1}
    reopened.append(envelope(2), PAYLOAD, {"cursor": {"sequence": 2}})
    assert reopened.read_state("cursor") == {"sequence": 2}
    reopened.close()


def test_conditional_append_is_unique_across_journal_connections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    barrier = Barrier(2)

    def append_once(index: int) -> EventEnvelope | None:
        journal = Journal(path, tmp_path / "var")
        barrier.wait()
        result = journal.append_next_if_state_missing(
            state_key="once:key",
            state_value={"winner": index},
            source_id="test",
            feed="test",
            event_kind="ONCE",
            payload=f"{index}".encode(),
            system_first_seen_at=NOW,
            processed_at=NOW + timedelta(milliseconds=1),
        )
        journal.close()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(append_once, (1, 2)))

    assert sum(result is not None for result in results) == 1
    reopened = Journal(path, tmp_path / "var")
    assert reopened.read_event(1) is not None
    assert reopened.read_event(2) is None
    state = reopened.read_state("once:key")
    assert state in ({"winner": 1}, {"winner": 2})
    reopened.close()


def test_conditional_append_rolls_back_event_when_state_is_invalid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")

    def append(state_value: object) -> EventEnvelope | None:
        return journal.append_next_if_state_missing(
            state_key="once:key",
            state_value=state_value,
            source_id="test",
            feed="test",
            event_kind="ONCE",
            payload=b"once",
            system_first_seen_at=NOW,
            processed_at=NOW + timedelta(milliseconds=1),
        )

    with pytest.raises(TypeError):
        append({"invalid": object()})

    assert journal.read_event(1) is None
    assert journal.read_state("once:key") is None
    inserted = append({"valid": True})
    assert inserted is not None
    assert journal.read_event(1) is not None
    assert journal.read_state("once:key") == {"valid": True}
    journal.close()


def test_failed_state_write_rolls_back_event_and_state(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    with pytest.raises(TypeError):
        journal.append(envelope(), PAYLOAD, {"cursor": object()})
    journal.close()

    reopened = Journal(path, tmp_path / "var")
    assert reopened.read_event(1) is None
    assert reopened.read_state("cursor") is None
    reopened.append(envelope(), PAYLOAD)
    reopened.close()


def test_sequence_and_payload_integrity_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    with pytest.raises(JournalError, match="reference"):
        journal.append(replace(envelope(), payload_ref="sha256:wrong"), PAYLOAD)
    with pytest.raises(JournalError, match="sequence"):
        journal.append(envelope(2), PAYLOAD)
    journal.append(envelope(), PAYLOAD)
    with pytest.raises(JournalError, match="sequence"):
        journal.append(envelope(), PAYLOAD)
    journal.close()


def test_event_rows_reject_mutation(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    journal.append(envelope(), PAYLOAD)
    journal.close()
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM events WHERE sequence=1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE events SET feed='sip' WHERE sequence=1")


def test_runtime_path_and_unknown_schema_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(JournalError, match="runtime root"):
        Journal(tmp_path / "outside.sqlite3", tmp_path / "var")
    path = tmp_path / "var" / "journal.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(JournalError, match="schema version"):
        Journal(path, tmp_path / "var")


def test_order_intent_id_is_durable_unique_and_immutable(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    intent = {"paper_account_ref": "test-account", "decision_ref": "case-1"}
    journal.reserve_order_intent("client-1", intent)
    with pytest.raises(JournalError, match="already reserved"):
        journal.reserve_order_intent("client-1", intent)
    journal.close()

    reopened = Journal(path, tmp_path / "var")
    assert reopened.read_order_intent("client-1") == intent
    assert reopened.read_order_intent("missing") is None
    reopened.close()
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE order_intents SET intent_json='{}' "
                "WHERE client_order_id='client-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM order_intents WHERE client_order_id='client-1'"
            )


def test_failed_order_intent_does_not_reserve_id(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    with pytest.raises(TypeError):
        journal.reserve_order_intent("client-1", {"invalid": object()})
    with pytest.raises(JournalError, match="nonempty"):
        journal.reserve_order_intent(" ", {"valid": True})
    assert journal.read_order_intent("client-1") is None
    journal.reserve_order_intent("client-1", {"valid": True})
    journal.close()


def test_version_one_journal_migrates_without_losing_events(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    journal.append(envelope(), PAYLOAD)
    journal.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE order_send_attempts")
        connection.execute("DROP TABLE order_intents")
        connection.execute("PRAGMA user_version=1")

    reopened = Journal(path, tmp_path / "var")
    assert reopened.read_event(1) == (envelope(), PAYLOAD)
    reopened.reserve_order_intent("client-1", {"valid": True})
    assert reopened.read_order_intent("client-1") == {"valid": True}
    reopened.close()


def test_send_attempt_is_committed_once_before_transport(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    with pytest.raises(JournalError, match="reserved"):
        journal.mark_order_send_attempt("client-1", NOW)
    journal.reserve_order_intent("client-1", {"test": True})
    with pytest.raises(JournalError, match="UTC"):
        journal.mark_order_send_attempt("client-1", NOW.replace(tzinfo=None))
    journal.mark_order_send_attempt("client-1", NOW)
    with pytest.raises(JournalError, match="already attempted"):
        journal.mark_order_send_attempt("client-1", NOW)
    journal.close()

    reopened = Journal(path, tmp_path / "var")
    assert reopened.has_order_send_attempt("client-1")
    with pytest.raises(JournalError, match="already attempted"):
        reopened.mark_order_send_attempt("client-1", NOW)
    reopened.close()


def test_version_two_intents_survive_send_marker_migration(tmp_path: Path) -> None:
    path = tmp_path / "var" / "journal.sqlite3"
    journal = Journal(path, tmp_path / "var")
    journal.reserve_order_intent("client-1", {"test": True})
    journal.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE order_send_attempts")
        connection.execute("PRAGMA user_version=2")

    reopened = Journal(path, tmp_path / "var")
    assert reopened.read_order_intent("client-1") == {"test": True}
    assert not reopened.has_order_send_attempt("client-1")
    reopened.mark_order_send_attempt("client-1", NOW)
    reopened.close()
