import asyncio
import base64
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from alpaca_ross_bot.config import PaperConfig, PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.order_reconciliation import (
    OrderReconciler,
    ReconciliationPolicy,
    RestBatch,
)
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import (
    PaperHttpResponse,
    PaperOrderAuthenticationError,
    PaperOrderHttp,
)
from alpaca_ross_bot.paper_order_recovery import (
    collect_rest_batch,
    collect_rest_batch_async,
)

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
ACCOUNT = {"id": "11111111-1111-4111-8111-111111111111"}
ACCOUNT_BODY = json.dumps(ACCOUNT).encode()
ACCOUNT_REF = paper_account_ref(ACCOUNT_BODY)
POLICY = ReconciliationPolicy(timedelta(minutes=1), timedelta(seconds=30), 5)
CONFIG = PaperConfig(
    trading_base_url="https://paper-api.alpaca.markets",
    market_data_base_url="https://data.alpaca.markets",
    api_key_id_env="TEST_PAPER_KEY",
    api_secret_key_env="TEST_PAPER_SECRET",
)


def response(value: object) -> PaperHttpResponse:
    return PaperHttpResponse(200, json.dumps(value).encode())


def order(index: int = 1, **changes: object) -> dict[str, object]:
    return {
        "id": str(UUID(int=index)),
        "client_order_id": "client-" + str(index),
        "symbol": "AAPL",
        "qty": "1",
        "filled_qty": "0",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "1.00",
        "asset_class": "us_equity",
        "order_class": "simple",
        "extended_hours": False,
        "status": "new",
        "updated_at": "2026-10-15T15:00:00.123456789Z",
    } | changes


def fill(index: int, owned: dict[str, object]) -> dict[str, object]:
    return {
        "id": "activity-" + str(index),
        "activity_type": "FILL",
        "order_id": owned["id"],
        "symbol": owned["symbol"],
        "side": owned["side"],
        "qty": "0.001",
        "price": "1.00",
        "transaction_time": "2026-10-15T15:00:00.123456789Z",
    }


