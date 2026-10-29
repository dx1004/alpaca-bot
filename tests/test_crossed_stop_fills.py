"""Injected source-CLOSE attribution only; no broker or accounting mutation."""

import base64
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any, cast

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop_attempts import (
    SourceCloseCallbackResponse,
    _source_attempt_reader,
)
from alpaca_ross_bot.crossed_stop_capture import (
    KIND,
    SOURCE,
    SourceCloseCaptures,
    SourceCloseGetResponse,
)
from alpaca_ross_bot.crossed_stop_fills import derive_injected_source_close_fills
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import _json
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from alpaca_ross_bot.order_reconciliation import RestBatch
from test_account_risk_owner import RECON_POLICY
from test_crossed_stop_capture import MemoryGets, capture, fill, order
from test_crossed_stop_capture import captured as captured_fixture
from test_crossed_stop_source_attempts import invoke
from test_crossed_stop_source_attempts import source_attempt as source_attempt_fixture

captured = captured_fixture
source_attempt = source_attempt_fixture


def partial(
    case: Any, *, two: bool = False, status: str = "partially_filled", **extra: Any
) -> dict[str, Any]:
    get, at = case[2], case[3]
    quantity = "0.5" if two else "0.25"
    get.order.update(status=status, filled_qty=quantity, filled_at=at.isoformat())
    if status == "canceled":
        get.order["canceled_at"] = at.isoformat()
    rows = [fill(case, optional_provider_field={"numeric": 1.25}, **extra)]
    if two:
        rows.append(
            fill(
                case,
                id="fill-page-2",
                cum_qty="0.5",
                leaves_qty=str(Decimal(get.order["qty"]) - Decimal("0.5")),
            )
        )
    get.pages = [_json(rows), b"[]"]
    return capture(case)


def derive(case: Any, *packets: dict[str, Any], **changes: Any) -> dict[str, Any]:
    attempts = case[4][0]
    owner = case[4][4][2]
    return derive_injected_source_close_fills(
        attempts.journal,
        owner.initial,
        **(
            dict(
                client_order_id=case[1]["precall"]["client_order_id"],
                capture_sequences=tuple(
                    packet["capture_ref"]["sequence"] for packet in packets
                ),
                through_sequence=attempts.journal.read_evidence_sequence(),
                decision_at=case[3],
            )
            | changes
        ),
    )


