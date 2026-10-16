"""Synthetic immutable pre-session observations, never broker requests."""

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot import risk_baseline as baseline
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.risk_account_inputs import ENDPOINT, SCHEMA, SOURCE

NOW = datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
DAY = NOW.date()
ACCOUNT = {
    "id": "11111111-1111-4111-8111-111111111111",
    "currency": "USD",
    "status": "ACTIVE",
    "equity": "500",
    "cash": "500",
    "trading_blocked": False,
    "account_blocked": False,
    "trade_suspended_by_user": False,
}
REF = paper_account_ref(json.dumps(ACCOUNT).encode())


def request(path: str, body: object, now: datetime) -> dict[str, object]:
    return {
        "method": "GET",
        "path": path,
        "status": 200,
        "started_at": (now - timedelta(seconds=2)).isoformat(),
        "completed_at": (now - timedelta(seconds=1)).isoformat(),
        "body": json.dumps(body),
    }


def setup(
    tmp_path: Path,
    now: datetime = NOW,
    *,
    account: dict[str, object] | None = None,
    rows: list[dict[str, str]] | None = None,
    calendar_changes: dict[str, object] | None = None,
    source: str = SOURCE,
) -> tuple[Journal, int, int]:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "events.sqlite3", root)
    journal.record_paper_account_verification(REF, json.dumps(ACCOUNT).encode(), now)
    day = now.astimezone(baseline.NY).date()
    cal: dict[str, object] = {
        "account_ref": REF,
        "endpoint": ENDPOINT,
        "request": request(
            f"/v2/calendar?start={day}&end={day}",
            rows
            if rows is not None
            else [{"date": str(day), "open": "09:30", "close": "16:00"}],
            now,
        ),
    }
    cal.update(calendar_changes or {})
    c = journal.append_next(
        source_id=source,
        feed="paper",
        event_kind="CALENDAR_INPUT_CAPTURE",
        payload=json.dumps(cal).encode(),
        system_first_seen_at=now,
        processed_at=now,
    )
    requests = [
        request("/v2/account", account or ACCOUNT, now),
        request("/v2/positions", [], now),
        request("/v2/orders?status=open&limit=500&nested=false", [], now),
    ]
    capture = {
        "schema": SCHEMA,
        "account_ref": REF,
        "endpoint": ENDPOINT,
        "requests": requests,
    }
    for index, row in enumerate(requests):
        row["started_at"] = (now - timedelta(seconds=6 - index * 2)).isoformat()
        row["completed_at"] = (now - timedelta(seconds=5 - index * 2)).isoformat()
    a = journal.append_next(
        source_id=SOURCE,
        feed="paper",
        event_kind="ACCOUNT_INPUT_CAPTURE",
        payload=json.dumps(capture).encode(),
        system_first_seen_at=now,
        processed_at=now,
    )
    return journal, a.sequence, c.sequence


def freeze(
    journal: Journal, a: int, c: int, day: date = DAY
) -> baseline.EquityBaseline:
    return baseline.freeze_baseline(
        journal,
        account_ref=REF,
        account_sequence=a,
        calendar_sequence=c,
        session_day=day,
        max_age=timedelta(seconds=30),
    )


def test_missing_transfer_fields_do_not_block_equity_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    result = freeze(j, a, c)
    assert result.equity == 500 and not result.runtime_authorized
    assert result.frozen_at == NOW
    receipt_event = j.read_risk_baseline_receipt_events()[0][0]
    assert result.qualified_at == receipt_event.available_at == NOW
    assert result.receipt_sequence == receipt_event.sequence
    assert result.receipt_payload_ref == receipt_event.payload_ref
    assert result.session_open == NOW + timedelta(minutes=30)
    assert freeze(j, a, c) == result
    assert len(j.read_risk_baseline_events()) == 1
    j.close()
    root = tmp_path / "private"
    j = Journal.open_private(root / "events.sqlite3", root)
    assert baseline.read_baseline(j, account_ref=REF, session_day=DAY) == result
    j.reserve_order_intent("later", {"paper_account_ref": REF})
    assert baseline.read_baseline(j, account_ref=REF, session_day=DAY) == result
    j.close()


@pytest.mark.parametrize(
    "offset", [timedelta(seconds=-1), timedelta(seconds=31), timedelta(minutes=30)]
)
def test_future_stale_and_intraday_do_not_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offset: timedelta
) -> None:
    j, a, c = setup(tmp_path)
    monkeypatch.setattr(baseline, "_now", lambda: NOW + offset)
    with pytest.raises(ValueError):
        freeze(j, a, c)
    assert not j.read_risk_baseline_events()
    j.close()


