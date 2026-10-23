"""Injected callbacks only; neither broker transport nor protection acceptance."""

import base64
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from threading import Barrier, Lock
from typing import Any, cast

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_attempts import (
    NativeOcoAttempts,
    NativeOcoCallbackResponse,
)
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from test_account_risk_owner import SourceAccount
from test_native_oco_correlation_integration import ReportedExchange
from test_native_oco_requests import prepared as prepared

Prepared = tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime]


def run(
    attempts: NativeOcoAttempts,
    at: datetime,
    *,
    group: str = "group-a",
    identity: str = "attempt-a",
    response: NativeOcoCallbackResponse | None = None,
) -> dict[str, Any]:
    def callback(body: bytes) -> NativeOcoCallbackResponse:
        exchange = ReportedExchange(body.decode(), at)
        return response or NativeOcoCallbackResponse(
            201, json.dumps(exchange.order).encode()
        )

    return attempts.run_fake_once(
        group,
        identity,
        decision_at=at,
        started_at=at,
        callback=callback,
        clock=lambda: at,
    )


def test_positive_fields_keep_raw_bytes_but_never_ack_or_mutate_allocations(
    prepared: Prepared,
) -> None:
    source, owner, requests, at = prepared
    preparation = requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    original_owner = owner.view()
    result = run(attempts, at)
    before = source.journal.read_native_oco_attempt_events()
    precall, outcome = result["precall_receipt"], result["outcome"]
    assert result["status"] == "INJECTED_CALLBACK_OUTCOME_RECORDED"
    assert precall["prepared_ref"] == preparation["request_ref"]
    assert precall["request_sha256"] == preparation["receipt"]["request_sha256"]
    assert precall["source_horizon"] < result["attempt_ref"]["sequence"]
    assert result["attempt_ref"]["sequence"] < result["outcome_ref"]["sequence"]
    assert outcome["classification"] == "INJECTED_TEST_CALLBACK_NOT_BROKER_PROOF"
    assert outcome["correlation"]["status"] == "REPORTED_FIELDS_MATCH"
    raw = base64.b64decode(outcome["body_base64"], validate=True)
    assert json.loads(raw)["client_order_id"] == "native-cid-a"
    assert not any(result["authority"].values())
    assert result["send_proven"] is result["ack_proven"] is False
    assert owner.view() == original_owner
    assert owner.view().model.groups[0].acknowledgement == "UNKNOWN"
    assert source.journal.read_order_send_started_at("native-cid-a") is None
    assert run(attempts, at) == result
    assert source.journal.read_native_oco_attempt_events() == before
    assert attempts.verify_rebuild()


@pytest.mark.parametrize(
    "status,body,issue",
    [
        (422, b'{"message":"rejected"}', "NON_2XX_RESPONSE"),
        (503, b"unavailable", "NON_2XX_RESPONSE"),
        (201, b"not JSON", "RESPONSE_TOPOLOGY_UNQUALIFIED"),
        (200, b"{}", "RESPONSE_TOPOLOGY_UNQUALIFIED"),
    ],
)
def test_raw_status_and_arbitrary_body_survive_unqualified_correlation(
    prepared: Prepared,
    status: int,
    body: bytes,
    issue: str,
) -> None:
    _, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    result = run(
        NativeOcoAttempts(requests),
        at,
        response=NativeOcoCallbackResponse(status, body),
    )
    outcome = result["outcome"]
    assert outcome["http_status"] == status
    assert base64.b64decode(outcome["body_base64"]) == body
    assert issue in outcome["correlation"]["issues"]
    assert outcome["error_category"] is None


@pytest.mark.parametrize(
    "failure,category",
    [
        (TimeoutError, "TIMEOUT"),
        (ConnectionError, "CONNECTION_ERROR"),
        (FileNotFoundError, "OS_ERROR"),
        (RuntimeError, "CALLBACK_ERROR"),
    ],
)
def test_exceptions_are_controlled_unknowns_without_exception_text_or_retry(
    prepared: Prepared,
    failure: type[Exception],
    category: str,
) -> None:
    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    calls = []

    def callback(_: bytes) -> NativeOcoCallbackResponse:
        calls.append(1)
        raise failure("DO_NOT_STORE_CALLBACK_EXCEPTION_TEXT")

    result = attempts.run_fake_once(
        "group-a",
        "attempt-a",
        decision_at=at,
        started_at=at,
        callback=callback,
        clock=lambda: at,
    )
    assert result["outcome"]["result_kind"] == "CALLBACK_UNKNOWN"
    assert result["outcome"]["error_category"] == category
    assert result["outcome"]["body_base64"] is None
    assert result["outcome"]["http_status"] is None
    assert "DO_NOT_STORE" not in json.dumps(result)
    assert all(
        b"DO_NOT_STORE" not in raw
        for _, raw in source.journal.read_native_oco_attempt_events()
    )
    assert (
        attempts.run_fake_once(
            "group-a",
            "attempt-a",
            decision_at=at,
            started_at=at,
            callback=callback,
            clock=lambda: at,
        )
        == result
    )
    assert calls == [1]


