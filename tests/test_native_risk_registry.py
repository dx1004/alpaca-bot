"""Causal and immutable structure rejection; injected private fixtures only."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.native_risk_registry import POLICY, derive_native_registry
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, KIND, SOURCE
from test_native_oco_http import native_http as native_http
from test_risk_ledger_audit import POLICY as FEES


def _inputs(fixture: Any) -> tuple[dict[str, Any], int]:
    owner, _harness, at, _exchange, _formal = fixture
    initial = owner.requests.owner.initial
    context = {
        "policy": POLICY,
        "episodes": [
            {
                "entry_scope": initial.entry_scope,
                "symbol": initial.symbol,
                "limit": str(initial.limit),
                "stop": str(initial.stop),
                "quantity": 4,
                "closed": False,
                "census_refs": [],
            }
        ],
    }
    seq = owner.journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind=KIND,
        payload=json.dumps(
            {
                "schema": "PAPER_RISK_INPUT_CAPTURE_V2",
                "transport_origin": "INJECTED_EXCHANGE",
                "account_ref": initial.account_ref,
                "endpoint": ENDPOINT,
                "requests": [],
            }
        ).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence
    return context, seq


def _derive(fixture: Any, context: dict[str, Any], seq: int) -> Any:
    return derive_native_registry(
        fixture[0].journal,
        context,
        capture_sequence=seq,
        decision_at=fixture[2],
        max_age=timedelta(seconds=30),
        through_sequence=fixture[0].journal.read_evidence_sequence(),
    )


@pytest.mark.parametrize(
    "case", ["self_horizon", "future_horizon", "receipt", "binding", "schema", "kind"]
)
def test_registry_rejects_causal_or_structural_forgery(
    native_http: Any, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    context, seq = _inputs(native_http)
    journal = native_http[0].journal
    rows = list(journal.read_oco_research_events())
    index = next(
        i for i, (_, raw) in enumerate(rows) if json.loads(raw)["kind"] == "BOUND_ENTRY"
    )
    event, raw = rows[index]
    item = json.loads(raw)
    if case.endswith("horizon"):
        item["operation"]["source_horizon"] = event.sequence + (
            case == "future_horizon"
        )
    elif case == "receipt":
        item["operation"]["receipt"]["reservation"]["quantity"] = 999
    elif case == "binding":
        item["binding"]["manifest_sha256"] = "sha256:" + "0" * 64
    elif case == "schema":
        item["unexpected"] = True
    else:
        item["kind"] = "EXECUTION"
    payload = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    rows[index] = (
        replace(event, payload_ref="sha256:" + hashlib.sha256(payload).hexdigest()),
        payload,
    )
    monkeypatch.setattr(journal, "read_oco_research_events", lambda: rows)
    before = journal.read_account_risk_events()
    expected = {
        "self_horizon": "source horizon exceeds",
        "future_horizon": "source horizon exceeds",
        "receipt": "source entry differs from causal replay",
        "binding": "frozen binding differs",
        "schema": "invalid exact OCO research schema",
        "kind": "raw event metadata",
    }[case]
    with pytest.raises(JournalError, match=expected):
        _derive(native_http, context, seq)
    assert journal.read_account_risk_events() == before


def test_registry_rejects_duplicate_immutable_operation(
    native_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, seq = _inputs(native_http)
    journal = native_http[0].journal
    rows = list(journal.read_oco_research_events())
    rows.insert(1, rows[0])
    monkeypatch.setattr(journal, "read_oco_research_events", lambda: rows)
    with pytest.raises(JournalError, match="duplicate OCO"):
        _derive(native_http, context, seq)


def test_registry_rejects_noncanonical_cache_identity(
    native_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, seq = _inputs(native_http)
    journal = native_http[0].journal
    cached = journal.read_oco_research_evidence_cache(
        native_http[0].binding["account_ref"]
    )
    cached["[]"] = {"hash": "sha256:" + "0" * 64, "sequence": 1}
    monkeypatch.setattr(journal, "read_oco_research_evidence_cache", lambda _: cached)
    with pytest.raises(JournalError, match="cache identity"):
        _derive(native_http, context, seq)


def test_causal_reader_rejects_future_horizon(native_http: Any) -> None:
    journal = native_http[0].journal
    with pytest.raises(JournalError, match="causal risk horizon"):
        AccountRiskOwner._read_native_causal_evidence_at(
            journal,
            native_http[0].binding["account_ref"],
            native_http[2],
            FEES,
            through_sequence=journal.read_evidence_sequence() + 1,
        )


@pytest.mark.parametrize("case", ["schema", "kind", "clock"])
def test_causal_reader_validates_raw_envelope_before_reduce(
    native_http: Any, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    journal = native_http[0].journal
    rows = list(journal.read_account_risk_events())
    event, raw = rows[0]
    item = json.loads(raw)
    if case == "schema":
        item["extra"] = True
    elif case == "kind":
        item["kind"] = "UNSUPPORTED"
    else:
        item["at"] = (native_http[2] + timedelta(seconds=1)).isoformat()
    payload = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    rows[0] = (
        replace(event, payload_ref="sha256:" + hashlib.sha256(payload).hexdigest()),
        payload,
    )
    monkeypatch.setattr(journal, "read_account_risk_events", lambda: rows)
    with pytest.raises(
        JournalError,
        match="causal risk (exact event schema differs|event identity unavailable)",
    ):
        AccountRiskOwner._read_native_causal_evidence_at(
            journal,
            native_http[0].binding["account_ref"],
            native_http[2],
            FEES,
            through_sequence=journal.read_evidence_sequence(),
        )
