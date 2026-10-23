"""Independent durable workflow checks; declared references aren't certification."""

import json
from dataclasses import fields
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.declared_review import DeclaredReview, DeclaredReviewView
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from test_declared_review import DIGEST, NOW, bound, documents


def _uncertified(view: DeclaredReviewView) -> None:
    flags = [
        field.name
        for field in fields(view)
        if field.name.endswith(("verified", "eligible", "authorized"))
    ]
    assert flags
    assert all(getattr(view, name) is False for name in flags)


def test_second_connection_sees_seal_only_after_commit_then_reopen_retry_keeps_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = bound(tmp_path)
    root = tmp_path / "private"
    peer_journal = Journal.open_private(root / "journal.sqlite3", root)
    reopened: Journal | None = None
    try:
        first = DeclaredReview(writer, journal_scope="declared-local")
        peer = DeclaredReview(peer_journal, journal_scope="declared-local")
        package, expected, unmask, comparison = documents()
        first.record("cross-connection", "PACKAGE", package, recorded_at=NOW)
        before = writer.read_evidence_sequence()
        with pytest.raises(JournalError, match="prerequisite"):
            peer.record("cross-connection", "UNMASK", unmask, recorded_at=NOW)
        assert writer.read_evidence_sequence() == before
        append = writer._append_locked
        inside: list[tuple[str, ...]] = []

        def observed_append(
            event: EventEnvelope,
            payload: bytes,
            state_updates: Any = None,
        ) -> None:
            append(event, payload, state_updates)
            if event.event_kind == "DECLARED_REVIEW_EXPECTED":
                inside.append(
                    tuple(row.step for row in peer.view("cross-connection").receipts)
                )

        monkeypatch.setattr(writer, "_append_locked", observed_append)
        sealed = first.record(
            "cross-connection",
            "EXPECTED",
            expected,
            recorded_at=NOW + timedelta(seconds=1),
        )
        assert inside == [("PACKAGE",)]
        assert tuple(row.step for row in peer.view("cross-connection").receipts) == (
            "PACKAGE",
            "EXPECTED",
        )
        writer.close()
        reopened = Journal.open_private(root / "journal.sqlite3", root)
        recovered = DeclaredReview(reopened, journal_scope="declared-local")
        unmasked = peer.record(
            "cross-connection", "UNMASK", unmask, recorded_at=NOW + timedelta(seconds=2)
        )
        compared = recovered.record(
            "cross-connection",
            "COMPARISON",
            comparison,
            recorded_at=NOW + timedelta(seconds=3),
        )
        assert sealed.sequence < unmasked.sequence < compared.sequence
        assert (
            recovered.record(
                "cross-connection",
                "EXPECTED",
                expected,
                recorded_at=NOW + timedelta(days=1),
            )
            == sealed
        )
        assert recovered.view("cross-connection") == peer.view("cross-connection")
        assert recovered.verify_rebuild("cross-connection")
        _uncertified(recovered.view("cross-connection"))
    finally:
        writer.close()
        peer_journal.close()
        if reopened is not None:
            reopened.close()


def test_post_comparison_conflict_invalidates_current_verdict_but_not_original_prefix(
    tmp_path: Path,
) -> None:
    journal = bound(tmp_path)
    try:
        workflow = DeclaredReview(journal, journal_scope="declared-local")
        docs = documents()
        receipts = [
            workflow.record(
                "completed", step, document, recorded_at=NOW + timedelta(seconds=index)
            )
            for index, (step, document) in enumerate(
                zip(("PACKAGE", "EXPECTED", "UNMASK", "COMPARISON"), docs, strict=True)
            )
        ]
        original = workflow.view("completed")
        assert original.verdict_result == "MATCH"
        with pytest.raises(JournalError, match="conflict"):
            workflow.record(
                "completed",
                "EXPECTED",
                docs[1] + b" ",
                recorded_at=NOW + timedelta(seconds=4),
            )
        current = workflow.view("completed")
        assert current.conflict and current.verdict_result == "INVALID_CONFLICT"
        assert current.receipts == original.receipts
        assert current.receipts[1].exact_bytes == docs[1]
        assert (
            workflow.view("completed", through_sequence=receipts[-1].sequence)
            == original
        )
        assert workflow.verify_rebuild("completed")
        _uncertified(current)
    finally:
        journal.close()