class FakeExchange:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.open_views: list[list[dict[str, object]]] = [[]]
        self.position_views: list[list[dict[str, object]]] = [[]]
        self.lookup_views: dict[str, list[dict[str, object]]] = {}
        self.activity_rows: dict[str, list[dict[str, object]]] = {}
        self.account_views: list[PaperHttpResponse] = [response(ACCOUNT)]
        self.account_count = 0
        self.open_count = 0
        self.position_count = 0
        self.lookup_count: dict[str, int] = {}
        self.repeat_open = False
        self.repeat_activities = False
        self.fail_first_open: PaperHttpResponse | Exception | None = None
        self.position_failure: PaperHttpResponse | None = None
        self.on_first_positions: Callable[[], None] | None = None
        self.delay_seconds = 0.0
        self.call_threads: list[int] = []

    def __call__(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        assert method == "GET" and body is None
        self.call_threads.append(threading.get_ident())
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        self.calls.append((method, path, body))
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if parsed.path == "/v2/account":
            result = self.account_views[
                min(self.account_count, len(self.account_views) - 1)
            ]
            self.account_count += 1
            return result
        if parsed.path == "/v2/orders":
            assert query["status"] == ["open"] and query["limit"] == ["500"]
            assert query["nested"] == ["false"] and query["direction"] == ["asc"]
            assert "after" not in query and "until" not in query
            cursor = query.get("after_order_id", [None])[0]
            if cursor is None:
                self.open_count += 1
            if self.open_count == 1 and self.fail_first_open is not None:
                failure = self.fail_first_open
                self.fail_first_open = None
                if isinstance(failure, Exception):
                    raise failure
                return failure
            rows = self.open_views[min(self.open_count - 1, len(self.open_views) - 1)]
            start = 0
            if cursor is not None and not self.repeat_open:
                start = next(
                    index + 1 for index, row in enumerate(rows) if row["id"] == cursor
                )
            return response(rows[start : start + 500])
        if parsed.path == "/v2/positions":
            if self.on_first_positions is not None:
                callback, self.on_first_positions = self.on_first_positions, None
                callback()
            if self.position_failure is not None:
                return self.position_failure
            positions = self.position_views[
                min(self.position_count, len(self.position_views) - 1)
            ]
            self.position_count += 1
            return response(positions)
        if parsed.path == "/v2/orders:by_client_order_id":
            cid = query["client_order_id"][0]
            views = self.lookup_views.get(cid)
            if views is None:
                return PaperHttpResponse(404, b"not found")
            count = self.lookup_count.get(cid, 0)
            self.lookup_count[cid] = count + 1
            return response(views[min(count, len(views) - 1)])
        if parsed.path == "/v2/account/activities":
            assert query["activity_types"] == ["FILL"]
            assert query["direction"] == ["asc"] and query["page_size"] == ["100"]
            assert "after" not in query
            rows = self.activity_rows.get(query["order_id"][0], [])
            cursor = query.get("page_token", [None])[0]
            start = 0
            if cursor is not None and not self.repeat_activities:
                start = next(
                    index + 1 for index, row in enumerate(rows) if row["id"] == cursor
                )
            return response(rows[start : start + 100])
        raise AssertionError("unexpected fake endpoint")


def api(exchange: FakeExchange) -> PaperOrderHttp:
    return PaperOrderHttp(
        CONFIG,
        PaperCredentials(SecretValue("fake-key"), SecretValue("fake-secret")),
        ACCOUNT_REF,
        timeout_seconds=2,
        max_request_bytes=1024,
        max_response_bytes=1_000_000,
        exchange=exchange,
    )


@pytest.fixture
def bound(tmp_path: Path) -> Iterator[tuple[Journal, OrderReconciler]]:
    root = tmp_path / "private"
    path = root / "journal.sqlite3"
    journal = Journal(path, root)
    root.chmod(0o700)
    path.chmod(0o600)
    journal.record_paper_account_verification(ACCOUNT_REF, ACCOUNT_BODY, NOW)
    reconciler = OrderReconciler(journal, ACCOUNT_REF, clock=lambda: NOW)
    yield journal, reconciler
    journal.close()


def reserve(journal: Journal, owned: dict[str, object], *, sent: bool = True) -> None:
    request = {
        key: owned[key]
        for key in (
            "client_order_id",
            "symbol",
            "qty",
            "side",
            "type",
            "time_in_force",
            "limit_price",
        )
    }
    journal.reserve_order_intent(
        str(owned["client_order_id"]),
        {
            "paper_endpoint": CONFIG.trading_base_url,
            "paper_account_ref": ACCOUNT_REF,
            "purpose": "ENGINEERING_TEST",
            "request_body": json.dumps(request, sort_keys=True, separators=(",", ":")),
        },
    )
    if sent:
        journal.mark_order_send_attempt(str(owned["client_order_id"]), NOW)


def test_empty_stable_round_is_get_only_and_retains_raw_capture_evidence(
    bound: tuple[Journal, OrderReconciler],
) -> None:
    _, reconciler = bound
    exchange = FakeExchange()
    revision = reconciler.view()["watermark"]
    batch = collect_rest_batch(api(exchange), reconciler, POLICY, clock=lambda: NOW)
    assert batch.complete and batch.stable and batch.issues == ()
    assert batch.local_revision == revision == reconciler.view()["watermark"]
    assert batch.open_orders == () and batch.positions == ()
    raw = [capture for capture in batch.captures if "body_base64" in capture]
    assert len(raw) == 4
    assert all(
        base64.b64decode(str(capture["body_base64"])) == b"[]" for capture in raw
    )
    assert all(capture["observed_at"] == NOW.isoformat() for capture in batch.captures)
    assert all(method == "GET" and body is None for method, _, body in exchange.calls)


@pytest.mark.parametrize("count", [500, 501])
def test_uuid_cursor_covers_full_pages_with_identical_timestamps(
    bound: tuple[Journal, OrderReconciler], count: int
) -> None:
    exchange = FakeExchange()
    rows = [order(index) for index in range(1, count + 1)]
    exchange.open_views = [rows]
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert batch.complete and len(batch.open_orders) == count
    pages = [
        path for _, path, _ in exchange.calls if urlsplit(path).path == "/v2/orders"
    ]
    assert len(pages) == 4
    for path in (pages[1], pages[3]):
        assert parse_qs(urlsplit(path).query)["after_order_id"] == [
            str(rows[499]["id"])
        ]


@pytest.mark.parametrize("count", [100, 101])
def test_fill_pages_use_independent_activity_id_cursors(
    bound: tuple[Journal, OrderReconciler],
    count: int,
) -> None:
    journal, reconciler = bound
    owned = order(status="partially_filled", filled_qty="0.101")
    reserve(journal, owned)
    exchange = FakeExchange()
    exchange.open_views = [[owned]]
    exchange.lookup_views = {str(owned["client_order_id"]): [owned]}
    exchange.activity_rows = {
        str(owned["id"]): [fill(index, owned) for index in range(count)]
    }
    revision = reconciler.view()["watermark"]
    batch = collect_rest_batch(api(exchange), reconciler, POLICY, clock=lambda: NOW)
    assert batch.complete and batch.local_revision == revision
    assert len(batch.activities[str(owned["id"])]) == count
    pages = [
        path
        for _, path, _ in exchange.calls
        if urlsplit(path).path == "/v2/account/activities"
    ]
    assert len(pages) == 2
    assert parse_qs(urlsplit(pages[1]).query)["page_token"] == ["activity-99"]
    assert len(journal.read_order_http_observations(str(owned["client_order_id"]))) == 2
    assert journal.has_order_send_attempt(str(owned["client_order_id"]))


def test_every_sent_intent_is_looked_up_twice_and_unsent_is_not(
    bound: tuple[Journal, OrderReconciler],
) -> None:
    exchange = FakeExchange()
    for index in (1, 2, 3):
        owned = order(index, status="canceled")
        reserve(bound[0], owned, sent=index != 3)
        exchange.lookup_views[str(owned["client_order_id"])] = [owned]
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert batch.complete
    assert exchange.lookup_count == {"client-1": 2, "client-2": 2}
    assert set(batch.lookups) == {"client-1", "client-2"}


@pytest.mark.parametrize("activity", [False, True])
def test_duplicate_page_ids_and_loops_fail_closed(
    bound: tuple[Journal, OrderReconciler], activity: bool
) -> None:
    exchange = FakeExchange()
    if activity:
        owned = order(status="partially_filled", filled_qty="0.1")
        reserve(bound[0], owned)
        exchange.open_views = [[owned]]
        exchange.lookup_views = {"client-1": [owned]}
        exchange.activity_rows = {
            str(owned["id"]): [fill(index, owned) for index in range(100)]
        }
        exchange.repeat_activities = True
    else:
        exchange.open_views = [[order(index) for index in range(1, 501)]]
        exchange.repeat_open = True
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert not batch.complete and not batch.stable
    assert any(issue.startswith("DUPLICATE_PAGE_ID:") for issue in batch.issues)
    assert any(issue.startswith("CURSOR_LOOP:") for issue in batch.issues)
    assert len(exchange.calls) < 20


@pytest.mark.parametrize("activity", [False, True])
def test_full_final_page_never_proves_completeness(
    bound: tuple[Journal, OrderReconciler], activity: bool
) -> None:
    exchange = FakeExchange()
    if activity:
        owned = order(status="partially_filled", filled_qty="0.1")
        reserve(bound[0], owned)
        exchange.open_views = [[owned]]
        exchange.lookup_views = {"client-1": [owned]}
        exchange.activity_rows = {
            str(owned["id"]): [fill(index, owned) for index in range(100)]
        }
    else:
        exchange.open_views = [[order(index) for index in range(1, 501)]]
    policy = ReconciliationPolicy(POLICY.max_snapshot_age, POLICY.max_capture_span, 1)
    batch = collect_rest_batch(api(exchange), bound[1], policy, clock=lambda: NOW)
    assert not batch.complete and any(
        issue.startswith("PAGE_LIMIT_REACHED:") for issue in batch.issues
    )


@pytest.mark.parametrize("field", ["qty", "side"])
def test_changed_positions_are_explicit(
    bound: tuple[Journal, OrderReconciler], field: str
) -> None:
    original: dict[str, object] = {
        "symbol": "AAPL",
        "qty": "1",
        "side": "long",
        "asset_class": "us_equity",
        "market_value": "1",
    }
    changed = original | {field: "2" if field == "qty" else "short"}
    exchange = FakeExchange()
    exchange.position_views = [[original], [changed]]
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert not batch.complete and "SNAPSHOT_CHANGED:POSITIONS" in batch.issues
    assert batch.positions == (changed,)


def test_market_value_change_is_not_position_inventory_change(
    bound: tuple[Journal, OrderReconciler],
) -> None:
    original: dict[str, object] = {
        "symbol": "AAPL",
        "qty": "1",
        "side": "long",
        "asset_class": "us_equity",
        "market_value": "1",
    }
    exchange = FakeExchange()
    exchange.position_views = [[original], [original | {"market_value": "2"}]]
    assert collect_rest_batch(
        api(exchange), bound[1], POLICY, clock=lambda: NOW
    ).complete


def test_fill_between_reads_invalidates_stability(
    bound: tuple[Journal, OrderReconciler],
) -> None:
    first = order(status="partially_filled", filled_qty="0.1")
    second = first | {
        "filled_qty": "0.2",
        "updated_at": "2026-10-15T15:00:00.123456790Z",
    }
    reserve(bound[0], first)
    exchange = FakeExchange()
    exchange.open_views = [[first], [second]]
    exchange.lookup_views = {"client-1": [first, second]}
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert not batch.complete and not batch.stable
    assert "LOOKUP_CHANGED:client-1" in batch.issues
    assert "SNAPSHOT_CHANGED:OPEN_ORDERS" in batch.issues
    assert json.loads(batch.lookups["client-1"].body)["filled_qty"] == "0.2"


@pytest.mark.parametrize("after", [False, True])
def test_foreign_account_never_rebinds_or_grants_a_complete_batch(
    bound: tuple[Journal, OrderReconciler], after: bool
) -> None:
    wrong = response({"id": "33333333-3333-4333-8333-333333333333"})
    exchange = FakeExchange()
    exchange.account_views = [response(ACCOUNT), wrong] if after else [wrong]
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert not batch.complete and "ACCOUNT_VERIFICATION_FAILED" in batch.issues
    assert bound[0].read_paper_account_ref() == ACCOUNT_REF
    if after:
        assert len(batch.captures) > 1
    else:
        assert all(
            urlsplit(path).path == "/v2/account" for _, path, _ in exchange.calls
        )


@pytest.mark.parametrize(
    "failure", [PaperHttpResponse(503, b"temporary"), TimeoutError("simulated")]
)
def test_transient_failure_returns_partial_batch_with_response_evidence(
    bound: tuple[Journal, OrderReconciler], failure: PaperHttpResponse | Exception
) -> None:
    exchange = FakeExchange()
    exchange.fail_first_open = failure
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert not batch.complete and not batch.stable and len(batch.captures) == 6
    first = next(capture for capture in batch.captures if "body_base64" in capture)
    if isinstance(failure, PaperHttpResponse):
        assert first["status"] == 503
        assert base64.b64decode(str(first["body_base64"])) == b"temporary"
    else:
        assert first["error"] == "REQUEST_FAILED"
    assert any(capture.get("status") == 200 for capture in batch.captures)


@pytest.mark.parametrize("where", ["account", "positions"])
def test_authentication_failure_propagates(
    bound: tuple[Journal, OrderReconciler], where: str
) -> None:
    exchange = FakeExchange()
    if where == "account":
        exchange.account_views = [PaperHttpResponse(401, b"private auth failure")]
    else:
        exchange.position_failure = PaperHttpResponse(403, b"private auth failure")
    with pytest.raises(PaperOrderAuthenticationError, match="authentication failed"):
        collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)


