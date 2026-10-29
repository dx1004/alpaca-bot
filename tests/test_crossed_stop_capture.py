"""Memory callbacks only; raw capture never creates ownership or sender state."""

import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.crossed_stop_attempts import (
    SourceCloseAttempts,
    SourceCloseCallbackResponse,
)
from alpaca_ross_bot.crossed_stop_capture import (
    AUTHORITY,
    GET_ORIGIN,
    KIND,
    SOURCE,
    SourceCloseCaptures,
    SourceCloseGetResponse,
    _snapshot,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import _json
from alpaca_ross_bot.oco_journal import OcoJournal
from test_crossed_stop_source_attempts import invoke
from test_crossed_stop_source_attempts import source_attempt as source_attempt_fixture
from test_risk_ledger_audit import ACCOUNT

source_attempt = source_attempt_fixture

ORDER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


def order(body: bytes, at: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(body)) | {
        "id": ORDER_ID,
        "order_class": "simple",
        "asset_class": "us_equity",
        "extended_hours": False,
        "status": "new",
        "filled_qty": "0",
        "created_at": at.isoformat(),
        "submitted_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }


class MemoryGets:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.order = snapshot
        self.lookup: dict[str, Any] | None = None
        self.account = SourceCloseGetResponse(200, _json(ACCOUNT))
        self.pages: list[bytes] = [b"[]"]
        self.repeat = False
        self.calls: list[str] = []
        self.failure: Exception | SourceCloseGetResponse | None = None

    def __call__(self, path: str) -> SourceCloseGetResponse:
        self.calls.append(path)
        if path == "/v2/account":
            return self.account
        if self.failure is not None:
            if isinstance(self.failure, Exception):
                raise self.failure
            return self.failure
        if path.startswith("/v2/orders:by_client_order_id?"):
            query = parse_qs(urlsplit(path).query)
            assert query == {"client_order_id": [self.order["client_order_id"]]}
            return SourceCloseGetResponse(200, _json(self.lookup or self.order))
        if path == f"/v2/orders/{ORDER_ID}?nested=true":
            return SourceCloseGetResponse(200, _json(self.order))
        query = parse_qs(urlsplit(path).query)
        assert urlsplit(path).path == "/v2/account/activities"
        assert query["activity_types"] == ["FILL"]
        assert query["order_id"] == [ORDER_ID]
        assert query["direction"] == ["asc"] and query["page_size"] == ["100"]
        body = self.pages[0]
        if not self.repeat and len(self.pages) > 1:
            self.pages.pop(0)
        return SourceCloseGetResponse(200, body)


@pytest.fixture
def captured(source_attempt: Any) -> Any:
    attempts, _, _, posts, ready = source_attempt
    at = ready[4]
    saved: dict[str, Any] = {}

    def callback(body: bytes) -> SourceCloseCallbackResponse:
        posts.append(body)
        saved.update(order(body, at))
        return SourceCloseCallbackResponse(201, _json(saved))

    row = invoke(source_attempt, callback=callback)
    return SourceCloseCaptures(attempts), row, MemoryGets(saved), at, source_attempt


def capture(case: Any, **changes: Any) -> dict[str, Any]:
    captures, row, get, at, _ = case
    return cast(
        dict[str, Any],
        captures.capture(
            **(
                dict(
                    attempt_ref=row["attempt_ref"],
                    decision_at=at,
                    get=get,
                    clock=lambda: at,
                    max_body_bytes=10_000,
                    max_fill_pages=3,
                    page_size=100,
                )
                | changes
            )
        ),
    )


def fill(case: Any, **changes: Any) -> dict[str, Any]:
    _, _, get, at, _ = case
    return {
        "id": "fill-page-1",
        "order_id": ORDER_ID,
        "activity_type": "FILL",
        "type": "fill",
        "symbol": get.order["symbol"],
        "side": "sell",
        "qty": "0.25",
        "cum_qty": "0.25",
        "leaves_qty": str(Decimal(get.order["qty"]) - Decimal("0.25")),
        "price": "9.25",
        "transaction_time": at.isoformat(),
    } | changes


def test_matching_post_get_fractional_fill_raw_bytes_and_no_authority(
    captured: Any,
) -> None:
    captures, row, get, at, fixture = captured
    source, risk, owner, emergency, _, _, _ = fixture[4]
    before = (risk.view(), owner.view(), emergency.view())
    get.order.update(
        status="partially_filled", filled_qty="0.25", filled_at=at.isoformat()
    )
    raw_page = b"  " + _json([fill(captured, provider_optional_numeric=1.25)]) + b"\n"
    get.pages = [raw_page, b"[]"]
    result = capture(captured)
    summary = result["assessment"]
    assert result["origin"] == GET_ORIGIN
    assert result["pin"]["attempt_ref"] == row["attempt_ref"]
    assert result["pin"]["outcome_ref"] == row["outcome_ref"]
    assert summary["association"] == "INJECTED_POST_GET_IDENTITY_CONSISTENT"
    assert summary["account_consistency"] and summary["fill_pages_exhausted"]
    assert summary["fill_component_consistent"] and summary["fill_quantity"] == "1/4"
    assert summary["matching_fills"][0]["qty"] == "0.25"
    assert base64.b64decode(result["requests"][3]["body_base64"]) == raw_page
    assert summary["authority"] == AUTHORITY
    assert all(value is False for value in AUTHORITY.values())
    assert len(fixture[3]) == 1
    assert not source.journal.has_order_send_attempt(get.order["client_order_id"])
    assert (risk.view(), owner.view(), emergency.view()) == before
    assert captures.view() == [result]
    other = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:
        reopened = SourceCloseCaptures(
            SourceCloseAttempts(OcoJournal(other, owner.initial))
        )
        assert reopened.view() == [result]
    finally:
        other.close()
    forged = {k: v for k, v in result.items() if k != "capture_ref"}
    forged["origin"] = "INJECTED_EXCHANGE"
    captures.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=_json(forged),
        system_first_seen_at=at,
        processed_at=at,
    )
    with pytest.raises(JournalError):
        captures.view()
    assert captures.read_verified_at(result["capture_ref"]["sequence"], at) == [result]


@pytest.mark.parametrize("kind", ["unknown", "missing", "not_invoked"])
def test_unconfirmed_outcome_never_reconstructs_post_ack(
    source_attempt: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    attempts, _, kwargs, posts, ready = source_attempt
    at = ready[4]
    pending: list[Any] = []
    if kind == "missing":
        original = attempts._append

        def append(item: Any, clock: Any, prepare: Any) -> Any:
            if item["kind"] == "OUTCOME":
                pending.append((item, prepare))
                raise OSError("synthetic interruption")
            return original(item, clock, prepare)

        monkeypatch.setattr(attempts, "_append", append)

        def delayed_response(body: bytes) -> SourceCloseCallbackResponse:
            posts.append(body)
            return SourceCloseCallbackResponse(201, _json(order(body, at)))

        with pytest.raises(OSError):
            invoke(source_attempt, callback=delayed_response)
        monkeypatch.setattr(attempts, "_append", original)
        row = attempts.view()[0]
    elif kind == "not_invoked":
        count = 0

        def clock() -> Any:
            nonlocal count
            count += 1
            return at if count < 4 else at + timedelta(seconds=61)

        row = invoke(source_attempt, clock=clock)
        assert row["outcome"]["result_kind"] == "LOCAL_NOT_INVOKED"
        assert posts == []
        at += timedelta(seconds=61)
    else:

        def fail(body: bytes) -> Any:
            posts.append(body)
            raise TimeoutError("not logged")

        row = invoke(source_attempt, callback=fail)
    request = attempts.view()[0]["precall"]["request_body"].encode()
    get = MemoryGets(order(request, ready[4]))
    case = SourceCloseCaptures(attempts), row, get, at, source_attempt
    result = capture(case)
    assert result["assessment"]["post_reported_order_id"] is None
    expected = (
        "LOCAL_NOT_INVOKED_GET_CANDIDATE"
        if kind == "not_invoked"
        else "GET_CANDIDATE_ONLY"
    )
    assert result["assessment"]["association"] == expected
    assert result["assessment"]["authority"] == AUTHORITY
    assert case[0].view() == [result]
    if kind == "missing":
        # Complete the genuine deferred OUTCOME write after the capture. Its
        # later availability must not upgrade the older unknown association.
        item, prepare = pending[0]
        original(item, lambda: at + timedelta(seconds=1), prepare)
        assert attempts.view()[0]["outcome"]["result_kind"] == "INJECTED_RESPONSE"
        assert case[0].view() == [result]
        assert case[0].read_verified_at(result["capture_ref"]["sequence"], at) == [
            result
        ]
    assert not attempts.journal.has_order_send_attempt(kwargs["client_order_id"])


def test_account_mismatch_stops_before_cid_lookup(captured: Any) -> None:
    captured[2].account = SourceCloseGetResponse(200, _json(ACCOUNT | {"id": OTHER_ID}))
    result = capture(captured)
    assert result["assessment"]["issues"] == ["ACCOUNT_NOT_MATCHED"]
    assert captured[2].calls == ["/v2/account"]
    assert captured[0].view() == [result]


@pytest.mark.parametrize(
    "change", [{"id": OTHER_ID}, {"type": "limit"}, {"id": "not-a-uuid"}]
)
def test_mismatched_simple_market_lookup_never_follows_uuid(
    captured: Any, change: Any
) -> None:
    captured[2].lookup = captured[2].order | change
    result = capture(captured)
    assert len(captured[2].calls) == 2
    assert not result["assessment"]["fill_component_consistent"]
    assert captured[0].view() == [result]


def test_nanosecond_future_and_malformed_timestamps_remain_raw(captured: Any) -> None:
    _, _, get, at, _ = captured
    for stamp in (at.strftime("%Y-%m-%dT%H:%M:%S") + ".000000001Z", "invalid"):
        get.lookup = get.order | {"updated_at": stamp}
        result = capture(captured)
        assert result["assessment"]["issues"] == ["CID_LOOKUP_NOT_MATCHED"]
        assert base64.b64decode(result["requests"][1]["body_base64"]) == _json(
            get.lookup
        )
    assert len(captured[0].view()) == 2


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("secret-do-not-store"),
        SourceCloseGetResponse(503, b"\xff raw error"),
    ],
)
def test_raw_get_errors_are_diagnostic_and_secret_free(
    captured: Any, failure: Any
) -> None:
    captured[2].failure = failure
    result = capture(captured)
    assert len(result["requests"]) == 2
    if isinstance(failure, Exception):
        assert result["requests"][1]["error"] == "CALLBACK_EXCEPTION"
        assert b"secret-do-not-store" not in _json(result)
    else:
        assert base64.b64decode(result["requests"][1]["body_base64"]) == failure.body
    assert captured[0].view() == [result]


