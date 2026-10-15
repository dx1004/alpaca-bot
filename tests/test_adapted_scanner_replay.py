import base64
import hashlib
import importlib.util
import json
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.scanner_contract import load_accepted_scanner_contract
from alpaca_ross_bot.scanner_data import AlpacaScannerData

NOW = datetime(2026, 1, 15, 17, tzinfo=UTC)
NY = ZoneInfo("America/New_York")


def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "adapted_replay", "scripts/replay_adapted_scanner.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_capture(tmp_path: Path, mutation: str = "", paginated: bool = False) -> Path:
    directory = tmp_path / "capture"
    directory.mkdir()
    journal = Journal(directory / "journal.sqlite3", tmp_path)
    today = NOW.astimezone(NY).date()
    days = tuple(
        today - timedelta(days=n)
        for n in range(50, -1, -1)
        if (today - timedelta(days=n)).weekday() < 5
    )
    expected = tuple(day for day in days if day < today)[-30:]
    daily_stamp = datetime.combine(today, time.min, NY).astimezone(UTC).isoformat()
    prior_stamp = (
        datetime.combine(expected[-1], time.min, NY).astimezone(UTC).isoformat()
    )

    def fetch(
        url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        if "/v2/assets?" in url:
            return json.dumps(
                [
                    {"symbol": s, "class": "us_equity", "status": "active"}
                    for s in ("AAA", "EXP", "MISSING")
                ]
            ).encode()
        return json.dumps(
            {
                s: {
                    "latestTrade": {
                        "p": p,
                        "t": (NOW - timedelta(minutes=1)).isoformat(),
                    },
                    "prevDailyBar": {"c": 8, "t": prior_stamp},
                    "dailyBar": {"v": 500, "t": daily_stamp},
                }
                for s, p in (("AAA", 10), ("EXP", 20))
            }
        ).encode()

    client = AlpacaScannerData(
        PaperCredentials(SecretValue("synthetic-key"), SecretValue("synthetic-secret")),
        journal,
        timeout_seconds=1,
        fetch=fetch,
        clock=lambda: NOW,
    )
    run = client.collect_current_asset_iex_snapshots(max_symbols_per_batch=3)

    def emit(
        kind: str,
        feed: str,
        host: str,
        path: str,
        query: dict[str, str],
        response: object,
        at: datetime,
        prefix: str,
    ) -> int:
        raw = json.dumps(response).encode()
        if mutation == prefix + "_duplicate_json":
            raw = b'{"bars":{},"bars":{},"next_page_token":null}'
        if mutation == prefix + "_future":
            at = NOW + timedelta(seconds=10)
        metadata: dict[str, Any] = {
            "request_method": "GET",
            "request_url": "https://" + host + path + "?" + urlencode(query),
            "query": query,
            "requested_at": at.isoformat(),
            "received_at": at.isoformat(),
            "response_bytes": len(raw),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_response_base64": base64.b64encode(raw).decode(),
        }
        if mutation == prefix + "_url":
            metadata["request_url"] = "https://foreign.example/data"
        if mutation == prefix + "_hash":
            metadata["response_sha256"] = "0" * 64
        if mutation == prefix + "_size":
            metadata["response_bytes"] = True
        if mutation == prefix + "_naive":
            metadata["received_at"] = at.replace(tzinfo=None).isoformat()
        event = journal.append_next(
            source_id="foreign"
            if mutation == prefix + "_source"
            else "alpaca_scanner_data",
            feed="foreign" if mutation == prefix + "_feed" else feed,
            event_kind="OTHER" if mutation == prefix + "_kind" else kind,
            payload=json.dumps(metadata).encode(),
            system_first_seen_at=at,
            processed_at=at,
        )
        return event.sequence

    calendar = [{"date": str(day)} for day in days]
    if mutation == "calendar_duplicate_dates":
        calendar.append(calendar[-1])
    if mutation == "calendar_before_query":
        calendar.insert(0, {"date": str(days[0] - timedelta(days=1))})
    if mutation == "calendar_after_query":
        calendar.append({"date": str(today + timedelta(days=1))})
    calendar_sequence = emit(
        "ADAPTATION_CALENDAR_RAW_RESPONSE",
        "calendar",
        "paper-api.alpaca.markets",
        "/v2/calendar",
        {"start": str(days[0]), "end": str(today), "date_type": "TRADING"},
        calendar,
        NOW + timedelta(seconds=1),
        "calendar",
    )
    rows: list[dict[str, Any]] = [
        {"t": datetime.combine(day, time.min, NY).astimezone(UTC).isoformat(), "v": 100}
        for day in expected
    ]
    if mutation == "provider_naive":
        rows[0]["t"] = rows[0]["t"].replace("+00:00", "")
    if mutation == "missing_history_day":
        rows = rows[1:]
    history_pages = []
    chunks = [rows[:15], rows[15:]] if paginated else [rows]
    for index, chunk in enumerate(chunks):
        query = {
            "symbols": "FOREIGN" if mutation == "history_query_identity" else "AAA",
            "timeframe": "1Day",
            "start": datetime.combine(expected[0], time.min, NY).isoformat(),
            "end": (
                datetime.combine(today, time.min, NY) - timedelta(microseconds=1)
            ).isoformat(),
            "limit": "10000",
            "sort": "asc",
            "adjustment": "raw",
            "feed": "iex",
            "asof": str(today),
        }
        if index:
            query["page_token"] = "WRONG" if mutation == "wrong_token" else "NEXT"
        next_token = "NEXT" if index < len(chunks) - 1 else None
        if mutation == "unexhausted":
            next_token = "MORE"
        if mutation == "repeated_token":
            next_token = "NEXT"
        response = {"bars": {"AAA": chunk}, "next_page_token": next_token}
        at = NOW + timedelta(seconds=2, microseconds=index)
        sequence = emit(
            "ADAPTATION_HISTORY_RAW_RESPONSE",
            "iex",
            "data.alpaca.markets",
            "/v2/stocks/bars",
            query,
            response,
            at,
            "history",
        )
        history_pages.append(
            {"sequence": sequence, "received_at": at.isoformat(), "symbols": ["AAA"]}
        )
    capture = {
        "status": "CAPTURE_COMPLETE",
        "manifest_sequence": run.manifest_sequence,
        "calendar_sequence": calendar_sequence,
        "calendar_available_at": (NOW + timedelta(seconds=1)).isoformat(),
        "prior_sessions": [str(day) for day in expected],
        "history_pages": history_pages,
        "price_prefilter_symbols": ["AAA"],
        "completed_at": (NOW + timedelta(seconds=3)).isoformat(),
        "eodhd_requests": 0,
    }
    if mutation == "omit_pages":
        capture["history_pages"] = []
    if mutation == "omit_prefilter":
        capture["price_prefilter_symbols"] = []
    if mutation == "decision_naive":
        capture["completed_at"] = (
            (NOW + timedelta(seconds=3)).replace(tzinfo=None).isoformat()
        )
    (directory / "capture.json").write_text(json.dumps(capture))
    journal.close()
    return directory


@pytest.mark.parametrize("paginated", [False, True])
def test_real_collector_capture_replays_with_fixed_decision_and_all_input_clocks(
    tmp_path: Path,
    paginated: bool,
) -> None:
    directory = make_capture(tmp_path, paginated=paginated)
    module = runner()
    first = module.replay_capture(directory, tmp_path)
    second = module.replay_capture(directory, tmp_path)
    assert first["decision_at"] == (NOW + timedelta(seconds=3)).isoformat()
    assert first["result_sha256"] == second["result_sha256"]
    assert first["status_counts"] == {
        "SCREEN_MATCH": 1,
        "RULE_NOT_MET": 1,
        "INPUT_UNAVAILABLE": 1,
    }
    assert first["screen_match_symbols"] == ["AAA"]
    assert (
        first["result_count"] == first["catalog_count"] == first["requested_count"] == 3
    )
    assert first["returned_count"] == 2 and first["missing_snapshot_count"] == 1
    assert first["replay_before_result"] == 0 and first["replay_at_result"] == 1
    assert first["replay_before_total"] >= first["input_events_visible_at_decision"]
    assert first["replay_at_total"] == first["replay_before_total"] + 1
    assert first["strategy_eligibility"] == "BLOCKED_RESEARCH"
    assert not first["order_authority"] and first["eodhd_requests"] == 0


def test_every_outcome_binds_contract_rules_and_its_raw_inputs(tmp_path: Path) -> None:
    directory = make_capture(tmp_path)
    summary = runner().replay_capture(directory, tmp_path)
    capture = json.loads((directory / "capture.json").read_text())
    journal = Journal(directory / "journal.sqlite3", tmp_path)
    try:
        result_pair = journal.read_event(summary["result_sequence"])
        manifest_pair = journal.read_event(capture["manifest_sequence"])
        assert result_pair is not None and manifest_pair is not None
        result = json.loads(result_pair[1])
        manifest = json.loads(manifest_pair[1])
        context_refs = {
            manifest["asset_directory_sequence"],
            capture["manifest_sequence"],
            capture["calendar_sequence"],
        }
        snapshot_ref = manifest["snapshot_batch_sequences"][0]
        history_refs = {page["sequence"] for page in capture["history_pages"]}
        for row in result["results"]:
            assert row["contract_sha256"] == summary["contract_sha256"]
            assert tuple(row["rule_ids"]) == tuple(summary["rule_ids"])
            assert row["rule_ids"]
            expected_refs = set(context_refs)
            if row["symbol"] != "MISSING":
                expected_refs.add(snapshot_ref)
            if row["symbol"] == "AAA":
                expected_refs.update(history_refs)
            assert set(row["input_sequence_refs"]) == expected_refs
            assert all(journal.read_event(ref) is not None for ref in expected_refs)
    finally:
        journal.close()


def test_contract_drift_aborts_before_result_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = make_capture(tmp_path)
    capture = json.loads((directory / "capture.json").read_text())
    next_sequence = max(page["sequence"] for page in capture["history_pages"]) + 1
    module = runner()
    accepted_path = Path("evidence/general-iex-daily30-screen-v1.json")
    altered = accepted_path.read_bytes().replace(
        b'"rvol_minimum_inclusive": "5"', b'"rvol_minimum_inclusive": "4"'
    )
    assert altered != accepted_path.read_bytes()
    monkeypatch.setattr(
        module,
        "load_accepted_scanner_contract_file",
        lambda path: load_accepted_scanner_contract(altered),
    )
    with pytest.raises(ValueError, match="digest"):
        module.replay_capture(directory, tmp_path)
    journal = Journal(directory / "journal.sqlite3", tmp_path)
    try:
        assert journal.read_event(next_sequence) is None
    finally:
        journal.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "calendar_source",
        "calendar_feed",
        "calendar_kind",
        "calendar_url",
        "calendar_hash",
        "calendar_size",
        "calendar_naive",
        "calendar_future",
        "calendar_duplicate_dates",
        "calendar_before_query",
        "calendar_after_query",
        "history_source",
        "history_feed",
        "history_kind",
        "history_url",
        "history_hash",
        "history_size",
        "history_naive",
        "history_future",
        "history_duplicate_json",
        "history_query_identity",
        "provider_naive",
        "unexhausted",
        "omit_pages",
        "omit_prefilter",
        "decision_naive",
    ],
)
def test_unproven_capture_fails_before_recording_a_result(
    tmp_path: Path, mutation: str
) -> None:
    directory = make_capture(tmp_path, mutation)
    journal = Journal(directory / "journal.sqlite3", tmp_path)
    before = 0
    while journal.read_event(before + 1) is not None:
        before += 1
    journal.close()
    with pytest.raises(ValueError):
        runner().replay_capture(directory, tmp_path)
    journal = Journal(directory / "journal.sqlite3", tmp_path)
    assert journal.read_event(before + 1) is None
    journal.close()


