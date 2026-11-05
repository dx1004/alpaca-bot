"""Recovery observes consumed source fences without replay or authority."""

import base64
import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot import paper_order_http
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.paper_engineering import EngineeringHarness
from alpaca_ross_bot.paper_order_http import PaperHttpResponse, PaperOrderHttp
from alpaca_ross_bot.source_close_http import SourceCloseHttp, source_http_inventory
from test_paper_engineering import scope
from test_paper_order_recovery import NOW, POLICY, FakeExchange, response
from test_paper_order_recovery import api as recovery_api
from test_risk_ledger_audit import ACCOUNT
from test_source_close_http import api as source_api
from test_source_close_http import fake, submit
from test_source_close_http import source_http as source_http_fixture

source_http = source_http_fixture


class Lookup(FakeExchange):
    def __init__(self, cid: str) -> None:
        super().__init__()
        self.cid = cid
        self.account_views = [response(ACCOUNT)]
        self.result: PaperHttpResponse | Exception = PaperHttpResponse(404, b"missing")
        self.after_lookup: Any = None
        self.source_calls = 0

    def __call__(self, method: str, path: str, *args: Any) -> PaperHttpResponse:
        parsed = urlsplit(path)
        if parsed.path == "/v2/orders:by_client_order_id" and parse_qs(parsed.query)[
            "client_order_id"
        ] == [self.cid]:
            assert method == "GET" and args[1] is None
            self.calls.append((method, path, None))
            self.source_calls += 1
            if self.after_lookup is not None:
                self.after_lookup()
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        return super().__call__(method, path, *args)


def evidence(harness: EngineeringHarness, report: dict[str, object]) -> dict[str, Any]:
    refs = report["source_http_lookup_refs"]
    assert isinstance(refs, list) and len(refs) == 1
    row = harness.journal.read_event(refs[0]["sequence"])
    assert row is not None and row[0].payload_ref == refs[0]["hash"]
    assert row[0].source_id == "ALPACA_SOURCE_CLOSE_HTTP_RECOVERY"
    return cast(dict[str, Any], json.loads(row[1]))


