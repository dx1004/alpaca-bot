"""Real code transport with injected exchanges only; never sockets/accounts."""

import base64
import json
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from typing import Any, cast

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_attempts import (
    NativeOcoAttempts,
    NativeOcoCallbackResponse,
)
from alpaca_ross_bot.native_oco_http import NativeOcoHttp, native_http_inventory
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.paper_engineering import EngineeringHarness, EngineeringScope
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import PaperHttpResponse, PaperOrderHttp
from test_native_oco_attempts import run
from test_native_oco_requests import prepared as prepared_fixture
from test_paper_order_http import CONFIG, CREDENTIALS
from test_risk_ledger_audit import ACCOUNT, BEFORE, REF


class Exchange:
    def __init__(self, account: dict[str, object], response: object = None) -> None:
        self.account = account
        self.response = response or PaperHttpResponse(201, b'{"reported":"only"}')
        self.posts: list[bytes] = []
        self.gets = 0

    def __call__(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        assert path in {"/v2/account", "/v2/orders"}
        if method == "GET":
            self.gets += 1
            return PaperHttpResponse(200, json.dumps(self.account).encode())
        assert method == "POST" and body is not None
        self.posts.append(body)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, PaperHttpResponse)
        return self.response


def api(exchange: Exchange, *, cap: int = 10000) -> PaperOrderHttp:
    return PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        timeout_seconds=1,
        max_request_bytes=cap,
        max_response_bytes=10000,
        exchange=exchange,
    )


@pytest.fixture
def native_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[
    tuple[NativeOcoHttp, EngineeringHarness, datetime, Exchange, PaperOrderHttp]
]:
    formal_account = ACCOUNT | {"id": "22222222-2222-4222-8222-222222222222"}
    formal_ref = paper_account_ref(json.dumps(formal_account).encode())
    now = [BEFORE]
    from alpaca_ross_bot import paper_order_http

    class FixedDatetime:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now[0]

    monkeypatch.setattr(paper_order_http, "datetime", FixedDatetime)
    root = tmp_path / "private"
    harness = EngineeringHarness(
        root,
        EngineeringScope("native-experiment", REF, formal_ref, tmp_path / "formal"),
        clock=lambda: now[0],
    )
    open_private = Journal.open_private
    claimed = False

    def shared(cls: type[Journal], path: Path, runtime_root: Path) -> Journal:
        nonlocal claimed
        if path == root / "journal.sqlite3" and not claimed:
            claimed = True
            return harness.journal
        return open_private(path, runtime_root)

    monkeypatch.setattr(Journal, "open_private", classmethod(shared))
    fixture = cast(Any, prepared_fixture).__wrapped__(tmp_path, monkeypatch)
    try:
        _source, _oco, requests, at = next(fixture)
        now[0] = at
        requests.prepare("group-a", "native-cid-a", at, at)
        exchange = Exchange(ACCOUNT)
        yield (
            NativeOcoHttp(requests),
            harness,
            at,
            exchange,
            api(Exchange(formal_account)),
        )
    finally:
        fixture.close()


def submit(
    fixture: tuple[
        NativeOcoHttp, EngineeringHarness, datetime, Exchange, PaperOrderHttp
    ],
) -> dict[str, Any]:
    owner, harness, at, exchange, formal_api = fixture
    return api(exchange).post_native_oco_once(
        owner,
        harness,
        formal_api=formal_api,
        group_id="group-a",
        decision_at=at,
        started_at=at,
        clock=lambda: at,
    )


def test_exact_native_bytes_and_retry_never_replay(native_http: Any) -> None:
    owner, _harness, _at, exchange, formal_api = native_http
    row = submit(native_http)
    assert exchange.posts == [row["precall"]["request_body"].encode()]
    assert row["precall"]["origin"] == "INJECTED_EXCHANGE"
    assert row["outcome"]["result_kind"] == "HTTP_RESPONSE"
    assert base64.b64decode(row["outcome"]["body_base64"]) == b'{"reported":"only"}'
    assert not any(row["precall"]["authority"].values())
    assert not any(row["outcome"]["authority"].values())
    assert submit(native_http) == row
    assert exchange.gets == 1
    assert len(exchange.posts) == 1
    assert owner.view() == native_http_inventory(owner.journal)
    assert formal_api is not None


def test_timeout_consumed_and_unknown(native_http: Any) -> None:
    native_http[3].response = TimeoutError()
    row = submit(native_http)
    assert row["outcome"]["result_kind"] == "TRANSPORT_UNKNOWN"
    assert submit(native_http) == row
    assert len(native_http[3].posts) == 1


@pytest.mark.parametrize("status", [404, 422, 503])
def test_non_success_stays_consumed(native_http: Any, status: int) -> None:
    native_http[3].response = PaperHttpResponse(status, b"reported rejection")
    row = submit(native_http)
    assert row["outcome"]["http_status"] == status
    assert submit(native_http) == row
    assert len(native_http[3].posts) == 1