@pytest.mark.parametrize(
    "bad",
    [
        NativeOcoCallbackResponse(True, b"{}"),
        NativeOcoCallbackResponse(999, b"{}"),
        "not response object",
    ],
)
def test_invalid_callback_response_shape_still_consumes_attempt(
    prepared: Prepared,
    bad: object,
) -> None:
    _, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    result = attempts.run_fake_once(
        "group-a",
        "attempt-a",
        decision_at=at,
        started_at=at,
        callback=lambda _: cast(Any, bad),
        clock=lambda: at,
    )
    assert result["outcome"]["error_category"] == "INVALID_RESPONSE"
    assert attempts.verify_rebuild()


def test_crash_and_bad_completion_clock_leave_consumed_precall_without_fabricated_time(
    prepared: Prepared,
) -> None:
    _, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    calls = []

    def callback(_: bytes) -> NativeOcoCallbackResponse:
        calls.append(1)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        attempts.run_fake_once(
            "group-a",
            "attempt-a",
            decision_at=at,
            started_at=at,
            callback=callback,
            clock=lambda: at,
        )
    retry = run(attempts, at)
    assert calls == [1] and retry["outcome"] is None
    assert retry["status"] == "PRECALL_CONSUMED_OUTCOME_UNKNOWN"


def test_reversed_completion_clock_does_not_create_outcome_or_allow_another_callback(
    prepared: Prepared,
) -> None:
    _, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    calls = []

    def callback(_: bytes) -> NativeOcoCallbackResponse:
        calls.append(1)
        return NativeOcoCallbackResponse(422, b"{}")

    with pytest.raises(JournalError, match="clock"):
        attempts.run_fake_once(
            "group-a",
            "attempt-a",
            decision_at=at,
            started_at=at,
            callback=callback,
            clock=lambda: at - timedelta(seconds=1),
        )
    assert run(attempts, at)["outcome"] is None and calls == [1]


def test_caller_transaction_is_not_committed_rolled_back_or_used_for_callback(
    prepared: Prepared,
) -> None:
    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    before = source.journal.read_native_oco_attempt_events()
    source.journal._db.execute("BEGIN IMMEDIATE")
    source.journal._db.execute(
        "INSERT INTO state VALUES ('unrelated-uncommitted','{}')"
    )
    calls = []

    def callback(_: bytes) -> NativeOcoCallbackResponse:
        calls.append(1)
        return NativeOcoCallbackResponse(422, b"{}")

    try:
        with pytest.raises(JournalError, match="caller-owned"):
            attempts.run_fake_once(
                "group-a",
                "attempt-a",
                decision_at=at,
                started_at=at,
                callback=callback,
                clock=lambda: at,
            )
        assert source.journal._db.in_transaction
        assert source.journal.read_state("unrelated-uncommitted") == {}
        assert not calls
    finally:
        source.journal._db.rollback()
    assert source.journal.read_state("unrelated-uncommitted") is None
    assert source.journal.read_native_oco_attempt_events() == before


def test_changed_identity_is_sticky_original_result_retained_and_known_conflict_dedupes(
    prepared: Prepared,
) -> None:
    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    original = run(attempts, at)
    conflict = run(attempts, at, identity="changed-attempt")
    assert conflict["status"] == "CURRENT_CONFLICT"
    assert conflict["precall_receipt"] == original["precall_receipt"]
    assert conflict["attempt_ref"] == original["attempt_ref"]
    assert conflict["outcome"] == original["outcome"]
    before = source.journal.read_native_oco_attempt_events()
    assert run(attempts, at, identity="changed-attempt") == conflict
    assert source.journal.read_native_oco_attempt_events() == before
    state = NativeOcoAttempts(requests).view()
    assert state is not None and state["conflict_groups"] == ["group-a"]


