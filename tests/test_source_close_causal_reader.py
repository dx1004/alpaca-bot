"""Causal source proofs use immutable prefixes while current guards stay separate."""

import copy
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import alpaca_ross_bot._causal_risk_memo as memo_module
import alpaca_ross_bot.crossed_stop_source as crossing_module
from alpaca_ross_bot._causal_risk_memo import causal_risk_verification_scope
from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.crossed_stop import CrossedStopJournal
from alpaca_ross_bot.crossed_stop_attempts import (
    SourceCloseAttempts,
    SourceCloseCallbackResponse,
)
from alpaca_ross_bot.crossed_stop_preparation import native_preparation_request
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.native_oco_attempts import NativeOcoAttempts
from alpaca_ross_bot.native_oco_http import NativeOcoHttp
from alpaca_ross_bot.native_oco_requests import (
    NativeOcoRequests,
    _causal_native_requests,
)
from alpaca_ross_bot.oco_journal import _causal_oco_reader
from alpaca_ross_bot.risk_evidence_request import IncidentRiskRequest
from test_crossed_stop_source_attempts import invoke
from test_crossed_stop_source_attempts import source_attempt as source_attempt
from test_native_account_risk_owner import native_risk_book as native_risk_book
from test_native_crossed_stop_preparation import native_ready as native_ready
from test_risk_ledger_audit import POLICY, REF


def deny_current_constructor(self: AccountRiskOwner, *args: Any, **kwargs: Any) -> None:
    raise AssertionError("current risk constructor entered causal proof")