@pytest.mark.parametrize("mode", ["timeout", "rejected", "missing_outcome"])
def test_source_fence_recovery_get_only_restart_and_identity_faults(
    source_http: Any, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    owner, harness, kwargs, exchange, _, _, ready, _ = source_http
    cid = kwargs["client_order_id"]
    if mode == "missing_outcome":
        original_request = PaperOrderHttp._request

        def lost(
            self: PaperOrderHttp, method: str, path: str, body: bytes | None
        ) -> PaperHttpResponse:
            if method == "POST":
                raise KeyboardInterrupt
            return original_request(self, method, path, body)

        with monkeypatch.context() as context:
            context.setattr(PaperOrderHttp, "_request", lost)
            with pytest.raises(KeyboardInterrupt):
                submit(source_http)
        original = source_http_inventory(owner.journal)[0]
        assert original["outcome"] is None and not exchange.posts
    else:
        exchange.response = (
            TimeoutError("private exception text")
            if mode == "timeout"
            else PaperHttpResponse(422, b"rejected")
        )
        original = submit(source_http)
    risk_before = owner.journal.read_account_risk_events()
    source_before = source_http_inventory(owner.journal)
    model_before, emergency_before = ready[2].view(), ready[3].view()
    lookup = Lookup(cid)
    transport = recovery_api(lookup)

    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("recovery constructed a source sender or opened HTTPS")

    with monkeypatch.context() as context:
        context.setattr(SourceCloseHttp, "__init__", deny)
        report = harness.recover(transport, POLICY)
    assert report["recovery_schema"] == 2
    assert (
        report["source_http_attempt_count"]
        == report["source_http_unresolved_count"]
        == 1
    )
    assert report["source_http_missing_outcome_count"] == (mode == "missing_outcome")
    assert report["tracked_exit_unresolved_count"] == 1
    assert lookup.source_calls == 1
    raw = evidence(harness, report)
    assert raw["attempt_ref"] == original["attempt_ref"]
    assert (
        raw["http_status"] == 404 and base64.b64decode(raw["body_base64"]) == b"missing"
    )
    assert raw["origin"] == "INJECTED_EXCHANGE" and raw["account_and_origin_reverified"]
    for key in (
        "ownership_accepted",
        "protection_accepted",
        "retry_authorized",
        "cancel_authorized",
        "cleanup_accepted",
        "runtime_authorized",
        "formal_accepted",
    ):
        assert raw[key] is False
    assert all(method == "GET" for method, _, _ in lookup.calls)
    assert source_http_inventory(owner.journal) == source_before
    assert owner.journal.read_account_risk_events() == risk_before
    assert ready[2].view() == model_before and ready[3].view() == emergency_before
    assert submit(source_http) == original
    assert len(exchange.posts) == (mode != "missing_outcome")

    if mode != "timeout":
        return
    # Reopen the actual engineering journal; the consumed fence remains untouched.
    reopened = EngineeringHarness(harness.root, harness.scope, clock=harness.clock)
    try:
        lookup.result = TimeoutError("secret must never be persisted")
        report = reopened.recover(transport, POLICY)
        raw = evidence(reopened, report)
        assert (
            raw["error_category"] == "PaperOrderHttpError"
            and raw["body_base64"] is None
        )
        assert "secret" not in json.dumps(raw)
        assert lookup.source_calls == 2
        lookup.result = PaperHttpResponse(200, b'{"diagnostic":"raw-only"}')
        report = reopened.recover(transport, POLICY)
        assert (
            base64.b64decode(evidence(reopened, report)["body_base64"])
            == lookup.result.body
        )
        assert report["source_http_disposition_accepted"] is False
        # Both a builtin origin and a configured-account mismatch fail pre-GET.
        before = reopened.journal.read_evidence_sequence()
        with monkeypatch.context() as context:
            context.setattr(paper_order_http, "HTTPSConnection", deny)
            builtin = source_api(None)
            assert builtin.native_http_origin == "BUILTIN_PAPER_HTTPS"
            with pytest.raises(JournalError, match="origin differs"):
                reopened.recover(builtin, POLICY)
        assert reopened.journal.read_evidence_sequence() == before
        transport._paper_account_ref = "different-account"
        with pytest.raises(JournalError, match="origin differs"):
            reopened.recover(transport, POLICY)
        transport._paper_account_ref = reopened.scope.account_ref
        lookup.after_lookup = lambda: setattr(transport, "_exchange", builtin._exchange)
        with monkeypatch.context() as context:
            context.setattr(paper_order_http, "HTTPSConnection", deny)
            with pytest.raises(JournalError, match="identity or clock changed"):
                reopened.recover(transport, POLICY)
        transport._exchange = lookup
        # Changing authenticated account after the lookup retains its raw bytes,
        # then refuses general reconciliation and never clears the source fence.
        lookup.after_lookup = lambda: setattr(
            lookup,
            "account_views",
            [response(ACCOUNT | {"id": "33333333-3333-4333-8333-333333333333"})],
        )
        with pytest.raises(JournalError, match="identity or clock changed"):
            reopened.recover(transport, POLICY)
        event, payload = reopened.journal._read_private_events(
            "ALPACA_SOURCE_CLOSE_HTTP_RECOVERY", None
        )[-1]
        fault = json.loads(payload)
        assert (
            fault["http_status"] == 200 and not fault["account_and_origin_reverified"]
        )
        assert fault["error_category"] == "PaperOrderHttpError"
        assert source_http_inventory(reopened.journal) == source_before
    finally:
        reopened.close()


def test_callback_fence_is_local_unresolved_and_legacy_profile_unchanged(
    source_http: Any, tmp_path: Path
) -> None:
    row = fake(source_http)
    lookup = Lookup(source_http[2]["client_order_id"])
    report = source_http[1].recover(recovery_api(lookup), POLICY)
    assert (
        report["source_callback_consumed_count"]
        == report["source_callback_unresolved_count"]
        == 1
    )
    assert (
        report["source_callback_lookup_performed"] is False and lookup.source_calls == 0
    )
    assert (
        report["source_http_lookup_refs"] == []
        and report["tracked_exit_unresolved_count"] == 1
    )
    assert row["precall"]["origin"] == "INJECTED_TEST_CALLBACK_NOT_BROKER_PROOF"
    legacy = EngineeringHarness(tmp_path / "legacy", scope(tmp_path), clock=lambda: NOW)
    try:
        result = legacy.recover(recovery_api(FakeExchange()), POLICY)
        assert (
            "recovery_schema" not in result
            and "source_http_attempt_count" not in result
        )
        assert result["native_http_unresolved_count"] == 0
    finally:
        legacy.close()