@pytest.mark.parametrize("opener", ["callback", "clock"])
def test_callback_owned_transaction_survives_outcome_refusal_without_replay(
    prepared: Prepared,
    opener: str,
) -> None:
    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    calls = []

    def begin() -> None:
        source.journal._db.execute("BEGIN IMMEDIATE")
        source.journal._db.execute("INSERT INTO state VALUES ('callback-private','{}')")

    def callback(_: bytes) -> NativeOcoCallbackResponse:
        calls.append(1)
        if opener == "callback":
            begin()
        return NativeOcoCallbackResponse(422, b"{}")

    def clock() -> datetime:
        if opener == "clock":
            begin()
        return at

    with pytest.raises(JournalError, match="callback-owned"):
        attempts.run_fake_once(
            "group-a",
            "attempt-a",
            decision_at=at,
            started_at=at,
            callback=callback,
            clock=clock,
        )
    assert source.journal._db.in_transaction
    assert source.journal.read_state("callback-private") == {}
    source.journal._db.rollback()
    assert run(attempts, at)["outcome"] is None
    assert calls == [1]


def test_two_connections_compete_for_one_callback_after_serialized_attempt(
    prepared: Prepared,
    tmp_path: Path,
) -> None:
    _, owner, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    barrier, lock = Barrier(2), Lock()
    calls: list[bytes] = []

    def worker() -> dict[str, Any]:
        root = tmp_path / "private"
        journal = Journal.open_private(root / "journal.sqlite3", root)
        try:
            local = NativeOcoAttempts(
                NativeOcoRequests(OcoJournal(journal, owner.initial))
            )
            barrier.wait(timeout=10)

            def callback(body: bytes) -> NativeOcoCallbackResponse:
                with lock:
                    calls.append(body)
                return NativeOcoCallbackResponse(422, b"{}")

            return local.run_fake_once(
                "group-a",
                "attempt-a",
                decision_at=at,
                started_at=at,
                callback=callback,
                clock=lambda: at,
            )
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert len(calls) == 1
    assert results[0]["attempt_ref"] == results[1]["attempt_ref"]
    assert NativeOcoAttempts(requests).verify_rebuild()


@pytest.mark.parametrize("fault", ["raw", "projection", "dedupe"])
def test_corrupt_immutable_or_cache_bytes_refuse_without_repair_or_callback(
    prepared: Prepared,
    tmp_path: Path,
    fault: str,
) -> None:
    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    attempts = NativeOcoAttempts(requests)
    result = run(attempts, at)
    with sqlite3.connect(tmp_path / "private/journal.sqlite3") as db:
        if fault == "raw":
            db.execute("DROP TRIGGER events_no_update")
            db.execute(
                "UPDATE events SET payload=? WHERE sequence=?",
                (b"{}", result["outcome_ref"]["sequence"]),
            )
        elif fault == "projection":
            db.execute(
                "UPDATE state SET value_json='{}' WHERE key=?", (attempts.state_key,)
            )
        else:
            db.execute(
                "UPDATE state SET value_json='{}' "
                "WHERE key LIKE 'native_oco_attempt_evidence:%'"
            )
    before = tuple(source.journal._db.iterdump())
    with pytest.raises(JournalError):
        run(attempts, at)
    assert tuple(source.journal._db.iterdump()) == before


def test_preparation_conflict_blocks_new_attempt_without_callback(
    prepared: Prepared,
) -> None:
    source, _, requests, at = prepared
    requests.prepare("group-a", "native-cid-a", at, at)
    requests.prepare("group-a", "changed-cid", at, at)
    attempts = NativeOcoAttempts(requests)
    with pytest.raises(JournalError, match="conflict"):
        run(attempts, at)
    assert source.journal.read_native_oco_attempt_events() == ()


def test_broker_identity_reuse_is_reported_conflict_not_second_positive_match(
    prepared: Prepared,
) -> None:
    _, owner, requests, at = prepared
    owner.allocate_group(
        "group-b",
        (LotPortion(owner.view().model.executions[0].execution_id, Fraction(1)),),
        at,
    )
    requests.prepare("group-a", "native-cid-a", at, at)
    requests.prepare("group-b", "native-cid-b", at, at)
    attempts = NativeOcoAttempts(requests)
    run(attempts, at)
    result = run(attempts, at, group="group-b", identity="attempt-b")
    assert result["status"] == "CURRENT_CONFLICT"
    assert (
        "RESPONSE_BROKER_IDENTITY_REUSED" in result["outcome"]["correlation"]["issues"]
    )
    assert attempts.verify_rebuild()
