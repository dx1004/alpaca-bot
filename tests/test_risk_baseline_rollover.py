"""Retained historical order evidence may prove a fresh, flat next-session E0."""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

import test_order_reconciliation as lifecycle
from alpaca_ross_bot import risk_baseline as baseline
from alpaca_ross_bot.journal import Journal, OrderHttpObservation
from alpaca_ross_bot.order_reconciliation import OrderReconciler, RestBatch
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.risk_account_inputs import SCHEMA, SOURCE
from test_risk_baseline import ACCOUNT, NOW, REF, freeze, request, setup

NEXT = NOW + timedelta(days=3)
FLAT = ACCOUNT | {"multiplier": "1", "buying_power": "500"}


def append_flat_capture(
    journal: Journal, when: datetime, *, equity: str = "500"
) -> tuple[int, int]:
    """Append same-day real-shaped calendar/account responses to an existing journal."""
    day = when.astimezone(baseline.NY).date()
    cal = journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind="CALENDAR_INPUT_CAPTURE",
        payload=json.dumps(
            {
                "account_ref": REF,
                "endpoint": "https://paper-api.alpaca.markets",
                "request": request(
                    f"/v2/calendar?start={day}&end={day}",
                    [{"date": str(day), "open": "09:30", "close": "16:00"}],
                    when,
                ),
            }
        ).encode(),
        system_first_seen_at=when,
        processed_at=when,
    )
    requests = [
        request(
            "/v2/account",
            FLAT | {"cash": equity, "equity": equity, "buying_power": equity},
            when,
        ),
        request("/v2/positions", [], when),
        request("/v2/orders?status=open&limit=500&nested=false", [], when),
    ]
    for index, row in enumerate(requests):
        row["started_at"] = (when - timedelta(seconds=6 - index * 2)).isoformat()
        row["completed_at"] = (when - timedelta(seconds=5 - index * 2)).isoformat()
    account = journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind="ACCOUNT_INPUT_CAPTURE",
        payload=json.dumps(
            {
                "schema": SCHEMA,
                "account_ref": REF,
                "endpoint": "https://paper-api.alpaca.markets",
                "requests": requests,
            }
        ).encode(),
        system_first_seen_at=when,
        processed_at=when,
    )
    return account.sequence, cal.sequence


def rollover_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stale: bool = False,
    unfilled: bool = False,
) -> tuple[Journal, int, int, OrderReconciler]:
    """Keep a prior session's completed round trip and freshly reconcile all CIDs."""
    journal, old_account, old_calendar = setup(tmp_path, account=FLAT)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    freeze(journal, old_account, old_calendar)
    monkeypatch.setattr(lifecycle, "NOW", NOW)
    reconciler = lifecycle._with_baseline(journal)
    lifecycle._reserve(journal)
    lifecycle._reserve(journal, "owned-sell", side="sell")
    lifecycle._connect(reconciler)
    buy = lifecycle._order("new", "0") if unfilled else lifecycle._order("filled", "10")
    sell = lifecycle._order(
        "canceled" if unfilled else "filled",
        "0" if unfilled else "10",
        cid="owned-sell",
        broker_id=lifecycle.OTHER_BROKER_ID,
        side="sell",
    )
    at = NEXT - timedelta(seconds=61 if stale else 1)
    result = reconciler.apply_rest_batch(
        RestBatch(
            account_ref=REF,
            started_at=at - timedelta(seconds=2),
            completed_at=at - timedelta(seconds=1),
            local_revision=cast(int, reconciler.view()["watermark"]),
            open_orders=(buy,) if unfilled else (),
            positions=(),
            lookups={
                lifecycle.CID: PaperHttpResponse(200, json.dumps(buy).encode()),
                "owned-sell": PaperHttpResponse(200, json.dumps(sell).encode()),
            },
            activities={
                lifecycle.BROKER_ID: () if unfilled else (lifecycle._activity("10"),),
                lifecycle.OTHER_BROKER_ID: ()
                if unfilled
                else (
                    lifecycle._activity(
                        "10",
                        "sell-activity",
                        broker_id=lifecycle.OTHER_BROKER_ID,
                        side="sell",
                    ),
                ),
            },
            complete=True,
            stable=True,
        ),
        lifecycle.POLICY,
        at=at,
    )
    assert result.status == "MATCHED"
    account, calendar = append_flat_capture(journal, NEXT)
    monkeypatch.setattr(baseline, "_now", lambda: NEXT)
    return journal, account, calendar, reconciler


