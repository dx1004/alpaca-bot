"""Injected source integration updates actual OCO model, never broker authority."""

import json
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any, cast

import pytest

from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.native_oco_capture import capture_native_oco_group
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import Execution, LotPortion
from alpaca_ross_bot.paper_order_http import PaperHttpResponse
from test_native_oco_correlation_integration import ROOT, STOP, ReportedExchange
from test_native_oco_http import api, submit
from test_native_oco_http import native_http as native_http


def post(fixture: Any, *, root_only: bool = False) -> ReportedExchange:
    owner, _harness, at, exchange, _formal = fixture
    prepared = owner.requests.view()["requests"][0]["receipt"]
    reported = ReportedExchange(prepared["request_body"], at)
    response = reported.order | ({"legs": None} if root_only else {})
    exchange.response = PaperHttpResponse(201, json.dumps(response).encode())
    submit(fixture)
    return reported


def fill(
    reported: ReportedExchange, at: datetime, *, target: str = "0", stop: str = "0"
) -> None:
    for order_id, quantity in ((ROOT, target), (STOP, stop)):
        row = reported.order if order_id == ROOT else reported.order["legs"][0]
        row.update(filled_qty=quantity, updated_at=at.isoformat())
        if Fraction(quantity):
            reported.fills[order_id] = [
                {
                    "id": "activity-" + order_id,
                    "activity_type": "FILL",
                    "order_id": order_id,
                    "symbol": "AAPL",
                    "side": "sell",
                    "qty": quantity,
                    "price": "11.03" if order_id == ROOT else "9.50",
                    "transaction_time": at.isoformat(),
                }
            ]
        else:
            reported.fills[order_id] = []


def capture(fixture: Any, reported: ReportedExchange, at: datetime) -> int:
    owner = fixture[0]
    return capture_native_oco_group(
        api(cast(Any, reported)),
        owner.journal,
        account_ref=owner.binding["account_ref"],
        broker_order_id=ROOT,
        clock=lambda: at,
        max_fill_pages=3,
    )


def bind(fixture: Any, seq: int, at: datetime, event: str = "exit-a") -> Any:
    return fixture[0].requests.owner.bind_native_exit_census(
        event, "group-a", seq, at, timedelta(seconds=30), at
    )


def test_source_native_exit_updates_exact_model_and_preserves_risk(
    native_http: Any,
) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    risk = owner.journal.read_account_risk_events()
    portions = owner.view().model.groups[0].portions
    fill(reported, at, target="0.5")
    seq = capture(native_http, reported, at)
    view = bind(native_http, seq, at)
    assert view.model.exit_quantity == Fraction(1, 2)
    assert view.model.owned_quantity == view.model.entry_quantity - Fraction(1, 2)
    assert view.model.groups[0].portions == portions
    assert view.model.groups[0].acknowledgement_id == ROOT
    receipt = view.native_exit_bindings[0]
    assert receipt["status"] == "BOUND_NATIVE_EXIT_CENSUS"
    assert receipt["source_classification"] == "INJECTED_TEST_SOURCE"
    assert (
        receipt["broker_ownership_accepted"]
        is receipt["risk_release_authorized"]
        is False
    )
    execution = view.model.executions[-1]
    assert execution.execution_id.startswith("native-fill:")
    assert json.loads(execution.execution_id.removeprefix("native-fill:"))[1] == ROOT
    assert execution.role == "TARGET" and execution.quantity == Fraction(1, 2)
    assert owner.journal.read_account_risk_events() == risk
    assert owner.verify_rebuild()
    before = owner.journal.read_oco_research_events()
    assert bind(native_http, seq, at) == view
    assert owner.journal.read_oco_research_events() == before
    assert OcoJournal(owner.journal, owner.initial).view() == view


def test_complete_double_exit_is_imported_as_incident(native_http: Any) -> None:
    reported = post(native_http)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="1", stop="1")
    view = bind(native_http, capture(native_http, reported, at), at)
    assert view.model.exit_quantity == 2
    assert view.model.group_residual("group-a") == -1
    assert view.model.incidents
    assert view.native_exit_bindings[0]["status"] == "BOUND_NATIVE_EXIT_CENSUS"
    assert (
        "GROUP_FILLS_EXCEED_ROOT_REPORTED_QUANTITY"
        in view.native_exit_bindings[0]["issues"]
    )


