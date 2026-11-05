"""Authenticated memory exchanges only; builtin source dispatch stays closed."""

import base64
import json
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any, cast

import pytest

import alpaca_ross_bot.source_close_http as source_http_module
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop_attempts import (
    AUTHORITY as CALLBACK_AUTHORITY,
)
from alpaca_ross_bot.crossed_stop_attempts import (
    ORIGIN as CALLBACK_ORIGIN,
)
from alpaca_ross_bot.crossed_stop_attempts import (
    SOURCE as CALLBACK_SOURCE,
)
from alpaca_ross_bot.crossed_stop_attempts import (
    SourceCloseAttempts,
    SourceCloseCallbackResponse,
    source_close_fences,
)
from alpaca_ross_bot.crossed_stop_preparation import (
    derive_source_close_precall,
    preparation_request,
)
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_http import _check_scope_proof
from alpaca_ross_bot.native_oco_requests import _digest, _json
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.paper_engineering import EngineeringHarness, EngineeringScope
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import (
    PaperHttpResponse,
    PaperOrderHttp,
    PaperOrderHttpError,
)
from alpaca_ross_bot.source_close_http import (
    AUTHORITY,
    DEDUPE,
    ORIGIN,
    SOURCE,
    SourceCloseHttp,
    read_source_close_http_at,
    source_http_inventory,
)
from test_crossed_stop_preparation import ready as ready_fixture
from test_paper_order_http import CONFIG, CREDENTIALS
from test_risk_ledger_audit import ACCOUNT, BEFORE, REF
from test_source_close_handoff import handoff

ORDER_ID = "77777777-7777-4777-8777-777777777777"
FORMAL_ACCOUNT = ACCOUNT | {"id": "22222222-2222-4222-8222-222222222222"}
FORMAL_REF = paper_account_ref(_json(FORMAL_ACCOUNT))


class Exchange:
    def __init__(self, account: dict[str, Any], now: list[datetime]) -> None:
        self.account, self.now = dict(account), now
        self.response: PaperHttpResponse | Exception | None = None
        self.posts: list[bytes] = []
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        assert set(headers) >= {"APCA-API-KEY-ID", "APCA-API-SECRET-KEY"}
        assert timeout > 0 and cap > 0
        self.calls.append((method, path))
        if method == "GET":
            assert path == "/v2/account" and body is None
            return PaperHttpResponse(200, _json(self.account))
        assert method == "POST" and path == "/v2/orders" and body is not None
        self.posts.append(body)
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return self.response
        request = json.loads(body)
        return PaperHttpResponse(
            201,
            _json(
                request
                | {
                    "id": ORDER_ID,
                    "status": "new",
                    "filled_qty": "0",
                    "asset_class": "us_equity",
                    "order_class": "simple",
                    "extended_hours": False,
                    "created_at": self.now[0].isoformat(),
                    "submitted_at": self.now[0].isoformat(),
                    "updated_at": self.now[0].isoformat(),
                }
            ),
        )


def api(
    exchange: Exchange | None,
    *,
    expected: str = REF,
    request_cap: int = 10000,
    response_cap: int = 10000,
) -> PaperOrderHttp:
    return PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        expected,
        timeout_seconds=1,
        max_request_bytes=request_cap,
        max_response_bytes=response_cap,
        exchange=exchange,
    )


@pytest.fixture
def source_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[Any]:
    now = [BEFORE]
    from alpaca_ross_bot import paper_order_http

    class FixedDatetime:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now[0]

    monkeypatch.setattr(paper_order_http, "datetime", FixedDatetime)
    root = tmp_path / "private"
    scope = EngineeringScope("source-http-test", REF, FORMAL_REF, tmp_path / "formal")
    if getattr(request, "param", {}).get("late_scope"):
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
            # Fault injection: simulate a persisted late scope. The current
            # harness already refuses normal adoption of unscoped history.
            prior.append_next_if_state_missing(
                state_key="engineering_scope",
                state_value=_digest(scope.payload()).removeprefix("sha256:"),
                source_id="PAPER_ENGINEERING_HARNESS",
                feed="paper",
                event_kind="SCOPE",
                payload=scope.payload(),
                system_first_seen_at=BEFORE,
                processed_at=BEFORE,
            )
        finally:
            prior.close()
    harness = EngineeringHarness(root, scope, clock=lambda: now[0])
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
        _, risk, owner, _, at, assets, statuses = ready
        now[0] = at
        _, cid = handoff(ready)
        risk.bind_exit("source-http-role", at, cid, "CLOSE")
        exchange, formal = Exchange(ACCOUNT, now), Exchange(FORMAL_ACCOUNT, now)
        kwargs = dict(
            client_order_id=cid,
            attempt_id="source-http-attempt",
            preparation=preparation_request(
                assets, statuses, 0, timedelta(seconds=1), timedelta(seconds=1)
            ),
            decision_at=at,
            started_at=at,
            clock=lambda: now[0],
        )
        yield (
            SourceCloseHttp(owner),
            harness,
            kwargs,
            exchange,
            api(exchange),
            api(formal, expected=FORMAL_REF),
            ready,
            now,
        )
    finally:
        fixture.close()


