"""Canonical memory-only durable attribution claims and atomicity tests."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from typing import Any

import pytest

import alpaca_ross_bot.crossed_stop_attribution as attribution_module
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop_attribution import (
    DEDUPE,
    EVENT_KIND,
    SOURCE,
    SourceCloseAttributions,
    read_source_close_attributions_at,
)
from alpaca_ross_bot.crossed_stop_fills import derive_injected_source_close_fills
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.native_oco_requests import _digest, _json
from alpaca_ross_bot.oco_journal import OcoJournal
from test_crossed_stop_capture import capture
from test_crossed_stop_capture import captured as captured_fixture
from test_crossed_stop_fills import partial
from test_crossed_stop_source_attempts import source_attempt as source_attempt_fixture

captured = captured_fixture
source_attempt = source_attempt_fixture


def book(case: Any) -> SourceCloseAttributions:
    return SourceCloseAttributions(case[4][0].journal, case[4][4][2].initial)


def observe(
    owner: SourceCloseAttributions,
    case: Any,
    identity: str,
    *packets: dict[str, Any],
    at: Any = None,
) -> dict[str, Any]:
    return owner.observe(
        identity,
        case[3] if at is None else at,
        client_order_id=case[1]["precall"]["client_order_id"],
        capture_sequences=tuple(p["capture_ref"]["sequence"] for p in packets),
    )


def test_incremental_union_two_connections_restart_and_retry_original_bounds(
    captured: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = partial(captured)
    owner = book(captured)
    source, risk, oco, emergency, at, _, _ = captured[4][4]
    before_risk, before_close = risk.view(), emergency.view()
    row = observe(owner, captured, "partial", first)
    second = partial(captured, two=True)
    later = observe(owner, captured, "later", first, second)
    state = owner.view()
    cid = row["command"]["client_order_id"]
    assert state["cid_claims"][cid]["cumulative_fill_quantity"] == "1/2"
    assert len(state["activity_claims"]) == 2 and len(state["order_claims"]) == 1
    assert (
        state["cid_claims"][cid]["binding"]["original_portions"]
        == row["proof"]["original_portions"]
    )
    assert state["cid_claims"][cid]["first_ref"] == row["observation_ref"]
    assert state["cid_claims"][cid]["latest_ref"] == later["observation_ref"]
    assert all(value is False for value in state["authority"].values())
    assert state["finality"] == "UNKNOWN" and not state["conflicts"]
    other = Journal.open_private(source.journal._path, source.journal._runtime_root)
    try:
        assert SourceCloseAttributions(other, oco.initial).view() == state
        assert (
            read_source_close_attributions_at(
                other,
                oco.initial,
                through_sequence=later["observation_ref"]["sequence"],
                decision_at=at,
            )
            == state
        )
    finally:
        other.close()
    # Later valid sources and a consistent model census cannot reinterpret a retry.
    partial(captured, two=True)
    entry_ids = tuple(
        sorted(e.execution_id for e in oco.view().model.executions if e.role == "ENTRY")
    )
    oco.reconcile_entry_terminal(
        "after-attribution-census", entry_ids, at + timedelta(microseconds=1)
    )
    expected_horizons = {row["source_horizon"], later["source_horizon"]}
    derive = derive_injected_source_close_fills
    seen = []

    def bounded(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["through_sequence"])
        assert kwargs["through_sequence"] in expected_horizons
        return derive(*args, **kwargs)

    before = tuple(source.journal._db.iterdump())
    monkeypatch.setattr(
        attribution_module, "derive_injected_source_close_fills", bounded
    )
    assert (
        observe(owner, captured, "partial", first, at=at + timedelta(seconds=1)) == row
    )
    assert seen and tuple(source.journal._db.iterdump()) == before
    assert risk.view() == before_risk and emergency.view() == before_close
    assert len(captured[4][3]) == 1
    with pytest.raises(JournalError):
        observe(owner, captured, "partial", first, at=at - timedelta(microseconds=1))
    seeded = replace(oco.initial, executions=oco.view().model.executions)
    with pytest.raises(JournalError, match="empty OCO initial"):
        SourceCloseAttributions(source.journal, seeded)


def test_changed_identity_latches_once_and_preserves_original_claims(
    captured: Any,
) -> None:
    first = partial(captured)
    owner = book(captured)
    original = observe(owner, captured, "stable", first)
    prior = owner.view()
    second = partial(captured, two=True)
    initial = captured[4][4][2].initial
    changed = SourceCloseAttributions(
        owner.journal, replace(initial, limit=initial.limit + 1)
    )
    conflict = observe(changed, captured, "stable", first, second)
    assert conflict["kind"] == "CONFLICT" and conflict["proof"] is None
    state = owner.view()
    assert state["latched"] and len(state["conflicts"]) == 1
    assert state["cid_claims"] == prior["cid_claims"]
    assert state["order_claims"] == prior["order_claims"]
    assert state["activity_claims"] == prior["activity_claims"]
    before = tuple(owner.journal._db.iterdump())
    assert (
        observe(
            changed,
            captured,
            "stable",
            first,
            second,
            at=captured[3] + timedelta(seconds=1),
        )
        == conflict
    )
    assert (
        observe(owner, captured, "stable", first, at=captured[3] + timedelta(seconds=1))
        == original
    )
    assert tuple(owner.journal._db.iterdump()) == before
    with pytest.raises(JournalError, match="latch"):
        observe(owner, captured, "new-identity", second)
    with pytest.raises(JournalError, match="latch"):
        observe(changed, captured, "stable", first)
    assert owner.view()["latched"]
    # A fully rehashed second-conflict packet and matching projection cannot
    # bypass the same account latch enforced by the normal writer.
    forged = {
        k: copy.deepcopy(v) for k, v in conflict.items() if k != "observation_ref"
    }
    forged["command"]["capture_sequences"] = [first["capture_ref"]["sequence"]]
    forged["identity_hash"] = _digest(_json(forged["command"])).removeprefix("sha256:")
    forged["source_horizon"] = owner.journal.read_evidence_sequence()
    event = owner.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=EVENT_KIND,
        payload=_json(forged),
        system_first_seen_at=captured[3],
        processed_at=captured[3],
    )
    forged_row = {
        "observation_ref": {"sequence": event.sequence, "hash": event.payload_ref},
        **forged,
    }
    consistent = copy.deepcopy(state)
    consistent["conflicts"].append(forged_row)
    consistent["observations"].append(forged_row)
    consistent["watermark"] = event.sequence
    owner.journal._db.execute(
        "UPDATE state SET value_json=? WHERE key=?",
        (_json(consistent).decode(), owner.state_key),
    )
    owner.journal._db.commit()
    with pytest.raises(JournalError, match="conflict latch forbids"):
        owner.view()


def test_atomic_append_failure_and_failed_derivation_preserve_old_claims(
    captured: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = partial(captured)
    owner = book(captured)
    observe(owner, captured, "first", first)
    prior = owner.view()
    second = partial(captured, two=True)
    before = tuple(owner.journal._db.iterdump())
    append = owner.journal._append_locked

    def fail(event: Any, *args: Any, **kwargs: Any) -> None:
        append(event, *args, **kwargs)
        if event.source_id == SOURCE:
            raise OSError("synthetic attribution append fault")

    monkeypatch.setattr(owner.journal, "_append_locked", fail)
    with pytest.raises(OSError):
        observe(owner, captured, "later", first, second)
    monkeypatch.setattr(owner.journal, "_append_locked", append)
    assert tuple(owner.journal._db.iterdump()) == before and owner.view() == prior
    accepted = observe(owner, captured, "later", first, second)
    assert accepted["proof"]["cumulative_fill_quantity"] == "1/2"
    # A diagnostic partial-page capture is retained, but cannot mutate the index.
    expected = owner.view()
    captured[2].pages = [b"not-json"]
    invalid = capture(captured)
    before = tuple(owner.journal._db.iterdump())
    with pytest.raises(JournalError):
        observe(owner, captured, "invalid-source", invalid)
    assert tuple(owner.journal._db.iterdump()) == before
    assert owner.view() == expected


def test_origin_ref_dedupe_projection_and_visibility_tamper(captured: Any) -> None:
    packet = partial(captured)
    owner = book(captured)
    row = observe(owner, captured, "tamper-boundary", packet)
    journal = owner.journal
    event, raw = journal._read_private_events(SOURCE, None)[0]
    expected = owner.view()
    journal._db.execute("BEGIN")
    try:
        for fault in (
            "origin",
            "reference",
            "dedupe",
            "projection",
            "visibility",
            "json",
        ):
            journal._db.execute("SAVEPOINT attribution_fault")
            try:
                if fault == "projection":
                    value = copy.deepcopy(expected)
                    value["latched"] = True
                    journal._db.execute(
                        "UPDATE state SET value_json=? WHERE key=?",
                        (_json(value).decode(), owner.state_key),
                    )
                elif fault == "dedupe":
                    key = DEDUPE + ":" + owner.account_ref + ":" + row["dedupe_key"]
                    journal._db.execute(
                        "UPDATE state SET value_json=? WHERE key=?",
                        (
                            _json(
                                {"sequence": event.sequence, "hash": "sha256:wrong"}
                            ).decode(),
                            key,
                        ),
                    )
                else:
                    journal._db.execute("DROP TRIGGER events_no_update")
                    if fault == "visibility":
                        journal._db.execute(
                            "UPDATE events SET available_at=? WHERE sequence=?",
                            (
                                (captured[3] + timedelta(seconds=1)).isoformat(),
                                event.sequence,
                            ),
                        )
                    else:
                        item = json.loads(raw)
                        if fault == "origin":
                            item["origin"] = "INJECTED_EXCHANGE"
                        elif fault == "reference":
                            item["proof"]["capture_refs"][0]["hash"] = "sha256:wrong"
                        changed = b"not-json" if fault == "json" else _json(item)
                        journal._db.execute(
                            "UPDATE events SET payload=?,payload_ref=? "
                            "WHERE sequence=?",
                            (changed, _digest(changed), event.sequence),
                        )
                with pytest.raises(JournalError):
                    owner.view()
            finally:
                journal._db.execute("ROLLBACK TO attribution_fault")
                journal._db.execute("RELEASE attribution_fault")
        assert owner.view() == expected
    finally:
        journal._db.rollback()


def test_two_connection_race_and_bounded_read_skips_future_before_deriving(
    captured: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = partial(captured)
    source, _, oco, _, at, _, _ = captured[4][4]
    barrier = Barrier(2)

    def worker() -> dict[str, Any]:
        journal = Journal.open_private(
            source.journal._path, source.journal._runtime_root
        )
        try:
            # Test-only wait budget; production Journal settings remain unchanged.
            journal._db.execute("PRAGMA busy_timeout=120000")
            owner = SourceCloseAttributions(journal, oco.initial)
            barrier.wait(timeout=30)
            return observe(
                owner, captured, "raced", packet, at=at + timedelta(microseconds=1)
            )
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        rows = [future.result(timeout=180) for future in futures]
    assert rows[0] == rows[1]
    assert len(source.journal._read_private_events(SOURCE, None)) == 1
    owner = book(captured)
    assert len(owner.view()["activity_claims"]) == 1
    before = tuple(source.journal._db.iterdump())

    def deny(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("future proof/current constructor entered bounded read")

    with monkeypatch.context() as context:
        context.setattr(attribution_module, "derive_injected_source_close_fills", deny)
        context.setattr(SourceCloseAttributions, "__init__", deny)
        context.setattr(AccountRiskOwner, "__init__", deny)
        context.setattr(OcoJournal, "__init__", deny)
        past = read_source_close_attributions_at(
            source.journal,
            oco.initial,
            through_sequence=rows[0]["observation_ref"]["sequence"],
            decision_at=at,
        )
        assert past["activity_claims"] == {} and past["observations"] == []
    assert tuple(source.journal._db.iterdump()) == before


def test_process_interruptions_rollback_owned_lock_and_leave_claims_intact(
    captured: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = partial(captured)
    owner = book(captured)
    observe(owner, captured, "first", first)
    second = partial(captured, two=True)
    original_derive, original_project = (
        attribution_module._derive,
        attribution_module._project,
    )
    for phase, error in (
        ("derive", KeyboardInterrupt),
        ("derive", SystemExit),
        ("reduce", KeyboardInterrupt),
        ("reduce", SystemExit),
    ):
        before = tuple(owner.journal._db.iterdump())

        def derive(
            journal: Any,
            item: Any,
            *,
            phase: str = phase,
            error: type[BaseException] = error,
        ) -> Any:
            if phase == "derive" and item["command"]["identity"] == "interrupted":
                raise error
            return original_derive(journal, item)

        def project(
            state: Any,
            item: Any,
            ref: Any,
            *,
            phase: str = phase,
            error: type[BaseException] = error,
        ) -> Any:
            if phase == "reduce" and item["command"]["identity"] == "interrupted":
                raise error
            return original_project(state, item, ref)

        with monkeypatch.context() as context:
            context.setattr(attribution_module, "_derive", derive)
            context.setattr(attribution_module, "_project", project)
            with pytest.raises(error):
                observe(owner, captured, "interrupted", first, second)
        assert not owner.journal._db.in_transaction
        assert tuple(owner.journal._db.iterdump()) == before
        other = Journal.open_private(owner.journal._path, owner.journal._runtime_root)
        try:
            other._db.execute("BEGIN IMMEDIATE")
            other._db.rollback()
        finally:
            other.close()
    accepted = observe(owner, captured, "interrupted", first, second)
    assert accepted["proof"]["cumulative_fill_quantity"] == "1/2"
