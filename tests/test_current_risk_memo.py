"""Current consistency reuse stays confined to verified transaction roots."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextvars import copy_context
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot._causal_risk_memo import (
    causal_risk_verification_scope,
    memoized_causal_risk_prefix,
    memoized_current_risk_verification,
    record_path_resolution,
)
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.journal import JournalError
from test_causal_risk_memo import corrupt_cache
from test_causal_risk_memo import memo_book as memo_book
from test_observation_candidate_binding import AT
from test_risk_ledger_audit import REF


@pytest.fixture
def scoped_book(memo_book: Any) -> Iterator[Any]:
    source, _, _ = memo_book
    source.journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            yield memo_book
    finally:
        source.journal._db.rollback()


def count_rebuilds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    original = AccountRiskOwner._verify_rebuild_uncached

    def counted(self: AccountRiskOwner) -> bool:
        calls[0] += 1
        return original(self)

    monkeypatch.setattr(AccountRiskOwner, "_verify_rebuild_uncached", counted)
    return calls


def current(book: Any, rebuild: Callable[[], bool], *, name: str = "outer") -> bool:
    source, _, fee = book
    return memoized_current_risk_verification(
        source.journal,
        account_ref=REF,
        version="test-current",
        state_key=name,
        fee_policy=fee,
        verifier=(name,),
        rebuild=rebuild,
    )


def test_current_reuses_full_verification_without_mutating_database(
    scoped_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, _ = scoped_book
    before = tuple(source.journal._db.iterdump())
    state = risk.view()
    calls = count_rebuilds(monkeypatch)
    assert risk.verify_rebuild() is True
    assert risk.verify_rebuild() is True
    assert calls == [1]
    assert risk.view() == state
    assert tuple(source.journal._db.iterdump()) == before
    assert state["runtime_authorized"] is False


def test_unscoped_and_new_roots_rebuild(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, _ = memo_book
    calls = count_rebuilds(monkeypatch)
    assert risk.verify_rebuild() and risk.verify_rebuild()
    assert calls == [2]
    for _ in range(2):
        source.journal._db.execute("BEGIN")
        try:
            with causal_risk_verification_scope(source.journal):
                assert risk.verify_rebuild() and risk.verify_rebuild()
        finally:
            source.journal._db.rollback()
    assert calls == [4]


@pytest.mark.parametrize("failure", ["false", "exception", "cycle"])
def test_failed_current_proofs_are_not_reused_and_cleanup_allows_retry(
    scoped_book: Any, failure: str
) -> None:
    calls = [0]
    failing = [True]

    def rebuild() -> bool:
        calls[0] += 1
        if failing[0]:
            if failure == "exception":
                raise JournalError("failed current proof")
            if failure == "cycle":
                return current(scoped_book, rebuild)
            return False
        return True

    for _ in range(2):
        if failure == "false":
            assert current(scoped_book, rebuild) is False
        else:
            with pytest.raises(JournalError):
                current(scoped_book, rebuild)
    assert calls == [2]
    failing[0] = False
    assert current(scoped_book, rebuild) is True
    assert current(scoped_book, rebuild) is True
    assert calls == [3]


@pytest.mark.parametrize("table", ["projection", "deduplication"])
def test_corrupt_projection_or_dedup_cannot_hide_and_rollback_rebuilds(
    scoped_book: Any, monkeypatch: pytest.MonkeyPatch, table: str
) -> None:
    source, risk, _ = scoped_book
    db = source.journal._db
    calls = count_rebuilds(monkeypatch)
    assert risk.verify_rebuild()
    db.execute("SAVEPOINT corrupt")
    if table == "deduplication":
        corrupt_cache(db)
    else:
        state = risk.view()
        state["capacity"] = "999"
        db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (json.dumps(state), risk.state_key),
        )
    assert risk.verify_rebuild() is False
    assert risk.verify_rebuild() is False
    db.execute("ROLLBACK TO corrupt")
    db.execute("RELEASE corrupt")
    assert risk.verify_rebuild()
    assert calls == [4]


def test_raw_payload_corruption_cannot_hide(scoped_book: Any) -> None:
    source, risk, _ = scoped_book
    db = source.journal._db
    assert risk.verify_rebuild()
    db.execute("DROP TRIGGER events_no_update")
    sequence, payload = db.execute(
        "SELECT sequence,payload FROM events "
        "WHERE source_id='ALPACA_ACCOUNT_RISK' LIMIT 1"
    ).fetchone()
    db.execute(
        "UPDATE events SET payload=? WHERE sequence=?", (payload + b" ", sequence)
    )
    with pytest.raises(JournalError, match="integrity"):
        risk.verify_rebuild()


def test_schema_change_without_dml_invalidates_current_success(
    scoped_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, _ = scoped_book
    calls = count_rebuilds(monkeypatch)
    assert risk.verify_rebuild()
    changes = source.journal._db.total_changes
    source.journal._db.execute("CREATE TABLE current_schema_probe(value TEXT)")
    assert source.journal._db.total_changes == changes
    assert risk.verify_rebuild()
    assert calls == [2]


def test_current_hit_keeps_owner_inputs_and_private_storage_checks(
    scoped_book: Any, tmp_path: Path
) -> None:
    source, risk, fee = scoped_book
    assert risk.verify_rebuild()
    risk.account_ref = "foreign-account"
    with pytest.raises(JournalError, match="bound"):
        risk.verify_rebuild()
    risk.account_ref = REF
    original_key = risk.state_key
    risk.state_key = "missing-account-risk-projection"
    assert risk.verify_rebuild() is False
    risk.state_key = original_key
    risk.fee_policy = tmp_path / "missing-fees.json"
    with pytest.raises(FileNotFoundError):
        risk.verify_rebuild()
    risk.fee_policy = fee
    original = fee.read_bytes()
    try:
        fee.write_bytes(b"invalid fee policy")
        with pytest.raises((JournalError, ValueError)):
            risk.verify_rebuild()
    finally:
        fee.write_bytes(original)
    assert risk.verify_rebuild()
    source.journal._path.chmod(0o640)
    try:
        with pytest.raises(JournalError, match="private"):
            risk.verify_rebuild()
    finally:
        source.journal._path.chmod(0o600)


def test_subclass_cannot_inherit_base_current_success(scoped_book: Any) -> None:
    _, risk, _ = scoped_book
    assert risk.verify_rebuild()

    class DifferentOwner(AccountRiskOwner):
        def _verify_rebuild_uncached(self) -> bool:
            return False

    other = object.__new__(DifferentOwner)
    other.__dict__.update(risk.__dict__)
    assert other.verify_rebuild() is False


def test_consistent_unqualified_state_is_still_a_reusable_true(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, _ = memo_book
    risk.observe("stale-source", source.selection(AT), AT + timedelta(seconds=60))
    state = risk.view()
    assert state["source_snapshot_qualified"] is False
    assert state["runtime_authorized"] is False
    calls = count_rebuilds(monkeypatch)
    source.journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            assert risk.verify_rebuild() is True
            assert risk.verify_rebuild() is True
            assert calls == [1]
    finally:
        source.journal._db.rollback()


@pytest.mark.parametrize("outer_current", [True, False])
def test_nested_current_and_historical_hits_propagate_dependencies(
    scoped_book: Any, tmp_path: Path, outer_current: bool
) -> None:
    source, _, fee = scoped_book
    nested_fee = tmp_path / "nested-fees.json"
    nested_fee.write_bytes(fee.read_bytes())
    original = nested_fee.read_bytes()
    target = tmp_path / "scope-target"
    target.mkdir()
    alias = tmp_path / "scope-alias"
    alias.symlink_to(target, target_is_directory=True)
    calls = [0]

    def verify_child() -> bool:
        calls[0] += 1
        if nested_fee.read_bytes() != original:
            raise JournalError("nested fee changed")
        record_path_resolution(source.journal, alias, target)
        return True

    def prefix(rebuild: Callable[[], bool], policy: Path, name: str) -> bool:
        def replay() -> tuple[dict[str, Any], tuple[int, ...]]:
            assert rebuild()
            return {
                "source_snapshot_qualified": True,
                "last_snapshot": {"qualified": True},
                "veto": False,
            }, ()

        result, _ = memoized_causal_risk_prefix(
            source.journal,
            account_ref=REF,
            version=name,
            decision_at=AT,
            fee_policy=policy,
            through_sequence=source.journal.read_evidence_sequence(),
            rebuild=replay,
        )
        return isinstance(result, dict)

    def child() -> bool:
        if outer_current:
            return prefix(verify_child, nested_fee, "child")
        return current((source, None, nested_fee), verify_child, name="child")

    def outer() -> bool:
        if outer_current:
            return current(scoped_book, child)
        return prefix(child, fee, "outer")

    assert child() and outer() and outer()
    assert calls == [1]
    nested_fee.write_bytes(original + b"\n")
    with pytest.raises(JournalError):
        outer()
    nested_fee.write_bytes(original)
    assert outer()
    alias.unlink()
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(JournalError):
        outer()


def test_closed_copied_context_cannot_reuse_current_success(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, _ = memo_book
    calls = count_rebuilds(monkeypatch)
    source.journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            assert risk.verify_rebuild()
            inherited = copy_context()
        assert inherited.run(risk.verify_rebuild)
        assert inherited.run(risk.verify_rebuild)
        assert calls == [3]
    finally:
        source.journal._db.rollback()


def test_connection_replacement_cannot_reuse_current_success(scoped_book: Any) -> None:
    source, risk, _ = scoped_book
    assert risk.verify_rebuild()
    original = source.journal._db
    replacement = sqlite3.connect(source.journal._path)
    try:
        source.journal._db = replacement
        with pytest.raises(JournalError):
            risk.verify_rebuild()
    finally:
        source.journal._db = original
        replacement.close()


def test_mutation_during_current_rebuild_rejects_success(scoped_book: Any) -> None:
    source, _, _ = scoped_book

    def mutate() -> bool:
        source.journal._db.execute("CREATE TABLE during_current(value TEXT)")
        return True

    with pytest.raises(JournalError, match="changed during verification"):
        current(scoped_book, mutate)
    assert current(scoped_book, lambda: True)
