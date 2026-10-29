"""Native allocations and risk survive an injected-only residual attempt."""

import json
from datetime import timedelta
from typing import Any

from alpaca_ross_bot.crossed_stop_attempts import (
    SourceCloseAttempts,
    SourceCloseCallbackResponse,
)
from alpaca_ross_bot.crossed_stop_preparation import native_preparation_request
from test_native_account_risk_owner import native_risk_book as native_risk_book
from test_native_crossed_stop_preparation import native_ready as native_ready


def test_native_source_attempt_preserves_group_and_original_risk(
    native_ready: Any,
) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, http_fixture, _ = book
    harness = http_fixture[1]
    request = native_preparation_request(
        assets, statuses, 0, timedelta(seconds=1), timedelta(seconds=1)
    )
    prepared = emergency.prepare_native_source(
        "native-close",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
        for_handoff=True,
    )
    original = prepared["closes"]["native-close"]
    state = emergency.bind_source_intent("native-close", at)
    cid = json.loads(
        state["closes"]["native-close"]["source_intent_binding"]["intent"][
            "request_body"
        ]
    )["client_order_id"]
    risk.bind_exit("native-source-close-role", at, cid, "CLOSE")
    before_risk, before_oco, before_close = risk.view(), owner.view(), emergency.view()
    calls: list[bytes] = []

    def callback(body: bytes) -> SourceCloseCallbackResponse:
        calls.append(body)
        return SourceCloseCallbackResponse(201, b'{"reported":"native-residual"}')

    attempts = SourceCloseAttempts(owner)
    arguments = dict(
        client_order_id=cid,
        attempt_id="native-attempt",
        preparation=request,
        decision_at=at,
        started_at=at,
        max_response_bytes=1000,
        clock=lambda: at,
        callback=callback,
    )
    row = attempts.invoke_once(harness, **arguments)
    assert len(calls) == 1
    assert json.loads(calls[0])["qty"] == "1"
    assert original["quantity"] == "1"
    assert attempts.invoke_once(harness, **arguments) == row and len(calls) == 1
    qualification = row["precall"]["qualification"]
    assert qualification["native_ownership"]["unallocated_quantity"] == "1"
    assert qualification["metadata_bridge"]["snapshot_refreshed"] is False
    assert all(value is False for value in row["precall"]["authority"].values())
    assert not source.journal.has_order_send_attempt(cid)
    assert risk.view() == before_risk
    assert owner.view() == before_oco
    assert emergency.view() == before_close
    assert (
        before_risk["held"]["source_close_bindings"][cid]["portions"]
        == original["portions"]
    )
    assert before_close["closes"]["native-close"]["filled"] == "0"
    assert attempts.verify_rebuild()
