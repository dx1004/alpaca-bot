"""Independent injected-only durability checks; no broker or order authority."""

import json
from datetime import datetime
from typing import Any

import pytest

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_attempts import (
    NativeOcoAttempts,
    NativeOcoCallbackResponse,
)
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from test_account_risk_owner import SourceAccount
from test_native_oco_correlation_integration import ReportedExchange
from test_native_oco_requests import prepared as prepared


def test_callback_sees_committed_marker_from_an_independent_connection(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, owner, requests, at = prepared
    saved = requests.prepare("group-a", "native-integration", at, at)
    boundary = NativeOcoAttempts(requests)
    request_body = saved["receipt"]["request_body"].encode()
    risk_before = source.journal.read_account_risk_events()
    lifecycle_before = source.journal.read_order_events()
    calls = 0

    def callback(body: bytes) -> NativeOcoCallbackResponse:
        nonlocal calls
        calls += 1
        assert body == request_body
        assert not source.journal._db.in_transaction
        other = Journal.open_private(source.journal._path, source.journal._runtime_root)
        try:
            events = other.read_native_oco_attempt_events()
            assert len(events) == 1
            assert events[0][0].event_kind == "PRECALL_INTENT"
            recovered = NativeOcoAttempts(
                NativeOcoRequests(OcoJournal(other, owner.initial))
            )
            assert recovered.verify_rebuild()
            assert recovered.view() is not None
        finally:
            other.close()
        response = ReportedExchange(body.decode(), at).order
        return NativeOcoCallbackResponse(200, json.dumps(response).encode())

    first = boundary.run_fake_once(
        "group-a",
        "independent-attempt",
        decision_at=at,
        started_at=at,
        callback=callback,
        clock=lambda: at,
    )
    repeated = boundary.run_fake_once(
        "group-a",
        "independent-attempt",
        decision_at=at,
        started_at=at,
        callback=callback,
        clock=lambda: at,
    )
    assert first == repeated and calls == 1
    assert first["send_proven"] is False and first["ack_proven"] is False
    assert not any(first["authority"].values())
    assert owner.view().model.groups[0].acknowledgement == "UNKNOWN"
    assert source.journal.read_account_risk_events() == risk_before
    assert source.journal.read_order_events() == lifecycle_before
    assert source.journal.read_order_intent("native-integration") is None
    with pytest.raises(JournalError):
        source.journal.mark_order_send_attempt("native-integration", at)
    assert source.journal.read_order_send_started_at("native-integration") is None


def test_failed_outcome_transaction_survives_reopen_without_callback_replay(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, owner, requests, at = prepared
    requests.prepare("group-a", "native-failed-outcome", at, at)
    boundary = NativeOcoAttempts(requests)
    original = Journal.apply_native_oco_attempt_event
    calls = 0

    def fail_outcome(self: Journal, **kwargs: Any) -> EventEnvelope | None:
        if kwargs["event_kind"] == "FAKE_OUTCOME":
            reducer = kwargs["reduce"]

            def fail(old: object | None, conflict: bool, sequence: int) -> object:
                reducer(old, conflict, sequence)
                raise JournalError("injected outcome rollback")

            kwargs["reduce"] = fail
        return original(self, **kwargs)

    def callback(body: bytes) -> NativeOcoCallbackResponse:
        nonlocal calls
        calls += 1
        return NativeOcoCallbackResponse(
            200, json.dumps(ReportedExchange(body.decode(), at).order).encode()
        )

    with monkeypatch.context() as patch:
        patch.setattr(Journal, "apply_native_oco_attempt_event", fail_outcome)
        with pytest.raises(JournalError, match="injected outcome rollback"):
            boundary.run_fake_once(
                "group-a",
                "lost-outcome",
                decision_at=at,
                started_at=at,
                callback=callback,
                clock=lambda: at,
            )
    assert calls == 1
    assert len(source.journal.read_native_oco_attempt_events()) == 1
    path, root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        recovered = NativeOcoAttempts(
            NativeOcoRequests(OcoJournal(reopened, owner.initial))
        )
        result = recovered.run_fake_once(
            "group-a",
            "lost-outcome",
            decision_at=at,
            started_at=at,
            callback=callback,
            clock=lambda: at,
        )
        assert calls == 1 and result["outcome"] is None
        assert len(reopened.read_native_oco_attempt_events()) == 1
        assert recovered.verify_rebuild()
        assert reopened.read_order_intent("native-failed-outcome") is None
    finally:
        reopened.close()