@pytest.mark.parametrize(
    "changes", [{"account_ref": "foreign"}, {"endpoint": "https://api.alpaca.markets"}]
)
def test_calendar_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    j, a, c = setup(tmp_path, calendar_changes=changes)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with pytest.raises(ValueError):
        freeze(j, a, c)
    j.close()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"date": str(DAY), "open": "09:30", "close": "16:00"}] * 2,
        [{"date": str(DAY), "open": "16:00", "close": "09:30"}],
    ],
)
def test_closed_duplicate_invalid_calendar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]]
) -> None:
    j, a, c = setup(tmp_path, rows=rows)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with pytest.raises(ValueError):
        freeze(j, a, c)
    j.close()


@pytest.mark.parametrize(
    "when,opened,close",
    [
        (datetime(2026, 11, 27, 14, tzinfo=UTC), 14, "13:00"),
        (datetime(2026, 7, 2, 13, tzinfo=UTC), 13, "13:00"),
    ],
)
def test_dst_and_official_halfday(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    when: datetime,
    opened: int,
    close: str,
) -> None:
    j, a, c = setup(
        tmp_path,
        when,
        rows=[{"date": str(when.date()), "open": "09:30", "close": close}],
    )
    monkeypatch.setattr(baseline, "_now", lambda: when)
    assert freeze(j, a, c, when.date()).session_open.hour == opened
    j.close()


def test_state_tampering_and_changed_source_cannot_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    freeze(j, a, c)
    with pytest.raises(ValueError, match="replaced"):
        freeze(j, a + 1, c)
    with sqlite3.connect(tmp_path / "private" / "events.sqlite3") as db:
        db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            ("{}", f"risk_baseline:{REF}:{DAY}"),
        )
    with pytest.raises(ValueError, match="projection"):
        baseline.read_baseline(j, account_ref=REF, session_day=DAY)
    j.close()


@pytest.mark.parametrize(
    "account",
    [
        ACCOUNT | {"cash": "499"},
        ACCOUNT | {"equity": "0", "cash": "0"},
        ACCOUNT | {"account_blocked": True},
    ],
)
def test_nonflat_or_inactive_equity_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account: dict[str, object]
) -> None:
    j, a, c = setup(tmp_path, account=account)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with pytest.raises(ValueError):
        freeze(j, a, c)
    j.close()


def test_existing_local_intent_blocks_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    j.reserve_order_intent("prior", {"paper_account_ref": REF})
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with pytest.raises(ValueError):
        freeze(j, a, c)
    j.close()


def test_delay_until_open_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    clocks = iter([NOW, NOW, NOW + timedelta(minutes=30)])
    monkeypatch.setattr(baseline, "_now", lambda: next(clocks))
    with pytest.raises(ValueError):
        freeze(j, a, c)
    assert not j.read_risk_baseline_events()
    assert j.read_state(f"risk_baseline:{REF}:{DAY}") is None
    j.close()


def test_intent_inserted_before_transaction_blocks_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    original = j.append_next_if_state_missing

    def interleave(**kwargs: object) -> object:
        j.reserve_order_intent("interleaved", {"paper_account_ref": REF})
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(j, "append_next_if_state_missing", interleave)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with pytest.raises(ValueError):
        freeze(j, a, c)
    assert not j.read_risk_baseline_events()
    j.close()


def test_calendar_hash_corruption_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with sqlite3.connect(tmp_path / "private" / "events.sqlite3") as db:
        db.execute("DROP TRIGGER events_no_update")
        db.execute("UPDATE events SET payload=? WHERE sequence=?", (b"{}", c))
    with pytest.raises(ValueError, match="integrity"):
        freeze(j, a, c)
    j.close()


def test_foreign_calendar_source_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path, source="OTHER")
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with pytest.raises(ValueError, match="source"):
        freeze(j, a, c)
    j.close()


