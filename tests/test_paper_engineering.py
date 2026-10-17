import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.paper_engineering import EngineeringHarness, EngineeringScope
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import (
    PaperHttpResponse,
    PaperOrderHttp,
    PaperOrderHttpError,
)
from test_paper_order_cancel import (
    BROKER_ID,
    CONFIG,
    CREDENTIALS,
    _request,
)
from test_paper_order_cancel import (
    FakeExchange as CancelExchange,
)
from test_paper_order_recovery import (
    ACCOUNT_BODY,
    ACCOUNT_REF,
    NOW,
    POLICY,
    FakeExchange,
    api,
    order,
    reserve,
)

FORMAL_BODY = b'{"id":"33333333-3333-4333-8333-333333333333"}'
FORMAL_REF = paper_account_ref(FORMAL_BODY)


def proof_api() -> PaperOrderHttp:
    exchange = FakeExchange()
    exchange.account_views = [PaperHttpResponse(200, FORMAL_BODY)]
    return PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        FORMAL_REF,
        timeout_seconds=2,
        max_request_bytes=4096,
        max_response_bytes=4096,
        exchange=exchange,
    )


def scope(tmp_path: Path) -> EngineeringScope:
    return EngineeringScope(
        "experiment-1", ACCOUNT_REF, FORMAL_REF, tmp_path / "formal"
    )


def test_account_and_nested_or_symlink_storage_isolation(tmp_path: Path) -> None:
    spec = scope(tmp_path)
    with pytest.raises(ValueError, match="accounts must differ"):
        EngineeringHarness(
            tmp_path / "engineering",
            replace(spec, formal_account_ref=ACCOUNT_REF),
            clock=lambda: NOW,
        )
    for root in (tmp_path, tmp_path / "formal", tmp_path / "formal" / "child"):
        with pytest.raises(ValueError, match="overlap"):
            EngineeringHarness(root, spec, clock=lambda: NOW)
    (tmp_path / "formal").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "formal", target_is_directory=True)
    with pytest.raises(ValueError, match="overlap"):
        EngineeringHarness(tmp_path / "alias", spec, clock=lambda: NOW)


def test_scope_survives_restart_and_cannot_be_relabelled(tmp_path: Path) -> None:
    root = tmp_path / "engineering"
    spec = scope(tmp_path)
    harness = EngineeringHarness(root, spec, clock=lambda: NOW)
    before = harness.journal.read_engineering_events()
    harness.close()
    reopened = EngineeringHarness(root, spec, clock=lambda: NOW)
    assert reopened.journal.read_engineering_events() == before
    reopened.close()
    with pytest.raises(JournalError, match="conflicts"):
        EngineeringHarness(
            root, replace(spec, experiment_id="another"), clock=lambda: NOW
        )


def test_existing_unscoped_account_journal_cannot_be_adopted(tmp_path: Path) -> None:
    root = tmp_path / "engineering"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    journal.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_BODY, NOW)
    journal.close()
    with pytest.raises(JournalError, match="unscoped"):
        EngineeringHarness(root, scope(tmp_path), clock=lambda: NOW)


def test_empty_recovery_does_not_claim_cleanup_or_stream_continuity(
    tmp_path: Path,
) -> None:
    harness = EngineeringHarness(
        tmp_path / "engineering", scope(tmp_path), clock=lambda: NOW
    )
    exchange = FakeExchange()
    try:
        result = harness.recover(api(exchange), POLICY)
        assert result["observed_empty_book"] is True
        assert result["status"] == "RECOVERY_REQUIRED"
        assert result["cleanup_accepted"] is False
        assert result["runtime_authorized"] is False
        assert result["send_attempt_count"] == 0
        assert all(method == "GET" for method, _, _ in exchange.calls)
        assert (
            harness.journal.read_engineering_events()[-1][0].event_kind
            == "RECOVERY_ASSESSMENT"
        )
        assert ACCOUNT_REF not in json.dumps(result)
    finally:
        harness.close()


def test_restart_recovers_unknown_send_without_resend_or_cancel(tmp_path: Path) -> None:
    root = tmp_path / "engineering"
    spec = scope(tmp_path)
    harness = EngineeringHarness(root, spec, clock=lambda: NOW)
    harness.journal.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_BODY, NOW)
    reserve(harness.journal, order())
    harness.close()
    harness = EngineeringHarness(root, spec, clock=lambda: NOW)
    exchange = FakeExchange()  # 404 for a timed-out send, never replay authority
    try:
        result = harness.recover(api(exchange), POLICY)
        assert result["send_attempt_count"] == 1
        assert result["cleanup_accepted"] is False
        assert all(method == "GET" for method, _, _ in exchange.calls)
        assert not harness.journal.has_order_cancel_attempt("client-1")
    finally:
        harness.close()