@pytest.mark.parametrize(
    "field, value",
    [
        ("transaction_time", None),
        ("price", "NaN"),
        ("qty", None),
        ("order_id", "not-a-uuid"),
    ],
)
def test_malformed_activity_fields_are_retained_and_block_completeness(
    bound: tuple[Journal, OrderReconciler], field: str, value: object
) -> None:
    owned = order(status="partially_filled", filled_qty="0.1")
    reserve(bound[0], owned)
    exchange = FakeExchange()
    exchange.lookup_views = {"client-1": [owned]}
    exchange.activity_rows = {str(owned["id"]): [fill(1, owned) | {field: value}]}
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert not batch.complete
    assert "FILL_ACTIVITY_INVALID:" + str(owned["id"]) in batch.issues
    assert batch.activities[str(owned["id"])][0][field] == value


def test_local_revision_is_captured_before_the_requests(
    bound: tuple[Journal, OrderReconciler],
) -> None:
    exchange = FakeExchange()
    revision = bound[1].view()["watermark"]
    exchange.on_first_positions = lambda: bound[1].stream_health(
        "ERROR", NOW, "session"
    )
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert batch.local_revision == revision
    assert not batch.complete and "LOCAL_REVISION_CHANGED" in batch.issues


def test_private_storage_failure_propagates_before_any_request(
    bound: tuple[Journal, OrderReconciler], tmp_path: Path
) -> None:
    exchange = FakeExchange()
    (tmp_path / "private").chmod(0o755)
    with pytest.raises(JournalError, match="not private"):
        collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert exchange.calls == []