def test_next_session_rollover_retains_history_and_source_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar, _ = rollover_sources(tmp_path, monkeypatch)
    try:
        old = baseline.read_baseline(journal, account_ref=REF, session_day=NOW.date())
        intents = journal.read_order_intent_ids()
        order_events = journal.read_order_events()
        result = freeze(journal, account, calendar, NEXT.date())
        assert result.equity == 500 and result.frozen_at == NEXT
        assert tuple(cid for cid, _ in result.rollover_intents) == intents
        assert result.rollover_source_refs and not result.runtime_authorized
        assert journal.read_order_intent_ids() == intents
        assert journal.read_order_events() == order_events
        assert len(journal.read_risk_baseline_events()) == 2
        assert (
            baseline.read_baseline(journal, account_ref=REF, session_day=NOW.date())
            == old
        )
        assert freeze(journal, account, calendar, NEXT.date()) == result
        for sequence, digest in result.rollover_source_refs:
            event = journal.read_event(sequence)
            assert event is not None and event[0].payload_ref == digest
        journal.close()
        root = tmp_path / "private"
        journal = Journal.open_private(root / "events.sqlite3", root)
        assert (
            baseline.read_baseline(journal, account_ref=REF, session_day=NEXT.date())
            == result
        )
    finally:
        journal.close()


def test_later_intents_cannot_expand_frozen_rollover_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar, _ = rollover_sources(tmp_path, monkeypatch)
    try:
        result = freeze(journal, account, calendar, NEXT.date())
        lifecycle._reserve(journal, "later", sent=False)
        assert (
            baseline.read_baseline(journal, account_ref=REF, session_day=NEXT.date())
            == result
        )
    finally:
        journal.close()


@pytest.mark.parametrize("after_freeze", [False, True])
def test_changed_local_http_inputs_require_fresh_proof_only_for_new_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_freeze: bool
) -> None:
    journal, account, calendar, _ = rollover_sources(tmp_path, monkeypatch)
    try:
        result = (
            freeze(journal, account, calendar, NEXT.date()) if after_freeze else None
        )
        journal.record_order_http_observation(
            lifecycle.CID,
            OrderHttpObservation(
                "GET",
                "HTTP_RESPONSE",
                NEXT,
                200,
                json.dumps(lifecycle._order("filled", "10")).encode(),
            ),
        )
        if after_freeze:
            assert (
                baseline.read_baseline(
                    journal, account_ref=REF, session_day=NEXT.date()
                )
                == result
            )
        else:
            with pytest.raises(ValueError, match="local inputs changed"):
                freeze(journal, account, calendar, NEXT.date())
            assert len(journal.read_risk_baseline_events()) == 1
    finally:
        journal.close()


@pytest.mark.parametrize("fault", ["stale", "unfilled", "gap", "unsent", "unknown"])
def test_unproven_retained_history_cannot_create_rollover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    journal, account, calendar, reconciler = rollover_sources(
        tmp_path, monkeypatch, stale=fault == "stale", unfilled=fault == "unfilled"
    )
    try:
        if fault == "gap":
            reconciler.stream_health("DISCONNECTED", NEXT, "session-1")
        elif fault in {"unsent", "unknown"}:
            lifecycle._reserve(journal, "unresolved", sent=fault == "unknown")
        with pytest.raises(ValueError, match="rollover"):
            freeze(journal, account, calendar, NEXT.date())
        assert len(journal.read_risk_baseline_events()) == 1
        assert journal.read_state(f"risk_baseline:{REF}:{NEXT.date()}") is None
    finally:
        journal.close()


