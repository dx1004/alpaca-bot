"""Immutable synthetic evidence, with no broker or private account access."""

import json
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.risk_account_inputs import KIND, SOURCE
from alpaca_ross_bot.risk_ownership import PROFILE, OwnershipFacts, qualify_ownership
from test_risk_account_inputs import ACCOUNT, NOW, OPEN, REF, flat_capture


def evaluate(
    tmp_path: Path,
    *,
    fields: dict[str, object] | None = None,
    positions: list[object] | None = None,
    orders: list[object] | None = None,
    intent: bool = False,
) -> OwnershipFacts:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "j.sqlite3", root)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), NOW
        )
        payload = flat_capture()
        requests = payload["requests"]
        assert isinstance(requests, list)
        account = json.loads(requests[0]["body"])
        account.update(fields or {})
        requests[0]["body"] = json.dumps(account)
        if positions is not None:
            requests[1]["body"] = json.dumps(positions)
        if orders is not None:
            requests[2]["body"] = json.dumps(orders)
        if intent:
            journal.reserve_order_intent("synthetic", {"paper_account_ref": REF})
        event = journal.append_next(
            source_id=SOURCE,
            feed="paper",
            event_kind=KIND,
            payload=json.dumps(payload).encode(),
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        return qualify_ownership(
            journal,
            event.sequence,
            account_ref=REF,
            decision_at=NOW,
            session_open=OPEN,
            max_age=timedelta(seconds=30),
        )
    finally:
        journal.close()


def test_flat_unused_account_proves_ownership_not_runtime(tmp_path: Path) -> None:
    facts = evaluate(tmp_path)
    assert facts.ownership_verified and facts.reported_available_funds == 500
    assert facts.funds_profile == PROFILE
    assert facts.owned_long_positions == () and not facts.runtime_authorized
    assert "NOT_SETTLEMENT_PROOF" in facts.limitations
    assert "500" not in str(facts.public_summary())
    assert "11111111" not in repr(facts)


@pytest.mark.parametrize(
    "field", ["pending_transfer_in", "pending_transfer_out", "unsettled_funds"]
)
def test_optional_unknown_is_not_zero_or_settlement(tmp_path: Path, field: str) -> None:
    facts = evaluate(tmp_path, fields={field: None})
    assert facts.reported_available_funds == 500
    assert "UNKNOWN_" + field.upper() in facts.limitations


@pytest.mark.parametrize(
    "field", ["pending_transfer_in", "pending_transfer_out", "unsettled_funds"]
)
def test_explicit_cashflow_requires_verification(tmp_path: Path, field: str) -> None:
    facts = evaluate(tmp_path, fields={field: "1"})
    assert facts.reported_available_funds is None
    assert "PENDING_CASHFLOW_REQUIRES_VERIFICATION" in facts.issues


@pytest.mark.parametrize(
    "fields",
    [
        {"multiplier": "2"},
        {"multiplier": 1},
        {"cash": "-1"},
        {"equity": "0"},
        {"trading_blocked": True},
        {"buying_power": None},
    ],
)
def test_bad_funds_block(tmp_path: Path, fields: dict[str, object]) -> None:
    assert evaluate(tmp_path, fields=fields).reported_available_funds is None


def test_broker_hold_is_not_subtracted_twice_or_settled_policy(tmp_path: Path) -> None:
    with localcontext() as context:
        context.prec = 1
        facts = evaluate(
            tmp_path,
            fields={"buying_power": "420.123", "non_marginable_buying_power": "0"},
        )
    assert facts.reported_available_funds == Decimal("420.123")


@pytest.mark.parametrize(
    "fields", [{"cash": "NaN"}, {"buying_power": "-1"}, {"equity": False}]
)
def test_malformed_source_rejected(tmp_path: Path, fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        evaluate(tmp_path, fields=fields)


def test_unsupported_position_shape_is_not_owned(tmp_path: Path) -> None:
    facts = evaluate(
        tmp_path,
        positions=[
            {"symbol": "SPY", "qty": "-1", "side": "short", "asset_class": "us_equity"}
        ],
    )
    assert not facts.ownership_verified and facts.reported_available_funds is None


def test_manual_inventory_without_lifecycle_is_not_owned(tmp_path: Path) -> None:
    facts = evaluate(
        tmp_path,
        positions=[
            {"symbol": "SPY", "qty": "1", "side": "long", "asset_class": "us_equity"}
        ],
    )
    assert not facts.ownership_verified and facts.reported_available_funds is None


def test_unsent_local_intent_vetoes_even_flat_account(tmp_path: Path) -> None:
    facts = evaluate(tmp_path, intent=True)
    assert not facts.ownership_verified and facts.reported_available_funds is None


@pytest.mark.parametrize("foreign", [False, True])
def test_nonflat_requires_real_replayed_lifecycle(
    tmp_path: Path, foreign: bool
) -> None:
    from test_order_reconciliation import (
        POLICY,
        _at,
        _batch,
        _connect,
        _order,
        _position,
        _reserve,
        _with_baseline,
    )

    root = tmp_path / "private"
    journal = Journal.open_private(root / "j.sqlite3", root)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), _at()
        )
        reconciler = _with_baseline(journal)
        _reserve(journal)
        _connect(reconciler)
        order = _order()
        report = reconciler.apply_rest_batch(
            _batch(reconciler, order), POLICY, at=_at(10)
        )
        assert report.status == "MATCHED"
        payload = flat_capture()
        requests = payload["requests"]
        assert isinstance(requests, list)
        requests[1]["body"] = json.dumps([_position("3" if foreign else "2")])
        requests[2]["body"] = json.dumps([order])
        for request in requests:
            request["started_at"] = request["completed_at"] = _at(10).isoformat()
        event = journal.append_next(
            source_id=SOURCE,
            feed="paper",
            event_kind=KIND,
            payload=json.dumps(payload).encode(),
            system_first_seen_at=_at(10),
            processed_at=_at(10),
        )
        before = len(journal.read_order_events())
        facts = qualify_ownership(
            journal,
            event.sequence,
            account_ref=REF,
            decision_at=_at(10),
            session_open=_at(1000),
            max_age=timedelta(seconds=30),
        )
        assert facts.ownership_verified is not foreign
        assert len(journal.read_order_events()) == before  # no START or mutation
        if foreign:
            assert "OWNED_POSITION_MISMATCH" in facts.issues
        else:
            assert facts.reported_available_funds == 500
            assert len(facts.source_refs) > 1
    finally:
        journal.close()
