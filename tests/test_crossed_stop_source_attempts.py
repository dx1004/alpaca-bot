"""Memory-only injected provenance tests; no broker transport."""

import base64
import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from typing import Any, cast

import pytest

from alpaca_ross_bot.crossed_stop_attempts import (
    AUTHORITY,
    ORIGIN,
    SOURCE,
    SourceCloseAttempts,
    SourceCloseCallbackResponse,
)
from alpaca_ross_bot.crossed_stop_contract import load_crossed_stop_contract
from alpaca_ross_bot.crossed_stop_preparation import (
    derive_source_close_precall,
    preparation_request,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.order_reconciliation import RestBatch
from alpaca_ross_bot.paper_engineering import EngineeringHarness, EngineeringScope
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from test_account_risk_owner import RECON_POLICY
from test_crossed_stop_preparation import ready as ready_fixture
from test_observation_candidate_binding import CID
from test_risk_ledger_audit import BEFORE, REF
from test_source_close_handoff import handoff


@pytest.fixture
def source_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[Any]:
    options: dict[str, Any] = getattr(request, "param", {})
    root = tmp_path / "private"
    scope = EngineeringScope(
        "source-close-test", REF, "formal-synthetic-other", tmp_path / "formal"
    )
    if options.get("late_scope"):
        # Negative persisted-history fixture: real immutable writes retain an
        # unscoped first event; a later SCOPE cannot relabel that history.
        prior = Journal.open_private(root / "journal.sqlite3", root)
        try:
            prior.append_next(
                source_id="SYNTHETIC_PRE_SCOPE",
                feed="paper",
                event_kind="UNSCOPED_HISTORY",
                payload=b"{}",
                system_first_seen_at=BEFORE,
                processed_at=BEFORE,
            )
            prior.append_next_if_state_missing(
                state_key="engineering_scope",
                state_value=hashlib.sha256(scope.payload()).hexdigest(),
                source_id="PAPER_ENGINEERING_HARNESS",
                feed="paper",
                event_kind="SCOPE",
                payload=scope.payload(),
                system_first_seen_at=BEFORE,
                processed_at=BEFORE,
            )
        finally:
            prior.close()
    harness = EngineeringHarness(root, scope, clock=lambda: BEFORE)
    original = Journal.open_private
    claimed = False

    def shared(cls: type[Journal], path: Path, runtime_root: Path) -> Journal:
        nonlocal claimed
        if path == root / "journal.sqlite3" and not claimed:
            claimed = True
            return harness.journal
        return original(path, runtime_root)

    monkeypatch.setattr(Journal, "open_private", classmethod(shared))
    fixture = cast(Any, ready_fixture).__wrapped__(tmp_path, monkeypatch, object())
    try:
        ready = next(fixture)
        source, risk, owner, _, at, assets, statuses = ready
        _, cid = handoff(ready)
        if options.get("dirty_before_bind"):
            risk.bind_emergency_policy(
                "dirty-before-close-binding",
                at,
                client_order_id=CID,
                contract=load_crossed_stop_contract(),
            )
            assert risk.view()["issues"] == ["REFRESH_REQUIRED"]
        risk.bind_exit("source-close-role", at, cid, "CLOSE")
        assert risk.view()["issues"] == ["REFRESH_REQUIRED"]
        harness.clock = lambda: at
        attempts = SourceCloseAttempts(owner)
        posts: list[bytes] = []

        def callback(body: bytes) -> SourceCloseCallbackResponse:
            posts.append(body)
            return SourceCloseCallbackResponse(201, b' { "reported" : "only" }\n')

        kwargs = dict(
            client_order_id=cid,
            attempt_id="test-attempt",
            preparation=preparation_request(
                assets, statuses, 0, timedelta(seconds=1), timedelta(seconds=1)
            ),
            decision_at=at,
            started_at=at,
            max_response_bytes=1000,
            clock=lambda: at,
            callback=callback,
        )
        yield attempts, harness, kwargs, posts, ready
    finally:
        fixture.close()


def invoke(fixture: Any, **changes: Any) -> dict[str, Any]:
    attempts, harness, kwargs, _, _ = fixture
    return cast(dict[str, Any], attempts.invoke_once(harness, **(kwargs | changes)))


def test_raw_once_retry_no_simple_send_and_original_receipts(
    source_attempt: Any,
) -> None:
    attempts, _, kwargs, posts, ready = source_attempt
    source, risk, _, emergency, _, _, _ = ready
    before = emergency.view()
    prior = risk.view()
    row = invoke(source_attempt)
    assert len(posts) == 1
    assert row["precall"]["origin"] == ORIGIN
    assert row["precall"]["authority"] == AUTHORITY
    assert (
        base64.b64decode(row["outcome"]["body_base64"]) == b' { "reported" : "only" }\n'
    )
    assert (
        posts[0]
        == source.journal.read_order_intent(kwargs["client_order_id"])[
            "request_body"
        ].encode()
    )
    assert not source.journal.has_order_send_attempt(kwargs["client_order_id"])
    assert invoke(source_attempt) == row and len(posts) == 1
    assert emergency.view() == before and risk.view() == prior
    assert attempts.verify_rebuild()


@pytest.mark.parametrize(
    "change",
    [
        {"attempt_id": "changed"},
        {"max_response_bytes": 999},
        {"started_at": BEFORE},
    ],
)
def test_changed_retry_cannot_invoke(
    source_attempt: Any, change: dict[str, Any]
) -> None:
    invoke(source_attempt)
    with pytest.raises(JournalError):
        invoke(source_attempt, **change)
    assert len(source_attempt[3]) == 1


@pytest.mark.parametrize("failure", ["timeout", "crash", "outcome"])
def test_consumed_fence_survives_failures(
    source_attempt: Any, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    attempts, _, _, posts, _ = source_attempt

    def callback(body: bytes) -> SourceCloseCallbackResponse:
        posts.append(body)
        if failure == "crash":
            raise KeyboardInterrupt
        raise TimeoutError

    if failure == "outcome":
        append = attempts._append

        def fail(item: dict[str, Any], at: Any, prepare: Any) -> bool:
            if item["kind"] == "OUTCOME":
                raise OSError("outcome write fault")
            return cast(bool, append(item, at, prepare))

        monkeypatch.setattr(attempts, "_append", fail)
        with pytest.raises(OSError):
            invoke(source_attempt)
    elif failure == "crash":
        with pytest.raises(KeyboardInterrupt):
            invoke(source_attempt, callback=callback)
    else:
        row = invoke(source_attempt, callback=callback)
        assert row["outcome"]["result_kind"] == "TRANSPORT_UNKNOWN"
    row = invoke(source_attempt)
    assert len(posts) == 1
    if failure != "timeout":
        assert row["outcome"] is None


@pytest.mark.parametrize(
    "response",
    [
        SourceCloseCallbackResponse(201, b"x" * 1001),
        SourceCloseCallbackResponse(True, b"{}"),
    ],
)
def test_invalid_or_unbounded_response_is_unknown(
    source_attempt: Any, response: Any
) -> None:
    row = invoke(source_attempt, callback=lambda _: response)
    assert row["outcome"]["result_kind"] == "TRANSPORT_UNKNOWN"
    assert row["outcome"]["body_base64"] is None


@pytest.mark.parametrize(
    "fault", ["stale", "forged_send", "unrelated", "duplicate_binding"]
)
def test_source_faults_refuse_before_callback(source_attempt: Any, fault: str) -> None:
    _, _, kwargs, posts, ready = source_attempt
    source, risk, _, _, at, _, _ = ready
    changes: dict[str, Any] = {}
    if fault == "stale":
        # Caller-supplied old started_at cannot stand in for the actual clock.
        changes["clock"] = lambda: at + timedelta(seconds=61)
    elif fault == "duplicate_binding":
        risk.bind_exit("duplicate-close-role", at, kwargs["client_order_id"], "CLOSE")
    elif fault == "forged_send":
        source.journal._db.execute(
            "INSERT INTO order_send_attempts(client_order_id,started_at) VALUES (?,?)",
            (kwargs["client_order_id"], at.isoformat()),
        )
        source.journal._db.commit()
    else:
        intent = source.journal.read_order_intent(kwargs["client_order_id"])
        body = json.loads(intent["request_body"])
        body.update(client_order_id="unrelated", symbol="MSFT")
        source.journal.reserve_order_intent(
            "unrelated", intent | {"request_body": json.dumps(body)}
        )
    with pytest.raises(JournalError):
        invoke(source_attempt, **changes)
    assert posts == []
    assert source.journal._read_private_events(SOURCE, None) == ()


def test_reopen_and_corrupt_raw_origin_refuse(source_attempt: Any) -> None:
    attempts, _, _, posts, ready = source_attempt
    row = invoke(source_attempt)
    source, _, owner, _, _, _, _ = ready
    other = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:
        reopened = SourceCloseAttempts(OcoJournal(other, owner.initial))
        assert reopened.view() == [row]
        event, raw = other._read_private_events(SOURCE, None)[0]
        other._db.execute("DROP TRIGGER events_no_update")
        item = json.loads(raw)
        item["origin"] = "BUILTIN_PAPER_HTTPS"
        other._db.execute(
            "UPDATE events SET payload=? WHERE sequence=?",
            (json.dumps(item).encode(), event.sequence),
        )
        other._db.commit()
        with pytest.raises(JournalError):
            attempts.view()
        assert len(posts) == 1
    finally:
        other.close()


@pytest.mark.parametrize("stage", ["proof", "append"])
def test_clock_advances_after_qualification_sample_never_invokes(
    source_attempt: Any, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from alpaca_ross_bot import crossed_stop_attempts

    attempts, _, _, posts, ready = source_attempt
    source, _, _, _, at, _, _ = ready
    now = [at]
    if stage == "proof":
        derive = derive_source_close_precall

        def delayed_proof(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = derive(*args, **kwargs)
            now[0] = at + timedelta(seconds=61)
            return result

        # Negative time fault: execute the genuine proof before advancing time.
        monkeypatch.setattr(
            crossed_stop_attempts, "derive_source_close_precall", delayed_proof
        )
        with pytest.raises(JournalError):
            invoke(source_attempt, clock=lambda: now[0])
        assert source.journal._read_private_events(SOURCE, None) == ()
    else:
        append = source.journal._append_locked

        def delayed_append(event: Any, *args: Any, **kwargs: Any) -> Any:
            if event.source_id == SOURCE and event.event_kind == "PRECALL":
                now[0] = at + timedelta(seconds=61)
            return append(event, *args, **kwargs)

        # Negative time fault after the producer stamped its real PRECALL.
        monkeypatch.setattr(source.journal, "_append_locked", delayed_append)
        row = invoke(source_attempt, clock=lambda: now[0])
        assert row["outcome"]["result_kind"] == "LOCAL_NOT_INVOKED"
        assert row["precall"]["qualified_at"] == at.isoformat()
        assert row["precall"]["recorded_at"] == at.isoformat()
        assert attempts.view() == [row]
    assert posts == []


def test_clock_expires_after_committed_fence_retains_local_no_resend(
    source_attempt: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts, _, kwargs, posts, ready = source_attempt
    source, risk, _, _, at, _, _ = ready
    before = risk.view()
    append = attempts._append
    now = [at]

    def expire_after_commit(item: dict[str, Any], clock: Any, prepare: Any) -> bool:
        result = cast(bool, append(item, clock, prepare))
        if result and item["kind"] == "PRECALL":
            assert not source.journal._db.in_transaction
            assert len(source.journal._read_private_events(SOURCE, None)) == 1
            now[0] = at + timedelta(seconds=61)
        return result

    monkeypatch.setattr(attempts, "_append", expire_after_commit)
    row = invoke(source_attempt, clock=lambda: now[0])
    assert row["outcome"]["result_kind"] == "LOCAL_NOT_INVOKED"
    assert row["outcome"]["http_status"] is None
    assert row["outcome"]["body_base64"] is None
    assert len(source.journal._read_private_events(SOURCE, None)) == 2
    assert invoke(source_attempt, clock=lambda: now[0]) == row
    assert posts == [] and risk.view() == before
    assert not source.journal.has_order_send_attempt(kwargs["client_order_id"])


def test_separate_private_connections_race_consumes_one_callback(
    source_attempt: Any,
) -> None:
    _, harness, kwargs, posts, ready = source_attempt
    source, risk, owner, _, at, _, _ = ready
    before = risk.view()
    barrier = Barrier(2)

    def run() -> dict[str, Any]:
        other = EngineeringHarness(harness.root, harness.scope, clock=lambda: at)
        try:
            other.journal._db.execute("PRAGMA busy_timeout=120000")
            attempts = SourceCloseAttempts(OcoJournal(other.journal, owner.initial))
            barrier.wait(timeout=120)
            return attempts.invoke_once(other, **kwargs)
        finally:
            other.journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [future.result(timeout=180) for future in futures]
    assert results[0]["precall"] == results[1]["precall"]
    assert results[0]["attempt_ref"] == results[1]["attempt_ref"]
    assert len(posts) == 1
    assert len(source.journal._read_private_events(SOURCE, None)) == 2
    final = SourceCloseAttempts(owner).view()
    assert len(final) == 1 and final[0]["outcome"]["result_kind"] == "INJECTED_RESPONSE"
    for result in results:
        if result["outcome"] is not None:
            assert result == final[0]
    assert risk.view() == before
    assert not source.journal.has_order_send_attempt(kwargs["client_order_id"])


@pytest.mark.parametrize("source_attempt", [{"late_scope": True}], indirect=True)
def test_late_scope_cannot_relabel_populated_journal(source_attempt: Any) -> None:
    _, _, _, posts, ready = source_attempt
    source = ready[0]
    assert source.journal.read_engineering_events()[0][0].sequence == 2
    horizon = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="engineering scope differs"):
        invoke(source_attempt)
    assert source.journal.read_evidence_sequence() == horizon
    assert source.journal._read_private_events(SOURCE, None) == ()
    assert posts == []


@pytest.mark.parametrize("source_attempt", [{"dirty_before_bind": True}], indirect=True)
def test_metadata_bridge_does_not_waive_dirty_prebinding_risk(
    source_attempt: Any,
) -> None:
    _, _, _, posts, ready = source_attempt
    source, risk, _, _, _, _, _ = ready
    before = risk.view()
    assert before["issues"] == ["REFRESH_REQUIRED"]
    with pytest.raises(JournalError, match="pre-binding risk state is unqualified"):
        invoke(source_attempt)
    assert posts == []
    assert source.journal._read_private_events(SOURCE, None) == ()
    assert risk.view() == before


def test_other_empty_scope_and_later_sources_retain_historical_attempt(
    source_attempt: Any,
) -> None:
    attempts, _, kwargs, posts, ready = source_attempt
    source, risk, owner, _, at, _, _ = ready
    before = risk.view()
    row = invoke(source_attempt)
    qualification = row["precall"]["qualification"]
    bridge = qualification["metadata_bridge"]
    assert bridge["snapshot_refreshed"] is False
    assert bridge["snapshot_observed_at"] == before["last_snapshot"]["observed_at"]
    assert risk.view() == before
    for key in (
        "last_snapshot",
        "pending_open_loss_reserve",
        "additional_local_cash_hold",
    ):
        assert risk.view()[key] == before[key]
    second_initial = replace(owner.initial, entry_scope="other-empty-canonical-scope")
    empty = OcoJournal(source.journal, second_initial)
    assert empty.view().model.executions == ()
    assert SourceCloseAttempts(empty).view() == []
    assert attempts.view() == [row]
    later = at + timedelta(seconds=1)
    report = source.reconciler.apply_rest_batch(
        RestBatch(
            REF,
            later,
            later,
            cast(int, source.reconciler.view()["watermark"]),
            (),
            (
                {
                    "symbol": "AAPL",
                    "qty": "2",
                    "side": "long",
                    "asset_class": "us_equity",
                },
            ),
            {
                cid: PaperHttpResponse(200, json.dumps(order).encode())
                for cid, order in source.orders.items()
            },
            {
                str(order["id"]): tuple(
                    fill for fill in source.fills if fill["order_id"] == order["id"]
                )
                for order in source.orders.values()
            },
            True,
            True,
        ),
        RECON_POLICY,
        at=later,
    )
    assert report.status == "DIFFERENCES"
    assert {difference["code"] for difference in report.differences} == {
        "INTENT_NOT_SENT"
    }
    prior_capture = source.journal.read_event(
        before["last_snapshot"]["selection"]["capture_sequence"]
    )
    assert prior_capture is not None
    # A later arrival of identical raw account bytes is still unassessed input.
    # It cannot refresh risk or bypass the unsent close's reconciliation gap.
    source.journal.append_next(
        source_id=prior_capture[0].source_id,
        feed=prior_capture[0].feed,
        event_kind=prior_capture[0].event_kind,
        payload=prior_capture[1],
        system_first_seen_at=later,
        processed_at=later,
    )
    assert attempts.view() == [row]
    with pytest.raises(JournalError):
        derive_source_close_precall(
            owner,
            client_order_id=kwargs["client_order_id"],
            horizon=source.journal.read_evidence_sequence(),
            at=later,
            request=kwargs["preparation"],
            _require_current_census=True,
        )
    other = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:
        assert SourceCloseAttempts(OcoJournal(other, owner.initial)).view() == [row]
        assert SourceCloseAttempts(OcoJournal(other, second_initial)).view() == []
    finally:
        other.close()
    assert len(posts) == 1


@pytest.mark.parametrize("fault", ["expired_recording", "outcome_before_recording"])
def test_hash_consistent_replay_time_mutation_is_semantically_rejected(
    source_attempt: Any, fault: str
) -> None:
    attempts, _, _, posts, ready = source_attempt
    source, _, _, _, at, _, _ = ready
    row = invoke(source_attempt)
    pre, out = row["precall"], row["outcome"]
    if fault == "expired_recording":
        pre["recorded_at"] = (at + timedelta(seconds=2)).isoformat()
        out["completed_at"] = (at + timedelta(seconds=3)).isoformat()
        out["recorded_at"] = out["completed_at"]
    else:
        earlier = (at - timedelta(seconds=1)).isoformat()
        pre["command"]["decision_at"] = earlier
        pre["command"]["started_at"] = earlier
        pre["recorded_at"] = (at + timedelta(milliseconds=500)).isoformat()
        out["completed_at"] = (at + timedelta(milliseconds=250)).isoformat()
        out["recorded_at"] = (at + timedelta(seconds=1)).isoformat()
    events = source.journal._read_private_events(SOURCE, None)
    assert len(events) == 2

    def canonical(value: Any) -> bytes:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()

    # Negative storage fault: rehash both immutable events, their time envelopes,
    # downstream attempt reference, projection and dedupe refs consistently.
    # Raw integrity must succeed; only semantic replay may reject this history.
    source.journal._db.execute("DROP TRIGGER events_no_update")
    for index, (event, _) in enumerate(events):
        item = pre if index == 0 else out
        payload = canonical(item)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        ref = {"sequence": event.sequence, "hash": digest}
        if index == 0:
            row["attempt_ref"] = ref
            out["attempt_ref"] = ref
        else:
            row["outcome_ref"] = ref
        stamp = item["recorded_at"]
        source.journal._db.execute(
            "UPDATE events SET payload=?,payload_ref=?,system_first_seen_at=?,"
            "processed_at=?,available_at=? WHERE sequence=?",
            (payload, digest, stamp, stamp, stamp, event.sequence),
        )
        key = (
            "source_close_attempt_evidence:"
            + item["account_ref"]
            + ":"
            + canonical(
                [item["entry_scope"], item["client_order_id"], item["kind"]]
            ).decode()
        )
        cursor = source.journal._db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (canonical(ref).decode(), key),
        )
        assert cursor.rowcount == 1
    cursor = source.journal._db.execute(
        "UPDATE state SET value_json=? WHERE key=?",
        (canonical([row]).decode(), attempts.state_key),
    )
    assert cursor.rowcount == 1
    source.journal._db.commit()
    raw_events = source.journal._read_private_events(SOURCE, None)
    assert [json.loads(raw) for _, raw in raw_events] == [pre, out]
    assert source.journal.read_state(attempts.state_key) == [row]
    for event, raw in raw_events:
        item = json.loads(raw)
        key = (
            "source_close_attempt_evidence:"
            + item["account_ref"]
            + ":"
            + canonical(
                [item["entry_scope"], item["client_order_id"], item["kind"]]
            ).decode()
        )
        assert source.journal.read_state(key) == {
            "sequence": event.sequence,
            "hash": event.payload_ref,
        }
    with pytest.raises(JournalError):
        attempts.view()
    assert len(posts) == 1