def test_rollover_cannot_backfill_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar, _ = rollover_sources(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(baseline, "_now", lambda: NEXT + timedelta(minutes=30))
        with pytest.raises(ValueError):
            freeze(journal, account, calendar, NEXT.date())
        assert len(journal.read_risk_baseline_events()) == 1
    finally:
        journal.close()


@pytest.mark.parametrize("component", ["intent", "proof"])
def test_rollover_detects_retained_source_or_proof_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str
) -> None:
    journal, account, calendar, _ = rollover_sources(tmp_path, monkeypatch)
    try:
        freeze(journal, account, calendar, NEXT.date())
        with sqlite3.connect(tmp_path / "private" / "events.sqlite3") as db:
            if component == "intent":
                db.execute("DROP TRIGGER order_intents_no_update")
                db.execute(
                    "UPDATE order_intents SET intent_json=? WHERE client_order_id=?",
                    (json.dumps({"paper_account_ref": REF}), lifecycle.CID),
                )
            else:
                event, raw = journal.read_risk_baseline_events()[-1]
                payload = json.loads(raw)
                payload["rollover_proof"]["source_refs"][0][1] = "sha256:" + "0" * 64
                changed = json.dumps(payload, sort_keys=True).encode()
                db.execute("DROP TRIGGER events_no_update")
                db.execute(
                    "UPDATE events SET payload=?, payload_ref=? WHERE sequence=?",
                    (
                        changed,
                        "sha256:" + hashlib.sha256(changed).hexdigest(),
                        event.sequence,
                    ),
                )
                db.execute(
                    "UPDATE state SET value_json=? WHERE key=?",
                    (changed.decode(), f"risk_baseline:{REF}:{NEXT.date()}"),
                )
        with pytest.raises(ValueError):
            baseline.read_baseline(journal, account_ref=REF, session_day=NEXT.date())
    finally:
        journal.close()


def test_sequence_horizon_excludes_same_clock_candidate_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar = setup(tmp_path)
    try:
        monkeypatch.setattr(baseline, "_now", lambda: NOW)
        result = freeze(journal, account, calendar)
        candidate = journal.read_risk_baseline_events()[-1][0]
        assert result.receipt_sequence is not None
        for horizon in (0, account, candidate.sequence):
            assert (
                baseline.read_baseline(
                    journal,
                    account_ref=REF,
                    session_day=NOW.date(),
                    decision_at=NOW,
                    through_sequence=horizon,
                )
                is None
            )
        assert (
            baseline.read_baseline(
                journal,
                account_ref=REF,
                session_day=NOW.date(),
                decision_at=NOW,
                through_sequence=result.receipt_sequence,
            )
            == result
        )
        assert (
            baseline.read_baseline(
                journal,
                account_ref=REF,
                session_day=NOW.date(),
                through_sequence=candidate.sequence,
            )
            is None
        )
    finally:
        journal.close()


@pytest.mark.parametrize("horizon", [-1, True, 1.0, "1"])
def test_invalid_sequence_horizon_rejected(tmp_path: Path, horizon: object) -> None:
    journal, _, _ = setup(tmp_path)
    try:
        with pytest.raises(ValueError, match="sequence horizon"):
            baseline.read_baseline(
                journal,
                account_ref=REF,
                session_day=NOW.date(),
                through_sequence=cast(int, horizon),
            )
    finally:
        journal.close()


def test_later_same_clock_lifecycle_event_cannot_change_rollover_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar, reconciler = rollover_sources(tmp_path, monkeypatch)
    try:
        result = freeze(journal, account, calendar, NEXT.date())
        assert result.receipt_sequence is not None
        reconciler.stream_health("DISCONNECTED", NEXT, "session-1")
        assert (
            baseline.read_baseline(
                journal,
                account_ref=REF,
                session_day=NEXT.date(),
                decision_at=NEXT,
                through_sequence=result.receipt_sequence,
            )
            == result
        )
        assert (
            baseline.read_baseline(
                journal,
                account_ref=REF,
                session_day=NEXT.date(),
            )
            == result
        )
    finally:
        journal.close()


def test_horizon_does_not_hide_candidate_projection_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, account, calendar = setup(tmp_path)
    try:
        monkeypatch.setattr(baseline, "_now", lambda: NOW)
        freeze(journal, account, calendar)
        with sqlite3.connect(tmp_path / "private" / "events.sqlite3") as db:
            db.execute(
                "UPDATE state SET value_json=? WHERE key=?",
                ("{}", f"risk_baseline:{REF}:{NOW.date()}"),
            )
        with pytest.raises(ValueError, match="projection"):
            baseline.read_baseline(
                journal,
                account_ref=REF,
                session_day=NOW.date(),
                decision_at=NOW,
                through_sequence=0,
            )
    finally:
        journal.close()