def test_stop_and_later_distinct_fill_extend_census_without_risk_release(
    native_http: Any,
) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    risk = owner.journal.read_account_risk_events()
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, stop="0.5")
    first = bind(native_http, capture(native_http, reported, at), at)
    later = at + timedelta(seconds=1)
    reported.fills[STOP].append(
        reported.fills[STOP][0]
        | {"id": "later-stop-fill", "transaction_time": later.isoformat()}
    )
    reported.order["legs"][0].update(filled_qty="1", updated_at=later.isoformat())
    second = bind(native_http, capture(native_http, reported, later), later, "exit-b")
    assert second.model.exit_quantity == 1
    assert second.model.group_residual("group-a") == 0
    assert len([e for e in second.model.executions if e.role == "STOP"]) == 2
    assert all(e in second.model.executions for e in first.model.executions)
    assert second.native_exit_bindings[0]["risk_qualified"] is False
    assert owner.journal.read_account_risk_events() == risk
    assert owner.verify_rebuild()


def test_changed_fill_census_is_quarantined_without_erasing_exits(
    native_http: Any,
) -> None:
    reported = post(native_http)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    first = bind(native_http, capture(native_http, reported, at), at)
    later = at + timedelta(seconds=1)
    fill(reported, later, target="0.25")
    second = bind(native_http, capture(native_http, reported, later), later, "exit-b")
    assert second.model.executions == first.model.executions
    assert "NATIVE_EXIT_CENSUS_QUARANTINED" in second.issues
    assert (
        "NATIVE_EXIT_FILL_REGRESSION_OR_CONFLICT"
        in second.native_exit_bindings[0]["issues"]
    )
    with pytest.raises(ValueError):
        native_http[0].requests.owner.allocate_group(
            "group-b",
            (LotPortion(first.model.executions[0].execution_id, Fraction(1)),),
            later,
        )


def test_incomplete_capture_keeps_raw_positive_facts_without_zero_adoption(
    native_http: Any,
) -> None:
    reported = post(native_http)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.order["filled_qty"] = "0.75"
    view = bind(native_http, capture(native_http, reported, at), at)
    assert view.model.exit_quantity == 0
    receipt = view.native_exit_bindings[0]
    assert receipt["status"] == "QUARANTINED_NATIVE_EXIT_CENSUS"
    assert receipt["executions"][0]["quantity"] == "1/2"
    assert "NATIVE_EXIT_CENSUS_QUARANTINED" in view.issues
    assert view.model.groups[0].acknowledgement == "UNKNOWN"


def test_declared_exit_cannot_silently_gain_native_ownership(native_http: Any) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    owner.observe_execution(
        Execution(
            "declared-exit",
            owner.initial.account_ref,
            "AAPL",
            owner.initial.entry_scope,
            "TARGET",
            Fraction(1, 4),
            Fraction("11.03"),
            "group-a",
        ),
        at,
    )
    fill(reported, at, target="0.5")
    view = bind(native_http, capture(native_http, reported, at), at)
    assert view.model.exit_quantity == Fraction(1, 4)
    assert "DECLARED_NATIVE_EXIT_MIXING" in view.native_exit_bindings[0]["issues"]


def test_changed_request_latches_conflict_preserving_original(native_http: Any) -> None:
    reported = post(native_http)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    seq = capture(native_http, reported, at)
    original = bind(native_http, seq, at)
    changed = bind(native_http, seq, at + timedelta(seconds=1))
    assert changed.model == original.model
    assert changed.conflict_keys
    assert changed.native_exit_bindings == original.native_exit_bindings
    assert native_http[0].requests.owner.verify_rebuild()


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"null"])
def test_malformed_success_post_cannot_create_exit_binding(
    native_http: Any, body: bytes
) -> None:
    owner, _harness, at, exchange, _formal = native_http
    prepared = owner.requests.view()["requests"][0]["receipt"]
    reported = ReportedExchange(prepared["request_body"], at)
    exchange.response = PaperHttpResponse(201, body)
    submit(native_http)
    before = owner.requests.owner.view()
    later = at + timedelta(seconds=1)
    with pytest.raises(JournalError, match="POST"):
        bind(native_http, capture(native_http, reported, later), later)
    assert owner.requests.owner.view() == before