def test_oversize_keeps_explicit_prefix_only_and_blocks_followup(captured: Any) -> None:
    captured[2].failure = SourceCloseGetResponse(200, b"x" * 10_001)
    result = capture(captured)
    row = result["requests"][1]
    assert row["body_size"] == 10_001
    assert len(base64.b64decode(row["body_base64"])) == 10_000
    assert row["error"] == "BODY_LIMIT_EXCEEDED_PREFIX_ONLY"
    assert len(captured[2].calls) == 2
    assert captured[0].view() == [result]


def test_page_loop_budget_and_conflicting_fill_ids_never_complete(
    captured: Any,
) -> None:
    _, _, get, at, _ = captured
    get.order.update(
        status="partially_filled", filled_qty="0.25", filled_at=at.isoformat()
    )
    get.pages, get.repeat = [_json([fill(captured)])], True
    loop = capture(captured)
    assert "FILL_CURSOR_INVALID_OR_LOOP" in loop["assessment"]["issues"]
    assert "DUPLICATE_FILL_ID" in loop["assessment"]["issues"]
    truncated = capture(captured, max_fill_pages=1)
    assert "FILL_PAGE_BUDGET_TRUNCATED" in truncated["assessment"]["issues"]
    get.repeat = False
    get.pages = [_json([fill(captured), fill(captured, price="9.30")]), b"[]"]
    conflict = capture(captured)
    assert "CONFLICTING_FILL_ID" in conflict["assessment"]["issues"]
    assert conflict["assessment"]["fill_pages_exhausted"]
    assert all(
        not r["assessment"]["fill_component_consistent"]
        for r in (loop, truncated, conflict)
    )
    assert captured[0].view() == [loop, truncated, conflict]


