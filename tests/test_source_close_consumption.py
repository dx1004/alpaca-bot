"""Canonical source facts consumption, with all execution/release gates retained."""

import base64
import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop import CrossedStopJournal
from alpaca_ross_bot.crossed_stop_attribution import SourceHttpCloseAttributions
from alpaca_ross_bot.crossed_stop_consumption import read_source_close_consumptions_at
from alpaca_ross_bot.crossed_stop_preparation import (
    preparation_request,
    source_close_handoff_lineage,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_risk_registry import POLICY as NATIVE_POLICY
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from alpaca_ross_bot.source_close_http import SourceCloseHttp
from alpaca_ross_bot.source_close_http_capture import SourceCloseHttpCaptures
from test_risk_ledger_audit import ACCOUNT
from test_source_account_risk_owner import account_book, report
from test_source_account_risk_owner import http_capture as http_capture_fixture
from test_source_account_risk_owner import native_ready as native_ready_fixture
from test_source_account_risk_owner import native_risk_book as native_risk_book_fixture
from test_source_account_risk_owner import source_http as source_http_fixture
from test_source_close_http import ORDER_ID, Exchange, api, submit
from test_source_close_http_capture import MemoryHttpGets, capture

source_http = source_http_fixture
http_capture = http_capture_fixture
native_risk_book = native_risk_book_fixture
native_ready = native_ready_fixture


def test_fractional_partial_then_terminal_canonical_consumption(
    http_capture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = http_capture
    captures, attempt, gets, fixture = case
    source, risk, owner, emergency, at, assets, statuses = fixture[6]
    before = emergency.view()
    original_risk = risk.view()
    original_oco = owner.view()
    cid = gets.order["client_order_id"]
    initial = owner.initial
    # An original handoff and a confirmed HTTP outcome alone are insufficient.
    seq = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError):
        emergency.consume_source_fills("no-account", "residual", at)
    assert source.journal.read_evidence_sequence() == seq
    packet, rows = report(case, "0.5")
    index = SourceHttpCloseAttributions(source.journal, initial)
    index.observe(
        "partial",
        at,
        client_order_id=cid,
        capture_sequences=(packet["capture_ref"]["sequence"],),
    )
    cash = str(Decimal("480") + Decimal("0.5") * Decimal("9.49"))
    selection = account_book(case, at, cash=cash, position="1.5", source_fills=rows)
    risk.observe_source("partial-account", selection, at)
    partial_risk = risk.view()
    assert partial_risk["last_snapshot"]["qualified"]
    assert "quantity_incident" in partial_risk["held"]
    assert "UNSUPPORTED_FRACTIONAL_RISK_QUANTITY" in partial_risk["issues"]
    seq = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="unqualified|stale"):
        emergency.consume_source_fills(
            "stale-account", "residual", at + timedelta(seconds=31)
        )
    assert source.journal.read_evidence_sequence() == seq
    get_calls = list(gets.calls)
    first = emergency.consume_source_fills("partial-consume", "residual", at)
    first_horizon = source.journal.read_evidence_sequence()
    partial = emergency.view()
    close = partial["closes"]["residual"]
    assert close["filled"] == "1/2" and close["executions"] == {rows[0]["id"]: "1/2"}
    assert (
        close["attempt"]
        == attempt["precall"]["command"]["attempt_id"]
        == first["attempt_id"]
    )
    assert close["portions"] == before["closes"]["residual"]["portions"]
    assert (
        first["retained_quantity_incident"] == partial_risk["held"]["quantity_incident"]
    )
    assert not any(first["authority"].values())
    assert first["source_horizon"] == first_horizon - 1
    assert risk.view() == partial_risk and owner.view() == original_oco
    assert gets.calls == get_calls
    assert source.journal.read_order_send_started_at(cid) is None
    assert (
        emergency.consume_source_fills(
            "partial-consume", "residual", at + timedelta(seconds=1)
        )
        == first
    )
    assert source.journal.read_evidence_sequence() == first_horizon
    # Full-basket reservations stay assigned even while exact partial facts accrue.
    with pytest.raises(JournalError):
        emergency.prepare_source(
            "cannot-reuse",
            at,
            asset_sequence=assets,
            status_sequence=statuses,
            status_record_index=0,
            max_asset_age=timedelta(seconds=1),
            max_status_age=timedelta(seconds=1),
        )
    later = at + timedelta(seconds=1)
    fixture[7][0] = later
    gets.order.update(status="filled", filled_qty="2", updated_at=later.isoformat())
    terminal_rows = rows + [
        rows[0]
        | {
            "id": "source-consume-final",
            "qty": "1.5",
            "cum_qty": "2",
            "leaves_qty": "0",
            "transaction_time": later.isoformat(),
        }
    ]
    gets.pages = [json.dumps(terminal_rows).encode(), b"[]"]
    terminal_packet = capture(case)
    terminal_selection = account_book(
        case, later, cash="498.98", position="0", source_fills=terminal_rows
    )
    risk.observe("unindexed-account", terminal_selection, later)
    assert not risk.view()["last_snapshot"]["qualified"]
    with pytest.raises(JournalError):
        emergency.consume_source_fills("cannot-skip-newest-bad", "residual", later)
    assert emergency.consume_source_fills("partial-consume", "residual", later) == first
    index.observe(
        "terminal",
        later,
        client_order_id=cid,
        capture_sequences=(
            packet["capture_ref"]["sequence"],
            terminal_packet["capture_ref"]["sequence"],
        ),
    )
    risk.observe("terminal-account", terminal_selection, later)
    terminal_risk = risk.view()
    assert terminal_risk["last_snapshot"]["qualified"]
    assert (
        terminal_risk["held"]["quantity_incident"]
        == partial_risk["held"]["quantity_incident"]
    )
    final = emergency.consume_source_fills("terminal-consume", "residual", later)
    terminal_horizon = source.journal.read_evidence_sequence()
    terminal = emergency.view()
    close = terminal["closes"]["residual"]
    assert close["filled"] == "2"
    assert close["executions"] == {rows[0]["id"]: "1/2", terminal_rows[1]["id"]: "3/2"}
    assert close["portions"] == before["closes"]["residual"]["portions"]
    assert close["source_consumptions"]["partial-consume"] == first
    assert final["attempt_id"] == first["attempt_id"]
    assert terminal["latched"] and not terminal["conflicts"]
    assert risk.view() == terminal_risk and owner.view() == original_oco
    assert (
        terminal_risk["pending_open_loss_reserve"]
        == original_risk["pending_open_loss_reserve"]
    )
    assert (
        terminal_risk["additional_local_cash_hold"]
        == original_risk["additional_local_cash_hold"]
    )
    assert terminal_risk["capacity"] == "0" and terminal_risk["held"] is not None
    assert (
        emergency.consume_source_fills(
            "terminal-consume", "residual", later + timedelta(seconds=1)
        )
        == final
    )
    assert source.journal.read_evidence_sequence() == terminal_horizon
    with pytest.raises(JournalError, match="clock regressed"):
        emergency.consume_source_fills("terminal-consume", "residual", at)
    assert source.journal.read_evidence_sequence() == terminal_horizon
    lineage = source_close_handoff_lineage(
        source.journal, cid, through_sequence=terminal_horizon, at=later
    )
    assert lineage is not None and lineage["handoff_ref"] == first["handoff_ref"]
    # New events cannot disguise an earlier horizon to hide newer evidence.
    event, raw = source.journal.read_crossed_stop_events()[-1]
    shrunk = json.loads(raw)
    shrunk["horizon"] -= 1
    with pytest.raises(JournalError, match="immediately preceding"):
        emergency._reduce(partial, shrunk, False, sequence=event.sequence)
    # Storage failure rolls back the new receipt and canonical close projection.
    original_append = source.journal._append_locked

    def interrupted(event: Any, payload: bytes, updates: Any = None) -> Any:
        if event.event_kind == "SOURCE_CONSUME":
            raise JournalError("synthetic append interruption")
        return original_append(event, payload, updates)

    with monkeypatch.context() as context:
        context.setattr(source.journal, "_append_locked", interrupted)
        with pytest.raises(JournalError, match="append interruption"):
            emergency.consume_source_fills("rolled-back", "residual", later)
    assert source.journal.read_evidence_sequence() == terminal_horizon
    assert emergency.view() == terminal
    # A newer failed account is never replaced with an older qualified snapshot.
    bad_rows = terminal_rows + [terminal_rows[0] | {"id": "unexpected-source-activity"}]
    bad = account_book(case, later, cash="498.98", position="0", source_fills=bad_rows)
    risk.observe("bad-latest-account", bad, later)
    assert not risk.view()["last_snapshot"]["qualified"]
    with pytest.raises(JournalError):
        emergency.consume_source_fills("bad-account-consume", "residual", later)
    assert emergency.consume_source_fills("partial-consume", "residual", later) == first

    def no_constructor(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("current owner constructor forbidden")

    with monkeypatch.context() as context:
        context.setattr(AccountRiskOwner, "__init__", no_constructor)
        context.setattr(OcoJournal, "__init__", no_constructor)
        context.setattr(CrossedStopJournal, "__init__", no_constructor)
        assert (
            read_source_close_consumptions_at(
                source.journal, initial, through_sequence=first_horizon, decision_at=at
            )
            == partial
        )
        assert (
            read_source_close_consumptions_at(
                source.journal,
                initial,
                through_sequence=terminal_horizon,
                decision_at=later,
            )
            == terminal
        )
        reopened = Journal.open_private(
            source.journal._path, source.journal._runtime_root
        )
        try:
            assert (
                read_source_close_consumptions_at(
                    reopened,
                    initial,
                    through_sequence=terminal_horizon,
                    decision_at=later,
                )
                == terminal
            )
        finally:
            reopened.close()
    assert emergency.view() == terminal
    # Explicit isolated cache corruption is never an attribution producer.
    current_horizon = source.journal.read_evidence_sequence()
    source.journal._db.execute("SAVEPOINT consumption_cache_fault")
    try:
        poisoned = json.loads(json.dumps(terminal))
        poisoned["closes"]["residual"]["filled"] = "999"
        source.journal._db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (json.dumps(poisoned), emergency.state_key),
        )
        with pytest.raises(JournalError, match="projection differs"):
            read_source_close_consumptions_at(
                source.journal,
                initial,
                through_sequence=current_horizon,
                decision_at=later,
            )
        assert (
            read_source_close_consumptions_at(
                source.journal, initial, through_sequence=first_horizon, decision_at=at
            )
            == partial
        )
    finally:
        source.journal._db.execute("ROLLBACK TO consumption_cache_fault")
        source.journal._db.execute("RELEASE consumption_cache_fault")
    assert emergency.view() == terminal
    # Changed command identity is durable; the original successful receipt survives.
    with pytest.raises(JournalError, match="conflict retained"):
        emergency.consume_source_fills("partial-consume", "another-close", later)
    conflicted = emergency.view()
    assert conflicted["conflicts"] and conflicted["closes"]["residual"]["filled"] == "2"
    assert emergency.consume_source_fills("partial-consume", "residual", later) == first
    assert (
        read_source_close_consumptions_at(
            source.journal,
            initial,
            through_sequence=terminal_horizon,
            decision_at=later,
        )
        == terminal
    )