def submit(case: Any, **changes: Any) -> dict[str, Any]:
    selected = changes.pop("api", case[4])
    formal = changes.pop("formal_api", case[5])
    return cast(
        dict[str, Any],
        selected.post_source_close_once(
            case[0], case[1], formal_api=formal, **(case[2] | changes)
        ),
    )


def fake(
    case: Any,
    *,
    journal: Journal | None = None,
    owner: OcoJournal | None = None,
    harness: EngineeringHarness | None = None,
    posts: list[bytes] | None = None,
) -> dict[str, Any]:
    selected = case[6][2] if owner is None else owner
    callbacks = [] if posts is None else posts

    def callback(body: bytes) -> SourceCloseCallbackResponse:
        callbacks.append(body)
        return SourceCloseCallbackResponse(201, b'{"reported":"callback-only"}')

    return SourceCloseAttempts(selected).invoke_once(
        case[1] if harness is None else harness,
        **case[2],
        max_response_bytes=10000,
        callback=callback,
    )


def test_authenticated_exact_bytes_retry_reopen_and_historical_no_current_constructors(
    source_http: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _, kwargs, exchange, transport, formal, ready, now = source_http
    source, risk, oco, emergency, at, _, _ = ready
    before = risk.view(), emergency.view()
    row = submit(source_http)
    assert exchange.posts == [row["precall"]["request_body"].encode()]
    assert row["precall"]["origin"] == row["precall"]["formal_origin"] == ORIGIN
    assert row["precall"]["account_verification"]["body_sha256"] == _digest(
        _json(ACCOUNT)
    )
    assert row["precall"]["formal_verification"]["body_sha256"] == _digest(
        _json(FORMAL_ACCOUNT)
    )
    assert row["precall"]["authority"] == AUTHORITY and all(
        v is False for v in AUTHORITY.values()
    )
    assert row["outcome"]["result_kind"] == "HTTP_RESPONSE"
    assert json.loads(base64.b64decode(row["outcome"]["body_base64"]))["id"] == ORDER_ID
    assert [method for method, _ in exchange.calls] == ["GET", "POST"]
    assert not source.journal.has_order_send_attempt(kwargs["client_order_id"])
    assert submit(source_http) == row and len(exchange.posts) == 1
    assert (risk.view(), emergency.view()) == before
    for changes in (
        {"attempt_id": "changed"},
        {"started_at": at - timedelta(microseconds=1)},
    ):
        with pytest.raises(JournalError):
            submit(source_http, **changes)
    other = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:
        assert SourceCloseHttp(OcoJournal(other, oco.initial)).view() == [row]
        before_db = tuple(other._db.iterdump())

        def deny(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("current constructor entered historical source HTTP")

        with monkeypatch.context() as context:
            context.setattr(AccountRiskOwner, "__init__", deny)
            context.setattr(OcoJournal, "__init__", deny)
            context.setattr(SourceCloseHttp, "__init__", deny)
            assert read_source_close_http_at(
                other,
                oco.initial,
                through_sequence=row["outcome_ref"]["sequence"],
                decision_at=at,
            ) == [row]
            unknown = read_source_close_http_at(
                other,
                oco.initial,
                through_sequence=row["attempt_ref"]["sequence"],
                decision_at=at,
            )
            assert unknown[0]["outcome"] is None
        assert tuple(other._db.iterdump()) == before_db
    finally:
        other.close()


def test_builtin_auth_flags_caps_and_expiry_refuse_before_post(
    source_http: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alpaca_ross_bot import paper_order_http

    owner, _, _, exchange, transport, formal, _, now = source_http

    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("builtin socket/HTTPS request attempted")

    with monkeypatch.context() as context:
        context.setattr(paper_order_http, "HTTPSConnection", deny)
        import socket

        context.setattr(socket, "socket", deny)
        builtin = api(None)
        builtin_formal = api(None, expected=FORMAL_REF)
        assert (
            builtin.native_http_origin
            == builtin_formal.native_http_origin
            == "BUILTIN_PAPER_HTTPS"
        )
        for changes in (
            {"api": builtin},
            {"formal_api": builtin_formal},
            {"api": builtin, "formal_api": builtin_formal},
        ):
            with pytest.raises(JournalError, match="builtin"):
                submit(source_http, **changes)
    assert (
        exchange.calls == [] and owner.journal._read_private_events(SOURCE, None) == ()
    )
    formal_exchange = cast(Exchange, formal._exchange)
    for field, value in (
        ("id", FORMAL_ACCOUNT["id"]),
        ("trading_blocked", True),
        ("account_blocked", True),
        ("trade_suspended_by_user", True),
    ):
        exchange.account = ACCOUNT | {field: value}
        with pytest.raises((JournalError, PaperOrderHttpError)):
            submit(source_http)
        assert exchange.posts == [] and source_http_inventory(owner.journal) == []
    exchange.account = dict(ACCOUNT)
    formal_exchange.account = dict(ACCOUNT)
    with pytest.raises(PaperOrderHttpError):
        submit(source_http)
    formal_exchange.account = dict(FORMAL_ACCOUNT)
    for small in (api(exchange, request_cap=1), api(exchange, response_cap=1)):
        with pytest.raises(PaperOrderHttpError):
            submit(source_http, api=small)
    original = derive_source_close_precall

    def delayed(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        now[0] += timedelta(seconds=61)
        return result

    with monkeypatch.context() as context:
        context.setattr(source_http_module, "derive_source_close_precall", delayed)
        with pytest.raises(JournalError):
            submit(source_http)
    assert exchange.posts == [] and source_http_inventory(owner.journal) == []
    assert not owner.journal._db.in_transaction


@pytest.mark.parametrize("mode", ["timeout", "rejected", "missing_outcome"])
def test_consumed_unknown_rejection_or_missing_outcome_never_reposts(
    source_http: Any,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    owner, _, _, exchange, _, _, ready, _ = source_http
    if mode == "timeout":
        exchange.response = TimeoutError("synthetic transport unknown")
    elif mode == "rejected":
        exchange.response = PaperHttpResponse(422, b' {"message":"rejected"}\n')
    else:
        append = owner._append

        def fail(item: Any, clock: Any, prepare: Any) -> Any:
            if item.get("kind") == "OUTCOME":
                raise OSError("synthetic outcome append failure")
            return append(item, clock, prepare)

        monkeypatch.setattr(owner, "_append", fail)
    if mode == "missing_outcome":
        with pytest.raises(OSError):
            submit(source_http)
    else:
        submit(source_http)
    row = submit(source_http)
    assert len(exchange.posts) == 1
    if mode == "missing_outcome":
        assert row["outcome"] is None
    elif mode == "timeout":
        assert row["outcome"]["result_kind"] == "TRANSPORT_UNKNOWN"
    else:
        assert row["outcome"]["http_status"] == 422
        assert base64.b64decode(row["outcome"]["body_base64"]) == exchange.response.body
    other = Journal.open_private(owner.journal._path, owner.journal._runtime_root)
    try:
        assert read_source_close_http_at(
            other,
            ready[2].initial,
            through_sequence=other.read_evidence_sequence(),
            decision_at=ready[4],
        ) == [row]
    finally:
        other.close()


def test_http_then_callback_and_forged_dual_claim_are_rejected(
    source_http: Any,
) -> None:
    owner, _, kwargs, exchange, _, _, ready, _ = source_http
    row = submit(source_http)
    callbacks: list[bytes] = []
    with pytest.raises(JournalError, match="HTTP"):
        fake(source_http, posts=callbacks)
    assert callbacks == [] and len(exchange.posts) == 1
    pre = row["precall"]
    item = {
        "schema": 1,
        "kind": "PRECALL",
        "client_order_id": pre["client_order_id"],
        "entry_scope": pre["entry_scope"],
        "account_ref": pre["account_ref"],
        "origin": CALLBACK_ORIGIN,
        "authority": dict(CALLBACK_AUTHORITY),
        "recorded_at": pre["recorded_at"],
        "qualified_at": pre["qualified_at"],
        "source_horizon": pre["source_horizon"],
        "qualification": pre["qualification"],
        "request_body": pre["request_body"],
        "scope_payload": pre["scope_payload"],
        "command": dict(pre["command"]) | {"max_response_bytes": 10000},
    }
    raw = _json(item)
    event = owner.journal.append_next(
        source_id=CALLBACK_SOURCE,
        feed="paper",
        event_kind="PRECALL",
        payload=raw,
        system_first_seen_at=ready[4],
        processed_at=ready[4],
    )
    ref = {"sequence": event.sequence, "hash": event.payload_ref}
    callback_row = {
        "precall": item,
        "attempt_ref": ref,
        "outcome": None,
        "outcome_ref": None,
    }
    for key, value in (
        ("source_close_attempts:" + pre["entry_scope"], [callback_row]),
        (
            "source_close_attempt_evidence:"
            + REF
            + ":"
            + _json([pre["entry_scope"], pre["client_order_id"], "PRECALL"]).decode(),
            ref,
        ),
    ):
        owner.journal._db.execute(
            "INSERT INTO state(key,value_json) VALUES (?,?)",
            (key, _json(value).decode()),
        )
    owner.journal._db.commit()
    with pytest.raises(JournalError, match="duplicate callback/HTTP"):
        source_http_inventory(owner.journal)
    with pytest.raises(JournalError, match="duplicate callback/HTTP"):
        SourceCloseAttempts(ready[2]).view()
    assert read_source_close_http_at(
        owner.journal,
        ready[2].initial,
        through_sequence=row["outcome_ref"]["sequence"],
        decision_at=ready[4],
    ) == [row]


def test_callback_first_blocks_http_without_auth_or_retag(source_http: Any) -> None:
    owner, _, _, exchange, _, _, _, _ = source_http
    row = fake(source_http)
    assert row["precall"]["origin"] == CALLBACK_ORIGIN
    with pytest.raises(JournalError, match="callback"):
        submit(source_http)
    assert exchange.calls == [] and source_http_inventory(owner.journal) == []


def test_raw_origin_hash_ref_projection_and_dedupe_guards(source_http: Any) -> None:
    owner = source_http[0]
    row = submit(source_http)
    event, raw = owner.journal._read_private_events(SOURCE, None)[0]
    owner.journal._db.execute("BEGIN")
    try:
        for fault in (
            "origin",
            "hash",
            "reference",
            "projection",
            "dedupe",
            "late_scope",
        ):
            owner.journal._db.execute("SAVEPOINT source_http_fault")
            try:
                if fault == "late_scope":
                    owner.journal._db.execute("DROP TRIGGER events_no_update")
                    scope = owner.journal.read_engineering_events()[0][0]
                    owner.journal._db.execute(
                        "UPDATE events SET sequence=? WHERE sequence=?",
                        (owner.journal.read_evidence_sequence() + 1, scope.sequence),
                    )
                    # Existing Native semantics intentionally remain unchanged;
                    # all payload hashes/proof maps are still correct here.
                    _check_scope_proof(owner.journal, row["precall"])
                elif fault == "projection":
                    projected = owner.journal.read_state(owner.state_key)
                    projected[0]["precall"]["request_body"] += " "
                    owner.journal._db.execute(
                        "UPDATE state SET value_json=? WHERE key=?",
                        (_json(projected).decode(), owner.state_key),
                    )
                elif fault == "dedupe":
                    key = (
                        DEDUPE
                        + ":"
                        + REF
                        + ":"
                        + _json(
                            [
                                row["precall"]["entry_scope"],
                                row["precall"]["client_order_id"],
                                "PRECALL",
                            ]
                        ).decode()
                    )
                    owner.journal._db.execute(
                        "UPDATE state SET value_json=? WHERE key=?",
                        (
                            _json(
                                {"sequence": event.sequence, "hash": "sha256:wrong"}
                            ).decode(),
                            key,
                        ),
                    )
                else:
                    owner.journal._db.execute("DROP TRIGGER events_no_update")
                    item = json.loads(raw)
                    if fault == "origin":
                        item["origin"] = "BUILTIN_PAPER_HTTPS"
                    elif fault == "reference":
                        item["request_sha256"] = "sha256:wrong"
                    changed = raw + b" " if fault == "hash" else _json(item)
                    owner.journal._db.execute(
                        "UPDATE events SET payload=? WHERE sequence=?",
                        (changed, event.sequence),
                    )
                with pytest.raises(JournalError):
                    owner.view()
            finally:
                owner.journal._db.execute("ROLLBACK TO source_http_fault")
                owner.journal._db.execute("RELEASE source_http_fault")
        assert owner.view() == [row]
    finally:
        owner.journal._db.rollback()


def test_callback_http_two_connection_race_has_one_dispatch(source_http: Any) -> None:
    root = source_http[1].root
    scope = source_http[1].scope
    initial = source_http[6][2].initial
    barrier = Barrier(2)
    callbacks: list[bytes] = []
    exchanges: list[Exchange] = []

    def worker(http: bool) -> bool:
        harness = EngineeringHarness(root, scope, clock=lambda: source_http[7][0])
        try:
            harness.journal._db.execute("PRAGMA busy_timeout=120000")
            oco = OcoJournal(harness.journal, initial)
            owner = SourceCloseHttp(oco) if http else None
            exchange = Exchange(ACCOUNT, source_http[7])
            exchanges.append(exchange)
            barrier.wait(timeout=60)
            try:
                if http:
                    assert owner is not None
                    api(exchange).post_source_close_once(
                        owner,
                        harness,
                        formal_api=api(
                            Exchange(FORMAL_ACCOUNT, source_http[7]),
                            expected=FORMAL_REF,
                        ),
                        **source_http[2],
                    )
                else:
                    fake(source_http, owner=oco, harness=harness, posts=callbacks)
                return True
            except JournalError:
                return False
        finally:
            harness.journal.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, (True, False)))
    assert sorted(results) == [False, True]
    assert len(callbacks) + sum(len(exchange.posts) for exchange in exchanges) == 1
    assert len(source_close_fences(source_http[0].journal)) == 1


def test_expired_after_fence_local_not_invoked_and_interrupt_cleans_lock(
    source_http: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _, _, exchange, _, _, _, now = source_http
    owner.journal._db.execute("BEGIN")
    with pytest.raises(JournalError, match="caller transaction"):
        submit(source_http)
    assert owner.journal._db.in_transaction
    owner.journal._db.rollback()

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    with monkeypatch.context() as context:
        context.setattr(source_http_module, "derive_source_close_precall", interrupted)
        with pytest.raises(KeyboardInterrupt):
            submit(source_http)
    assert not owner.journal._db.in_transaction
    assert source_http_inventory(owner.journal) == [] and exchange.posts == []
    append = owner._append

    def expire_after_fence(item: Any, clock: Any, prepare: Any) -> Any:
        result = append(item, clock, prepare)
        if item["kind"] == "PRECALL":
            now[0] += timedelta(seconds=61)
        return result

    monkeypatch.setattr(owner, "_append", expire_after_fence)
    row = submit(source_http)
    assert row["outcome"]["result_kind"] == "LOCAL_NOT_INVOKED"
    assert exchange.posts == [] and submit(source_http) == row


@pytest.mark.parametrize("source_http", [{"late_scope": True}], indirect=True)
def test_unscoped_history_before_scope_cannot_be_relabelled(source_http: Any) -> None:
    owner, _, _, exchange, _, formal, _, _ = source_http
    before = tuple(owner.journal._db.iterdump())
    with pytest.raises(JournalError, match="scope differs"):
        submit(source_http)
    assert exchange.calls == [] and cast(Exchange, formal._exchange).calls == []
    assert owner.journal._read_private_events(SOURCE, None) == ()
    assert tuple(owner.journal._db.iterdump()) == before


def test_post_response_cap_mutation_cannot_exceed_frozen_budget(
    source_http: Any,
) -> None:
    owner, _, _, exchange, transport, _, _, _ = source_http
    original = transport._exchange

    def enlarge(
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        response = original(method, path, headers, body, timeout, cap)
        if method == "POST":
            transport._max_response_bytes = 20000
            return PaperHttpResponse(201, b"x" * 10001)
        return cast(PaperHttpResponse, response)

    transport._exchange = enlarge
    row = submit(source_http)
    assert row["precall"]["limits"]["max_response_bytes"] == 10000
    assert row["outcome"]["result_kind"] == "TRANSPORT_UNKNOWN"
    assert (
        row["outcome"]["http_status"] is None and row["outcome"]["body_base64"] is None
    )
    assert owner.view() == [row] and len(exchange.posts) == 1
    assert submit(source_http, api=api(exchange)) == row
    assert len(exchange.posts) == 1
