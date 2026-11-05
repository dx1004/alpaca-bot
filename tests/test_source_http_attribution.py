"""HTTP-family immutable claim namespace, retries, and corruption rejection."""

import base64
import copy
import json
from datetime import timedelta
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop_attribution import (
    SourceCloseAttributions,
    SourceHttpCloseAttributions,
    read_source_http_close_attributions_at,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import _json
from alpaca_ross_bot.oco_journal import OcoJournal
from test_source_close_http import source_http as source_http_fixture
from test_source_close_http_capture import http_capture as http_capture_fixture
from test_source_http_fills import partial_http, rest_rows

source_http = source_http_fixture
http_capture = http_capture_fixture


def observe(
    owner: SourceHttpCloseAttributions,
    case: Any,
    identity: str,
    *packets: dict[str, Any],
    at: Any = None,
) -> dict[str, Any]:
    fixture = case[3]
    return owner.observe(
        identity,
        fixture[7][0] if at is None else at,
        client_order_id=fixture[2]["client_order_id"],
        capture_sequences=tuple(p["capture_ref"]["sequence"] for p in packets),
    )


def test_http_claim_union_account_corroboration_reopen_and_historical_readonly(
    http_capture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = http_capture[3]
    journal, initial = fixture[0].journal, fixture[6][2].initial
    first = partial_http(http_capture)
    owner = SourceHttpCloseAttributions(journal, initial)
    original = observe(owner, http_capture, "partial", first)
    second = partial_http(http_capture, two=True)
    row = observe(owner, http_capture, "cumulative", first, second)
    state = owner.view()
    cid = fixture[2]["client_order_id"]
    assert (
        state["classification"]
        == "INJECTED_HTTP_ATTRIBUTION_INDEX_NOT_OPERATIONAL_OWNERSHIP"
    )
    assert state["cid_claims"][cid]["cumulative_fill_quantity"] == "1/2"
    assert len(state["activity_claims"]) == 2 and len(state["order_claims"]) == 1
    assert (
        state["cid_claims"][cid]["binding"]["original_portions"]
        == original["proof"]["original_portions"]
    )
    assert SourceCloseAttributions(journal, initial).view()["observations"] == []
    source = fixture[6][0]
    rows = json.loads(base64.b64decode(second["requests"][3]["body_base64"]))
    rest_rows(http_capture, rows)
    before = tuple(journal._db.iterdump())
    assert (
        observe(
            owner,
            http_capture,
            "partial",
            first,
            at=fixture[7][0] + timedelta(seconds=1),
        )
        == original
    )
    assert tuple(journal._db.iterdump()) == before
    other = Journal.open_private(journal._path, journal._runtime_root)
    try:
        assert SourceHttpCloseAttributions(other, initial).view() == state

        def deny(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("current constructor entered bounded HTTP index")

        with monkeypatch.context() as context:
            context.setattr(AccountRiskOwner, "__init__", deny)
            context.setattr(OcoJournal, "__init__", deny)
            context.setattr(SourceHttpCloseAttributions, "__init__", deny)
            assert (
                read_source_http_close_attributions_at(
                    other,
                    initial,
                    through_sequence=row["observation_ref"]["sequence"],
                    decision_at=fixture[7][0],
                )
                == state
            )
    finally:
        other.close()
    assert all(value is False for value in state["authority"].values())
    assert state["finality"] == "UNKNOWN" and not state["conflicts"]
    assert not source.journal.has_order_send_attempt(cid) and len(fixture[3].posts) == 1


def test_http_claim_changed_key_latches_and_projection_origin_horizon_tamper(
    http_capture: Any,
) -> None:
    fixture = http_capture[3]
    journal, initial = fixture[0].journal, fixture[6][2].initial
    first = partial_http(http_capture)
    owner = SourceHttpCloseAttributions(journal, initial)
    original = observe(owner, http_capture, "stable", first)
    second = partial_http(http_capture, two=True)
    conflict = observe(owner, http_capture, "stable", first, second)
    state = owner.view()
    assert state["latched"] and conflict["proof"] is None
    assert len(state["activity_claims"]) == 1
    assert observe(owner, http_capture, "stable", first, second) == conflict
    assert observe(owner, http_capture, "stable", first) == original
    with pytest.raises(JournalError, match="latch"):
        observe(owner, http_capture, "new-key", second)
    assert (
        read_source_http_close_attributions_at(
            journal,
            initial,
            through_sequence=original["observation_ref"]["sequence"],
            decision_at=fixture[7][0],
        )["conflicts"]
        == []
    )
    journal._db.execute("BEGIN")
    try:
        projected = copy.deepcopy(state)
        projected["latched"] = False
        journal._db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (_json(projected).decode(), owner.state_key),
        )
        with pytest.raises(JournalError, match="projection"):
            owner.view()
    finally:
        journal._db.rollback()
    event, raw = journal._read_private_events(owner._source, None)[0]
    journal._db.execute("BEGIN")
    try:
        journal._db.execute("DROP TRIGGER events_no_update")
        item = json.loads(raw)
        item["origin"] = "INJECTED_TEST_CALLBACK_NOT_BROKER_PROOF"
        changed = _json(item)
        journal._db.execute(
            "UPDATE events SET payload=? WHERE sequence=?", (changed, event.sequence)
        )
        with pytest.raises(JournalError):
            owner.view()
    finally:
        journal._db.rollback()