def test_native_source_consumption_keeps_group_and_risk_owners(
    native_ready: Any,
) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, native_fixture, reported = book
    emergency.prepare_native_source(
        "native-consume",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
        for_handoff=True,
    )
    handed = emergency.bind_source_intent("native-consume", at)
    cid = handed["closes"]["native-consume"]["request"]["client_order_id"]
    risk.bind_exit("native-consume-role", at, cid, "CLOSE")
    now = [at]
    exchange = Exchange(ACCOUNT, now)
    transport = api(exchange)
    fixture = (
        SourceCloseHttp(owner),
        native_fixture[1],
        dict(
            client_order_id=cid,
            attempt_id="native-consume-attempt",
            preparation=preparation_request(
                assets, statuses, 0, timedelta(seconds=1), timedelta(seconds=1)
            )
            | {"ownership_policy": NATIVE_POLICY},
            decision_at=at,
            started_at=at,
            clock=lambda: now[0],
        ),
        exchange,
        transport,
        native_fixture[4],
        (source, risk, owner, emergency, at, assets, statuses),
        now,
    )
    attempt = submit(fixture)
    gets = MemoryHttpGets(
        json.loads(base64.b64decode(attempt["outcome"]["body_base64"]))
    )
    transport._exchange = gets
    case = SourceCloseHttpCaptures(fixture[0]), attempt, gets, fixture
    packet, rows = report(case, "1", terminal=True)
    index = SourceHttpCloseAttributions(source.journal, owner.initial)
    index.observe(
        "native-source-claim",
        at,
        client_order_id=cid,
        capture_sequences=(packet["capture_ref"]["sequence"],),
    )
    selection = account_book(
        case,
        at,
        cash="491.12",
        position="1",
        source_fills=rows,
        native_open=[reported.order | {"legs": None}, reported.order["legs"][0]],
        native_fills=[row for values in reported.fills.values() for row in values],
    )
    risk.observe_source_native("native-source-account", selection, at)
    before_risk, before_oco = risk.view(), owner.view()
    assert before_risk["last_snapshot"]["qualified"]
    receipt = emergency.consume_source_fills("native-facts", "native-consume", at)
    state = emergency.view()
    assert state["closes"]["native-consume"]["filled"] == "1"
    assert (
        state["closes"]["native-consume"]["portions"]
        == handed["closes"]["native-consume"]["portions"]
    )
    assert (
        receipt["broker_order_id"] == gets.order["id"]
        and receipt["attempt_id"] == "native-consume-attempt"
    )
    assert not any(receipt["authority"].values()) and state["latched"]
    assert owner.view() == before_oco and risk.view() == before_risk
    assert before_risk["held"] is not None and before_risk["capacity"] == "0"
    assert owner.view().model.unallocated_quantity == 1
    assert source.journal.read_order_send_started_at(cid) is None


