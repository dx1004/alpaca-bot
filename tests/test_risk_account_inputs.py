"""Synthetic account snapshots; no broker access or financial value disclosure."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.risk_account_inputs import (
    ENDPOINT,
    KIND,
    MONEY_FIELDS,
    SCHEMA,
    SOURCE,
    AccountInputFacts,
    inspect_account_inputs,
)

NOW = datetime(2026, 10, 16, 13, tzinfo=UTC)
OPEN = NOW + timedelta(minutes=30)
ACCOUNT = {
    "id": "11111111-1111-4111-8111-111111111111",
    "status": "ACTIVE",
    "currency": "USD",
    "trading_blocked": False,
    "account_blocked": False,
    "trade_suspended_by_user": False,
} | dict.fromkeys(MONEY_FIELDS, "0")
REF = paper_account_ref(json.dumps(ACCOUNT).encode())


def capture(account: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "account_ref": REF,
        "endpoint": ENDPOINT,
        "requests": [
            {
                "method": "GET",
                "path": "/v2/account",
                "status": 200,
                "started_at": (NOW - timedelta(seconds=2)).isoformat(),
                "completed_at": (NOW - timedelta(seconds=1)).isoformat(),
                "body": json.dumps(ACCOUNT if account is None else account),
            }
        ],
    }


def inspect(
    tmp_path: Path,
    payload: dict[str, object],
    *,
    with_local_intent: bool = False,
    **kwargs: object,
) -> AccountInputFacts:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "evidence.sqlite3", root)
    try:
        journal.record_paper_account_verification(
            REF, json.dumps(ACCOUNT).encode(), NOW
        )
        if with_local_intent:
            journal.reserve_order_intent("synthetic-entry", {"paper_account_ref": REF})
        event = journal.append_next(
            source_id=SOURCE,
            feed="paper",
            event_kind=KIND,
            payload=json.dumps(payload).encode(),
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        options = dict(
            account_ref=REF,
            decision_at=NOW,
            session_open=OPEN,
            max_age=timedelta(seconds=30),
        )
        options.update(kwargs)
        return inspect_account_inputs(journal, event.sequence, **options)  # type: ignore[arg-type]
    finally:
        journal.close()


def test_zero_is_observed_but_not_funds_authority(tmp_path: Path) -> None:
    facts = inspect(tmp_path, capture())
    assert dict(facts.money)["cash"] == 0
    assert facts.active_usd_flags_clear and facts.before_session_open
    assert "ONE_X_FLAT_AVAILABLE_PROFILE_UNQUALIFIED" in facts.reasons
    assert not facts.runtime_authorized
    assert "11111111" not in repr(facts)


def test_missing_is_not_zero_and_midday_not_baseline(tmp_path: Path) -> None:
    account = ACCOUNT.copy()
    del account["cash"]
    facts = inspect(tmp_path, capture(account), session_open=NOW)
    assert dict(facts.money)["cash"] is None
    assert "MISSING_CASH" in facts.reasons
    assert "NO_PRESESSION_BASELINE" in facts.reasons


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "abc", True, 5, 0.1])
def test_malformed_money_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError):
        inspect(tmp_path, capture(ACCOUNT | {"equity": value}))


def test_negative_equity_rejected_negative_cash_preserved(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        inspect(tmp_path, capture(ACCOUNT | {"equity": "-1"}))
    facts = inspect(tmp_path, capture(ACCOUNT | {"cash": "-1"}))
    assert "NEGATIVE_CASH" in facts.reasons


@pytest.mark.parametrize(
    "field", ["pending_transfer_in", "pending_transfer_out", "unsettled_funds"]
)
def test_pending_funds_are_not_spendable(tmp_path: Path, field: str) -> None:
    assert (
        "PENDING_TRANSFER_OR_UNSETTLED_FUNDS"
        in inspect(tmp_path, capture(ACCOUNT | {field: "1"})).reasons
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "EUR"),
        ("status", "BLOCKED"),
        ("account_blocked", 0),
        ("trading_blocked", True),
    ],
)
def test_bad_account_flags_do_not_qualify(
    tmp_path: Path, field: str, value: object
) -> None:
    assert not inspect(
        tmp_path, capture(ACCOUNT | {field: value})
    ).active_usd_flags_clear


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "https://api.alpaca.markets"),
        ("account_ref", "foreign"),
        ("schema", "other"),
    ],
)
def test_envelope_identity_rejected(tmp_path: Path, field: str, value: str) -> None:
    with pytest.raises(ValueError):
        inspect(tmp_path, capture() | {field: value})


def test_foreign_raw_account_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        inspect(
            tmp_path, capture(ACCOUNT | {"id": "22222222-2222-4222-8222-222222222222"})
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("method", "POST"),
        ("status", 201),
        ("path", "https://paper-api.alpaca.markets/v2/account"),
        ("started_at", "2026-10-16T14:00:00+00:00"),
        ("body", "{bad"),
    ],
)
def test_invalid_request_rejected(tmp_path: Path, field: str, value: object) -> None:
    payload = capture()
    payload["requests"][0][field] = value  # type: ignore[index]
    with pytest.raises(ValueError):
        inspect(tmp_path, payload)


def test_future_and_stale_capture_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        inspect(tmp_path, capture(), decision_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError):
        inspect(tmp_path, capture(), decision_at=NOW + timedelta(seconds=31))


def test_unbound_input_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        inspect(tmp_path, capture(), account_ref="foreign")


def test_collection_capture_does_not_assert_completeness(tmp_path: Path) -> None:
    payload = capture()
    requests = payload["requests"]
    assert isinstance(requests, list)
    requests.append(
        {
            "method": "GET",
            "path": "/v2/orders?status=open&limit=500",
            "status": 200,
            "started_at": (NOW - timedelta(seconds=1)).isoformat(),
            "completed_at": NOW.isoformat(),
            "body": "[]",
        }
    )
    facts = inspect(tmp_path, payload)
    assert facts.collection_counts == (("/v2/orders?status=open&limit=500", 0),)
    assert "PAGINATION_COMPLETENESS_UNVERIFIED" in facts.reasons
    assert "RESERVATIONS_UNVERIFIED" in facts.reasons


def flat_capture() -> dict[str, object]:
    account = ACCOUNT | {
        "multiplier": "1",
        "cash": "500",
        "equity": "500",
        "buying_power": "500",
        "non_marginable_buying_power": "500",
    }
    del account["unsettled_funds"]
    payload = capture(account)
    requests = payload["requests"]
    assert isinstance(requests, list)
    for path in ("/v2/positions", "/v2/orders?status=open&limit=500&nested=false"):
        requests.append(
            {
                "method": "GET",
                "path": path,
                "status": 200,
                "started_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
                "body": "[]",
            }
        )
    return payload


def test_restricted_available_profile_does_not_claim_settlement_or_baseline(
    tmp_path: Path,
) -> None:
    facts = inspect(tmp_path, flat_capture())
    assert facts.available_funds == 500
    assert facts.available_profile == "ONE_X_FLAT_CURRENT_AVAILABLE_NOT_SETTLED"
    assert facts.settlement_status == "UNKNOWN"
    assert "PRESESSION_BASELINE_UNACCEPTED" in facts.reasons
    assert not facts.runtime_authorized


@pytest.mark.parametrize(
    "field,value",
    [
        ("multiplier", "2"),
        ("multiplier", 1),
        ("buying_power", "1000"),
        ("non_marginable_buying_power", "499"),
        ("equity", "501"),
        ("pending_transfer_in", "1"),
        ("pending_transfer_out", None),
        ("accrued_fees", "0.01"),
        ("account_blocked", True),
    ],
)
def test_restricted_profile_rejects_ambiguous_funds(
    tmp_path: Path, field: str, value: object
) -> None:
    payload = flat_capture()
    requests = payload["requests"]
    assert isinstance(requests, list)
    account = json.loads(requests[0]["body"])
    account[field] = value
    requests[0]["body"] = json.dumps(account)
    assert inspect(tmp_path, payload).available_funds is None


@pytest.mark.parametrize(
    "path",
    [
        "/v2/orders?status=closed&limit=500&nested=false",
        "/v2/orders?status=open&limit=1&nested=false",
        "/v2/orders?status=open&limit=500&nested=false&page_token=x",
    ],
)
def test_filtered_or_truncated_order_view_not_empty_account_proof(
    tmp_path: Path, path: str
) -> None:
    payload = flat_capture()
    requests = payload["requests"]
    assert isinstance(requests, list)
    requests[2]["path"] = path
    assert inspect(tmp_path, payload).available_funds is None


@pytest.mark.parametrize("index", [1, 2])
def test_observed_position_or_order_disallows_flat_profile(
    tmp_path: Path, index: int
) -> None:
    payload = flat_capture()
    requests = payload["requests"]
    assert isinstance(requests, list)
    requests[index]["body"] = '[{"id":"synthetic"}]'
    assert inspect(tmp_path, payload).available_funds is None


def test_conflicting_duplicate_view_does_not_qualify(tmp_path: Path) -> None:
    payload = flat_capture()
    requests = payload["requests"]
    assert isinstance(requests, list)
    requests.append(requests[2] | {"body": '[{"id":"synthetic"}]'})
    assert inspect(tmp_path, payload).available_funds is None


def test_local_intent_prevents_fresh_unused_account_profile(tmp_path: Path) -> None:
    facts = inspect(tmp_path, flat_capture(), with_local_intent=True)
    assert facts.available_funds is None


def test_duplicate_raw_field_is_not_accepted(tmp_path: Path) -> None:
    payload = capture()
    requests = payload["requests"]
    assert isinstance(requests, list)
    requests[0]["body"] = json.dumps(ACCOUNT)[:-1] + ',"cash":"1000"}'
    with pytest.raises(ValueError):
        inspect(tmp_path, payload)


@pytest.mark.parametrize(
    "source,feed,kind",
    [("OTHER", "paper", KIND), (SOURCE, "live", KIND), (SOURCE, "paper", "OTHER")],
)
def test_source_scope_cannot_be_substituted(
    tmp_path: Path, source: str, feed: str, kind: str
) -> None:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "evidence.sqlite3", root)
    try:
        journal.record_paper_account_verification(REF, b"synthetic", NOW)
        event = journal.append_next(
            source_id=source,
            feed=feed,
            event_kind=kind,
            payload=json.dumps(capture()).encode(),
            system_first_seen_at=NOW,
            processed_at=NOW,
        )
        with pytest.raises(ValueError, match="source"):
            inspect_account_inputs(
                journal,
                event.sequence,
                account_ref=REF,
                decision_at=NOW,
                session_open=OPEN,
                max_age=timedelta(seconds=30),
            )
    finally:
        journal.close()