def test_calendar_http_clock_conflict_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = request(
        f"/v2/calendar?start={DAY}&end={DAY}",
        [{"date": str(DAY), "open": "09:30", "close": "16:00"}],
        NOW,
    )
    bad["completed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    j, a, c = setup(tmp_path, calendar_changes={"request": bad})
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    with pytest.raises(ValueError, match="chronology"):
        freeze(j, a, c)
    j.close()


def test_first_commit_returning_after_open_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    current = [NOW]
    monkeypatch.setattr(baseline, "_now", lambda: current[0])
    original = j.append_next_if_state_missing

    def delayed(**kwargs: object) -> object:
        result = original(**kwargs)  # type: ignore[arg-type]
        if kwargs["source_id"] == "ALPACA_RISK_BASELINE":
            current[0] = NOW + timedelta(minutes=30)
        return result

    monkeypatch.setattr(j, "append_next_if_state_missing", delayed)
    with pytest.raises(ValueError, match="commit was not observed"):
        freeze(j, a, c)
    assert len(j.read_risk_baseline_events()) == 1
    assert not j.read_risk_baseline_receipt_events()
    with pytest.raises(ValueError, match="lacks"):
        baseline.read_baseline(j, account_ref=REF, session_day=DAY)
    j.close()


@pytest.mark.parametrize("after_open", [False, True])
def test_crash_between_candidate_and_receipt_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_open: bool
) -> None:
    j, a, c = setup(tmp_path)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    original = j.append_next_if_state_missing

    def crash(**kwargs: object) -> object:
        if kwargs["source_id"] == "ALPACA_RISK_BASELINE_RECEIPT":
            raise RuntimeError("synthetic crash")
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(j, "append_next_if_state_missing", crash)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        freeze(j, a, c)
    j.close()
    root = tmp_path / "private"
    j = Journal.open_private(root / "events.sqlite3", root)
    monkeypatch.setattr(
        baseline, "_now", lambda: NOW + timedelta(minutes=30) if after_open else NOW
    )
    if after_open:
        with pytest.raises(ValueError, match="commit was not observed"):
            freeze(j, a, c)
        with pytest.raises(ValueError, match="lacks"):
            baseline.read_baseline(j, account_ref=REF, session_day=DAY)
    else:
        assert freeze(j, a, c).durability_observed_at == NOW
    j.close()


def test_receipt_commit_may_follow_open_for_already_observed_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    current = [NOW]
    monkeypatch.setattr(baseline, "_now", lambda: current[0])
    original = j.append_next_if_state_missing

    def delayed(**kwargs: object) -> object:
        if kwargs["source_id"] == "ALPACA_RISK_BASELINE_RECEIPT":
            current[0] = NOW + timedelta(minutes=30)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(j, "append_next_if_state_missing", delayed)
    result = freeze(j, a, c)
    assert result.durability_observed_at == NOW
    assert result.qualified_at == NOW + timedelta(minutes=30)
    assert result.qualified_at > NOW  # unavailable to a historical NOW decision
    receipt_event = j.read_risk_baseline_receipt_events()[0][0]
    assert result.receipt_sequence == receipt_event.sequence
    assert result.receipt_payload_ref == receipt_event.payload_ref
    assert baseline.read_baseline(j, account_ref=REF, session_day=DAY) == result
    assert j.read_risk_baseline_receipt_events()[0][0].available_at == NOW + timedelta(
        minutes=30
    )
    j.close()


def test_receipt_projection_tamper_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    freeze(j, a, c)
    with sqlite3.connect(tmp_path / "private" / "events.sqlite3") as db:
        db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            ("{}", f"risk_baseline_receipt:{REF}:{DAY}"),
        )
    with pytest.raises(ValueError, match="receipt projection"):
        baseline.read_baseline(j, account_ref=REF, session_day=DAY)
    j.close()


def test_inspection_crossing_open_leaves_unaccepted_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    j, a, c = setup(tmp_path)
    current = [NOW]
    monkeypatch.setattr(baseline, "_now", lambda: current[0])
    original = baseline.inspect_baseline

    def delayed(*args: object, **kwargs: object) -> baseline.EquityBaseline:
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        current[0] = NOW + timedelta(minutes=30)
        return result

    monkeypatch.setattr(baseline, "inspect_baseline", delayed)
    with pytest.raises(ValueError, match="commit was not observed"):
        freeze(j, a, c)
    assert not j.read_risk_baseline_receipt_events()
    j.close()


def test_receipt_cannot_reference_a_different_candidate_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    j, a, c = setup(tmp_path)
    monkeypatch.setattr(baseline, "_now", lambda: NOW)
    freeze(j, a, c)
    event, raw = j.read_risk_baseline_receipt_events()[0]
    body = json.loads(raw)
    body["baseline_payload_ref"] = "sha256:" + "0" * 64
    changed = json.dumps(body, sort_keys=True).encode()
    with sqlite3.connect(tmp_path / "private" / "events.sqlite3") as db:
        db.execute("DROP TRIGGER events_no_update")
        db.execute(
            "UPDATE events SET payload=?, payload_ref=? WHERE sequence=?",
            (changed, "sha256:" + hashlib.sha256(changed).hexdigest(), event.sequence),
        )
        db.execute(
            "UPDATE state SET value_json=? WHERE key=?",
            (changed.decode(), f"risk_baseline_receipt:{REF}:{DAY}"),
        )
    with pytest.raises(ValueError, match="does not prove"):
        baseline.read_baseline(j, account_ref=REF, session_day=DAY)
    j.close()