@pytest.mark.parametrize("outcome", ["unknown", "rejected", "missing"])
def test_unconfirmed_source_http_cannot_be_consumed(
    source_http: Any, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    http, _, _, exchange, transport, _, ready, _ = source_http
    source, risk, owner, emergency, at, _, _ = ready
    if outcome == "unknown":
        exchange.response = TimeoutError("synthetic unknown outcome")
    elif outcome == "rejected":
        exchange.response = PaperHttpResponse(422, b'{"code":422}')
    if outcome == "missing":
        original = http._append

        def interrupt(item: dict[str, Any], clock: Any, prepare: Any) -> Any:
            if item.get("kind") == "OUTCOME":
                raise JournalError("synthetic missing outcome")
            return original(item, clock, prepare)

        with monkeypatch.context() as context:
            context.setattr(http, "_append", interrupt)
            with pytest.raises(JournalError, match="missing outcome"):
                submit(source_http)
        attempt = http.view()[0]
    else:
        attempt = submit(source_http)
    raw = json.loads(attempt["precall"]["request_body"]) | {
        "id": ORDER_ID,
        "status": "new",
        "filled_qty": "0",
        "asset_class": "us_equity",
        "order_class": "simple",
        "extended_hours": False,
        "created_at": at.isoformat(),
        "submitted_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }
    gets = MemoryHttpGets(raw)
    transport._exchange = gets
    case = SourceCloseHttpCaptures(http), attempt, gets, source_http
    packet, rows = report(case, "2", terminal=True)
    index = SourceHttpCloseAttributions(source.journal, owner.initial)
    with pytest.raises(JournalError):
        index.observe(
            "unconfirmed",
            at,
            client_order_id=gets.order["client_order_id"],
            capture_sequences=(packet["capture_ref"]["sequence"],),
        )
    selection = account_book(case, at, cash="498.98", position="0", source_fills=rows)
    risk.observe_source("unconfirmed-account", selection, at)
    seq, before = source.journal.read_evidence_sequence(), emergency.view()
    with pytest.raises(JournalError):
        emergency.consume_source_fills("unconfirmed-consume", "residual", at)
    assert source.journal.read_evidence_sequence() == seq and emergency.view() == before
    assert before["closes"]["residual"]["filled"] == "0"
    assert before["closes"]["residual"]["attempt"] is None
    assert len(exchange.posts) == 1 and risk.view()["held"] is not None