def test_read_methods_reject_invalid_cursors_without_network() -> None:
    exchange = FakeExchange()
    http = api(exchange)
    with pytest.raises(ValueError, match="UUID"):
        http.read_open_orders_page(after_order_id="not-a-uuid")
    with pytest.raises(ValueError, match="UUID"):
        http.read_fill_activities_page("not-a-uuid")
    with pytest.raises(ValueError, match="nonempty"):
        http.read_fill_activities_page(str(UUID(int=1)), page_token="")
    assert exchange.calls == []


@pytest.mark.parametrize("body", [b"{}", b"[null]", b"broken", b'[{"id": null}]'])
def test_malformed_order_page_retains_raw_evidence(
    bound: tuple[Journal, OrderReconciler],
    body: bytes,
) -> None:
    exchange = FakeExchange()
    exchange.fail_first_open = PaperHttpResponse(200, body)
    batch = collect_rest_batch(api(exchange), bound[1], POLICY, clock=lambda: NOW)
    assert not batch.complete and not batch.stable
    assert any(
        capture.get("body_base64") == base64.b64encode(body).decode("ascii")
        for capture in batch.captures
    )


def test_async_capture_preserves_heartbeat_and_owns_its_journal_in_one_thread(
    bound: tuple[Journal, OrderReconciler],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_thread = threading.get_ident()
    opened: list[tuple[int, int]] = []
    closed: list[tuple[int, int]] = []
    original_open = Journal.open_private
    original_close = Journal.close

    def track_open(cls: type[Journal], path: Path, root: Path) -> Journal:
        journal = original_open(path, root)
        opened.append((id(journal), threading.get_ident()))
        return journal

    def track_close(journal: Journal) -> None:
        closed.append((id(journal), threading.get_ident()))
        original_close(journal)

    monkeypatch.setattr(Journal, "open_private", classmethod(track_open))
    monkeypatch.setattr(Journal, "close", track_close)
    exchange = FakeExchange()
    exchange.delay_seconds = 0.01
    revision = bound[1].view()["watermark"]
    heartbeats = 0

    async def heartbeat() -> None:
        nonlocal heartbeats
        while True:
            heartbeats += 1
            await asyncio.sleep(0.001)

    async def exercise() -> RestBatch:
        task = asyncio.create_task(heartbeat())
        try:
            return await collect_rest_batch_async(
                api(exchange),
                tmp_path / "private",
                ACCOUNT_REF,
                POLICY,
                clock=lambda: NOW,
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    batch = asyncio.run(exercise())
    assert batch.complete and heartbeats >= 3
    assert batch.local_revision == revision == bound[1].view()["watermark"]
    assert len(opened) == 1 and closed == opened
    assert opened[0][0] != id(bound[0]) and opened[0][1] != main_thread
    assert set(exchange.call_threads) == {opened[0][1]}
    assert bound[0].read_paper_account_ref() == ACCOUNT_REF


def test_async_capture_detects_stream_revision_changes_without_cross_thread_sqlite(
    bound: tuple[Journal, OrderReconciler],
    tmp_path: Path,
) -> None:
    exchange = FakeExchange()
    entered = threading.Event()
    updated = threading.Event()

    def pause_read() -> None:
        entered.set()
        assert updated.wait(timeout=2)

    exchange.on_first_positions = pause_read
    revision = bound[1].view()["watermark"]

    async def exercise() -> RestBatch:
        task = asyncio.create_task(
            collect_rest_batch_async(
                api(exchange),
                tmp_path / "private",
                ACCOUNT_REF,
                POLICY,
                clock=lambda: NOW,
            )
        )
        while not entered.is_set():
            if task.done():
                return await task
            await asyncio.sleep(0.001)
        try:
            bound[1].stream_health("ERROR", NOW, "synthetic-session")
        finally:
            updated.set()
        return await task

    batch = asyncio.run(exercise())
    assert batch.local_revision == revision
    assert not batch.complete and "LOCAL_REVISION_CHANGED" in batch.issues
    assert bound[1].verify_rebuild()


def test_async_auth_failure_closes_worker_journal_and_keeps_main_connection_open(
    bound: tuple[Journal, OrderReconciler],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[tuple[int, int]] = []
    original_close = Journal.close

    def track_close(journal: Journal) -> None:
        closed.append((id(journal), threading.get_ident()))
        original_close(journal)

    monkeypatch.setattr(Journal, "close", track_close)
    exchange = FakeExchange()
    exchange.account_views = [PaperHttpResponse(401, b"synthetic auth failure")]
    with pytest.raises(PaperOrderAuthenticationError):
        asyncio.run(
            collect_rest_batch_async(
                api(exchange),
                tmp_path / "private",
                ACCOUNT_REF,
                POLICY,
                clock=lambda: NOW,
            )
        )
    assert len(closed) == 1
    assert closed[0][0] != id(bound[0])
    assert closed[0][1] != threading.get_ident()
    assert bound[0].read_paper_account_ref() == ACCOUNT_REF


@pytest.mark.parametrize("missing", [False, True])
def test_async_capture_rejects_foreign_binding_or_missing_journal_without_requests(
    bound: tuple[Journal, OrderReconciler],
    tmp_path: Path,
    missing: bool,
) -> None:
    exchange = FakeExchange()
    root = tmp_path / ("absent" if missing else "private")
    reference = ACCOUNT_REF if missing else "foreign-reference"
    with pytest.raises(JournalError):
        asyncio.run(
            collect_rest_batch_async(
                api(exchange), root, reference, POLICY, clock=lambda: NOW
            )
        )
    assert exchange.calls == []
    if missing:
        assert not root.exists()