def test_both_account_checks_precede_post(native_http: Any) -> None:
    native_http[3].account = ACCOUNT | {"trading_blocked": True}
    with pytest.raises(ValueError, match="blocked"):
        submit(native_http)
    assert native_http[0].view() == []
    assert native_http[3].posts == []


def test_request_cap_refuses_before_durable_attempt(native_http: Any) -> None:
    owner, harness, at, exchange, formal = native_http
    with pytest.raises(ValueError, match="cap"):
        api(exchange, cap=1).post_native_oco_once(
            owner,
            harness,
            formal_api=formal,
            group_id="group-a",
            decision_at=at,
            started_at=at,
            clock=lambda: at,
        )
    assert owner.view() == []
    assert exchange.posts == []


def test_same_account_formal_proof_refuses_before_post(native_http: Any) -> None:
    owner, harness, at, exchange, _formal = native_http
    with pytest.raises(ValueError, match="identity differs"):
        api(exchange).post_native_oco_once(
            owner,
            harness,
            formal_api=api(Exchange(ACCOUNT)),
            group_id="group-a",
            decision_at=at,
            started_at=at,
            clock=lambda: at,
        )
    assert owner.view() == []
    assert exchange.posts == []


def test_fake_then_real_cannot_send(native_http: Any) -> None:
    owner, _harness, at, exchange, _formal = native_http
    run(NativeOcoAttempts(owner.requests), at)
    with pytest.raises(JournalError, match="fake attempt"):
        submit(native_http)
    assert exchange.posts == []


def test_real_then_fake_cannot_invoke(native_http: Any) -> None:
    owner, _harness, at, _exchange, _formal = native_http
    submit(native_http)
    with pytest.raises(JournalError, match="HTTP transport"):
        run(NativeOcoAttempts(owner.requests), at)


def test_corrupt_projection_refuses_submission(native_http: Any) -> None:
    owner = native_http[0]
    owner.journal._db.execute(
        "INSERT INTO state(key,value_json) VALUES (?,?)", (owner.state_key, "{}")
    )
    owner.journal._db.commit()
    with pytest.raises(JournalError, match="projection"):
        submit(native_http)
    assert native_http[3].posts == []


@pytest.mark.parametrize("value", ["null", "{}", '{"hash":"orphan","sequence":999}'])
def test_orphan_native_dedupe_refuses_send(native_http: Any, value: str) -> None:
    owner = native_http[0]
    owner.journal._db.execute(
        "INSERT INTO state(key,value_json) VALUES (?,?)",
        ("native_oco_http_evidence:orphan", value),
    )
    owner.journal._db.commit()
    with pytest.raises(JournalError, match="map differs"):
        submit(native_http)
    assert native_http[3].posts == []


def test_builtin_cannot_use_injected_formal_authentication(
    native_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpaca_ross_bot import paper_order_http

    owner, harness, at, exchange, formal = native_http
    monkeypatch.setattr(paper_order_http, "_exchange_paper_https", exchange)
    actual = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        timeout_seconds=1,
        max_request_bytes=10000,
        max_response_bytes=10000,
    )
    with pytest.raises(JournalError, match="builtin formal"):
        actual.post_native_oco_once(
            owner,
            harness,
            formal_api=formal,
            group_id="group-a",
            decision_at=at,
            started_at=at,
            clock=lambda: at,
        )
    assert owner.view() == []
    assert exchange.posts == []


