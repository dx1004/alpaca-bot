"""Pinned raw-price components; no authenticated history/calendar admission."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.reported_price_policy import (
    ReportedPriceAssessment,
    assess_reported_price_record,
)
from alpaca_ross_bot.strategy_features import Session
from test_reported_price_policy import NOW, POLICY, SESSION, TRADE
from test_risk_marks import QUOTE, URL, put

EXTENDED = "EXTENDED_HOURS_CODE_IN_DECLARED_RTH_AFFECTED_SYMBOL"


def assess(
    journal: Journal,
    sequence: int,
    *,
    horizon: int | None = None,
    purpose: str = "TRIGGER",
    metadata: int | None = None,
    session: Session = SESSION,
) -> ReportedPriceAssessment:
    stored = journal.read_event(sequence)
    assert stored is not None
    return assess_reported_price_record(
        journal,
        POLICY,
        journal_scope="synthetic-price-observations",
        through_sequence=journal.read_evidence_sequence()
        if horizon is None
        else horizon,
        sequence=sequence,
        record_index=0,
        payload_ref=stored[0].payload_ref,
        decision_at=NOW,
        session=session,
        purpose=purpose,
        metadata_sequence=metadata,
        max_metadata_age=timedelta(days=1) if metadata is not None else None,
    )


def quote_pair(journal: Journal) -> tuple[int, int]:
    sequence = put(
        journal, [QUOTE | {"t": NOW.isoformat().replace("+00:00", "Z")}], at=NOW
    )
    metadata = put(
        journal,
        {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
        metadata=True,
        at=NOW,
    )
    return sequence, metadata


def uncertified(result: ReportedPriceAssessment) -> None:
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
    assert "DECLARED_SESSION_NOT_AUTHENTICATED_CALENDAR" in result.limitations
    assert result.coverage_label == "NOT_SIP_EQUIVALENT"


@pytest.mark.parametrize("tape", ["A", "B"])
def test_literal_space_survives_raw_schema_and_source_policy_without_normalization(
    tmp_path: Path,
    tape: str,
) -> None:
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        seq = put(journal, [TRADE | {"z": tape, "c": [" ", "F"]}], at=NOW)
        result = assess(journal, seq)
        assert result.component_qualified
        assert (
            result.conditions.minute_open_close
            is result.conditions.minute_high_low
            is True
        )
        assert result.conditions.volume is True
        stored = journal.read_event(seq)
        assert stored is not None and json.loads(stored[1])[0]["c"] == [" ", "F"]
        uncertified(result)


def test_unknown_mixed_with_known_volume_only_never_becomes_negative_witness(
    tmp_path: Path,
) -> None:
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        seq = put(journal, [TRADE | {"z": "A", "c": [" ", "I", "UNKNOWN"]}], at=NOW)
        result = assess(journal, seq)
        assert not result.component_qualified
        assert "UNKNOWN_CONDITION_OR_TAPE" in result.issues
        assert result.conditions.minute_open_close is None
        assert (
            result.conditions.minute_high_low is None
            and result.conditions.volume is None
        )
        assert not result.symbol_derivation_blocked
        uncertified(result)


@pytest.mark.parametrize("code", ["T", "U"])
def test_extended_hours_trade_conflict_is_local_to_affected_assessment(
    tmp_path: Path,
    code: str,
) -> None:
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        seq = put(journal, [TRADE | {"c": [code]}], at=NOW)
        conflict = assess(journal, seq)
        assert not conflict.component_qualified and conflict.symbol_derivation_blocked
        assert EXTENDED in conflict.issues
        other = put(journal, [TRADE | {"S": "MSFT", "i": 2}], at=NOW)
        independent = assess(journal, other)
        assert (
            independent.component_qualified
            and not independent.symbol_derivation_blocked
        )
        uncertified(conflict)
        uncertified(independent)


def test_quote_latest_and_fixed_horizon_do_not_apply_trade_T_conflict_to_quote(
    tmp_path: Path,
) -> None:
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        seq, meta = quote_pair(journal)
        prefix = journal.read_evidence_sequence()
        positive = assess(
            journal, seq, purpose="ENTRY_QUOTE", metadata=meta, horizon=prefix
        )
        assert positive.component_qualified
        latest = put(
            journal,
            [QUOTE | {"t": NOW.isoformat().replace("+00:00", "Z"), "c": ["T"]}],
            at=NOW,
        )
        current = assess(journal, latest, purpose="ENTRY_QUOTE", metadata=meta)
        assert not current.component_qualified and not current.symbol_derivation_blocked
        assert "QUOTE_COMPONENT_OR_METADATA_UNAVAILABLE" in current.issues
        assert EXTENDED not in current.issues
        old = assess(journal, seq, purpose="ENTRY_QUOTE", metadata=meta)
        assert not old.component_qualified and old.reported_bid is None
        assert (
            assess(journal, seq, purpose="ENTRY_QUOTE", metadata=meta, horizon=prefix)
            == positive
        )
        uncertified(current)


@pytest.mark.parametrize("fault", ["observed-wrong-mapping", "raw-hash", "future"])
def test_metadata_damage_blocks_quote_without_repair_or_certification(
    tmp_path: Path,
    fault: str,
) -> None:
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        seq, meta = quote_pair(journal)
        if fault == "observed-wrong-mapping":
            meta = put(
                journal,
                {"url": URL, "body": json.dumps({"R": "Different Meaning"})},
                metadata=True,
                at=NOW,
            )
        elif fault == "raw-hash":
            with sqlite3.connect(tmp_path / "journal.sqlite3") as peer:
                peer.execute("DROP TRIGGER events_no_update")
                peer.execute(
                    "UPDATE events SET payload=? WHERE sequence=?", (b"{}", meta)
                )
        else:
            meta = put(
                journal,
                {"url": URL, "body": json.dumps({"R": "Regular Market Maker Open"})},
                metadata=True,
                at=NOW + timedelta(microseconds=1),
            )
        before = tuple(journal._db.iterdump())
        result = assess(journal, seq, purpose="ENTRY_QUOTE", metadata=meta)
        assert not result.component_qualified
        assert "QUOTE_COMPONENT_OR_METADATA_UNAVAILABLE" in result.issues
        assert result.reported_bid is result.reported_ask is None
        if fault == "observed-wrong-mapping":
            assert [ref.sequence for ref in result.sources] == [seq, meta]
            assert all(ref.available_at <= NOW for ref in result.sources)
        else:
            assert [ref.sequence for ref in result.sources] == [seq]
        assert tuple(journal._db.iterdump()) == before
        uncertified(result)


def test_assessment_uses_read_snapshot_and_original_prefix_during_concurrent_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        seq, meta = quote_pair(journal)
        horizon = journal.read_evidence_sequence()
        source = journal.read_event(seq)
        assert source is not None
        with closing(
            Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
        ) as peer:
            read = journal.read_event
            appended: list[int] = []

            def interleaved(sequence: int) -> Any:
                result = read(sequence)
                if sequence == seq and not appended:
                    assert journal._db.in_transaction
                    appended.append(
                        put(
                            peer,
                            [
                                QUOTE
                                | {
                                    "t": NOW.isoformat().replace("+00:00", "Z"),
                                    "c": ["T"],
                                }
                            ],
                            at=NOW,
                        )
                    )
                return result

            monkeypatch.setattr(journal, "read_event", interleaved)
            result = assess_reported_price_record(
                journal,
                POLICY,
                journal_scope="snapshot-scope",
                through_sequence=horizon,
                sequence=seq,
                record_index=0,
                payload_ref=source[0].payload_ref,
                decision_at=NOW,
                session=SESSION,
                purpose="ENTRY_QUOTE",
                metadata_sequence=meta,
                max_metadata_age=timedelta(days=1),
            )
            assert result.component_qualified and len(appended) == 1
            assert all(ref.sequence <= horizon for ref in result.sources)
            assert not journal._db.in_transaction
            assert journal.read_evidence_sequence() == appended[0]
            assert not assess(
                journal, seq, purpose="ENTRY_QUOTE", metadata=meta
            ).component_qualified
            uncertified(result)


@pytest.mark.parametrize(
    "stamp,age",
    [
        ("2026-10-22T13:59:58.999999999Z", 1_000_000_001),
        ("2026-10-22T14:00:00.000000001Z", -1),
    ],
)
def test_one_nanosecond_stale_or_future_event_is_not_truncated_eligible(
    tmp_path: Path,
    stamp: str,
    age: int,
) -> None:
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        seq = put(journal, [TRADE | {"t": stamp}], at=NOW)
        result = assess(journal, seq)
        assert result.event_age_ns == age and result.receipt_age_ns == 0
        assert not result.component_qualified
        assert "EVENT_OR_RECEIPT_AGE_OUT_OF_BOUNDS" in result.issues
        uncertified(result)


def test_declared_early_close_uses_exact_event_boundary_without_calendar_proof(
    tmp_path: Path,
) -> None:
    session = replace(
        SESSION, close_at=NOW, calendar_ref="synthetic-early-close-not-calendar-proof"
    )
    with closing(
        Journal.open_private(tmp_path / "journal.sqlite3", tmp_path)
    ) as journal:
        at_close = put(journal, [TRADE], at=NOW)
        closed = assess(journal, at_close, session=session)
        assert not closed.component_qualified
        assert "OUTSIDE_DECLARED_RTH_INTERVAL" in closed.issues
        before_close = put(
            journal, [TRADE | {"t": "2026-10-22T13:59:59.999999999Z"}], at=NOW
        )
        just_inside = assess(journal, before_close, session=session)
        assert just_inside.component_qualified and just_inside.event_age_ns == 1
        assert just_inside.declared_calendar_ref == session.calendar_ref
        uncertified(just_inside)
