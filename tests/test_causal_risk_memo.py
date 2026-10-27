"""Successful prefix reuse never survives changed evidence or root scope."""

import json
import sqlite3
from collections.abc import Iterator
from contextvars import copy_context
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot._causal_risk_memo import (
    causal_risk_verification_scope,
    memoized_causal_risk_prefix,
)
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.native_oco_http import native_http_inventory
from test_account_risk_owner import account_sources
from test_native_oco_http import native_http as native_http
from test_native_oco_http import submit
from test_observation_candidate_binding import AT
from test_risk_ledger_audit import POLICY, REF


@pytest.fixture
def memo_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    fee = tmp_path / "fees.json"
    fee.write_bytes(POLICY.read_bytes())
    with account_sources(tmp_path, monkeypatch) as (source, _):
        risk = AccountRiskOwner(source.journal, REF, fee)
        risk.observe("memo-flat-source", source.selection(AT), AT)
        yield source, risk, fee


def read(book: Any) -> Any:
    source, _, fee = book
    return AccountRiskOwner._read_native_causal_evidence_at(
        source.journal,
        REF,
        AT,
        fee,
        through_sequence=source.journal.read_evidence_sequence(),
    )


def reductions(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    original = AccountRiskOwner._reduce

    def counted(
        self: AccountRiskOwner,
        old: object,
        item: dict[str, Any],
        conflict: bool,
        sequence: int,
    ) -> dict[str, Any]:
        calls[0] += 1
        return original(self, old, item, conflict, sequence)

    monkeypatch.setattr(AccountRiskOwner, "_reduce", counted)
    return calls


def corrupt_cache(db: sqlite3.Connection) -> None:
    key, raw = db.execute(
        "SELECT key,value_json FROM state "
        "WHERE key LIKE 'account_risk_evidence:%' LIMIT 1"
    ).fetchone()
    value = json.loads(raw)
    value["hash"] = "sha256:" + "0" * 64
    db.execute("UPDATE state SET value_json=? WHERE key=?", (json.dumps(value), key))


def test_reuses_only_verified_prefix_returns_copies_and_preserves_order(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, fee = memo_book
    db = source.journal._db
    expected = AccountRiskOwner._rebuild_native_causal_evidence_at(
        source.journal,
        REF,
        AT,
        fee,
        through_sequence=source.journal.read_evidence_sequence(),
    )
    assert expected[0] is not None
    calls = reductions(monkeypatch)
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            first = read(memo_book)
            count = calls[0]
            assert count > 0 and first == expected
            assert list(first[0]) == list(expected[0])
            first[0]["net"] = "999"
            second = read(memo_book)
            assert second == expected and calls[0] == count
            assert first[0] is not second[0]
        with causal_risk_verification_scope(source.journal):
            assert read(memo_book) == expected
            assert calls[0] > count
    finally:
        db.rollback()


def test_unscoped_reads_remain_uncached(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = reductions(monkeypatch)
    first = read(memo_book)
    count = calls[0]
    assert read(memo_book) == first
    assert calls[0] > count


def test_scope_requires_existing_transaction_and_resets_after_exception(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, _ = memo_book
    with pytest.raises(JournalError, match="transaction"):
        with causal_risk_verification_scope(source.journal):
            pass
    calls = reductions(monkeypatch)
    db = source.journal._db
    db.execute("BEGIN")
    try:
        with pytest.raises(RuntimeError, match="end-root"):
            with causal_risk_verification_scope(source.journal):
                read(memo_book)
                raise RuntimeError("end-root")
        count = calls[0]
        with causal_risk_verification_scope(source.journal):
            read(memo_book)
        assert calls[0] > count
    finally:
        db.rollback()


def test_cache_dml_and_rollback_cannot_reuse_a_previous_success(memo_book: Any) -> None:
    source, _, _ = memo_book
    db = source.journal._db
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            expected = read(memo_book)
            db.execute("SAVEPOINT corrupt")
            corrupt_cache(db)
            with pytest.raises(JournalError, match="deduplication"):
                read(memo_book)
            db.execute("ROLLBACK TO corrupt")
            db.execute("RELEASE corrupt")
            assert read(memo_book) == expected
    finally:
        db.rollback()


def test_raw_hash_mutation_cannot_hide_behind_memo(memo_book: Any) -> None:
    source, _, _ = memo_book
    db = source.journal._db
    db.execute("DROP TRIGGER events_no_update")
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            read(memo_book)
            sequence, payload = db.execute(
                "SELECT sequence,payload FROM events "
                "WHERE source_id='ALPACA_ACCOUNT_RISK' LIMIT 1"
            ).fetchone()
            db.execute(
                "UPDATE events SET payload=? WHERE sequence=?",
                (bytes(payload) + b" ", sequence),
            )
            with pytest.raises(JournalError, match="payload failed integrity"):
                read(memo_book)
    finally:
        db.rollback()


def test_schema_change_forces_rebuild_even_without_dml(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, _ = memo_book
    calls = reductions(monkeypatch)
    db = source.journal._db
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            first = read(memo_book)
            count, changes = calls[0], db.total_changes
            db.execute("CREATE TABLE memo_schema_probe(value TEXT)")
            assert db.total_changes == changes
            assert read(memo_book) == first and calls[0] > count
    finally:
        db.rollback()


def test_fee_bytes_and_storage_permissions_are_checked_on_hits(memo_book: Any) -> None:
    source, _, fee = memo_book
    db = source.journal._db
    original = fee.read_bytes()
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            read(memo_book)
            fee.write_bytes(original + b"\n")
            with pytest.raises(JournalError):
                read(memo_book)
        fee.write_bytes(original)
        with causal_risk_verification_scope(source.journal):
            read(memo_book)
            source.journal._path.chmod(0o640)
            try:
                with pytest.raises(JournalError, match="private"):
                    read(memo_book)
            finally:
                source.journal._path.chmod(0o600)
    finally:
        fee.write_bytes(original)
        db.rollback()


def test_new_transaction_does_not_inherit_old_snapshot_success(memo_book: Any) -> None:
    source, _, _ = memo_book
    db = source.journal._db
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            expected = read(memo_book)
            writer = sqlite3.connect(source.journal._path, isolation_level=None)
            try:
                corrupt_cache(writer)
            finally:
                writer.close()
            # The existing transaction still observes its original SQLite snapshot.
            assert read(memo_book) == expected
    finally:
        db.rollback()
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            with pytest.raises(JournalError, match="deduplication"):
                read(memo_book)
    finally:
        db.rollback()


def test_nested_fee_dependency_is_rechecked_on_outer_hit(
    memo_book: Any, tmp_path: Path
) -> None:
    source, _, outer_fee = memo_book
    nested_fee = tmp_path / "historical-fees.json"
    expected = outer_fee.read_bytes()
    nested_fee.write_bytes(expected)
    db = source.journal._db
    child_calls = [0]

    def child() -> Any:
        child_calls[0] += 1
        if nested_fee.read_bytes() != expected:
            raise JournalError("historical fee source changed")
        return {
            "historical_source": "verified",
            "source_snapshot_qualified": True,
            "last_snapshot": {"qualified": True},
            "veto": False,
        }, ()

    def nested() -> Any:
        return memoized_causal_risk_prefix(
            source.journal,
            account_ref=REF,
            version="child",
            decision_at=AT,
            fee_policy=nested_fee,
            through_sequence=source.journal.read_evidence_sequence(),
            rebuild=child,
        )

    def outer() -> Any:
        return memoized_causal_risk_prefix(
            source.journal,
            account_ref=REF,
            version="outer",
            decision_at=AT,
            fee_policy=outer_fee,
            through_sequence=source.journal.read_evidence_sequence(),
            rebuild=nested,
        )

    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            assert nested()[0]["historical_source"] == "verified"
            assert outer()[0]["historical_source"] == "verified"
            assert outer()[0]["historical_source"] == "verified"
            assert child_calls[0] == 1
            nested_fee.write_bytes(expected + b"\n")
            with pytest.raises(JournalError):
                outer()
    finally:
        db.rollback()


def test_native_scope_path_resolution_cannot_be_hidden_by_outer_hit(
    native_http: Any,
) -> None:
    owner, harness, at, _, _ = native_http
    journal = owner.journal
    formal = harness.scope.formal_runtime_root
    assert not formal.exists()
    submit(native_http)  # Injected callback only; establish an actual source proof.
    calls = [0]

    def rebuild() -> Any:
        calls[0] += 1
        rows = native_http_inventory(journal)
        return {
            "consumed_groups": len(rows),
            "source_snapshot_qualified": True,
            "last_snapshot": {"qualified": True},
            "veto": False,
        }, ()

    def read_inventory() -> Any:
        return memoized_causal_risk_prefix(
            journal,
            account_ref=owner.binding["account_ref"],
            version="scope-path",
            decision_at=at,
            fee_policy=POLICY,
            through_sequence=journal.read_evidence_sequence(),
            rebuild=rebuild,
        )

    journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            assert read_inventory()[0]["consumed_groups"] == 1
            assert read_inventory()[0]["consumed_groups"] == 1
            assert calls[0] == 1
            formal.symlink_to(harness.root, target_is_directory=True)
            try:
                with pytest.raises(JournalError):
                    read_inventory()
            finally:
                formal.unlink()
    finally:
        journal._db.rollback()


def test_unqualified_results_and_failures_are_not_memoized(memo_book: Any) -> None:
    source, _, fee = memo_book
    journal = source.journal
    calls = [0]

    def failed_source() -> Any:
        calls[0] += 1
        return {
            "source_snapshot_qualified": False,
            "last_snapshot": {"qualified": False},
            "veto": False,
        }, ()

    def attempt() -> Any:
        return memoized_causal_risk_prefix(
            journal,
            account_ref=REF,
            version="unqualified",
            decision_at=AT,
            fee_policy=fee,
            through_sequence=journal.read_evidence_sequence(),
            rebuild=failed_source,
        )

    journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            assert attempt() == attempt()
            assert calls[0] == 2
    finally:
        journal._db.rollback()


def test_inflight_cycle_is_not_a_cached_success_and_cleanup_allows_retry(
    memo_book: Any,
) -> None:
    source, _, fee = memo_book
    journal = source.journal
    recurse = [True]

    def rebuild() -> Any:
        if recurse[0]:
            return read_cycle()
        return {
            "source_snapshot_qualified": True,
            "last_snapshot": {"qualified": True},
            "veto": False,
        }, ()

    def read_cycle() -> Any:
        return memoized_causal_risk_prefix(
            journal,
            account_ref=REF,
            version="cycle",
            decision_at=AT,
            fee_policy=fee,
            through_sequence=journal.read_evidence_sequence(),
            rebuild=rebuild,
        )

    journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            with pytest.raises(JournalError, match="cycle"):
                read_cycle()
            recurse[0] = False
            assert read_cycle()[0]["source_snapshot_qualified"] is True
    finally:
        journal._db.rollback()


@pytest.mark.parametrize("case", ["account", "utc", "horizon"])
def test_fresh_reader_header_checks_remain_before_cache_lookup(
    memo_book: Any, case: str
) -> None:
    source, _, fee = memo_book
    journal = source.journal
    journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            read(memo_book)
            values: dict[str, Any] = {
                "account_ref": REF,
                "decision_at": AT,
                "through_sequence": journal.read_evidence_sequence(),
            }
            if case == "account":
                values["account_ref"] = "foreign-account"
            elif case == "utc":
                values["decision_at"] = AT.replace(tzinfo=None)
            else:
                values["through_sequence"] += 1
            with pytest.raises(JournalError):
                AccountRiskOwner._read_native_causal_evidence_at(
                    journal, fee_policy=fee, **values
                )
    finally:
        journal._db.rollback()


def test_source_mutation_during_rebuild_cannot_cache_or_return_partial_proof(
    memo_book: Any,
) -> None:
    source, _, fee = memo_book
    journal = source.journal

    def mutate() -> Any:
        journal._db.execute("CREATE TABLE mid_verification_change(value TEXT)")
        return {
            "source_snapshot_qualified": True,
            "last_snapshot": {"qualified": True},
            "veto": False,
        }, ()

    journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            with pytest.raises(JournalError, match="changed during verification"):
                memoized_causal_risk_prefix(
                    journal,
                    account_ref=REF,
                    version="changing",
                    decision_at=AT,
                    fee_policy=fee,
                    through_sequence=journal.read_evidence_sequence(),
                    rebuild=mutate,
                )
    finally:
        journal._db.rollback()


def test_copied_context_cannot_reactivate_a_closed_root(
    memo_book: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, _ = memo_book
    calls = reductions(monkeypatch)
    db = source.journal._db
    db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            expected = read(memo_book)
            inherited = copy_context()
        before = calls[0]
        assert inherited.run(read, memo_book) == expected
        first = calls[0]
        assert first > before
        assert inherited.run(read, memo_book) == expected
        assert calls[0] > first

        def fresh_root() -> None:
            with causal_risk_verification_scope(source.journal):
                assert read(memo_book) == expected
                once = calls[0]
                assert read(memo_book) == expected
                assert calls[0] == once

        before = calls[0]
        inherited.run(fresh_root)
        assert calls[0] > before
        before = calls[0]
        inherited.run(fresh_root)
        assert calls[0] > before
    finally:
        db.rollback()