def test_read_handles_preserve_source_proof_without_current_risk_or_writes(
    source_attempt: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts, _, kwargs, posts, ready = source_attempt
    source, _, owner, _, at, _, _ = ready
    row = invoke(source_attempt)
    journal = source.journal
    horizon = row["precall"]["source_horizon"]
    attempt_horizon = row["outcome_ref"]["sequence"]
    expected = owner.read_verified_at(horizon, at)
    before = tuple(journal._db.iterdump())
    reader = _causal_oco_reader(journal, owner.initial)
    requests = _causal_native_requests(reader)
    with monkeypatch.context() as context:
        context.setattr(AccountRiskOwner, "__init__", deny_current_constructor)
        assert reader.read_verified_at(horizon, at) == expected
        assert requests.read_verified_at(horizon, at) is None
        assert attempts.read_verified_at(attempt_horizon, at) == [row]
        assert attempts.view() == [row]
        # The normal owner retains its current-risk integrity check.
        with pytest.raises(AssertionError, match="current risk constructor"):
            owner.read_verified_at(horizon, at)
        for operation in (
            reader.view,
            reader.verify_rebuild,
            lambda: reader.allocate_group("must-not-write", (), at),
            lambda: reader.bind_entry_census("must-not-bind", at, POLICY, at),
            requests.view,
            requests.verify_rebuild,
            lambda: requests.prepare("must-not-prepare", "new-cid", at, at),
            lambda: NativeOcoRequests(reader),
            lambda: NativeOcoHttp(requests),
            lambda: NativeOcoAttempts(requests),
            lambda: CrossedStopJournal(reader),
            lambda: SourceCloseAttempts(reader),
        ):
            with pytest.raises(JournalError):
                operation()
    assert tuple(journal._db.iterdump()) == before
    assert len(posts) == 1
    # An OUTCOME beyond the requested prefix cannot upgrade unknown provenance.
    prefix = attempts.read_verified_at(row["attempt_ref"]["sequence"], at)
    assert len(prefix) == 1 and prefix[0]["outcome"] is None
    for invalid in (-1, True, journal.read_evidence_sequence() + 1):
        with pytest.raises(JournalError):
            reader.read_verified_at(invalid, at)
    with pytest.raises(JournalError):
        requests.read_verified_at(horizon, at.replace(tzinfo=None))


def test_causal_prefix_ignores_later_model_projection_but_checks_its_evidence(
    source_attempt: Any,
) -> None:
    attempts, _, _, _, ready = source_attempt
    source, _, owner, _, at, _, _ = ready
    row = invoke(source_attempt)
    journal = source.journal
    horizon = row["precall"]["source_horizon"]
    reader = _causal_oco_reader(journal, owner.initial)
    expected = reader.read_verified_at(horizon, at)
    assert expected is not None
    ids = tuple(
        sorted(e.execution_id for e in expected.model.executions if e.role == "ENTRY")
    )
    owner.reconcile_entry_terminal(
        "later-consistent-census", ids, at + timedelta(microseconds=1)
    )
    assert reader.read_verified_at(horizon, at) == expected
    assert attempts.read_verified_at(row["outcome_ref"]["sequence"], at) == [row]
    db = journal._db
    db.execute("BEGIN")
    try:
        for fault in ("projection", "dedupe", "raw", "visibility"):
            db.execute("SAVEPOINT prefix_fault")
            try:
                if fault == "projection":
                    cached = journal.read_state(owner.state_key)
                    cached["watermark"] += 100
                    db.execute(
                        "UPDATE state SET value_json=? WHERE key=?",
                        (json.dumps(cached), owner.state_key),
                    )
                    with pytest.raises(JournalError):
                        owner.read_verified_at(horizon, at)
                    assert reader.read_verified_at(horizon, at) == expected
                    continue
                selected = next(
                    (event, raw)
                    for event, raw in journal.read_oco_research_events()
                    if event.sequence <= horizon and event.event_kind == "BOUND_ENTRY"
                )
                event, raw = selected
                if fault == "dedupe":
                    key = next(
                        key
                        for key, value in journal.read_oco_research_evidence_cache(
                            REF
                        ).items()
                        if value["sequence"] == event.sequence
                    )
                    cache_key = "oco_research_evidence:" + REF + ":" + key
                    value = {"hash": "sha256:" + "0" * 64, "sequence": event.sequence}
                    cursor = db.execute(
                        "UPDATE state SET value_json=? WHERE key=?",
                        (json.dumps(value), cache_key),
                    )
                    assert cursor.rowcount == 1
                else:
                    db.execute("DROP TRIGGER events_no_update")
                    if fault == "raw":
                        db.execute(
                            "UPDATE events SET payload=? WHERE sequence=?",
                            (raw + b" ", event.sequence),
                        )
                    else:
                        stamp = (at + timedelta(seconds=1)).isoformat()
                        db.execute(
                            "UPDATE events SET system_first_seen_at=?,processed_at=?,"
                            "available_at=? WHERE sequence=?",
                            (stamp, stamp, stamp, event.sequence),
                        )
                with pytest.raises(JournalError):
                    reader.read_verified_at(horizon, at)
            finally:
                db.execute("ROLLBACK TO prefix_fault")
                db.execute("RELEASE prefix_fault")
        assert reader.read_verified_at(horizon, at) == expected
    finally:
        db.rollback()


def test_source_prefix_can_rebuild_inside_current_risk_verification(
    source_attempt: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts, _, _, _, ready = source_attempt
    source, risk, _, _, at, _, _ = ready
    row = invoke(source_attempt)
    attempt_horizon = row["outcome_ref"]["sequence"]
    selection = IncidentRiskRequest.from_dict(risk.view()["last_snapshot"]["selection"])
    # This genuine later observation is unqualified for the unsent source close;
    # it is not a source FILL consumer or a fabricated passing risk snapshot.
    risk.observe(
        "later-canonical-risk-observation", selection, at + timedelta(microseconds=1)
    )
    target = source.journal.read_account_risk_events()[-1][0].sequence
    expected = copy.deepcopy(risk.view())
    assert expected["last_snapshot"]["qualified"] is False
    before = tuple(source.journal._db.iterdump())
    original_reduce = AccountRiskOwner._reduce
    original_init = AccountRiskOwner.__init__
    depth = [0]
    consumed = [0]

    def current(self: AccountRiskOwner, *args: Any, **kwargs: Any) -> None:
        depth[0] += 1
        try:
            assert depth[0] == 1, "nested current risk verification"
            original_init(self, *args, **kwargs)
        finally:
            depth[0] -= 1

    def reduce(
        self: AccountRiskOwner,
        old: object,
        item: dict[str, Any],
        conflict: bool,
        sequence: int,
    ) -> dict[str, Any]:
        if sequence == target:
            assert attempts.read_verified_at(attempt_horizon, at) == [row]
            consumed[0] += 1
        return original_reduce(self, old, item, conflict, sequence)

    monkeypatch.setattr(AccountRiskOwner, "__init__", current)
    monkeypatch.setattr(AccountRiskOwner, "_reduce", reduce)
    # A source proof reads the reported-price child contract as well as its
    # existing parents. Cache hits must not conceal a changed child file.
    policy_name = "alpaca-reported-intraday-price-policy-v1.json"
    original_policy = crossing_module._ROOT / "evidence" / policy_name
    assert original_policy in memo_module._CONTRACT_INPUTS
    policy_root = tmp_path / "copied-policy-inputs"
    evidence = policy_root / "evidence"
    evidence.mkdir(parents=True)
    for name in (
        policy_name,
        "iex-snapshot-observation-age-research-v1.json",
        "oco-first-execution-research-v1.json",
    ):
        (evidence / name).write_bytes(
            (crossing_module._ROOT / "evidence" / name).read_bytes()
        )
    copied_policy = evidence / policy_name
    monkeypatch.setattr(crossing_module, "_ROOT", policy_root)
    monkeypatch.setattr(
        memo_module,
        "_CONTRACT_INPUTS",
        tuple(
            copied_policy if path == original_policy else path
            for path in memo_module._CONTRACT_INPUTS
        ),
    )
    source.journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(source.journal):
            assert AccountRiskOwner(source.journal, REF, POLICY).view() == expected
            copied_policy.write_bytes(copied_policy.read_bytes() + b"\n")
            with pytest.raises(JournalError):
                AccountRiskOwner(source.journal, REF, POLICY)
    finally:
        source.journal._db.rollback()
    assert consumed[0] > 0 and depth[0] == 0
    assert tuple(source.journal._db.iterdump()) == before


def test_native_source_prefix_has_no_current_risk_and_keeps_transport_integrity(
    native_ready: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    book, emergency, at, assets, statuses = native_ready
    source, risk, owner, http_fixture, _ = book
    request = native_preparation_request(
        assets, statuses, 0, timedelta(seconds=1), timedelta(seconds=1)
    )
    emergency.prepare_native_source(
        "causal-native-close",
        at,
        asset_sequence=assets,
        status_sequence=statuses,
        status_record_index=0,
        max_asset_age=timedelta(seconds=1),
        max_status_age=timedelta(seconds=1),
        for_handoff=True,
    )
    bound = emergency.bind_source_intent("causal-native-close", at)
    cid = json.loads(
        bound["closes"]["causal-native-close"]["source_intent_binding"]["intent"][
            "request_body"
        ]
    )["client_order_id"]
    risk.bind_exit("causal-native-role", at, cid, "CLOSE")
    attempts = SourceCloseAttempts(owner)
    row = attempts.invoke_once(
        http_fixture[1],
        client_order_id=cid,
        attempt_id="causal-native-attempt",
        preparation=request,
        decision_at=at,
        started_at=at,
        max_response_bytes=1000,
        clock=lambda: at,
        callback=lambda _: SourceCloseCallbackResponse(201, b"{}"),
    )
    horizon = row["outcome_ref"]["sequence"]
    before = tuple(source.journal._db.iterdump())
    reader = _causal_oco_reader(source.journal, owner.initial)
    native = _causal_native_requests(reader)
    with monkeypatch.context() as context:
        context.setattr(AccountRiskOwner, "__init__", deny_current_constructor)
        assert attempts.read_verified_at(horizon, at) == [row]
        assert native.read_verified_at(row["precall"]["source_horizon"], at) is not None
        db = source.journal._db
        db.execute("BEGIN")
        try:
            key, _ = db.execute(
                "SELECT key,value_json FROM state "
                "WHERE key LIKE 'native_oco_http:%' LIMIT 1"
            ).fetchone()
            db.execute("UPDATE state SET value_json=? WHERE key=?", ("[]", key))
            with pytest.raises(JournalError) as rejected:
                attempts.read_verified_at(horizon, at)
            cause: BaseException | None = rejected.value
            causes = []
            while cause is not None:
                causes.append(str(cause))
                cause = cause.__cause__
            assert any("native HTTP" in message for message in causes)
        finally:
            db.rollback()
    assert tuple(source.journal._db.iterdump()) == before