def test_scope_corruption_blocks_network(tmp_path: Path) -> None:
    harness = EngineeringHarness(
        tmp_path / "engineering", scope(tmp_path), clock=lambda: NOW
    )
    harness.journal.append_next(
        source_id="synthetic-corruption",
        feed="paper",
        event_kind="TEST",
        payload=b"{}",
        system_first_seen_at=NOW,
        processed_at=NOW,
        state_updates={"engineering_scope": "changed"},
    )
    exchange = FakeExchange()
    try:
        with pytest.raises(JournalError, match="conflicts"):
            harness.recover(api(exchange), POLICY)
        assert exchange.calls == []
    finally:
        harness.close()


def test_explicit_cancel_timeout_restarts_without_repeated_delete(
    tmp_path: Path,
) -> None:
    root = tmp_path / "engineering"
    spec = scope(tmp_path)
    harness = EngineeringHarness(root, spec, clock=lambda: NOW)
    harness.journal.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_BODY, NOW)
    request = _request()
    harness.journal.reserve_order_intent(
        "entry-1",
        {
            "paper_endpoint": CONFIG.trading_base_url,
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": request,
        },
    )
    harness.journal.mark_order_send_attempt("entry-1", NOW)
    exchange = CancelExchange(request)
    exchange.cancel = TimeoutError("synthetic unknown cancellation")
    http = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        ACCOUNT_REF,
        timeout_seconds=2,
        max_request_bytes=4096,
        max_response_bytes=4096,
        exchange=exchange,
    )
    with pytest.raises(PaperOrderHttpError, match="unknown"):
        harness.cancel_entry_once(http, "entry-1", formal_api=proof_api())
    attempt = harness.journal.read_order_cancel_attempt("entry-1")
    assert attempt is not None and attempt[0] == BROKER_ID
    harness.close()
    harness = EngineeringHarness(root, spec, clock=lambda: NOW)
    try:
        exchange.calls.clear()
        with pytest.raises(JournalError, match="already attempted"):
            harness.cancel_entry_once(http, "entry-1", formal_api=proof_api())
        assert all(method == "GET" for method, _, _ in exchange.calls)
    finally:
        harness.close()


def test_incomplete_capture_never_reports_empty_book_acceptance(tmp_path: Path) -> None:
    harness = EngineeringHarness(
        tmp_path / "engineering", scope(tmp_path), clock=lambda: NOW
    )
    exchange = FakeExchange()
    exchange.position_failure = PaperHttpResponse(503, b"unavailable")
    try:
        result = harness.recover(api(exchange), POLICY)
        assert result["capture_complete"] is False
        assert result["observed_empty_book"] is False
        assert result["cleanup_accepted"] is False
    finally:
        harness.close()


def test_hardlinked_database_is_rejected_before_open(tmp_path: Path) -> None:
    formal = tmp_path / "formal"
    j = Journal.open_private(formal / "journal.sqlite3", formal)
    j.close()
    before = (formal / "journal.sqlite3").read_bytes()
    root = tmp_path / "engineering"
    root.mkdir(mode=0o700)
    os.link(formal / "journal.sqlite3", root / "journal.sqlite3")
    with pytest.raises(JournalError, match="one link"):
        EngineeringHarness(root, scope(tmp_path), clock=lambda: NOW)
    assert (formal / "journal.sqlite3").read_bytes() == before


def test_same_real_account_disguised_by_formal_reference_blocks_cancel(
    tmp_path: Path,
) -> None:
    harness = EngineeringHarness(
        tmp_path / "engineering", scope(tmp_path), clock=lambda: NOW
    )
    exchange = FakeExchange()
    try:
        with pytest.raises(PaperOrderHttpError, match="identity differs"):
            harness.cancel_entry_once(
                api(exchange), "entry-1", formal_api=api(FakeExchange())
            )
        assert exchange.calls == []
    finally:
        harness.close()


def test_cli_default_never_reads_scope_or_credentials(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/run_paper_engineering.py"),
            "--scope-file",
            str(tmp_path / "absent-scope"),
            "--runtime-root",
            str(tmp_path / "absent-runtime"),
            "--credentials-file",
            str(tmp_path / "absent-credentials"),
        ],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(result.stdout) == {
        "status": "NOT_RUN",
        "runtime_authorized": False,
    }
    assert not (tmp_path / "absent-runtime").exists()


def test_new_scope_persistence_failure_cannot_leave_usable_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic scope disk failure")

    monkeypatch.setattr(Journal, "append_next_if_state_missing", fail)
    with pytest.raises(OSError, match="disk failure"):
        EngineeringHarness(tmp_path / "engineering", scope(tmp_path), clock=lambda: NOW)


def test_hardlink_created_after_open_blocks_recovery(tmp_path: Path) -> None:
    root = tmp_path / "engineering"
    harness = EngineeringHarness(root, scope(tmp_path), clock=lambda: NOW)
    os.link(root / "journal.sqlite3", tmp_path / "another-name.sqlite3")
    exchange = FakeExchange()
    try:
        with pytest.raises(JournalError, match="one link"):
            harness.recover(api(exchange), POLICY)
        assert exchange.calls == []
    finally:
        harness.close()
