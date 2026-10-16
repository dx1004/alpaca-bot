"""Finite GET/data-stream evidence capture; never orders or baseline retro-freeze."""

import argparse
import asyncio
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.feed_clients import make_observable_stream
from alpaca_ross_bot.feed_health import FeedKind
from alpaca_ross_bot.http_transport import bounded_alpaca_get
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.local_credentials import load_paper_credentials
from alpaca_ross_bot.market_input_checks import assess_market_record
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.risk_account_inputs import inspect_account_inputs
from alpaca_ross_bot.risk_baseline import freeze_baseline
from alpaca_ross_bot.risk_ledger_audit import audit_risk_ledger


def get_record(url: str, headers: dict[str, str]) -> dict[str, object]:
    started = datetime.now(UTC)
    body = bounded_alpaca_get(url, headers, 5, 2_097_152)
    path = url.removeprefix("https://paper-api.alpaca.markets")
    return {
        "method": "GET",
        "path": path,
        "status": 200,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "body": body.decode(),
    }


async def capture(args: argparse.Namespace) -> dict[str, Any]:
    config = PaperConfig.from_toml(args.config)
    environment = load_paper_credentials(args.credentials_file, config)
    credentials = config.resolve_credentials(environment)
    headers = {
        "APCA-API-KEY-ID": credentials.key_id.reveal(),
        "APCA-API-SECRET-KEY": credentials.secret_key.reveal(),
    }
    root = args.runtime_root.resolve()
    journal = Journal.open_private(root / "journal.sqlite3", root)
    stream = None
    report: dict[str, Any] = {
        "broker_writes_performed": False,
        "runtime_authorized": False,
    }
    try:
        base = "https://paper-api.alpaca.markets"
        account = get_record(base + "/v2/account", headers)
        raw_account = str(account["body"]).encode()
        account_ref = paper_account_ref(raw_account)
        journal.record_paper_account_verification(
            account_ref, raw_account, datetime.now(UTC)
        )
        day = datetime.now(ZoneInfo("America/New_York")).date()
        calendar = get_record(
            base
            + "/v2/calendar?"
            + urlencode(
                {"start": day.isoformat(), "end": (day + timedelta(days=7)).isoformat()}
            ),
            headers,
        )
        calendar_rows = json.loads(str(calendar["body"]))
        today = next((v for v in calendar_rows if v["date"] == day.isoformat()), None)
        if today is None:
            raise ValueError("no exchange session today; no baseline may be created")
        opened = (
            datetime.fromisoformat(day.isoformat() + "T" + today["open"])
            .replace(tzinfo=ZoneInfo("America/New_York"))
            .astimezone(UTC)
        )
        calendar_at = datetime.now(UTC)
        calendar_event = journal.append_next(
            source_id="ALPACA_PAPER_RISK_HTTP",
            feed="paper",
            event_kind="CALENDAR_INPUT_CAPTURE",
            payload=json.dumps(
                {"account_ref": account_ref, "endpoint": base, "request": calendar}
            ).encode(),
            system_first_seen_at=calendar_at,
            processed_at=calendar_at,
        )
        requests = [account]
        for path in (
            "/v2/account/configurations",
            "/v2/positions",
            "/v2/orders?status=open&limit=500&nested=false",
        ):
            requests.append(get_record(base + path, headers))
        # Retain bounded activity pages; API exhaustion is not settlement proof.
        token = None
        activity_pages = 0
        for _ in range(20):
            query = {"direction": "desc", "page_size": "100"}
            if token is not None:
                query["page_token"] = token
            record = get_record(
                base + "/v2/account/activities?" + urlencode(query), headers
            )
            requests.append(record)
            page = json.loads(str(record["body"]))
            if not isinstance(page, list):
                raise ValueError("activity page is not an array")
            activity_pages += 1
            if not page:
                break
            next_token = page[-1].get("id")
            if not isinstance(next_token, str) or next_token == token:
                raise ValueError("activity pagination identity is unavailable")
            token = next_token
        at = datetime.now(UTC)
        payload = {
            "schema": "PAPER_RISK_INPUT_CAPTURE_V1",
            "account_ref": account_ref,
            "endpoint": base,
            "requests": requests,
        }
        event = journal.append_next(
            source_id="ALPACA_PAPER_RISK_HTTP",
            feed="paper",
            event_kind="ACCOUNT_INPUT_CAPTURE",
            payload=json.dumps(payload, separators=(",", ":")).encode(),
            system_first_seen_at=at,
            processed_at=at,
        )
        facts = inspect_account_inputs(
            journal,
            event.sequence,
            account_ref=account_ref,
            decision_at=datetime.now(UTC),
            session_open=opened,
            max_age=timedelta(seconds=60),
        )
        money = dict(facts.money)
        baseline_frozen = False
        baseline_status = "NOT_REQUESTED"
        if getattr(args, "freeze_baseline", False):
            try:
                freeze_baseline(
                    journal,
                    account_ref=account_ref,
                    account_sequence=event.sequence,
                    calendar_sequence=calendar_event.sequence,
                    session_day=day,
                    max_age=timedelta(seconds=60),
                )
                baseline_frozen = True
                baseline_status = "SOURCE_BOUND_PRESESSION_E0"
            except ValueError:
                baseline_status = "BASELINE_QUALIFICATION_FAILED"
        assessed_at = datetime.now(UTC)
        ledger_audit = audit_risk_ledger(
            journal,
            event.sequence,
            calendar_event.sequence,
            account_ref=account_ref,
            decision_at=assessed_at,
            max_age=timedelta(seconds=60),
            fee_policy=Path(__file__).resolve().parents[1]
            / "evidence/alpaca-equity-fees-20261012.json",
        )
        audit_event = journal.append_next(
            source_id="ALPACA_RISK_LEDGER",
            feed="paper",
            event_kind="LEDGER_ASSESSMENT",
            payload=json.dumps(
                {
                    "account_ref": account_ref,
                    "capture_sequence": event.sequence,
                    "calendar_sequence": calendar_event.sequence,
                    "assessment": asdict(ledger_audit),
                    "baseline_status": baseline_status,
                },
                default=str,
                sort_keys=True,
            ).encode(),
            system_first_seen_at=assessed_at,
            processed_at=datetime.now(UTC),
        )
        report["ledger"] = ledger_audit.public_summary() | {
            "assessment_sequence": audit_event.sequence,
            "baseline_status": baseline_status,
        }
        report["account"] = {
            "capture_sequence": event.sequence,
            "calendar_sequence": calendar_event.sequence,
            "available_profile": facts.available_profile,
            "available_funds_observed": facts.available_funds is not None,
            "before_session_open": facts.before_session_open,
            "starting_equity_matches_500": money.get("equity") == 500,
            "settlement_status": facts.settlement_status,
            "reason_codes": list(facts.reasons),
            "activity_pages": activity_pages,
            "pre_session_baseline_frozen": baseline_frozen,
        }
        names: dict[tuple[str, str], dict[str, str]] = {}
        metadata_refs = []
        for kind in ("trade", "quote"):
            for tape in ("A", "B", "C"):
                url = (
                    "https://data.alpaca.markets/v2/stocks/meta/conditions/"
                    + kind
                    + "?tape="
                    + tape
                )
                started = datetime.now(UTC)
                raw = bounded_alpaca_get(url, headers, 5, 1_048_576)
                metadata = json.loads(raw)
                if not isinstance(metadata, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in metadata.items()
                ):
                    raise ValueError("condition metadata is malformed")
                names[kind, tape] = metadata
                item = journal.append_next(
                    source_id="ALPACA_MARKET_METADATA",
                    feed="iex",
                    event_kind="CONDITION_CODE_METADATA",
                    payload=json.dumps({"url": url, "body": raw.decode()}).encode(),
                    system_first_seen_at=started,
                    processed_at=datetime.now(UTC),
                )
                metadata_refs.append(item.sequence)
        stream = make_observable_stream(
            config,
            environment,
            FeedKind.EQUITIES,
            timeout_seconds=5,
            heartbeat_interval_seconds=2,
            heartbeat_timeout_seconds=5,
            journal=journal,
        )
        await stream.open()
        await stream.subscribe(
            {"trades": ("SPY",), "quotes": ("SPY",), "bars": ("SPY",)}
        )
        subscribed = stream.subscribed
        deadline = asyncio.get_running_loop().time() + args.stream_seconds
        assessed = fresh = valid = 0
        codes: set[tuple[str, str, str]] = set()
        while asyncio.get_running_loop().time() < deadline:
            left = deadline - asyncio.get_running_loop().time()
            try:
                frame = await asyncio.wait_for(stream.receive(), timeout=left)
            except TimeoutError:
                break
            if frame.journal_sequence is None:
                raise ValueError("stream frame was not durable")
            stored = journal.read_event(frame.journal_sequence)
            if stored is None:
                raise ValueError("raw frame disappeared")
            records = json.loads(stored[1])
            for index, value in enumerate(records):
                kind = "trade" if value.get("T") == "t" else "quote"
                mapping = names.get((kind, value.get("z")))
                checked = assess_market_record(
                    journal,
                    sequence=stored[0].sequence,
                    record_index=index,
                    payload_ref=stored[0].payload_ref,
                    decision_at=datetime.now(UTC),
                    condition_names=mapping,
                )
                assessed += 1
                valid += int(checked.schema_valid)
                fresh += int(checked.measured_fresh)
                for code, name in checked.conditions:
                    codes.add((kind, code, name or "UNMAPPED"))
        await stream.close()
        report["market"] = {
            "feed": "iex",
            "coverage": "NOT_SIP_EQUIVALENT",
            "diagnostic_symbol": "SPY",
            "subscription_acknowledged": subscribed,
            "assessed_records": assessed,
            "schema_valid_records": valid,
            "measured_fresh_records": fresh,
            "metadata_refs": metadata_refs,
            "observed_condition_names": sorted(codes),
            "clock_origin_qualified": False,
            "trigger_policy_accepted": False,
            "runtime_eligible": False,
            "continuity_recovery_required_after_close": stream.gap_unresolved,
        }
        report["observed_at"] = datetime.now(UTC).isoformat()
        with sqlite3.connect(
            (root / "journal.sqlite3").as_uri() + "?mode=ro", uri=True
        ) as audit:
            if audit.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("private journal integrity failed")
            sequences = [
                row[0]
                for row in audit.execute(
                    "SELECT sequence FROM events ORDER BY sequence"
                )
            ]
        if any(journal.read_event(sequence) is None for sequence in sequences):
            raise ValueError("private source event disappeared")
        report["private_integrity_verified"] = True
        return report
    finally:
        if stream is not None:
            await stream.close()
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true")
    parser.add_argument(
        "--freeze-baseline",
        action="store_true",
        help="Freeze only a qualified source-bound pre-session E0; never backfill it",
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config/paper.example.toml")
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=Path.home() / ".local/share/alpaca-bot/paper.json",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path.home()
        / ".local/state/alpaca-bot/risk-input-qualification-20261016",
    )
    parser.add_argument("--stream-seconds", type=float, default=10)
    args = parser.parse_args()
    if not args.connect:
        print(json.dumps({"status": "NOT_RUN", "runtime_authorized": False}))
        return
    if not 0 < args.stream_seconds <= 30:
        parser.error("stream capture must be positive and at most 30 seconds")
    try:
        report = asyncio.run(capture(args))
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_class": type(error).__name__,
                    "private_values_logged": False,
                    "broker_writes_performed": False,
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
