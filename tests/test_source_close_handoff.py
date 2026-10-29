"""Local canonical source-close handoff; never transport/fill acceptance."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from threading import Barrier
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop import CrossedStopJournal
from alpaca_ross_bot.crossed_stop_preparation import source_close_handoff_lineage
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.oco_journal import OcoJournal
from test_crossed_stop_preparation import prepare
from test_crossed_stop_preparation import ready as ready
from test_observation_candidate_binding import CID


def handoff(ready: Any) -> tuple[dict[str, Any], str]:
    source, _risk, _owner, emergency, at, _assets, _status = ready
    prepare(ready, for_handoff=True)
    state = emergency.bind_source_intent("residual", at)
    binding = state["closes"]["residual"]["source_intent_binding"]
    intent = binding["intent"]
    cid = json.loads(intent["request_body"])["client_order_id"]
    assert source.journal.read_order_intent(cid) == intent
    return state, cid


def test_source_handoff_atomically_links_intent_and_same_risk_slot(ready: Any) -> None:
    source, risk, owner, emergency, at, _, _ = ready
    prior = risk.view()
    state, cid = handoff(ready)
    assert source.journal.read_order_intent_ids() == tuple(sorted((CID, cid)))
    intent = source.journal.read_order_intent(cid)
    assert set(intent) == {
        "paper_endpoint",
        "paper_account_ref",
        "purpose",
        "request_body",
    }
    assert json.loads(intent["request_body"])["qty"] == "2"
    assert not source.journal.has_order_send_attempt(cid)
    count = source.journal.read_evidence_sequence()
    assert emergency.bind_source_intent("residual", at) == state
    assert source.journal.read_evidence_sequence() == count
    risk.bind_exit("source-close-role", at + timedelta(seconds=1), cid, "CLOSE")
    bound = risk.view()
    assert bound["held"]["client_order_id"] == CID
    assert bound["held"]["exits"][cid] == "CLOSE"
    assert (
        bound["held"]["source_close_bindings"][cid]["portions"]
        == state["closes"]["residual"]["portions"]
    )
    assert bound["pending_open_loss_reserve"] == prior["pending_open_loss_reserve"]
    assert not bound["held"]["source_close_bindings"][cid][
        "source_fill_ownership_accepted"
    ]
    with pytest.raises(JournalError, match="source emergency"):
        source.journal.mark_order_send_attempt(cid, at + timedelta(seconds=2))
    with pytest.raises(JournalError, match="declared exit inputs"):
        emergency.observe_exit(
            "residual", "unowned-execution", Fraction(1), at + timedelta(seconds=2)
        )
    assert emergency.view()["closes"]["residual"]["filled"] == "0"
    assert (
        AccountRiskOwner(source.journal, risk.account_ref, risk.fee_policy).view()
        == bound
    )
    assert owner.view().model.owned_quantity == 2


def test_source_handoff_append_failure_rolls_back_intent_and_receipt(
    ready: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, risk, _, emergency, at, _, _ = ready
    prepared = prepare(ready, for_handoff=True)
    cid = prepared["closes"]["residual"]["request"]["client_order_id"]
    before = risk.view()
    original = source.journal._append_locked

    def fail(event: Any, *args: Any, **kwargs: Any) -> Any:
        if event.event_kind == "SOURCE_BIND_INTENT":
            raise OSError("synthetic source handoff storage fault")
        return original(event, *args, **kwargs)

    monkeypatch.setattr(source.journal, "_append_locked", fail)
    with pytest.raises(OSError, match="storage fault"):
        emergency.bind_source_intent("residual", at)
    assert source.journal.read_order_intent(cid) is None
    assert emergency.view() == prepared
    assert risk.view() == before
    monkeypatch.setattr(source.journal, "_append_locked", original)
    state = emergency.bind_source_intent("residual", at)
    assert state["closes"]["residual"]["source_intent_binding"][
        "intent"
    ] == source.journal.read_order_intent(cid)


def test_legacy_preparation_cannot_silently_upgrade_to_handoff(ready: Any) -> None:
    source, _, _, emergency, at, _, _ = ready
    before = prepare(ready)
    assert "reservation" not in before["closes"]["residual"]["source_preparation"]
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="exact handoff reservation"):
        emergency.bind_source_intent("residual", at)
    assert source.journal.read_evidence_sequence() == count
    assert source.journal.read_order_intent_ids() == (CID,)
    assert emergency.view() == before


def test_handoff_restarts_then_role_binds_at_fresh_clock_without_rewriting_intent(
    ready: Any,
) -> None:
    source, risk, owner, emergency, at, _, _ = ready
    state, cid = handoff(ready)
    path, root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        other_owner = OcoJournal(reopened, owner.initial)
        other = CrossedStopJournal(other_owner)
        assert other.view() == state
        count = reopened.read_evidence_sequence()
        assert other.bind_source_intent("residual", at) == state
        assert reopened.read_evidence_sequence() == count
        other_risk = AccountRiskOwner(reopened, risk.account_ref, risk.fee_policy)
        later = at + timedelta(seconds=3)
        other_risk.bind_exit("recovered-role", later, cid, "CLOSE")
        bound = other_risk.view()
        assert bound is not None
        assert bound["held"]["client_order_id"] == CID
        assert bound["held"]["exits"][cid] == "CLOSE"
        count = reopened.read_evidence_sequence()
        other_risk.bind_exit("recovered-role", later, cid, "CLOSE")
        assert reopened.read_evidence_sequence() == count
        assert not reopened.has_order_send_attempt(cid)
    finally:
        reopened.close()


def test_source_close_role_reducer_refuses_another_current_slot(ready: Any) -> None:
    source, risk, _, _, at, _, _ = ready
    _, cid = handoff(ready)
    prior = risk.view()
    risk.bind_exit("original-role", at + timedelta(seconds=1), cid, "CLOSE")
    event, raw = source.journal.read_account_risk_events()[-1]
    wrong_slot = copy.deepcopy(prior)
    wrong_slot["held"]["client_order_id"] = "new-same-symbol-slot"
    # A negative reducer fault case, not a claim that the still-blocked source
    # close/finality pipeline can already finish A and create B operationally.
    with pytest.raises(JournalError, match="cannot be reparented"):
        risk._reduce(wrong_slot, json.loads(raw), False, event.sequence)
    assert risk.view()["held"]["client_order_id"] == CID


@pytest.mark.parametrize(
    "case",
    ["extra_event", "wrong_key", "boolean_horizon", "boolean_schema", "changed_lots"],
)
def test_hash_consistent_lineage_mutation_cannot_bind_role(
    ready: Any, case: str
) -> None:
    source, _, _, _, at, _, _ = ready
    _, cid = handoff(ready)
    event, raw = next(
        (event, raw)
        for event, raw in source.journal.read_crossed_stop_events()
        if event.event_kind == "SOURCE_BIND_INTENT"
    )
    item = json.loads(raw)
    key = item["key"]
    if case == "extra_event":
        item["unexpected"] = False
    elif case == "wrong_key":
        item["key"] = json.dumps(
            [item["binding"]["entry_scope"], item["kind"], "wrong-close"],
            separators=(",", ":"),
        )
    elif case == "boolean_horizon":
        item["horizon"] = True
    elif case == "boolean_schema":
        item["data"]["receipt"]["schema"] = True
    else:
        item["data"]["receipt"]["portions"] = {"not-the-owned-lot": "2"}
    payload = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    source.journal._db.execute("DROP TRIGGER events_no_update")
    source.journal._db.execute(
        "UPDATE events SET payload=?,payload_ref=? WHERE sequence=?",
        (payload, digest, event.sequence),
    )
    cache_key = (
        "crossed_stop_evidence:" + source.journal.read_paper_account_ref() + ":" + key
    )
    updated = source.journal._db.execute(
        "UPDATE state SET value_json=? WHERE key=?",
        (json.dumps({"hash": digest, "sequence": event.sequence}), cache_key),
    )
    assert updated.rowcount == 1
    source.journal._db.commit()
    expected = (
        "anchored reservation"
        if case == "changed_lots"
        else "schema|key|identity|metadata|receipt"
    )
    with pytest.raises(JournalError, match=expected):
        source_close_handoff_lineage(
            source.journal,
            cid,
            through_sequence=source.journal.read_evidence_sequence(),
            at=at,
        )
    assert not source.journal.has_order_send_attempt(cid)


def test_journal_primitive_cannot_expand_anchored_close_quantity(ready: Any) -> None:
    source, _, _, emergency, at, _, _ = ready
    prepared = prepare(ready, for_handoff=True)
    close = prepared["closes"]["residual"]
    request = dict(close["request"], qty="3")
    cid = request["client_order_id"]
    ref_event, _ = next(
        (event, raw)
        for event, raw in source.journal.read_crossed_stop_events()
        if event.event_kind == "SOURCE_PREPARE"
    )
    intent = {
        "paper_endpoint": "https://paper-api.alpaca.markets",
        "paper_account_ref": emergency.binding["account_ref"],
        "purpose": "ENGINEERING_TEST",
        "request_body": json.dumps(request, sort_keys=True, separators=(",", ":")),
    }
    source.journal._db.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(JournalError, match="anchored reservation"):
            source.journal._reserve_source_close_intent_locked(
                cid,
                intent,
                preparation_ref=[ref_event.sequence, ref_event.payload_ref],
                binding=emergency.binding,
                close_id="residual",
            )
    finally:
        source.journal._db.rollback()
    assert source.journal.read_order_intent(cid) is None
    assert emergency.view() == prepared


def test_role_lineage_keeps_original_prefix_after_later_handoff_conflict(
    ready: Any,
) -> None:
    source, risk, _, emergency, at, _, _ = ready
    _, cid = handoff(ready)
    later = at + timedelta(seconds=1)
    risk.bind_exit("historical-role", later, cid, "CLOSE")
    _, raw = source.journal.read_account_risk_events()[-1]
    horizon = json.loads(raw)["source_horizon"]
    original = source_close_handoff_lineage(
        source.journal, cid, through_sequence=horizon, at=later
    )
    emergency.bind_source_intent("residual", at + timedelta(seconds=2))
    assert (
        source_close_handoff_lineage(
            source.journal, cid, through_sequence=horizon, at=later
        )
        == original
    )
    with pytest.raises(JournalError, match="duplicate/conflicting"):
        source_close_handoff_lineage(
            source.journal,
            cid,
            through_sequence=source.journal.read_evidence_sequence(),
            at=at + timedelta(seconds=3),
        )
    count = len(source.journal.read_account_risk_events())
    risk.bind_exit("historical-role", later, cid, "CLOSE")
    assert len(source.journal.read_account_risk_events()) == count


@pytest.mark.parametrize("value", [1, "yes", None])
def test_handoff_marker_rejects_truthy_nonboolean_options(
    ready: Any, value: Any
) -> None:
    with pytest.raises(JournalError, match="explicit boolean"):
        prepare(ready, for_handoff=value)


def test_preclaimed_identical_intent_is_not_adopted_without_handoff(ready: Any) -> None:
    source, _, _, emergency, at, _, _ = ready
    prepared = prepare(ready, for_handoff=True)
    close = prepared["closes"]["residual"]
    cid = close["request"]["client_order_id"]
    intent = {
        "paper_endpoint": "https://paper-api.alpaca.markets",
        "paper_account_ref": emergency.binding["account_ref"],
        "purpose": "ENGINEERING_TEST",
        "request_body": json.dumps(
            close["request"], sort_keys=True, separators=(",", ":")
        ),
    }
    # Storage-fault injection: an identical orphan is not an authenticated
    # handoff and cannot be silently adopted by the new public operation.
    source.journal._db.execute(
        "INSERT INTO order_intents(client_order_id,intent_json) VALUES (?,?)",
        (cid, json.dumps(intent)),
    )
    source.journal._db.commit()
    with pytest.raises(JournalError, match="census changed|already reserved"):
        emergency.bind_source_intent("residual", at)
    assert source.journal.read_order_intent(cid) == intent
    assert emergency.view() == prepared
    assert not any(
        event.event_kind == "SOURCE_BIND_INTENT"
        for event, _ in source.journal.read_crossed_stop_events()
    )
    with pytest.raises(JournalError, match="lineage is missing"):
        source_close_handoff_lineage(
            source.journal,
            cid,
            through_sequence=source.journal.read_evidence_sequence(),
            at=at,
        )


def test_racing_independent_writer_connections_create_one_canonical_handoff(
    ready: Any,
) -> None:
    source, _, owner, emergency, at, _, _ = ready
    prepared = prepare(ready, for_handoff=True)
    cid = prepared["closes"]["residual"]["request"]["client_order_id"]
    path, root = source.journal._path, source.journal._runtime_root
    barrier = Barrier(2)

    def worker() -> dict[str, Any]:
        journal = Journal.open_private(path, root)
        try:
            # Test-only lock-wait budget, not a production latency guarantee.
            journal._db.execute("PRAGMA busy_timeout=60000")
            local = CrossedStopJournal(OcoJournal(journal, owner.initial))
            barrier.wait(timeout=60)
            return local.bind_source_intent("residual", at)
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(worker)
        two = pool.submit(worker)
        first, second = one.result(timeout=180), two.result(timeout=180)
    assert first == second == emergency.view()
    assert (
        sum(
            event.event_kind == "SOURCE_BIND_INTENT"
            for event, _ in source.journal.read_crossed_stop_events()
        )
        == 1
    )
    assert (
        source.journal.read_order_intent(cid)
        == first["closes"]["residual"]["source_intent_binding"]["intent"]
    )
    assert not source.journal.has_order_send_attempt(cid)


def test_reported_fill_without_source_send_fence_cannot_release_risk(
    ready: Any,
) -> None:
    """Injected reports cannot turn a local unsent handoff into owned disposal."""
    source, risk, _, emergency, at, _, _ = ready
    state, cid = handoff(ready)
    later = at + timedelta(seconds=1)
    risk.bind_exit("unsent-source-close-role", later, cid, "CLOSE")
    before = risk.view()
    assert before is not None
    # Source-shaped facts are deliberately unexpected: no send marker is planted
    # and no transport, permission check or source proof is mocked.
    source.order(cid, later, "filled", 2)
    source.fill(cid, later, 2, "9.49")
    from alpaca_ross_bot.incident_risk import IncidentRiskRequest
    from alpaca_ross_bot.order_reconciliation import RestBatch
    from alpaca_ross_bot.paper_order_http import PaperHttpResponse
    from alpaca_ross_bot.risk_account_inputs import ENDPOINT, KIND, SCHEMA, SOURCE
    from test_account_risk_owner import RECON_POLICY
    from test_risk_ledger_audit import ACCOUNT, REF, calendar, request

    # The ordinary SourceAccount.selection helper requires MATCHED and therefore
    # cannot exercise this deliberately inconsistent capture. Retain real REST
    # differences and an independently stored account capture instead.
    report = source.reconciler.apply_rest_batch(
        RestBatch(
            REF,
            later,
            later,
            source.reconciler.view()["watermark"],
            (),
            (),
            {
                key: PaperHttpResponse(200, json.dumps(row).encode())
                for key, row in source.orders.items()
            },
            {
                str(row["id"]): tuple(
                    fill for fill in source.fills if fill["order_id"] == row["id"]
                )
                for row in source.orders.values()
            },
            True,
            True,
        ),
        RECON_POLICY,
        at=later,
    )
    assert report.status == "DIFFERENCES"
    rows = [
        request(
            "/v2/account",
            ACCOUNT
            | {
                "cash": "498.98",
                "equity": "498.98",
                "buying_power": "498.98",
                "non_marginable_buying_power": "498.98",
                "multiplier": "1",
            },
            later,
        ),
        request("/v2/positions", [], later),
        request("/v2/orders?status=open&limit=500&nested=false", [], later),
        request(
            "/v2/account/activities?direction=desc&page_size=100", source.fills, later
        ),
        request(
            "/v2/account/activities?direction=desc&page_size=100&page_token="
            + str(source.fills[-1]["id"]),
            [],
            later,
        ),
    ]
    capture = source.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(
            {
                "schema": SCHEMA,
                "account_ref": REF,
                "endpoint": ENDPOINT,
                "requests": rows,
            }
        ).encode(),
        system_first_seen_at=later,
        processed_at=later,
    ).sequence
    selection = replace(
        IncidentRiskRequest.from_dict(before["last_snapshot"]["selection"]),
        capture_sequence=capture,
        calendar_sequence=calendar(source.journal, later),
        mark_refs=(),
    )
    risk.observe("unsent-source-close-report", selection, later)
    after = risk.view()
    assert after is not None
    assert not after["last_snapshot"]["qualified"]
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" in after["issues"]
    assert after["held"] is not None
    assert after["held"]["client_order_id"] == CID
    assert after["completed"] == before["completed"]
    assert after["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert after["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    assert Fraction(after["capacity"]) == 0
    assert not source.journal.has_order_send_attempt(cid)
    assert emergency.view()["closes"]["residual"]["filled"] == "0"
    assert (
        state["closes"]["residual"]["source_intent_binding"][
            "source_fill_ownership_accepted"
        ]
        is False
    )
    differences = report.differences
    assert any(
        row["code"] == "INTENT_NOT_SENT" and row["subject"] == cid
        for row in differences
    )


def fault_inject_source_send_marker(source: Any, cid: str, at: Any) -> None:
    """TEST STORAGE CORRUPTION, never legitimate attempt provenance/producer."""
    with pytest.raises(JournalError, match="source emergency"):
        source.journal.mark_order_send_attempt(cid, at)
    assert not source.journal.has_order_send_attempt(cid)
    source.journal._db.execute(
        "INSERT INTO order_send_attempts(client_order_id, started_at) VALUES (?, ?)",
        (cid, at.isoformat()),
    )
    source.journal._db.commit()


def test_fault_injected_sent_close_cannot_finalize_unreconciled_source_book(
    ready: Any,
) -> None:
    source, risk, _, emergency, at, _, _ = ready
    state, cid = handoff(ready)
    later = at + timedelta(seconds=1)
    risk.bind_exit("fault-close-role", later, cid, "CLOSE")
    before = risk.view()
    fault_inject_source_send_marker(source, cid, later)
    source.order(cid, later, "filled", 2)
    source.fill(cid, later, 2, "9.49")
    selection = source.selection(later, cash="498.98", position=0)
    risk.observe("fault-close-flat", selection, later)
    after = risk.view()
    assert after["last_snapshot"]["qualified"], after["issues"]
    assert after["last_snapshot"]["flat"]
    assert after["held"] is not None
    assert after["held"]["client_order_id"] == CID
    assert after["completed"] == before["completed"]
    assert after["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert after["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    assert after["capacity"] == "0"
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" in after["issues"]
    assert emergency.view()["closes"]["residual"]["filled"] == "0"
    assert not state["closes"]["residual"]["source_intent_binding"][
        "source_fill_ownership_accepted"
    ]
    event, raw = source.journal.read_account_risk_events()[-1]
    item = json.loads(raw)
    assert item["source_close_ownership_policy"] == "LATCH_UNRECONCILED_SOURCE_CLOSE_V1"
    # A declared metadata flag is not the missing reconciliation producer.
    declared = copy.deepcopy(before)
    declared["held"]["source_close_bindings"][cid]["source_fill_ownership_accepted"] = (
        True
    )
    still_held = risk._reduce(declared, item, False, event.sequence)
    assert still_held["held"] is not None
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" in still_held["issues"]
    for changed in (
        item | {"source_close_ownership_policy": "UNKNOWN_POLICY"},
        item | {"selection": None},
        item | {"kind": "BIND_EXIT"},
    ):
        with pytest.raises(JournalError, match="policy scope"):
            risk._reduce(before, changed, False, event.sequence)
    unbound = copy.deepcopy(before)
    unbound["held"].pop("source_close_bindings")
    with pytest.raises(JournalError, match="policy scope"):
        risk._reduce(unbound, item, False, event.sequence)
    rows = source.journal.read_account_risk_events()
    risk.observe("fault-close-flat", selection, later)
    assert source.journal.read_account_risk_events() == rows
    assert risk.view() == after
    next_at = later + timedelta(seconds=1)
    risk.observe(
        "fault-close-flat-again",
        source.selection(next_at, cash="498.98", position=0),
        next_at,
    )
    repeated = risk.view()
    assert repeated["held"] is not None
    assert repeated["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert (
        repeated["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    )
    causal, refs = AccountRiskOwner._read_native_causal_evidence_at(
        source.journal,
        risk.account_ref,
        next_at,
        risk.fee_policy,
        through_sequence=source.journal.read_evidence_sequence(),
    )
    assert refs and causal == repeated
    # Rebinding and clock/identity faults cannot reopen capacity or drop floors.
    risk.bind_exit("fault-close-role-again", next_at, cid, "CLOSE")
    rebound = risk.view()
    assert rebound["held"] is not None and rebound["capacity"] == "0"
    assert rebound["pending_open_loss_reserve"] == before["pending_open_loss_reserve"]
    assert rebound["additional_local_cash_hold"] == before["additional_local_cash_hold"]
    event, raw = source.journal.read_account_risk_events()[-1]
    for changed, conflict in (
        (json.loads(raw), True),
        (json.loads(raw) | {"at": at.isoformat()}, False),
    ):
        fault = risk._reduce(rebound, changed, conflict, event.sequence)
        assert fault["held"] == rebound["held"] and fault["capacity"] == "0"
        assert (
            fault["pending_open_loss_reserve"] == rebound["pending_open_loss_reserve"]
        )
        assert (
            fault["additional_local_cash_hold"] == rebound["additional_local_cash_hold"]
        )
    path, root = source.journal._path, source.journal._runtime_root
    source.journal.close()
    reopened = Journal.open_private(path, root)
    try:
        recovered = AccountRiskOwner(reopened, risk.account_ref, risk.fee_policy)
        assert recovered.view() == rebound
        assert recovered.verify_rebuild()
    finally:
        reopened.close()


def test_legacy_source_close_observation_retry_keeps_marker_absence(
    ready: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, risk, _, _, at, _, _ = ready
    _, cid = handoff(ready)
    later = at + timedelta(seconds=1)
    risk.bind_exit("legacy-source-close-role", later, cid, "CLOSE")
    fault_inject_source_send_marker(source, cid, later)
    source.order(cid, later, "new", 0)
    selection = source.selection(later, cash="480", position=2, bid="9.49")
    apply = source.journal.apply_account_risk_event

    def legacy_apply(**kwargs: Any) -> Any:
        # Historical-schema fixture only: retain actual canonical source facts
        # while producing the previously supported marker-free event bytes.
        prepare_payload = kwargs["prepare_payload"]
        legacy: dict[str, Any] = {}

        def prepare_legacy(raw: bytes) -> bytes:
            legacy.update(json.loads(prepare_payload(raw)))
            legacy.pop("source_close_ownership_policy", None)
            return json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()

        kwargs["prepare_payload"] = prepare_legacy
        kwargs["reduce"] = lambda old, conflict, seq: risk._reduce(
            old, legacy, conflict, seq
        )
        return apply(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(source.journal, "apply_account_risk_event", legacy_apply)
        risk.observe("legacy-source-close-observe", selection, later)
    before = risk.view()
    rows = source.journal.read_account_risk_events()
    assert "source_close_ownership_policy" not in json.loads(rows[-1][1])
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" not in before["issues"]
    assert risk.verify_rebuild()
    risk.observe("legacy-source-close-observe", selection, later)
    assert source.journal.read_account_risk_events() == rows
    assert risk.view() == before
    next_at = later + timedelta(seconds=1)
    source.order(cid, next_at, "filled", 2)
    source.fill(cid, next_at, 2, "9.49")
    risk.observe(
        "new-source-close-observe",
        source.selection(next_at, cash="498.98", position=0),
        next_at,
    )
    after = risk.view()
    assert after["held"] is not None
    assert "SOURCE_CLOSE_OWNERSHIP_PENDING" in after["issues"]
    assert risk.verify_rebuild()
