"""A source attribution component cannot change the shared risk owner."""

import copy
import json
from datetime import timedelta
from fractions import Fraction
from typing import Any

import pytest

from alpaca_ross_bot._causal_risk_memo import causal_risk_verification_scope
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop_attempts import (
    SourceCloseAttempts,
    SourceCloseCallbackResponse,
)
from alpaca_ross_bot.crossed_stop_capture import SourceCloseCaptures
from alpaca_ross_bot.crossed_stop_fills import derive_injected_source_close_fills
from alpaca_ross_bot.crossed_stop_preparation import native_preparation_request
from alpaca_ross_bot.native_oco_requests import _json
from alpaca_ross_bot.risk_evidence_request import IncidentRiskRequest
from test_crossed_stop_capture import MemoryGets, capture, fill, order
from test_crossed_stop_capture import captured as captured
from test_crossed_stop_source_attempts import source_attempt as source_attempt
from test_native_account_risk_owner import native_risk_book as native_risk_book
from test_native_crossed_stop_preparation import native_ready as native_ready
from test_risk_ledger_audit import POLICY, REF


def test_attributed_fill_replays_inside_current_risk_without_releasing_reserves(
    captured: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captures, _, get, at, fixture = captured
    source, risk, owner, emergency, _, _, _ = fixture[4]
    original_risk = copy.deepcopy(risk.view())
    original_emergency = copy.deepcopy(emergency.view())
    get.order.update(
        status="partially_filled", filled_qty="0.25", filled_at=at.isoformat()
    )
    get.pages = [_json([fill(captured)]), b"[]"]
    packet = capture(captured)
    horizon = packet["capture_ref"]["sequence"]
    arguments: dict[str, Any] = dict(
        client_order_id=get.order["client_order_id"],
        capture_sequences=(horizon,),
        through_sequence=horizon,
        decision_at=at,
    )
    before = tuple(source.journal._db.iterdump())
    expected_component = derive_injected_source_close_fills(
        source.journal, owner.initial, **arguments
    )
    assert expected_component["component_attributed"], expected_component["issues"]
    assert Fraction(expected_component["cumulative_fill_quantity"]) == Fraction(1, 4)
    assert expected_component["finality"] == "UNKNOWN"
    assert all(value is False for value in expected_component["authority"].values())
    assert tuple(source.journal._db.iterdump()) == before
    assert risk.view() == original_risk
    assert emergency.view() == original_emergency

    # This is an ordinary genuine observation. No simple send marker or passing
    # source-owned account snapshot is manufactured to satisfy the test.
    selection = IncidentRiskRequest.from_dict(
        original_risk["last_snapshot"]["selection"]
    )
    risk.observe(
        "source-fill-component-boundary", selection, at + timedelta(microseconds=1)
    )
    expected_risk = copy.deepcopy(risk.view())
    assert expected_risk["last_snapshot"]["qualified"] is False
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" in expected_risk["issues"]
    assert (
        expected_risk["held"]["client_order_id"]
        == original_risk["held"]["client_order_id"]
    )
    assert expected_risk["completed"] == original_risk["completed"]
    for key in ("pending_open_loss_reserve", "additional_local_cash_hold"):
        assert Fraction(expected_risk[key]) >= Fraction(original_risk[key])
    assert Fraction(expected_risk["capacity"]) == 0
    assert not source.journal.has_order_send_attempt(arguments["client_order_id"])
    assert emergency.view() == original_emergency

    _verify_inside_current_risk(
        source, risk, owner, expected_component, arguments, monkeypatch
    )
    assert len(fixture[3]) == 1
    assert captures.view() == [packet]


def _verify_inside_current_risk(
    source: Any,
    risk: Any,
    owner: Any,
    expected_component: dict[str, Any],
    arguments: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_risk = copy.deepcopy(risk.view())
    target = source.journal.read_account_risk_events()[-1][0].sequence
    original_reduce = AccountRiskOwner._reduce
    original_init = AccountRiskOwner.__init__
    depth = [0]
    consumed = [0]

    def current(self: AccountRiskOwner, *args: Any, **kwargs: Any) -> None:
        depth[0] += 1
        try:
            assert depth[0] == 1, "attribution recursively constructed current risk"
            original_init(self, *args, **kwargs)
        finally:
            depth[0] -= 1

    def reduce(
        self: AccountRiskOwner,
        old: object,
        item: dict[str, Any],
        conflict: bool,
        sequence: int,
    ) -> dict[str, Any]:
        if sequence == target:
            assert self.journal._db.in_transaction
            assert (
                derive_injected_source_close_fills(
                    self.journal, owner.initial, **arguments
                )
                == expected_component
            )
            consumed[0] += 1
        return original_reduce(self, old, item, conflict, sequence)

    monkeypatch.setattr(AccountRiskOwner, "__init__", current)
    monkeypatch.setattr(AccountRiskOwner, "_reduce", reduce)
    before = tuple(source.journal._db.iterdump())
    source.journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            assert AccountRiskOwner(source.journal, REF, POLICY).view() == expected_risk
    finally:
        source.journal._db.rollback()
    assert consumed[0] > 0 and depth[0] == 0
    assert tuple(source.journal._db.iterdump()) == before


def test_native_source_attribution_has_no_recursive_risk_or_implicit_debit(
    native_ready: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, http_fixture, _ = book
    emergency.prepare_native_source(
        "native-attribution-boundary",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
        for_handoff=True,
    )
    state = emergency.bind_source_intent("native-attribution-boundary", at)
    binding = state["closes"]["native-attribution-boundary"]["source_intent_binding"]
    cid = json.loads(binding["intent"]["request_body"])["client_order_id"]
    risk.bind_exit("native-attribution-close-role", at, cid, "CLOSE")
    before_risk = copy.deepcopy(risk.view())
    before_model, before_close = owner.view(), emergency.view()
    saved: dict[str, Any] = {}

    def callback(body: bytes) -> SourceCloseCallbackResponse:
        saved.update(order(body, at))
        return SourceCloseCallbackResponse(201, _json(saved))

    attempts = SourceCloseAttempts(owner)
    row = attempts.invoke_once(
        http_fixture[1],
        client_order_id=cid,
        attempt_id="native-attribution-attempt",
        preparation=native_preparation_request(
            assets, statuses, 0, timedelta(seconds=1), timedelta(seconds=1)
        ),
        decision_at=at,
        started_at=at,
        max_response_bytes=10_000,
        clock=lambda: at,
        callback=callback,
    )
    gets = MemoryGets(saved)
    gets.order.update(
        status="partially_filled", filled_qty="0.25", filled_at=at.isoformat()
    )
    captures = SourceCloseCaptures(attempts)
    case = captures, row, gets, at, None
    gets.pages = [_json([fill(case)]), b"[]"]
    packet = capture(case)
    horizon = packet["capture_ref"]["sequence"]
    arguments: dict[str, Any] = dict(
        client_order_id=cid,
        capture_sequences=(horizon,),
        through_sequence=horizon,
        decision_at=at,
    )
    before = tuple(source.journal._db.iterdump())
    component = derive_injected_source_close_fills(
        source.journal, owner.initial, **arguments
    )
    assert component["original_portions"] == binding["portions"]
    assert component["finality"] == "UNKNOWN"
    assert all(value is False for value in component["authority"].values())
    assert tuple(source.journal._db.iterdump()) == before
    assert risk.view() == before_risk
    assert owner.view() == before_model
    assert emergency.view() == before_close
    # Any unresolved global native identity census stays an explicit veto; this
    # test never supplies an invented acceptance flag or registry to clear it.
    if not component["component_attributed"]:
        assert component["issues"]
    risk.observe_native(
        "native-source-fill-boundary",
        IncidentRiskRequest.from_dict(before_risk["last_snapshot"]["selection"]),
        at + timedelta(microseconds=1),
    )
    after = risk.view()
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" in after["issues"]
    assert after["held"]["client_order_id"] == before_risk["held"]["client_order_id"]
    assert after["completed"] == before_risk["completed"]
    for key in ("pending_open_loss_reserve", "additional_local_cash_hold"):
        assert Fraction(after[key]) >= Fraction(before_risk[key])
    assert after["capacity"] == "0"
    assert not source.journal.has_order_send_attempt(cid)
    _verify_inside_current_risk(source, risk, owner, component, arguments, monkeypatch)