def test_fill_after_detail_before_page_availability_is_inconsistent(
    captured: Any,
) -> None:
    _, _, get, at, _ = captured
    get.order.update(
        status="partially_filled", filled_qty="0.25", filled_at=at.isoformat()
    )
    get.pages = [
        _json(
            [
                fill(
                    captured,
                    transaction_time=(at + timedelta(microseconds=1)).isoformat(),
                )
            ]
        ),
        b"[]",
    ]
    now = [at]

    def moving_get(path: str) -> SourceCloseGetResponse:
        if path.startswith("/v2/account/activities?"):
            now[0] += timedelta(seconds=1)
        return cast(SourceCloseGetResponse, get(path))

    result = capture(captured, get=moving_get, clock=lambda: now[0])
    assert result["requests"][2]["available_at"] == at.isoformat()
    assert result["requests"][3]["available_at"] > at.isoformat()
    assert "FILL_ROW_INCONSISTENT" in result["assessment"]["issues"]
    assert not result["assessment"]["fill_component_consistent"]
    assert captured[0].view() == [result]


def test_tampered_reference_or_origin_fails_before_get(captured: Any) -> None:
    captures, row, get, _, _ = captured
    with pytest.raises(JournalError):
        capture(captured, attempt_ref=row["attempt_ref"] | {"hash": "sha256:wrong"})
    assert get.calls == []
    valid = capture(captured)
    forged = {k: v for k, v in valid.items() if k != "capture_ref"}
    forged = json.loads(_json(forged))
    forged["pin"]["request_sha256"] = "sha256:wrong"
    event = captures.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=_json(forged),
        system_first_seen_at=captured[3],
        processed_at=captured[3],
    )
    with pytest.raises(JournalError):
        captures.view()
    assert event.sequence > valid["capture_ref"]["sequence"]
    assert captures.read_verified_at(valid["capture_ref"]["sequence"], captured[3]) == [
        valid
    ]


def test_snapshot_scientific_quantity_uses_existing_body_resource_cap() -> None:
    at = datetime(2026, 10, 16, 13, tzinfo=UTC)
    request = _json(
        {
            "client_order_id": "bounded-scientific-close",
            "symbol": "AAPL",
            "qty": "1",
            "side": "sell",
            "type": "market",
            "time_in_force": "day",
        }
    )
    pin = {
        "request_body": request.decode(),
        "precall_recorded_at": at.isoformat(),
        "max_body_bytes": 10_000,
    }
    raw = order(request, at) | {
        "status": "partially_filled",
        "filled_qty": "1E-8",
        "filled_at": at.isoformat(),
    }
    snapshot = _snapshot(pin, _json(raw), at)
    assert Fraction(snapshot["filled_quantity"]) == Fraction(1, 100_000_000)
    with pytest.raises(ValueError, match="expansion exceeds capture body budget"):
        _snapshot(pin, _json(raw | {"filled_qty": "1E-1000000000"}), at)
    zero = _snapshot(
        pin,
        _json(
            raw
            | {
                "status": "new",
                "filled_qty": "0E-1000000000",
                "filled_at": None,
            }
        ),
        at,
    )
    assert zero["filled_quantity"] == "0"