def test_post_positive_get_zero_is_quarantined(native_http: Any) -> None:
    owner, _harness, at, exchange, _formal = native_http
    prepared = owner.requests.view()["requests"][0]["receipt"]
    reported = ReportedExchange(prepared["request_body"], at)
    reported.order["filled_qty"] = "0.5"
    exchange.response = PaperHttpResponse(201, json.dumps(reported.order).encode())
    submit(native_http)
    later = at + timedelta(seconds=1)
    fill(reported, later)
    view = bind(native_http, capture(native_http, reported, later), later)
    assert "POST_CAPTURE_SOURCE_REGRESSION" in view.native_exit_bindings[0]["issues"]
    assert "NATIVE_EXIT_CENSUS_QUARANTINED" in view.issues
    assert view.model.exit_quantity == 0
    assert view.native_exit_bindings[0]["risk_release_authorized"] is False


def test_fill_before_creation_is_quarantined(native_http: Any) -> None:
    reported = post(native_http)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.fills[ROOT][0]["transaction_time"] = (
        native_http[2] - timedelta(seconds=1)
    ).isoformat()
    view = bind(native_http, capture(native_http, reported, at), at)
    assert (
        "NATIVE_EXIT_FILL_PREDATES_ORDER_OR_PRECALL"
        in view.native_exit_bindings[0]["issues"]
    )
    assert view.model.exit_quantity == 0
    assert view.native_exit_bindings[0]["executions"][0]["quantity"] == "1/2"


def test_native_raw_activity_id_cannot_relabel_entry_activity(native_http: Any) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.fills[ROOT][0]["id"] = owner.view().model.executions[0].execution_id
    view = bind(native_http, capture(native_http, reported, at), at)
    assert (
        "NATIVE_EXIT_RAW_ACTIVITY_COLLIDES_WITH_ENTRY"
        in view.native_exit_bindings[0]["issues"]
    )
    assert view.model.exit_quantity == 0


def test_declared_entry_before_native_binding_quarantines_ownership(
    native_http: Any,
) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    owner.observe_execution(
        Execution(
            "manual-entry",
            owner.initial.account_ref,
            "AAPL",
            owner.initial.entry_scope,
            "ENTRY",
            Fraction(1),
            Fraction(10),
        ),
        at,
    )
    fill(reported, at, target="0.5")
    view = bind(native_http, capture(native_http, reported, at), at)
    assert "DECLARED_ENTRY_AFTER_BOUND_SOURCE" in view.native_exit_bindings[0]["issues"]
    assert view.model.exit_quantity == 0
    assert "NATIVE_EXIT_CENSUS_QUARANTINED" in view.issues


def test_declared_entry_after_native_binding_cannot_inflate_ownership(
    native_http: Any,
) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    original = bind(native_http, capture(native_http, reported, at), at)
    with pytest.raises(JournalError, match="declared executions"):
        owner.observe_execution(
            Execution(
                "manual-entry",
                owner.initial.account_ref,
                "AAPL",
                owner.initial.entry_scope,
                "ENTRY",
                Fraction(1),
                Fraction(10),
            ),
            at,
        )
    assert owner.view() == original


def test_root_only_post_adopts_independently_captured_nested_child(
    native_http: Any,
) -> None:
    reported = post(native_http, root_only=True)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    view = bind(native_http, capture(native_http, reported, at), at)
    assert view.model.exit_quantity == Fraction(1, 2)
    assert (
        view.native_exit_bindings[0]["identity_basis"] == "POST_ROOT_AND_GET_NESTED_LEG"
    )
    assert view.native_exit_bindings[0]["identity"]["stop_order_id"] == STOP


def test_root_only_post_wrong_get_stop_terms_are_quarantined(native_http: Any) -> None:
    reported = post(native_http, root_only=True)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.order["legs"][0]["stop_price"] = "9.49"
    view = bind(native_http, capture(native_http, reported, at), at)
    assert view.model.exit_quantity == 0
    assert (
        "POST_CAPTURE_IDENTITY_OR_TERMS_DIFFER"
        in view.native_exit_bindings[0]["issues"]
    )


def test_present_post_child_cannot_change_identity_at_get(native_http: Any) -> None:
    reported = post(native_http)
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    reported.order["legs"][0]["id"] = "bbbbbbbb-4444-4444-8444-444444444444"
    view = bind(native_http, capture(native_http, reported, at), at)
    assert view.model.exit_quantity == 0
    assert (
        "POST_CAPTURE_IDENTITY_OR_TERMS_DIFFER"
        in view.native_exit_bindings[0]["issues"]
    )