@pytest.mark.parametrize("mutation", ["wrong_token", "repeated_token"])
def test_pagination_links_are_verified(tmp_path: Path, mutation: str) -> None:
    directory = make_capture(tmp_path, mutation, paginated=True)
    with pytest.raises(ValueError, match="token|pagination"):
        runner().replay_capture(directory, tmp_path)


def test_missing_returned_history_is_unavailable_not_a_trading_rule_failure(
    tmp_path: Path,
) -> None:
    directory = make_capture(tmp_path, "missing_history_day")
    summary = runner().replay_capture(directory, tmp_path)
    assert "AAA" not in summary["screen_match_symbols"]
    assert summary["status_counts"]["INPUT_UNAVAILABLE"] == 2
    assert any(
        reason.startswith("MISSING_SESSION:") for reason in summary["reason_counts"]
    )


@pytest.mark.parametrize("text", ["2026-01-15T17:00:00", "2026-01-15T12:00:00-05:00"])
def test_source_clocks_cannot_use_local_timezone_fallback(text: str) -> None:
    with pytest.raises(ValueError, match="UTC"):
        runner().stamp(text)


@pytest.mark.parametrize("suffix", ["Z", "+00:00"])
def test_source_nanoseconds_cannot_be_floored_into_earlier_availability(
    suffix: str,
) -> None:
    parsed = runner().stamp("2026-01-15T17:00:00.000000001" + suffix)
    assert parsed == NOW + timedelta(microseconds=1)
    assert runner().stamp("2026-01-15T17:00:00.000000000" + suffix) == NOW


@pytest.mark.parametrize(
    "text",
    [
        "2026-01-15T17:00:00,000000001Z",
        "2026-01-15T17:00:00.000000001+0000",
        "2026-01-15 17:00:00.000000001Z",
        "2026-01-15T17:00:00.0000000001Z",
    ],
)
def test_alternate_timestamp_spellings_cannot_bypass_precision_guard(text: str) -> None:
    with pytest.raises(ValueError, match="UTC ISO"):
        runner().stamp(text)
