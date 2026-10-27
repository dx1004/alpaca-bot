"""Local canonical source-close handoff; never transport/fill acceptance."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
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