def test_second_group_root_only_post_uses_get_child_without_null_dereference(
    native_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpaca_ross_bot import paper_order_http

    first = post(native_http, root_only=True)
    http, harness, initial_at, exchange, formal = native_http
    owner = http.requests.owner
    at = cast(datetime, initial_at) + timedelta(seconds=1)
    bind(native_http, capture(native_http, first, at), at)
    entry_id = owner.view().model.executions[0].execution_id
    owner.allocate_group("group-b", (LotPortion(entry_id, Fraction(1)),), at)
    prepared = http.requests.prepare("group-b", "native-cid-b", at, at)
    second = ReportedExchange(prepared["receipt"]["request_body"], at)
    second.order["id"] = "aaaaaaaa-4444-4444-8444-444444444444"
    second.order["group_id"] = "cccccccc-4444-4444-8444-444444444444"
    second.order["legs"][0].update(
        id="bbbbbbbb-4444-4444-8444-444444444444", group_id=second.order["group_id"]
    )
    exchange.response = PaperHttpResponse(
        201, json.dumps(second.order | {"legs": None}).encode()
    )

    class Clock:
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return at

    monkeypatch.setattr(paper_order_http, "datetime", Clock)
    harness.clock = lambda: at
    api(exchange).post_native_oco_once(
        http,
        harness,
        formal_api=formal,
        group_id="group-b",
        decision_at=at,
        started_at=at,
        clock=lambda: at,
    )
    later = at + timedelta(seconds=1)
    seq = capture_native_oco_group(
        api(cast(Any, second)),
        owner.journal,
        account_ref=http.binding["account_ref"],
        broker_order_id=second.order["id"],
        clock=lambda: later,
        max_fill_pages=3,
    )
    view = owner.bind_native_exit_census(
        "exit-b", "group-b", seq, later, timedelta(seconds=30), later
    )
    assert len(view.native_exit_bindings) == 2
    assert all(
        receipt["status"] == "BOUND_NATIVE_EXIT_CENSUS"
        for receipt in view.native_exit_bindings
    )


def test_prior_local_conflict_preserves_economic_exit_facts_and_risk_veto(
    native_http: Any,
) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    risk = owner.journal.read_account_risk_events()
    entry = owner.view().model.executions[0].execution_id
    original_portions = owner.view().model.groups[0].portions
    conflict = owner.allocate_group("group-a", (LotPortion(entry, Fraction(2)),), at)
    assert conflict.conflict_keys
    fill(reported, at, target="0.5")
    view = bind(native_http, capture(native_http, reported, at), at)
    assert view.model.exit_quantity == Fraction(1, 2)
    assert view.conflict_keys == conflict.conflict_keys
    assert view.model.groups[0].portions == original_portions
    with pytest.raises(JournalError, match="conflict"):
        owner.allocate_group("group-b", (LotPortion(entry, Fraction(1)),), at)
    assert owner.journal.read_account_risk_events() == risk
    assert view.native_exit_bindings[0]["risk_qualified"] is False


@pytest.mark.parametrize("variant", ["legacy", "wrong-origin"])
def test_observation_only_or_foreign_origin_capture_cannot_adopt(
    native_http: Any, variant: str
) -> None:
    reported = post(native_http)
    owner = native_http[0].requests.owner
    at = native_http[2] + timedelta(seconds=1)
    fill(reported, at, target="0.5")
    seq = capture(native_http, reported, at)
    stored = owner.journal.read_event(seq)
    assert stored is not None
    payload = json.loads(stored[1])
    if variant == "legacy":
        payload["schema"] = 1
        del payload["transport_origin"]
    else:
        payload["transport_origin"] = "BUILTIN_PAPER_HTTPS"
    duplicate = owner.journal.append_next(
        source_id="ALPACA_NATIVE_OCO",
        feed="paper",
        event_kind="NATIVE_OCO_CAPTURE",
        payload=json.dumps(payload).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence
    before = owner.view()
    with pytest.raises(JournalError):
        bind(native_http, duplicate, at)
    assert owner.view() == before


def test_unsuccessful_post_never_promotes_matching_get_into_owned_exits(
    native_http: Any,
) -> None:
    owner, _harness, at, exchange, _formal = native_http
    prepared = owner.requests.view()["requests"][0]["receipt"]
    reported = ReportedExchange(prepared["request_body"], at)
    exchange.response = PaperHttpResponse(503, b"unknown")
    submit(native_http)
    later = at + timedelta(seconds=1)
    fill(reported, later, target="0.5")
    before = owner.requests.owner.view()
    with pytest.raises(JournalError, match="unresolved native POST"):
        bind(native_http, capture(native_http, reported, later), later)
    assert owner.requests.owner.view() == before
