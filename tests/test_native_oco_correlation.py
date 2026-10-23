"""Synthetic immutable association evidence; no transport or exit adoption."""

import base64
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from threading import Barrier
from typing import Any
from unittest.mock import Mock

import pytest

import test_native_oco_requests
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_correlation import NativeOcoCorrelations
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from test_account_risk_owner import SourceAccount, account_sources
from test_observation_candidate_binding import AT, CID, new_decision
from test_oco_entry_evidence import _initial, _prepare
from test_risk_ledger_audit import ACCOUNT, POLICY, REF

ROOT = "aaaaaaaa-1111-4111-8111-111111111111"
STOP = "bbbbbbbb-2222-4222-8222-222222222222"
GROUP = "cccccccc-3333-4333-8333-333333333333"
partial_prepared = test_native_oco_requests.prepared


@pytest.fixture
def prepared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime]]:
    record = OcoJournal._record

    def timed(
        self: OcoJournal, kind: str, operation: dict[str, object], at: datetime
    ) -> object:
        return record(self, kind, operation, AT if kind == "BIND" else at)

    monkeypatch.setattr(OcoJournal, "_record", timed)
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        at = _prepare(source, risk, new_decision()) + timedelta(seconds=1)
        source.order(CID, at, "filled", 4)
        source.fill(CID, at, 2, "10")
        risk.observe("full-entry", source.selection(at, cash="460", position=4), at)
        owner = OcoJournal(source.journal, _initial())
        bound = owner.bind_entry_census("full-entry", at, POLICY, at)
        owner.allocate_group(
            "group-a",
            tuple(
                LotPortion(e.execution_id, e.quantity) for e in bound.model.executions
            ),
            at,
        )
        requests = NativeOcoRequests(owner)
        requests.prepare("group-a", "native-a", at, at)
        yield source, owner, requests, at


def _rows(journal: Journal) -> tuple[object, ...]:
    return (
        tuple(journal._db.execute("SELECT * FROM events ORDER BY sequence")),
        tuple(journal._db.execute("SELECT * FROM state ORDER BY key")),
    )


def _order(
    at: datetime,
    *,
    target_fill: str = "0",
    stop_qty: str = "4",
    stop_fill: str = "0",
    root_id: str = ROOT,
) -> dict[str, Any]:
    shared: dict[str, Any] = {
        "group_id": GROUP,
        "symbol": "AAPL",
        "side": "sell",
        "time_in_force": "day",
        "status": "new",
        "created_at": at.isoformat(),
        "updated_at": at.isoformat(),
        "replaces": None,
        "replaced_by": None,
    }
    return shared | {
        "id": root_id,
        "client_order_id": "native-a",
        "group_role": "take_profit",
        "order_class": "oco",
        "type": "limit",
        "qty": "4",
        "filled_qty": target_fill,
        "limit_price": "11.03",
        "stop_price": None,
        "legs": [
            shared
            | {
                "id": STOP,
                "client_order_id": "different-stop-cid",
                "group_role": "stop_loss",
                "order_class": "simple",
                "type": "stop",
                "qty": stop_qty,
                "filled_qty": stop_fill,
                "stop_price": "9.50",
                "limit_price": None,
                "legs": None,
            }
        ],
    }