@pytest.mark.parametrize(
    "corruption", ["formal-proof", "different-scope", "blocked-account"]
)
def test_recovery_rejects_unqualified_immutable_preproof_without_lookup(
    native_http: Any, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    import hashlib

    from test_paper_order_recovery import FakeExchange
    from test_paper_order_recovery import api as recovery_api

    owner, harness, _at, exchange, _formal = native_http
    risk_before = owner.journal.read_account_risk_events()
    allocations_before = owner.journal.read_oco_research_events()
    simple_ids_before = owner.journal.read_order_intent_ids()
    append = owner._append

    def corrupt(item: dict[str, Any], at: datetime, prepare: Any) -> bool:
        if item["kind"] == "PRECALL":
            if corruption == "formal-proof":
                item["formal_verification"]["body_sha256"] = "sha256:invalid"
            elif corruption == "different-scope":
                scope = json.loads(item["scope_payload"])
                scope["experiment_id"] = "another-valid-experiment"
                item["scope_payload"] = json.dumps(
                    scope, sort_keys=True, separators=(",", ":")
                )
            else:
                raw = json.dumps(ACCOUNT | {"trading_blocked": True}).encode()
                item["account_verification"]["body_base64"] = base64.b64encode(
                    raw
                ).decode()
                item["account_verification"]["body_sha256"] = (
                    "sha256:" + hashlib.sha256(raw).hexdigest()
                )
        return bool(append(item, at, prepare))

    monkeypatch.setattr(owner, "_append", corrupt)
    with pytest.raises(JournalError) as rejected:
        submit(native_http)
    assert (
        str(rejected.value.__cause__) == "native HTTP authenticated scope proof differs"
    )
    recorded = owner.journal._read_private_events("ALPACA_NATIVE_OCO_HTTP", None)
    assert len(recorded) == 1 and recorded[0][0].event_kind == "PRECALL"
    lookup = FakeExchange()
    with pytest.raises(JournalError) as recovery_rejected:
        harness._recover_native_http(recovery_api(lookup))
    assert (
        str(recovery_rejected.value.__cause__)
        == "native HTTP authenticated scope proof differs"
    )
    assert lookup.calls == []
    assert exchange.posts == []
    assert owner.journal.read_account_risk_events() == risk_before
    assert owner.journal.read_oco_research_events() == allocations_before
    assert owner.journal.read_order_intent_ids() == simple_ids_before


def test_failed_outcome_write_leaves_durable_unknown(
    native_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = native_http[0]
    append = owner._append

    def fail(item: dict[str, Any], at: datetime, prepare: Any) -> bool:
        if item["kind"] == "OUTCOME":
            raise OSError("simulated disk failure")
        return bool(append(item, at, prepare))

    monkeypatch.setattr(owner, "_append", fail)
    with pytest.raises(OSError):
        submit(native_http)
    row = submit(native_http)
    assert row["outcome"] is None
    assert len(native_http[3].posts) == 1


def test_builtin_https_path_is_distinct_without_sockets(
    native_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpaca_ross_bot import paper_order_http

    owner, harness, at, exchange, _formal = native_http
    formal_exchange = Exchange(ACCOUNT | {"id": "22222222-2222-4222-8222-222222222222"})

    def default_dispatch(
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        cap: int,
    ) -> PaperHttpResponse:
        if method == "GET" and formal_exchange.gets == 0:
            return formal_exchange(method, path, headers, body, timeout, cap)
        return cast(
            PaperHttpResponse, exchange(method, path, headers, body, timeout, cap)
        )

    monkeypatch.setattr(paper_order_http, "_exchange_paper_https", default_dispatch)
    actual = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        timeout_seconds=1,
        max_request_bytes=10000,
        max_response_bytes=10000,
    )
    formal = PaperOrderHttp(
        CONFIG,
        CREDENTIALS,
        timeout_seconds=1,
        max_request_bytes=10000,
        max_response_bytes=10000,
    )
    row = actual.post_native_oco_once(
        owner,
        harness,
        formal_api=formal,
        group_id="group-a",
        decision_at=at,
        started_at=at,
        clock=lambda: at,
    )
    assert row["precall"]["origin"] == "BUILTIN_PAPER_HTTPS"
    assert row["precall"]["formal_origin"] == "BUILTIN_PAPER_HTTPS"
    assert exchange.posts == [row["precall"]["request_body"].encode()]


def test_two_connections_cannot_duplicate_native_post(native_http: Any) -> None:
    owner, harness, at, exchange, formal = native_http

    def worker() -> str:
        second = EngineeringHarness(harness.root, harness.scope, clock=lambda: at)
        try:
            local = NativeOcoHttp(
                NativeOcoRequests(
                    OcoJournal(second.journal, owner.requests.owner.initial)
                )
            )
            try:
                api(exchange).post_native_oco_once(
                    local,
                    second,
                    formal_api=formal,
                    group_id="group-a",
                    decision_at=at,
                    started_at=at,
                    clock=lambda: at,
                )
                return "retained"
            except JournalError as exc:
                assert "consumed" in str(exc)
                return "consumed"
        finally:
            second.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert len(exchange.posts) == 1
    assert "retained" in results
    assert len(owner.view()) == 1


def test_fake_real_race_claims_only_one_origin(native_http: Any) -> None:
    owner, harness, at, exchange, formal = native_http
    barrier = Barrier(2)
    fake_calls: list[bytes] = []

    def fake_callback(body: bytes) -> NativeOcoCallbackResponse:
        fake_calls.append(body)
        return NativeOcoCallbackResponse(422, b"test rejection")

    def worker(real: bool) -> str:
        second = EngineeringHarness(harness.root, harness.scope, clock=lambda: at)
        try:
            requests = NativeOcoRequests(
                OcoJournal(second.journal, owner.requests.owner.initial)
            )
            barrier.wait(timeout=10)
            try:
                if real:
                    api(exchange).post_native_oco_once(
                        NativeOcoHttp(requests),
                        second,
                        formal_api=formal,
                        group_id="group-a",
                        decision_at=at,
                        started_at=at,
                        clock=lambda: at,
                    )
                else:
                    NativeOcoAttempts(requests).run_fake_once(
                        "group-a",
                        "fake-a",
                        decision_at=at,
                        started_at=at,
                        callback=fake_callback,
                        clock=lambda: at,
                    )
                return "retained"
            except JournalError as exc:
                assert "claimed" in str(exc)
                return "excluded"
        finally:
            second.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [False, True]))
    assert sorted(results) == ["excluded", "retained"]
    assert len(fake_calls) + len(exchange.posts) == 1
