"""Native recovery retains GET evidence without clearing consumed attempts."""

import base64
import json
from datetime import datetime

import pytest

from alpaca_ross_bot.native_oco_http import NativeOcoHttp
from alpaca_ross_bot.paper_engineering import EngineeringHarness
from alpaca_ross_bot.paper_order_http import PaperOrderHttp
from test_native_oco_http import Exchange, submit
from test_native_oco_http import native_http as native_http
from test_paper_order_recovery import POLICY, FakeExchange, response
from test_paper_order_recovery import api as recovery_api
from test_risk_ledger_audit import ACCOUNT

NativeFixture = tuple[
    NativeOcoHttp, EngineeringHarness, datetime, Exchange, PaperOrderHttp
]


def test_unknown_native_post_get404_never_releases_or_replays(
    native_http: NativeFixture,
) -> None:
    owner, harness, _at, exchange, _formal = native_http
    exchange.response = TimeoutError()
    original = submit(native_http)
    allocation = owner.requests.owner.view()
    risk_events = owner.journal.read_account_risk_events()
    lookup = FakeExchange()
    lookup.account_views = [response(ACCOUNT)]
    report = harness.recover(recovery_api(lookup), POLICY)
    assert report["native_http_attempt_count"] == 1
    assert report["native_http_unresolved_count"] == 1
    assert report["native_http_missing_outcome_count"] == 0
    assert report["native_http_disposition_accepted"] is False
    assert report["native_http_transport_provenance_verified"] is True
    assert report["native_http_source_replay_verified"] is False
    assert report["cleanup_accepted"] is report["runtime_authorized"] is False
    refs = report["native_http_lookup_refs"]
    assert isinstance(refs, list) and len(refs) == 1
    event = owner.journal.read_event(refs[0]["sequence"])
    assert event is not None
    envelope, raw = event
    assert envelope.source_id == "ALPACA_NATIVE_OCO_HTTP_RECOVERY"
    assert envelope.payload_ref == refs[0]["hash"]
    evidence = json.loads(raw)
    assert evidence["attempt_ref"] == original["attempt_ref"]
    assert evidence["http_status"] == 404
    assert base64.b64decode(evidence["body_base64"]) != b""
    assert evidence["retry_authorized"] is False
    assert evidence["ownership_accepted"] is evidence["protection_accepted"] is False
    assert evidence["origin"] == "INJECTED_EXCHANGE"
    assert all(method == "GET" for method, _path, _body in lookup.calls)
    assert submit(native_http) == original
    assert len(exchange.posts) == 1
    assert owner.requests.owner.view() == allocation
    assert owner.journal.read_account_risk_events() == risk_events


def test_crash_after_fence_before_exchange_restart_never_posts(
    native_http: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _harness, _at, exchange, _formal = native_http

    class ProcessLost(BaseException):
        pass

    request = PaperOrderHttp._request

    def lost(
        self: PaperOrderHttp, method: str, path: str, body: bytes | None
    ) -> object:
        if method == "POST":
            raise ProcessLost
        return request(self, method, path, body)

    with monkeypatch.context() as patch:
        patch.setattr(PaperOrderHttp, "_request", lost)
        with pytest.raises(ProcessLost):
            submit(native_http)
    recovered = NativeOcoHttp(owner.requests)
    original = recovered.view()[0]
    assert original["outcome"] is None
    assert submit((recovered, *native_http[1:])) == original
    assert exchange.posts == []


def test_changed_account_refuses_before_native_fence(
    native_http: NativeFixture,
) -> None:
    owner, _harness, _at, exchange, _formal = native_http
    exchange.account = ACCOUNT | {"id": "44444444-4444-4444-8444-444444444444"}
    with pytest.raises(ValueError, match="identity differs"):
        submit(native_http)
    assert owner.view() == []
    assert exchange.posts == []
    assert owner.journal.read_order_send_started_at("native-cid-a") is None
    assert not owner.journal.read_state(owner.state_key)