def test_fractional_cumulative_replay_canceled_partial_reopen_and_terminal_guard(
    captured: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = partial(captured)
    second = partial(captured, two=True)
    replay = partial(captured, two=True)
    canceled = partial(captured, two=True, status="canceled")
    attempts, _, _, posts, ready = captured[4]
    source, risk, owner, emergency, at, _, _ = ready
    before = tuple(source.journal._db.iterdump())

    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("current constructor entered pure attribution")

    with monkeypatch.context() as context:
        context.setattr(AccountRiskOwner, "__init__", deny)
        context.setattr(OcoJournal, "__init__", deny)
        reader = _source_attempt_reader(source.journal, owner.initial)
        assert reader.read_verified_at(source.journal.read_evidence_sequence(), at)
        for operation in (
            reader.view,
            reader.verify_rebuild,
            lambda: reader.invoke_once(captured[4][1], **captured[4][2]),
            lambda: reader._append(
                dict(captured[1]["precall"]), lambda: at, lambda: None
            ),
        ):
            with pytest.raises(JournalError):
                operation()
        result = derive(captured, first, second, replay, canceled)
        assert result["component_attributed"] is True
        assert result["cumulative_fill_quantity"] == "1/2"
        assert result["remaining_quantity"] == "3/2"
        assert len(result["executions"]) == 2
        assert len(result["executions"][0]["raw_refs"]) == 4
        assert (
            result["original_portions"]
            == captured[1]["precall"]["qualification"]["handoff"]["portions"]
        )
        assert result["finality"] == "UNKNOWN"
        assert all(value is False for value in result["authority"].values())
        other = Journal.open_private(source.journal._path, source.journal._runtime_root)
        try:
            assert (
                derive_injected_source_close_fills(
                    other,
                    owner.initial,
                    client_order_id=result["client_order_id"],
                    capture_sequences=tuple(
                        p["capture_ref"]["sequence"]
                        for p in (first, second, replay, canceled)
                    ),
                    through_sequence=other.read_evidence_sequence(),
                    decision_at=at,
                )
                == result
            )
        finally:
            other.close()
    assert tuple(source.journal._db.iterdump()) == before
    assert len(posts) == 1
    assert not source.journal.has_order_send_attempt(result["client_order_id"])
    assert (
        risk.view()["held"] is not None
        and emergency.view()["closes"]["residual"]["filled"] == "0"
    )
    # A later injected packet can be locally arithmetic-consistent while
    # contradicting the prior terminal lifecycle observation.
    captured[2].order["canceled_at"] = None
    later = partial(captured, two=True, status="new")
    with pytest.raises(JournalError, match="history regressed or changed"):
        derive(captured, first, second, replay, canceled, later)


def test_full_raw_optional_field_change_is_not_hidden_by_normalized_facts(
    captured: Any,
) -> None:
    first = partial(captured)
    get = captured[2]
    changed = fill(captured, optional_provider_field={"numeric": 2.5})
    get.pages = [_json([changed]), b"[]"]
    second = capture(captured)
    assert (
        first["assessment"]["matching_fills"] == second["assessment"]["matching_fills"]
    )
    assert base64.b64decode(first["requests"][3]["body_base64"]) != base64.b64decode(
        second["requests"][3]["body_base64"]
    )
    for selected in ((first,), (second,), (first, second)):
        with pytest.raises(JournalError, match="contents changed"):
            derive(captured, *selected)


def test_cumulative_disappearance_and_omitted_later_capture_fail_closed(
    captured: Any,
) -> None:
    first = partial(captured)
    second = partial(captured, two=True)
    with pytest.raises(JournalError, match="omit newer"):
        derive(captured, first)
    third = partial(captured)
    for selected in ((third,), (first, second, third)):
        with pytest.raises(JournalError, match="history regressed or changed"):
            derive(captured, *selected)


def test_capture_prefix_and_origin_integrity(captured: Any) -> None:
    packet = partial(captured)
    expected = derive(captured, packet)
    for changes in (
        {"capture_sequences": (True,)},
        {"capture_sequences": (packet["capture_ref"]["sequence"],) * 2},
        {"through_sequence": packet["capture_ref"]["sequence"] - 1},
        {"decision_at": captured[3] - timedelta(microseconds=1)},
    ):
        with pytest.raises(JournalError):
            derive(captured, packet, **changes)
    forged = {key: value for key, value in packet.items() if key != "capture_ref"}
    forged["origin"] = "INJECTED_EXCHANGE"
    journal = captured[4][0].journal
    journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=_json(forged),
        system_first_seen_at=captured[3],
        processed_at=captured[3],
    )
    with pytest.raises(JournalError, match="capture provenance"):
        derive(captured, packet)
    assert (
        derive(captured, packet, through_sequence=packet["capture_ref"]["sequence"])
        == expected
    )


