"""Read-only native engineering attribution; no formal samples or broker calls."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any, cast

import pytest

from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.native_oco_evidence import NativeFillFact
from alpaca_ross_bot.native_risk_registry import derive_native_registry
from alpaca_ross_bot.paper_execution_adapter import (
    inspect_native_engineering_executions,
)
from alpaca_ross_bot.paper_ledger_sources import (
    NATIVE_LEDGER_POLICY,
    PaperLedgerSources,
    _merge_native_execution_targets,
    _native_scope_orders,
    inspect_native_paper_ledger_sources,
    inspect_paper_ledger_sources,
)
from alpaca_ross_bot.risk_account_inputs import KIND, SCHEMA, SOURCE
from test_account_risk_owner import account_sources
from test_native_account_risk_owner import (
    first_native_fill,
    full_book,
)
from test_native_account_risk_owner import (
    native_risk_book as native_risk_book,
)
from test_native_oco_correlation_integration import ROOT, STOP
from test_observation_candidate_binding import CID
from test_oco_exit_evidence import capture
from test_paper_ledger_sources import complete
from test_risk_ledger_audit import POLICY, REF


@pytest.fixture
def native_completed(native_risk_book: Any) -> Any:
    source, risk, owner, fixture, reported = native_risk_book
    at = fixture[2] + timedelta(seconds=2)
    original = (at - timedelta(seconds=1)).isoformat(timespec="seconds")
    nanos = original.replace("+00:00", ".123456789+00:00")
    reported.order.update(status="filled", filled_qty="2", updated_at=at.isoformat())
    reported.order["legs"][0].update(status="canceled", updated_at=at.isoformat())
    reported.fills[ROOT] = [
        {
            "id": "native-report-a",
            "activity_type": "FILL",
            "order_id": ROOT,
            "symbol": "AAPL",
            "side": "sell",
            "qty": "0.125",
            "price": "11.03",
            "transaction_time": nanos,
        },
        {
            "id": "native-report-b",
            "activity_type": "FILL",
            "order_id": ROOT,
            "symbol": "AAPL",
            "side": "sell",
            "qty": "1.875",
            "price": "11.03",
            "transaction_time": at.isoformat(),
        },
    ]
    owner.bind_native_exit_census(
        "native-ledger-terminal",
        "group-a",
        capture(fixture, reported, at),
        at,
        timedelta(seconds=30),
        at,
    )
    selection = full_book(source, reported, at, cash="502.06", position=0)
    risk.observe_native("native-ledger-terminal-risk", selection, at)
    state = risk.view()
    assert state is not None and state["held"] is None
    assert state["last_snapshot"]["qualified"] and state["issues"] == []
    return native_risk_book, selection, at


def arguments(book: Any, at: datetime, **changes: Any) -> dict[str, Any]:
    return {
        "journal_scope": "synthetic-native-engineering-ledger",
        "account_ref": REF,
        "decision_at": at,
        "through_sequence": book[0].journal.read_evidence_sequence(),
        "max_age": timedelta(seconds=30),
        "fee_policy": POLICY,
    } | changes


def inspect(completed: Any, **changes: Any) -> PaperLedgerSources:
    book, selection, at = completed
    return inspect_native_paper_ledger_sources(
        book[0].journal,
        selection.capture_sequence,
        **arguments(book, at, **changes),
    )


def test_native_roundtrip_exact_provenance_is_read_only_and_explicit(
    native_completed: Any,
) -> None:
    book, selection, at = native_completed
    source = book[0]
    before = tuple(source.journal._db.iterdump())
    result = inspect(native_completed)
    assert tuple(source.journal._db.iterdump()) == before
    assert result.ownership_policy == NATIVE_LEDGER_POLICY
    assert len(result.observed_roundtrips) == 1
    trip = result.observed_roundtrips[0]
    assert trip.entry_client_order_id == CID and trip.filled_quantity == 2
    assert trip.native_order_ids == tuple(sorted((ROOT, STOP)))
    assert trip.native_group_ids == ("group-a",)
    assert trip.transport_origin == "INJECTED_EXCHANGE"
    assert (
        trip.classification
        == "OBSERVED_NATIVE_ENGINEERING_ROUNDTRIP_NOT_STUDY_OWNERSHIP"
    )
    assert set(trip.execution_ids) == {row.activity_id for row in result.activities}
    assert {row.quantity for row in result.activities} == {
        Fraction(2),
        Fraction(1, 8),
        Fraction(15, 8),
    }
    fractional = next(
        row for row in result.activities if row.activity_id == "native-report-a"
    )
    assert fractional.submicrosecond_remainder == Fraction(789, 1_000_000_000)
    assert "NATIVE_GROUP_OWNERSHIP_UNSUPPORTED" not in result.issues
    assert all(
        ref.declared_journal_scope == result.declared_journal_scope
        for ref in result.source_refs
    )
    assert result.formal_origin is None
    assert not any(
        (
            result.formal_sample_eligible,
            result.runtime_authorized,
            result.dispatch_authorized,
            result.source_admission_accepted,
            result.activity_finality_accepted,
        )
    )
    legacy = inspect_paper_ledger_sources(
        source.journal, selection.capture_sequence, **arguments(book, at)
    )
    assert not legacy.observed_roundtrips
    assert "NATIVE_GROUP_OWNERSHIP_UNSUPPORTED" in legacy.issues
    assert tuple(source.journal._db.iterdump()) == before


def test_native_execution_conversion_preserves_raw_fractional_facts(
    native_completed: Any,
) -> None:
    book, selection, at = native_completed
    result = inspect_native_engineering_executions(
        book[0].journal,
        selection.capture_sequence,
        **arguments(book, at),
    )
    assert len(result.executions) == 3 and not result.unconverted_observations
    assert (
        sum(
            (row.quantity for row in result.executions if row.side == "sell"),
            Fraction(),
        )
        == 2
    )
    assert (
        result.classification
        == "OBSERVED_NATIVE_ENGINEERING_EXECUTIONS_NOT_FORMAL_PERFORMANCE"
    )
    assert all(row.origin == "ENGINEERING_TEST" for row in result.executions)
    assert all(
        row.transport_origin == "INJECTED_EXCHANGE"
        for row in result.executions
        if row.side == "sell"
    )
    assert all(
        row.transport_origin is None for row in result.executions if row.side == "buy"
    )
    observed = {row.activity_id: row for row in result.observations}
    for row in result.executions:
        assert (
            row.canonical_row_hash
            == "sha256:" + hashlib.sha256(row.canonical_row).hexdigest()
        )
        assert row.canonical_row == observed[row.execution_id].canonical_row
        assert row.activity_source == observed[row.execution_id].source
    assert not result.formal_sample_eligible and not result.runtime_authorized
    assert "FORMAL_PERFORMANCE_INPUT_CONVERSION_UNACCEPTED" in result.issues


def test_native_reader_retains_caller_transaction(native_completed: Any) -> None:
    source = native_completed[0][0]
    source.journal._db.execute("BEGIN")
    before = tuple(source.journal._db.iterdump())
    try:
        assert len(inspect(native_completed).observed_roundtrips) == 1
        assert source.journal._db.in_transaction
        assert tuple(source.journal._db.iterdump()) == before
    finally:
        source.journal._db.rollback()


def test_native_report_rejects_unadopted_conflicting_activity_capture(
    native_completed: Any,
) -> None:
    book, selection, at = native_completed
    source = book[0]
    horizon = source.journal.read_evidence_sequence()
    original = inspect(native_completed, through_sequence=horizon)
    saved = source.journal.read_event(selection.capture_sequence)
    assert saved is not None
    envelope = json.loads(saved[1])
    for row in envelope["requests"]:
        if row["path"] == "/v2/account/activities?direction=desc&page_size=100":
            activities = json.loads(row["body"])
            activities.append(activities[-1] | {"qty": "1.875000001"})
            row["body"] = json.dumps(activities)
    new = source.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(envelope).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence
    result = inspect_native_paper_ledger_sources(
        source.journal, new, **arguments(book, at)
    )
    assert not result.observed_roundtrips
    assert "ACTIVITY_ID_CONFLICT" in result.issues
    assert len(result.activities) == 4
    assert any(row.quantity == Fraction("1.875000001") for row in result.activities)
    assert inspect(native_completed, through_sequence=horizon) == original


def test_native_partial_episode_remains_raw_without_roundtrip(
    native_risk_book: Any,
) -> None:
    book = native_risk_book
    source, risk, _, _, _ = book
    at, selection = first_native_fill(book)
    risk.observe_native("native-report-partial", selection, at)
    result = inspect_native_paper_ledger_sources(
        source.journal, selection.capture_sequence, **arguments(book, at)
    )
    assert not result.observed_roundtrips
    assert len(result.activities) == 2
    assert "RISK_OWNERSHIP_UNQUALIFIED" in result.issues
    assert not result.formal_sample_eligible


def test_native_reader_does_not_invent_native_context_for_simple_episode(
    tmp_path: Any, monkeypatch: Any
) -> None:
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        selection, at, _ = complete(source, risk)
        result = inspect_native_paper_ledger_sources(
            source.journal, selection.capture_sequence, **arguments((source,), at)
        )
        assert not result.observed_roundtrips
        assert "NATIVE_OWNER_CONTEXT_UNAVAILABLE" in result.issues
        assert result.activities


def test_episode_order_mapping_rejects_duplicate_assignment(
    native_completed: Any,
) -> None:
    book, selection, at = native_completed
    source, risk, _, _, _ = book
    state = risk.view()
    assert state is not None
    registry = derive_native_registry(
        source.journal,
        state["last_snapshot"]["native"]["context"],
        capture_sequence=selection.capture_sequence,
        decision_at=at,
        max_age=timedelta(seconds=30),
        through_sequence=source.journal.read_evidence_sequence(),
    )
    assert _native_scope_orders(source.journal, registry)[CID][0] == tuple(
        sorted((ROOT, STOP))
    )
    with pytest.raises(JournalError, match="multiple episodes"):
        _native_scope_orders(
            source.journal, replace(registry, census_refs=registry.census_refs * 2)
        )


def test_identical_native_execution_observations_are_counted_once() -> None:
    fill = NativeFillFact(
        "same-activity",
        "native-order",
        "AAPL",
        "sell",
        Fraction(1, 8),
        Fraction("11.03"),
        "2026-10-16T13:31:03.123456789+00:00",
        "unused-unit-hash",
    )
    target: dict[str, tuple[object, ...]] = {}
    issues: set[str] = set()
    _merge_native_execution_targets(target, (fill,), issues)
    first = target.copy()
    # One broker order may have many executions; one execution may be observed
    # in both raw feeds. Neither changes ownership or cumulative quantity.
    other = replace(fill, activity_id="other-activity", quantity=Fraction(15, 8))
    _merge_native_execution_targets(target, (fill, other), issues)
    assert not issues and target[fill.activity_id] == first[fill.activity_id]
    assert len(target) == 2
    assert sum((cast(Fraction, row[3]) for row in target.values()), Fraction()) == 2


def test_conflicting_native_execution_identity_retains_original_fact() -> None:
    fill = NativeFillFact(
        "activity",
        "native-order",
        "AAPL",
        "sell",
        Fraction(1),
        Fraction(11),
        "2026-10-16T13:31:03+00:00",
        "unused-unit-hash",
    )
    target: dict[str, tuple[object, ...]] = {}
    issues: set[str] = set()
    _merge_native_execution_targets(target, (fill,), issues)
    before = target.copy()
    _merge_native_execution_targets(
        target, (replace(fill, order_id="different-order"),), issues
    )
    assert issues == {"EXECUTION_ID_SCOPE_CONFLICT"} and target == before


def test_multiple_episode_mapping_uses_disjoint_census_not_account_list(
    native_completed: Any,
) -> None:
    """Join-level unit test, not a second qualified source/account roundtrip."""
    book, selection, at = native_completed
    source, risk, _, _, _ = book
    state = risk.view()
    assert state is not None
    registry = derive_native_registry(
        source.journal,
        state["last_snapshot"]["native"]["context"],
        capture_sequence=selection.capture_sequence,
        decision_at=at,
        max_age=timedelta(seconds=30),
        through_sequence=source.journal.read_evidence_sequence(),
    )
    _, refs = registry.census_refs[0]
    stored = source.journal.read_event(refs[0][0])
    assert stored is not None
    copied = json.loads(stored[1])
    second_scope = "synthetic-second-scope"
    copied["binding"]["entry_scope"] = second_scope
    second = {
        ROOT: "00000000-0000-4000-8000-000000000010",
        STOP: "00000000-0000-4000-8000-000000000011",
    }
    identity = copied["operation"]["receipt"]["identity"]
    identity["root_order_id"], identity["stop_order_id"] = second[ROOT], second[STOP]
    event = source.journal.append_next(
        source_id=stored[0].source_id,
        feed="paper",
        event_kind=stored[0].event_kind,
        payload=json.dumps(copied).encode(),
        system_first_seen_at=at,
        processed_at=at,
    )
    supplied = replace(
        registry,
        orders=registry.orders
        + tuple(
            replace(
                order,
                order_id=second[order.order_id],
                client_order_id="second-" + order.client_order_id,
            )
            for order in registry.orders
        ),
        census_refs=registry.census_refs
        + ((second_scope, ((event.sequence, event.payload_ref),)),),
    )
    result = _native_scope_orders(source.journal, supplied)
    assert result[CID][0] == tuple(sorted((ROOT, STOP)))
    assert result[second_scope][0] == tuple(sorted(second.values()))
    assert not set(result[CID][0]) & set(result[second_scope][0])


def test_legacy_origin_capture_stays_observable_without_native_adoption(
    native_completed: Any,
) -> None:
    book, selection, at = native_completed
    source = book[0]
    stored = source.journal.read_event(selection.capture_sequence)
    assert stored is not None
    envelope = json.loads(stored[1])
    envelope["schema"] = SCHEMA
    envelope.pop("transport_origin")
    event = source.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(envelope).encode(),
        system_first_seen_at=at,
        processed_at=at,
    )
    result = inspect_native_paper_ledger_sources(
        source.journal, event.sequence, **arguments(book, at)
    )
    assert len(result.activities) == 3 and not result.observed_roundtrips
    assert "NATIVE_CAPTURE_ORIGIN_UNQUALIFIED" in result.issues
    assert not result.source_admission_accepted and not result.formal_sample_eligible
