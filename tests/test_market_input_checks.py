import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.market_input_checks import (
    MarketInputAssessment,
    assess_market_record,
)

NOW = datetime(2026, 10, 16, 14, tzinfo=UTC)
TRADE = {
    "T": "t",
    "S": "AAPL",
    "p": "100.01",
    "s": 1,
    "x": "V",
    "i": 1,
    "z": "C",
    "c": ["@"],
    "t": "2026-10-16T13:59:59Z",
}
QUOTE = {
    "T": "q",
    "S": "AAPL",
    "bp": "100",
    "ap": "101",
    "bs": 1,
    "as": 1,
    "bx": "V",
    "ax": "V",
    "z": "C",
    "c": ["R"],
    "t": "2026-10-16T14:00:00Z",
}


def assess(
    tmp_path: Path,
    record: dict[str, object],
    *,
    condition_names: dict[str, str] | None = None,
) -> MarketInputAssessment:
    journal = Journal(tmp_path / "journal.sqlite3", tmp_path)
    event = journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="RAW_FRAME",
        payload=json.dumps([record]).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    try:
        return assess_market_record(
            journal,
            sequence=event.sequence,
            record_index=0,
            payload_ref=event.payload_ref,
            decision_at=NOW,
            condition_names=condition_names,
        )
    finally:
        journal.close()


@pytest.mark.parametrize("record", [TRADE, QUOTE])
def test_good_observation_never_grants_runtime_authority(
    tmp_path: Path, record: dict[str, object]
) -> None:
    result = assess(tmp_path, record, condition_names={"@": "regular", "R": "regular"})
    assert result.schema_valid and result.measured_fresh
    assert not result.runtime_eligible
    assert result.coverage_label == "NOT_SIP_EQUIVALENT"


@pytest.mark.parametrize(
    ("stamp", "age", "fresh"),
    [
        ("2026-10-16T13:59:59Z", 1_000_000_000, True),
        ("2026-10-16T13:59:58.999999999Z", 1_000_000_001, False),
        ("2026-10-16T14:00:00.000000001Z", -1, False),
    ],
)
def test_exact_nanosecond_boundaries(
    tmp_path: Path, stamp: str, age: int, fresh: bool
) -> None:
    result = assess(tmp_path, {**TRADE, "t": stamp})
    assert result.event_age_ns == age
    assert result.measured_fresh is fresh


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("p", "NaN"),
        ("p", "Infinity"),
        ("s", 0),
        ("s", True),
        ("c", []),
        ("c", None),
        ("x", ""),
        ("z", None),
        ("i", True),
        ("t", "2026-10-16T14:00:00.0000000001Z"),
    ],
)
def test_bad_trade_schema(tmp_path: Path, field: str, value: object) -> None:
    assert not assess(tmp_path, {**TRADE, field: value}).schema_valid


@pytest.mark.parametrize(
    ("field", "value"), [("bp", "102"), ("ap", 0), ("bs", 0), ("ax", ""), ("c", [])]
)
def test_bad_quote_schema(tmp_path: Path, field: str, value: object) -> None:
    assert not assess(tmp_path, {**QUOTE, field: value}).schema_valid


@pytest.mark.parametrize("kind", ["c", "x", "u"])
def test_revisions_are_explicit_not_ignored(tmp_path: Path, kind: str) -> None:
    result = assess(tmp_path, {**TRADE, "T": kind})
    assert result.revision_observed
    assert not result.schema_valid and not result.runtime_eligible
    assert "REVISION_REQUIRES_ORDERED_RECONSTRUCTION" in result.reasons


def test_unmapped_condition_is_observed(tmp_path: Path) -> None:
    result = assess(tmp_path, TRADE)
    assert result.conditions == (("@", None),)
    assert "UNMAPPED_CONDITION_CODE" in result.reasons


def test_literal_space_regular_sale_code_is_preserved(tmp_path: Path) -> None:
    result = assess(
        tmp_path,
        {**TRADE, "z": "A", "c": [" "]},
        condition_names={" ": "Regular Sale"},
    )
    assert result.schema_valid
    assert result.conditions == ((" ", "Regular Sale"),)
    assert "UNMAPPED_CONDITION_CODE" not in result.reasons
    assert not result.runtime_eligible


def test_space_without_tape_metadata_stays_unmapped(tmp_path: Path) -> None:
    result = assess(tmp_path, {**TRADE, "c": [" "]})
    assert result.schema_valid
    assert result.conditions == ((" ", None),)
    assert "UNMAPPED_CONDITION_CODE" in result.reasons
    assert not result.runtime_eligible


@pytest.mark.parametrize("code", ["", "  ", "\t", "\n"])
def test_other_blank_condition_codes_remain_invalid(tmp_path: Path, code: str) -> None:
    assert not assess(tmp_path, {**TRADE, "c": [code]}).schema_valid


@pytest.mark.parametrize("field", ["S", "x", "z"])
def test_space_condition_exception_does_not_allow_blank_identity(
    tmp_path: Path, field: str
) -> None:
    assert not assess(tmp_path, {**TRADE, field: " "}).schema_valid


@pytest.mark.parametrize("case", ["foreign", "rawref", "future", "index"])
def test_binding_and_availability(tmp_path: Path, case: str) -> None:
    journal = Journal(tmp_path / "journal.sqlite3", tmp_path)
    event = journal.append_next(
        source_id="alpaca_market_data",
        feed="sip" if case == "foreign" else "iex",
        event_kind="RAW_FRAME",
        payload=json.dumps([TRADE]).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW + timedelta(microseconds=1) if case == "future" else NOW,
    )
    with pytest.raises(ValueError):
        assess_market_record(
            journal,
            sequence=event.sequence,
            record_index=1 if case == "index" else 0,
            payload_ref="bad" if case == "rawref" else event.payload_ref,
            decision_at=NOW,
        )
    journal.close()


@pytest.mark.parametrize("receipt_age_us", [1_000_000, 1_000_001])
def test_receipt_age_is_independent_of_fresh_event(
    tmp_path: Path, receipt_age_us: int
) -> None:
    journal = Journal(tmp_path / "journal.sqlite3", tmp_path)
    received = NOW - timedelta(microseconds=receipt_age_us)
    event = journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="RAW_FRAME",
        payload=json.dumps([QUOTE]).encode(),
        system_first_seen_at=received,
        processed_at=received,
    )
    result = assess_market_record(
        journal,
        sequence=event.sequence,
        record_index=0,
        payload_ref=event.payload_ref,
        decision_at=NOW,
    )
    assert result.event_age_ns == 0
    assert result.receipt_age_ns == receipt_age_us * 1000
    assert result.measured_fresh is (receipt_age_us == 1_000_000)
    journal.close()


def test_duplicate_json_keys_are_not_resolved_by_last_value(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.sqlite3", tmp_path)
    event = journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="RAW_FRAME",
        payload=b'[{"T":"q","T":"t"}]',
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    with pytest.raises(ValueError, match="duplicate"):
        assess_market_record(
            journal,
            sequence=event.sequence,
            record_index=0,
            payload_ref=event.payload_ref,
            decision_at=NOW,
        )
    journal.close()
