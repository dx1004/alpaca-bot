"""Native-owned entry updates preserve the original risk and lot identities."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.native_oco_requests import NativeOcoRequests
from alpaca_ross_bot.native_risk_registry import POLICY as NATIVE_POLICY
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.oco_research import LotPortion
from test_native_account_risk_owner import full_book
from test_native_account_risk_owner import native_risk_book as native_risk_book
from test_native_oco_correlation_integration import ROOT
from test_observation_candidate_binding import CID
from test_oco_exit_evidence import capture
from test_risk_ledger_audit import POLICY
from test_risk_marks import QUOTE, put


def native_partial(book: Any) -> datetime:
    source, risk, owner, fixture, reported = book
    at: datetime = fixture[2] + timedelta(seconds=1)
    reported.order.update(
        status="partially_filled", filled_qty="1", updated_at=at.isoformat()
    )
    reported.order["legs"][0].update(updated_at=at.isoformat())
    reported.fills[ROOT] = [
        {
            "id": "native-target-1",
            "activity_type": "FILL",
            "order_id": ROOT,
            "symbol": "AAPL",
            "side": "sell",
            "qty": "1",
            "price": "11.03",
            "transaction_time": at.isoformat(),
        }
    ]
    owner.bind_native_exit_census(
        "partial-before-late-entry",
        "group-a",
        capture(fixture, reported, at),
        at,
        timedelta(seconds=30),
        at,
    )
    selection = full_book(source, reported, at, cash="491.03", position=1)
    risk.observe_native("native-before-late-entry", selection, at)
    state = risk.view()
    assert state["last_snapshot"]["qualified"], state["issues"]
    assert state["issues"] == ["NATIVE_FINALITY_PENDING"]
    return at


def late_entry(
    book: Any, *, fractional: bool = False, daily_stop: bool = False
) -> datetime:
    source, risk, _, _, reported = book
    at = native_partial(book) + timedelta(seconds=1)
    quantity = Decimal("0.5") if fractional else Decimal(1)
    source.order(CID, at, "canceled", Decimal(2) + quantity)
    source.fill(CID, at, quantity, "9.40")
    selection = full_book(
        source,
        reported,
        at,
        cash="486.33" if fractional else "481.63",
        position=Decimal(1) + quantity,
    )
    if daily_stop:
        mark = selection.mark_refs[0]
        quote = put(
            source.journal,
            [
                QUOTE
                | {"bp": "5", "ap": "5", "t": at.isoformat().replace("+00:00", "Z")}
            ],
            at=at,
        )
        selection = replace(selection, mark_refs=((mark[0], quote, 0, mark[3]),))
    risk.observe_native("native-after-late-entry", selection, at)
    state = risk.view()
    assert state["last_snapshot"]["qualified"], state["last_snapshot"]["issues"]
    assert state["held"] is not None
    assert state["capacity"] == "0"
    return at


def test_late_source_entry_binding_preserves_exits_and_prepares_disjoint_group(
    native_risk_book: Any,
) -> None:
    source, risk, owner, _, _ = native_risk_book
    original = owner.view()
    reservation = original.entry_binding["reservation_ref"]
    at = late_entry(native_risk_book)
    before_risk = risk.view()
    before = owner.view()
    assert before.model.entry_quantity == 2
    assert before_risk["held"]["filled"] == 3
    with pytest.raises(JournalError, match="held source risk"):
        owner.bind_entry_census("legacy-refuses-pending", at, POLICY, at)
    binding_at = at
    updated = owner.bind_native_entry_census("native-late-entry", at, POLICY, at)
    assert updated.entry_binding["ownership_policy"] == NATIVE_POLICY
    assert updated.entry_binding["reservation_ref"] == reservation
    assert updated.model.entry_quantity == 3
    assert updated.model.owned_quantity == 2
    assert updated.model.unallocated_quantity == 1
    assert updated.model.group_residual("group-a") == 1
    assert updated.model.groups == before.model.groups
    assert updated.native_exit_bindings == before.native_exit_bindings
    assert tuple(e for e in updated.model.executions if e.role != "ENTRY") == tuple(
        e for e in before.model.executions if e.role != "ENTRY"
    )
    assert risk.view() == before_risk
    # A subsequent account observation must consume the new BOUND_ENTRY mode,
    # not merely keep replaying a prefix that predates this binding.
    later = at + timedelta(seconds=1)
    selection = full_book(source, native_risk_book[4], later, cash="481.63", position=2)
    risk.observe_native("risk-consumes-native-entry-binding", selection, later)
    after_binding = risk.view()
    assert after_binding["last_snapshot"]["qualified"], after_binding["issues"]
    assert after_binding["held"]["filled"] == 3
    assert after_binding["held"]["native_ownership"]["exit_quantity"] == "1"
    assert (
        after_binding["pending_open_loss_reserve"]
        == before_risk["pending_open_loss_reserve"]
    )
    assert after_binding["capacity"] == "0"
    before_risk = after_binding
    at = later
    late_id = source.fills[-1]["id"]
    owner.allocate_group("group-b", (LotPortion(late_id, Fraction(1)),), at)
    prepared = NativeOcoRequests(owner).prepare("group-b", "native-late-cid", at, at)
    assert prepared["receipt"]["entry_census"]["ownership_policy"] == NATIVE_POLICY
    assert prepared["receipt"]["reservation_ref"] == reservation
    assert source.journal.read_order_intent_ids() == (CID,)
    assert risk.view() == before_risk
    assert not any(prepared["receipt"]["authority"].values())
    count = source.journal.read_evidence_sequence()
    reopened = OcoJournal(source.journal, owner.initial)
    reopened.bind_native_entry_census(
        "native-late-entry", binding_at, POLICY, at + timedelta(seconds=1)
    )
    assert source.journal.read_evidence_sequence() == count
    assert reopened.view() == owner.view()
    assert (
        AccountRiskOwner(source.journal, risk.account_ref, POLICY).view() == before_risk
    )


@pytest.mark.parametrize("case", ["daily_stop", "fractional"])
def test_native_entry_binding_cannot_override_existing_risk_incidents(
    native_risk_book: Any, case: str
) -> None:
    source, risk, owner, _, _ = native_risk_book
    at = late_entry(
        native_risk_book,
        fractional=case == "fractional",
        daily_stop=case == "daily_stop",
    )
    before = risk.view()
    if case == "daily_stop":
        assert before["daily_stop_latched"]
    else:
        assert "quantity_incident" in before["held"]
    count = source.journal.read_evidence_sequence()
    with pytest.raises(JournalError):
        owner.bind_native_entry_census("incident-must-veto", at, POLICY, at)
    assert source.journal.read_evidence_sequence() == count
    assert risk.view() == before
    assert owner.view().model.entry_quantity == 2


def test_native_mode_cannot_reinterpret_existing_legacy_binding_identity(
    native_risk_book: Any,
) -> None:
    source, risk, owner, _, _ = native_risk_book
    at = late_entry(native_risk_book)
    before = risk.view()
    changed = owner.bind_native_entry_census("terminal-entry", at, POLICY, at)
    assert changed.conflict_keys
    assert changed.entry_binding.get("ownership_policy") is None
    assert changed.model.entry_quantity == 2
    assert changed.model.group_residual("group-a") == 1
    assert risk.view() == before
    assert source.journal.read_order_intent_ids() == (CID,)


@pytest.mark.parametrize("case", ["unknown", "null", "extra"])
def test_native_entry_request_policy_is_exact_on_replay(
    native_risk_book: Any, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    source, risk, owner, _, _ = native_risk_book
    at = native_partial(native_risk_book)
    bound = owner.bind_native_entry_census("policy-shape", at, POLICY, at)
    assert bound.entry_binding["ownership_policy"] == NATIVE_POLICY
    before_risk = risk.view()
    rows = list(source.journal.read_oco_research_events())
    event, raw = rows[-1]
    item = json.loads(raw)
    request = item["operation"]["request"]
    if case == "extra":
        request["allow_veto"] = True
        expected = "exact OCO research schema"
    else:
        request["ownership_policy"] = None if case == "null" else "UNKNOWN_POLICY"
        expected = "pinned native entry ownership policy"
    payload = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    rows[-1] = (
        replace(event, payload_ref="sha256:" + hashlib.sha256(payload).hexdigest()),
        payload,
    )
    monkeypatch.setattr(source.journal, "read_oco_research_events", lambda: rows)
    with pytest.raises(JournalError, match=expected):
        owner.view()
    assert risk.view() == before_risk


def test_native_entry_mode_does_not_refresh_a_stale_account_snapshot(
    native_risk_book: Any,
) -> None:
    source, risk, owner, _, _ = native_risk_book
    at = native_partial(native_risk_book) + timedelta(seconds=31)
    count = source.journal.read_evidence_sequence()
    before = risk.view()
    with pytest.raises(
        JournalError, match="stale or future entry-census risk snapshot"
    ):
        owner.bind_native_entry_census("stale-native-entry", at, POLICY, at)
    assert source.journal.read_evidence_sequence() == count
    assert risk.view() == before
    assert owner.view().model.entry_quantity == 2