def _capture(journal: Journal, at: datetime, order: dict[str, Any]) -> int:
    def request(path: str, body: object) -> dict[str, Any]:
        return {
            "method": "GET",
            "path": path,
            "status": 200,
            "error": None,
            "body": base64.b64encode(json.dumps(body).encode()).decode(),
            "started_at": at.isoformat(),
            "completed_at": at.isoformat(),
        }

    requests = [
        request("/v2/account", ACCOUNT),
        request(f"/v2/orders/{order['id']}?nested=true", order),
    ]
    for row in (order, order["legs"][0]):
        path = (
            f"/v2/account/activities?activity_types=FILL&order_id={row['id']}"
            "&direction=asc&page_size=100"
        )
        if Fraction(row["filled_qty"]) > 0:
            activity = {
                "id": "activity-" + row["id"],
                "activity_type": "FILL",
                "order_id": row["id"],
                "symbol": "AAPL",
                "side": "sell",
                "qty": row["filled_qty"],
                "cum_qty": "999",
                "price": row["limit_price"] or row["stop_price"],
                "transaction_time": at.isoformat(),
            }
            requests.append(request(path, [activity]))
            path += "&page_token=" + activity["id"]
        requests.append(request(path, []))
    return journal.append_next(
        source_id="ALPACA_NATIVE_OCO",
        feed="paper",
        event_kind="NATIVE_OCO_CAPTURE",
        payload=json.dumps(
            {
                "schema": 1,
                "paper_endpoint": "https://paper-api.alpaca.markets",
                "account_ref": REF,
                "requested_order_id": order["id"],
                "started_at": at.isoformat(),
                "completed_at": at.isoformat(),
                "requests": requests,
            }
        ).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence


def _observe(
    owner: NativeOcoCorrelations,
    sequence: int,
    at: datetime,
    event: str = "observation",
) -> dict[str, Any]:
    return owner.observe(event, "group-a", sequence, at, timedelta(seconds=5), at)


@pytest.mark.parametrize(
    "target,stop_qty,stop_filled,relation",
    [
        ("0", "4", "0", "REQUESTED_QTY_REPORTED"),
        ("1.25", "2.75", "0", "OBSERVED_RESIDUAL_AFTER_TARGET_FILLS"),
        ("1.25", "2.75", "2.75", "OBSERVED_RESIDUAL_AFTER_TARGET_FILLS"),
    ],
)
def test_capture_local_quantity_relations_never_grant_send_or_protection(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    target: str,
    stop_qty: str,
    stop_filled: str,
    relation: str,
) -> None:
    source, oco, requests, at = prepared
    order = _order(at, target_fill=target, stop_qty=stop_qty, stop_fill=stop_filled)
    sequence = _capture(source.journal, at, order)
    owner = NativeOcoCorrelations(requests)
    oco_before = source.journal.read_oco_research_events()
    risk_before = source.journal.read_account_risk_events()
    result = _observe(owner, sequence, at)
    assert result["receipt"]["status"] == "MATCHED_REPORTED_FIELDS"
    assert result["receipt"]["quantity_relation"] == relation
    assert result["send_proven"] is False and result["ack_proven"] is False
    assert not any(result["authority"].values())
    assert not any(result["receipt"]["authority"].values())
    assert source.journal.read_order_send_started_at("native-a") is None
    assert source.journal.read_oco_research_events() == oco_before
    assert source.journal.read_account_risk_events() == risk_before
    assert oco.view().model.groups[0].acknowledgement == "UNKNOWN"
    view = owner.view()
    assert view is not None and len(view["assignments"]) == 1
    assert owner.verify_rebuild()


def test_unadjusted_stop_delta_is_unknown_and_preserves_original_assignment(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    owner = NativeOcoCorrelations(requests)
    first = _observe(owner, _capture(source.journal, at, _order(at)), at)
    later = at + timedelta(seconds=1)
    blocked = _observe(
        owner,
        _capture(source.journal, later, _order(later, target_fill="1.25")),
        later,
        "delta",
    )
    assert blocked["receipt"]["status"] == "BLOCKED"
    assert "QUANTITY_RELATION_UNQUALIFIED" in blocked["issues"]
    view = owner.view()
    assert (
        view is not None
        and view["assignments"][0]["association_ref"] == first["association_ref"]
    )


@pytest.mark.parametrize(
    "failure", ["price", "cid", "replacement", "overexit", "precreation"]
)
def test_mismatch_or_unresolved_source_is_retained_without_assignment(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    failure: str,
) -> None:
    source, _, requests, at = prepared
    order = _order(at)
    if failure == "price":
        order["limit_price"] = "12"
    elif failure == "cid":
        order["client_order_id"] = "someone-else"
    elif failure == "replacement":
        order["replaced_by"] = GROUP
    elif failure == "overexit":
        order["filled_qty"] = "4"
        order["legs"][0]["filled_qty"] = "1"
    else:
        order["created_at"] = (at - timedelta(microseconds=1)).isoformat()
    owner = NativeOcoCorrelations(requests)
    result = _observe(owner, _capture(source.journal, at, order), at)
    assert result["receipt"]["status"] == "BLOCKED" and result["issues"]
    view = owner.view()
    assert view is not None and view["assignments"] == []
    assert owner.verify_rebuild()


def test_changed_broker_identity_conflicts_without_overwriting_first_mapping(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    owner = NativeOcoCorrelations(requests)
    first = _observe(owner, _capture(source.journal, at, _order(at)), at)
    later = at + timedelta(seconds=1)
    result = _observe(
        owner,
        _capture(source.journal, later, _order(later, root_id=GROUP)),
        later,
        "changed",
    )
    assert result["receipt"]["status"] == "IDENTITY_CONFLICT"
    view = owner.view()
    assert view is not None and view["conflict_keys"]
    assert view["assignments"][0]["association_ref"] == first["association_ref"]


def test_two_prepared_groups_cannot_claim_same_broker_assignment(
    partial_prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, oco, requests, at = partial_prepared
    requests.prepare("group-a", "native-a", at, at)
    oco.allocate_group(
        "group-b",
        (LotPortion(oco.view().model.executions[0].execution_id, Fraction(1)),),
        at,
    )
    requests.prepare("group-b", "native-b", at, at)
    owner = NativeOcoCorrelations(requests)
    order = _order(at, stop_qty="1")
    order["qty"] = "1"
    first = _observe(owner, _capture(source.journal, at, order), at)
    order["client_order_id"] = "native-b"
    second = owner.observe(
        "second-group",
        "group-b",
        _capture(source.journal, at, order),
        at,
        timedelta(seconds=5),
        at,
    )
    assert first["receipt"]["status"] == "MATCHED_REPORTED_FIELDS"
    assert second["receipt"]["status"] == "IDENTITY_CONFLICT"
    assert "BROKER_ID_ASSIGNED_TO_OTHER_REQUEST" in second["issues"]
    view = owner.view()
    assert view is not None and len(view["assignments"]) == 1


def test_original_retry_and_history_survive_later_preparation_conflict(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    sequence = _capture(source.journal, at, _order(at))
    owner = NativeOcoCorrelations(requests)
    original = _observe(owner, sequence, at)
    horizon = source.journal.read_evidence_sequence()
    requests.prepare("group-a", "changed-cid", at, at + timedelta(seconds=1))
    historical = requests.read_verified_at(horizon, at)
    assert historical is not None and historical["conflict_groups"] == []
    before = _rows(source.journal)
    retry = owner.observe(
        "observation",
        "group-a",
        sequence,
        at,
        timedelta(seconds=5),
        at + timedelta(days=1),
    )
    assert retry["receipt"] == original["receipt"]
    assert retry["association_ref"] == original["association_ref"]
    assert "PREPARATION_IDENTITY_CONFLICT" in retry["issues"]
    assert _rows(source.journal) == before
    with pytest.raises(JournalError, match="preparation conflict"):
        _observe(owner, sequence, at, "new-id")
    assert _rows(source.journal) == before


def test_changed_command_is_sticky_and_identical_conflict_deduplicates(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    owner = NativeOcoCorrelations(requests)
    sequence = _capture(source.journal, at, _order(at))
    original = _observe(owner, sequence, at)
    changed = owner.observe(
        "observation", "group-a", sequence, at, timedelta(seconds=6), at
    )
    before = _rows(source.journal)
    assert changed["receipt"] == original["receipt"] and changed["issues"]
    assert (
        owner.observe("observation", "group-a", sequence, at, timedelta(seconds=6), at)
        == changed
    )
    assert _rows(source.journal) == before
    assert owner.verify_rebuild()


def test_new_observation_cannot_skip_unavailable_scoped_prefix(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, _, requests, at = prepared
    owner = NativeOcoCorrelations(requests)
    sequence = _capture(source.journal, at, _order(at))
    owner.observe(
        "first",
        "group-a",
        sequence,
        at,
        timedelta(seconds=5),
        at + timedelta(seconds=1),
    )
    before = _rows(source.journal)
    with pytest.raises(JournalError, match="prefix is not decision-visible"):
        owner.observe(
            "earlier-decision",
            "group-a",
            sequence,
            at,
            timedelta(seconds=5),
            at + timedelta(seconds=1),
        )
    assert _rows(source.journal) == before


@pytest.mark.parametrize("fault", ["cache", "dedupe", "raw", "source"])
def test_corruption_aborts_replay_without_repair(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    fault: str,
) -> None:
    source, _, requests, at = prepared
    owner = NativeOcoCorrelations(requests)
    sequence = _capture(source.journal, at, _order(at))
    original = _observe(owner, sequence, at)
    if fault in {"cache", "dedupe"}:
        key = owner.state_key
        if fault == "dedupe":
            suffix = next(
                iter(source.journal.read_native_oco_correlation_evidence_cache(REF))
            )
            key = "native_oco_correlation_evidence:" + REF + ":" + suffix
        source.journal._db.execute("DELETE FROM state WHERE key=?", (key,))
    else:
        source.journal._db.execute("DROP TRIGGER events_no_update")
        target = (
            sequence if fault == "source" else original["association_ref"]["sequence"]
        )
        source.journal._db.execute(
            "UPDATE events SET payload=? WHERE sequence=?", (b"{}", target)
        )
    source.journal._db.commit()
    before = _rows(source.journal)
    with pytest.raises(JournalError):
        owner.verify_rebuild()
    assert _rows(source.journal) == before


def test_commit_failure_cannot_publish_association_and_physical_restart_rebuilds(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, oco, requests, at = prepared
    owner = NativeOcoCorrelations(requests)
    sequence = _capture(source.journal, at, _order(at))
    before = _rows(source.journal)
    original = source.journal._db
    broken = Mock(wraps=original)
    broken.commit.side_effect = OSError("synthetic commit failure")
    monkeypatch.setattr(source.journal, "_db", broken)
    with pytest.raises(OSError, match="commit failure"):
        _observe(owner, sequence, at)
    monkeypatch.setattr(source.journal, "_db", original)
    assert _rows(source.journal) == before
    result = _observe(owner, sequence, at)
    root, path = source.journal._runtime_root, source.journal._path
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        recovered = NativeOcoCorrelations(
            NativeOcoRequests(OcoJournal(reopened, oco.initial))
        )
        assert recovered.view() is not None
        assert (
            recovered.observe(
                "observation", "group-a", sequence, at, timedelta(seconds=5), at
            )["receipt"]
            == result["receipt"]
        )
        assert recovered.verify_rebuild()
    finally:
        reopened.close()


def test_racing_incompatible_observations_keep_only_one_assignment(
    prepared: tuple[SourceAccount, OcoJournal, NativeOcoRequests, datetime],
) -> None:
    source, oco, _, at = prepared
    first = _capture(source.journal, at, _order(at))
    second = _capture(source.journal, at, _order(at, root_id=GROUP))
    root, path, initial = (
        source.journal._runtime_root,
        source.journal._path,
        oco.initial,
    )
    barrier = Barrier(2)

    def observe(pair: tuple[str, int]) -> str:
        journal = Journal.open_private(path, root)
        try:
            owner = NativeOcoCorrelations(
                NativeOcoRequests(OcoJournal(journal, initial))
            )
            barrier.wait(timeout=10)
            return str(_observe(owner, pair[1], at, pair[0])["receipt"]["status"])
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(observe, [("one", first), ("two", second)])) == [
            "IDENTITY_CONFLICT",
            "MATCHED_REPORTED_FIELDS",
        ]