def test_missing_outcome_requires_new_confirmed_capture_and_preserves_old_unknown(
    source_attempt: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts, _, _, posts, ready = source_attempt
    at = ready[4]
    pending = []
    append = attempts._append
    snapshot: dict[str, Any] = {}

    def callback(body: bytes) -> SourceCloseCallbackResponse:
        posts.append(body)
        snapshot.update(order(body, at))
        return SourceCloseCallbackResponse(201, _json(snapshot))

    def defer(item: Any, clock: Any, prepare: Any) -> Any:
        if item["kind"] == "OUTCOME":
            pending.append((item, prepare))
            raise OSError("synthetic deferred storage")
        return append(item, clock, prepare)

    monkeypatch.setattr(attempts, "_append", defer)
    with pytest.raises(OSError):
        invoke(source_attempt, callback=callback)
    monkeypatch.setattr(attempts, "_append", append)
    row = attempts.view()[0]
    case = SourceCloseCaptures(attempts), row, MemoryGets(snapshot), at, source_attempt
    unknown = partial(case)
    with pytest.raises(JournalError, match="confirmed injected"):
        derive(case, unknown)
    item, prepare = pending[0]
    later = at + timedelta(seconds=1)
    append(item, lambda: later, prepare)
    case = case[0], row, case[2], later, source_attempt
    with pytest.raises(JournalError, match="POST acknowledgement"):
        derive(case, unknown)
    case[2].pages = [base64.b64decode(unknown["requests"][3]["body_base64"]), b"[]"]
    confirmed = capture(case)
    result = derive(case, confirmed)
    assert result["component_attributed"] is True
    assert result["cumulative_fill_quantity"] == "1/4"
    assert unknown["assessment"]["association"] == "GET_CANDIDATE_ONLY"
    assert case[0].read_verified_at(unknown["capture_ref"]["sequence"], at) == [unknown]
    assert len(posts) == 1


def test_native_overlap_writer_veto_preserves_original_source_basket(
    captured: Any,
) -> None:
    packet = partial(captured)
    owner = captured[4][4][2]
    identity = next(
        iter(captured[1]["precall"]["qualification"]["handoff"]["portions"])
    )
    before = tuple(owner.journal._db.iterdump())
    with pytest.raises(JournalError, match="incident blocks new OCO allocations"):
        owner.allocate_group(
            "later-native-overlap", (LotPortion(identity, Fraction(1)),), captured[3]
        )
    assert tuple(owner.journal._db.iterdump()) == before
    result = derive(captured, packet)
    assert result["component_attributed"] is True
    assert (
        result["original_portions"]
        == captured[1]["precall"]["qualification"]["handoff"]["portions"]
    )
    assert result["finality"] == "UNKNOWN"


def test_source_post_and_get_cannot_alias_prior_buy_entry_uuid(
    source_attempt: Any,
) -> None:
    attempts, _, _, posts, ready = source_attempt
    owner, at = ready[2], ready[4]
    view = owner.read_verified_at(attempts.journal.read_evidence_sequence(), at)
    assert view is not None and view.entry_binding is not None
    entry_id = view.entry_binding["entry"]["broker_order_id"]
    saved: dict[str, Any] = {}

    def callback(body: bytes) -> SourceCloseCallbackResponse:
        posts.append(body)
        saved.update(order(body, at) | {"id": entry_id})
        return SourceCloseCallbackResponse(201, _json(saved))

    row = invoke(source_attempt, callback=callback)
    memory = MemoryGets(saved)

    def get(path: str) -> SourceCloseGetResponse:
        if path.startswith("/v2/orders/"):
            assert path == f"/v2/orders/{entry_id}?nested=true"
            return SourceCloseGetResponse(200, _json(saved))
        if path.startswith("/v2/account/activities?"):
            return SourceCloseGetResponse(200, b"[]")
        return memory(path)

    case = SourceCloseCaptures(attempts), row, memory, at, source_attempt
    packet = capture(case, get=get)
    assert (
        packet["assessment"]["association"] == "INJECTED_POST_GET_IDENTITY_CONSISTENT"
    )
    result = derive(case, packet)
    assert result["component_attributed"] is False
    assert "SOURCE_ORDER_UUID_REUSES_ENTRY" in result["issues"]
    assert len(posts) == 1


def test_moving_clock_post_get_and_cross_capture_identity_time_drift(
    captured: Any,
) -> None:
    at = captured[3]
    now = [at]
    memory = cast(MemoryGets, captured[2])

    def get(path: str) -> SourceCloseGetResponse:
        now[0] += timedelta(seconds=1)
        return memory(path)

    first = capture(captured, get=get, clock=lambda: now[0])
    assert derive(captured, first, decision_at=now[0])["component_attributed"] is True
    changed = (at + timedelta(microseconds=1)).isoformat()
    memory.order.update(created_at=changed, submitted_at=changed, updated_at=changed)
    second = capture(captured, get=get, clock=lambda: now[0])
    assert (
        second["assessment"]["association"] == "INJECTED_POST_GET_IDENTITY_CONSISTENT"
    )
    assert second["assessment"]["fill_component_consistent"] is True
    assert (
        first["assessment"]["order_snapshot"]["submitted_at_ns"]
        != second["assessment"]["order_snapshot"]["submitted_at_ns"]
    )
    for selected in ((second,), (first, second)):
        with pytest.raises(JournalError, match="timestamps differ from original POST"):
            derive(captured, *selected, decision_at=now[0])


def other_simple(
    case: Any, *, reuse_uuid: bool = False, execution_id: str | None = None
) -> tuple[str, dict[str, Any]]:
    source, at = case[4][4][0], case[3]
    cid = "other-canonical-simple-cid"
    request = {
        "client_order_id": cid,
        "symbol": "MSFT",
        "qty": "1",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    source.journal.reserve_order_intent(
        cid,
        {
            "paper_endpoint": "https://paper-api.alpaca.markets",
            "paper_account_ref": source.journal.read_paper_account_ref(),
            "purpose": "ENGINEERING_TEST",
            "request_body": _json(request).decode(),
        },
    )
    reported = source.order(
        cid,
        at,
        "partially_filled" if execution_id else "new",
        Decimal("0.25") if execution_id else 0,
    )
    if reuse_uuid:
        reported["id"] = case[2].order["id"]
    data = {
        "event": "partial_fill" if execution_id else "new",
        "timestamp": at.isoformat(),
        "order": reported,
    }
    if execution_id:
        data["execution_id"] = execution_id
    source.reconciler.ingest_frame(
        _json({"stream": "trade_updates", "data": data}), at, "source-fixture"
    )
    assert not source.journal.has_order_send_attempt(cid)
    return cid, reported


def test_other_canonical_simple_cid_claiming_source_uuid_is_vetoed(
    captured: Any,
) -> None:
    packet = partial(captured)
    cid, reported = other_simple(captured, reuse_uuid=True)
    lifecycle = captured[4][4][0].reconciler.view()["orders"]
    assert lifecycle[cid]["broker_order_id"] == reported["id"]
    result = derive(captured, packet)
    assert result["component_attributed"] is False
    assert "SIMPLE_ORDER_UUID_REUSE" in result["issues"]
    source, at = captured[4][4][0], captured[3]
    source_cid = captured[1]["precall"]["client_order_id"]
    contradictory = source.order(source_cid, at, "new", 0)
    contradictory["id"] = "33333333-3333-4333-8333-333333333333"
    source.reconciler.ingest_frame(
        _json(
            {
                "stream": "trade_updates",
                "data": {
                    "event": "new",
                    "timestamp": at.isoformat(),
                    "order": contradictory,
                },
            }
        ),
        at,
        "source-fixture",
    )
    conflict = derive(captured, packet)
    assert "SOURCE_LIFECYCLE_UUID_CONFLICT" in conflict["issues"]
    contradictory["id"] = "44444444-4444-4444-8444-444444444444"
    source.reconciler.ingest_frame(
        _json(
            {
                "stream": "trade_updates",
                "data": {
                    "event": "new",
                    "timestamp": at.isoformat(),
                    "order": contradictory,
                },
            }
        ),
        at,
        "source-fixture",
    )
    unresolved = derive(captured, packet)
    assert unresolved["component_attributed"] is False
    assert "SIMPLE_LIFECYCLE_IDENTITY_UNRESOLVED" in unresolved["issues"]


def test_raw_rest_activity_reuse_and_stream_namespace_ambiguity(captured: Any) -> None:
    packet = partial(captured)
    identity = packet["assessment"]["matching_fills"][0]["id"]
    cid, reported = other_simple(captured, execution_id=identity)
    source, at = captured[4][4][0], captured[3]
    first = derive(captured, packet)
    assert "STREAM_EXECUTION_ACTIVITY_ID_AMBIGUITY" in first["issues"]
    source.reconciler.apply_rest_batch(
        RestBatch(
            source.journal.read_paper_account_ref(),
            at,
            at,
            source.reconciler.view()["watermark"],
            (),
            (),
            {},
            {
                cid: (
                    {
                        "id": identity,
                        "activity_type": "FILL",
                        "order_id": reported["id"],
                        "symbol": "MSFT",
                        "side": "buy",
                        "qty": "0.25",
                        "price": "9.25",
                        "transaction_time": at.isoformat(),
                    },
                )
            },
            False,
            False,
            ("BOUNDED_IDENTITY_DIAGNOSTIC",),
        ),
        RECON_POLICY,
        at=at,
    )
    result = derive(captured, packet)
    assert result["component_attributed"] is False
    assert "SIMPLE_REST_ACTIVITY_ID_REUSE" in result["issues"]
    assert "STREAM_EXECUTION_ACTIVITY_ID_AMBIGUITY" in result["issues"]