def test_external_declarations_can_be_retained_but_never_self_certify(
    tmp_path: Path,
) -> None:
    journal = bound(tmp_path)
    try:
        workflow = DeclaredReview(journal, journal_scope="declared-local")
        docs = list(documents())
        package = json.loads(docs[0])
        package["source_horizons"] = {"nonexistent-external-journal": 999999}
        package["source_refs"] = [
            {
                "journal_scope": "nonexistent-external-journal",
                "sequence": 999999,
                "payload_ref": DIGEST,
                "available_at": NOW.isoformat(),
            }
        ]
        docs[0] = json.dumps(package).encode()
        before = journal.read_evidence_sequence()
        forged = package | {
            "source_evidence_verified": True,
            "formal_sample_eligible": True,
        }
        with pytest.raises(JournalError):
            workflow.record(
                "forged-certification",
                "PACKAGE",
                json.dumps(forged).encode(),
                recorded_at=NOW,
            )
        assert journal.read_evidence_sequence() == before
        for index, (step, document) in enumerate(
            zip(
                ("PACKAGE", "EXPECTED", "UNMASK", "COMPARISON"),
                docs,
                strict=True,
            )
        ):
            workflow.record(
                "declared-only",
                step,
                document,
                recorded_at=NOW + timedelta(seconds=index),
            )
        view = workflow.view("declared-only")
        assert view.verdict_result == "MATCH"
        assert (
            json.loads(view.receipts[0].exact_bytes)["source_refs"][0]["sequence"]
            == 999999
        )
        _uncertified(view)
        assert workflow.verify_rebuild("declared-only")
    finally:
        journal.close()


def test_failed_seal_append_rolls_back_evidence_and_state_before_physical_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = bound(tmp_path)
    root = tmp_path / "private"
    try:
        workflow = DeclaredReview(journal, journal_scope="declared-local")
        package, expected, _, _ = documents()
        workflow.record("disk-fault", "PACKAGE", package, recorded_at=NOW)
        before = tuple(journal._db.iterdump())
        append = journal._append_locked

        def fail_after_append(
            event: EventEnvelope,
            payload: bytes,
            state_updates: Any = None,
        ) -> None:
            append(event, payload, state_updates)
            if event.event_kind == "DECLARED_REVIEW_EXPECTED":
                raise OSError("synthetic storage failure")

        monkeypatch.setattr(journal, "_append_locked", fail_after_append)
        with pytest.raises(OSError):
            workflow.record(
                "disk-fault",
                "EXPECTED",
                expected,
                recorded_at=NOW + timedelta(seconds=1),
            )
        assert tuple(journal._db.iterdump()) == before
        assert not journal._db.in_transaction
    finally:
        journal.close()
    reopened = Journal.open_private(root / "journal.sqlite3", root)
    try:
        recovered = DeclaredReview(reopened, journal_scope="declared-local")
        assert [row.step for row in recovered.view("disk-fault").receipts] == [
            "PACKAGE"
        ]
        assert recovered.verify_rebuild("disk-fault")
        recovered.record(
            "disk-fault", "EXPECTED", expected, recorded_at=NOW + timedelta(seconds=1)
        )
        assert [row.step for row in recovered.view("disk-fault").receipts] == [
            "PACKAGE",
            "EXPECTED",
        ]
    finally:
        reopened.close()


def test_declared_local_bot_reference_cannot_point_after_unmask_receipt(
    tmp_path: Path,
) -> None:
    journal = bound(tmp_path)
    try:
        workflow = DeclaredReview(journal, journal_scope="declared-local")
        package, expected, unmask, _ = documents()
        workflow.record("future-local", "PACKAGE", package, recorded_at=NOW)
        workflow.record(
            "future-local", "EXPECTED", expected, recorded_at=NOW + timedelta(seconds=1)
        )
        future = json.loads(unmask)
        future["bot_trace_ref"].update(journal_scope="declared-local", sequence=999999)
        before = tuple(journal._db.iterdump())
        with pytest.raises(JournalError):
            workflow.record(
                "future-local",
                "UNMASK",
                json.dumps(future).encode(),
                recorded_at=NOW + timedelta(seconds=2),
            )
        assert tuple(journal._db.iterdump()) == before
    finally:
        journal.close()
