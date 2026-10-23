"""Finite GET/data-stream evidence capture; never orders or baseline retro-freeze."""

import argparse
import asyncio
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.capital_stage import CapitalStageOwner
from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.feed_clients import make_observable_stream
from alpaca_ross_bot.feed_health import FeedKind
from alpaca_ross_bot.http_transport import bounded_alpaca_get
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.local_credentials import load_paper_credentials
from alpaca_ross_bot.market_input_checks import assess_market_record
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.risk_account_inputs import SCHEMA_V2, inspect_account_inputs
from alpaca_ross_bot.risk_baseline import freeze_baseline
from alpaca_ross_bot.risk_evidence_request import IncidentRiskRequest
from alpaca_ross_bot.risk_input_evidence import inspect_risk_inputs
from alpaca_ross_bot.risk_ledger_audit import audit_risk_ledger
from alpaca_ross_bot.risk_ownership import qualify_ownership


def check_collection_window(args: argparse.Namespace, at: datetime) -> None:
    """Bound the one-session diagnostic before loading credentials or connecting."""
    selected = getattr(args, "session_date", None)
    window = getattr(args, "collection_window", None)
    if selected is None and window is None:
        return
    if selected is None or window not in {"preopen", "open"}:
        raise ValueError("both session date and collection window required")
    if at.utcoffset() != timedelta(0):
        raise ValueError("UTC collection clock required")
    local = at.astimezone(ZoneInfo("America/New_York"))
    start, end = (
        (time(9, 15), time(9, 25))
        if window == "preopen"
        else (time(9, 30), time(9, 40))
    )
    if local.date() != selected or not start <= local.time() < end:
        raise ValueError("outside the declared diagnostic collection window")
    if window == "open" and getattr(args, "freeze_baseline", False):
        raise ValueError("open collection cannot create a baseline")


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


_BUILTIN_GET_RECORD = get_record
_BUILTIN_BOUNDED_GET = bounded_alpaca_get


def account_transport_origin() -> str:
    """Record configured transport identity, not a claim of actual API evidence."""
    return (
        "BUILTIN_PAPER_HTTPS"
        if get_record is _BUILTIN_GET_RECORD
        and bounded_alpaca_get is _BUILTIN_BOUNDED_GET
        else "INJECTED_EXCHANGE"
    )


async def capture(args: argparse.Namespace) -> dict[str, Any]:
    check_collection_window(args, datetime.now(UTC))
    transport_origin = account_transport_origin()
    root = args.runtime_root.resolve()
    if (
        getattr(args, "require_bound_account", False)
        and not (root / "journal.sqlite3").is_file()
    ):
        raise ValueError("an existing bound private journal is required")
    config = PaperConfig.from_toml(args.config)
    environment = load_paper_credentials(args.credentials_file, config)
    credentials = config.resolve_credentials(environment)
    headers = {
        "APCA-API-KEY-ID": credentials.key_id.reveal(),
        "APCA-API-SECRET-KEY": credentials.secret_key.reveal(),
    }
    journal = Journal.open_private(root / "journal.sqlite3", root)
    stream = None
    report: dict[str, Any] = {
        "broker_writes_performed": False,
        "runtime_authorized": False,
    }
    try:
        if (
            getattr(args, "require_bound_account", False)
            and journal.read_paper_account_ref() is None
        ):
            raise ValueError("dedicated account identity must already be bound")
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
        if getattr(args, "collection_window", None) is not None:
            # The declared 09:30 window must agree with fresh official evidence.
            if opened.astimezone(ZoneInfo("America/New_York")).time() != time(9, 30):
                raise ValueError("official session open differs from declared window")
            check_collection_window(args, datetime.now(UTC))
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
        if account_transport_origin() != transport_origin:
            raise ValueError("account transport changed during capture")
        payload = {
            "schema": SCHEMA_V2,
            "transport_origin": transport_origin,
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
        ownership = qualify_ownership(
            journal,
            event.sequence,
            account_ref=account_ref,
            decision_at=datetime.now(UTC),
            session_open=opened,
            max_age=timedelta(seconds=60),
        )
        baseline_frozen = False
        baseline_status = "NOT_REQUESTED"
        if getattr(args, "freeze_baseline", False):
            check_collection_window(args, datetime.now(UTC))
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
            "capture_schema": SCHEMA_V2,
            "transport_origin": transport_origin,
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
        metadata_sequences: dict[tuple[str, str], int] = {}
        for kind in ("trade", "quote"):
            for tape in ("A", "B", "C"):
                check_collection_window(args, datetime.now(UTC))
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
                metadata_sequences[kind, tape] = item.sequence
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
        symbols = tuple(
            sorted({"SPY", *(s for s, _ in ownership.owned_long_positions)})
        )
        channels = {"trades": symbols, "quotes": symbols, "bars": symbols}
        include_statuses = getattr(args, "include_statuses", False)
        if include_statuses:
            channels["statuses"] = symbols
        await stream.subscribe(channels)
        subscribed = stream.subscribed
        deadline = asyncio.get_running_loop().time() + args.stream_seconds
        assessed = fresh = valid = 0
        raw_records = 0
        non_price_types: dict[str, int] = {}
        status_refs: list[tuple[int, str, int]] = []
        codes: set[tuple[str, str, str]] = set()
        latest_quotes: dict[str, tuple[int, int, str]] = {}
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
                raw_records += 1
                record_type = value.get("T")
                if record_type not in {"t", "q"}:
                    non_price_types[record_type] = (
                        non_price_types.get(record_type, 0) + 1
                    )
                    if record_type == "s":
                        status_refs.append(
                            (stored[0].sequence, stored[0].payload_ref, index)
                        )
                    continue
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
                if value.get("T") == "q" and type(value.get("S")) is str:
                    latest_quotes[value["S"]] = (
                        stored[0].sequence,
                        index,
                        value.get("z", ""),
                    )
                for code, name in checked.conditions:
                    codes.add((kind, code, name or "UNMAPPED"))
        mark_refs = tuple(
            (
                symbol,
                latest_quotes[symbol][0],
                latest_quotes[symbol][1],
                metadata_sequences["quote", latest_quotes[symbol][2]],
            )
            for symbol, _ in ownership.owned_long_positions
            if symbol in latest_quotes
            and ("quote", latest_quotes[symbol][2]) in metadata_sequences
        )
        risk_at = datetime.now(UTC)
        check_collection_window(args, risk_at)
        risk_inputs = inspect_risk_inputs(
            journal,
            event.sequence,
            account_ref=account_ref,
            decision_at=risk_at,
            session_open=opened,
            max_age=timedelta(seconds=60),
            mark_refs=mark_refs,
            max_metadata_age=timedelta(seconds=60),
        )
        risk_event = journal.append_next(
            source_id="ALPACA_RISK_INPUTS",
            feed="paper",
            event_kind="REPORTED_INPUT_ASSESSMENT",
            payload=json.dumps(
                {"account_ref": account_ref, "assessment": asdict(risk_inputs)},
                default=str,
                sort_keys=True,
            ).encode(),
            system_first_seen_at=risk_at,
            processed_at=datetime.now(UTC),
        )
        report["risk_inputs"] = risk_inputs.public_summary() | {
            "assessment_sequence": risk_event.sequence,
        }
        if getattr(args, "observe_risk_owner", False):
            owner_at = datetime.now(UTC)
            check_collection_window(args, owner_at)
            owner = AccountRiskOwner(
                journal,
                account_ref,
                Path(__file__).resolve().parents[1]
                / "evidence/alpaca-equity-fees-20261012.json",
            )
            owner.observe(
                "read-only-capture:" + str(event.sequence),
                IncidentRiskRequest(
                    event.sequence,
                    calendar_event.sequence,
                    timedelta(seconds=60),
                    timedelta(seconds=60),
                    mark_refs,
                ),
                owner_at,
            )
            state = owner.view()
            assert state is not None
            current_snapshot = state["last_snapshot"]
            report["source_risk_owner"] = {
                "source_snapshot_qualified": state["source_snapshot_qualified"],
                "baseline_present": (
                    isinstance(current_snapshot, dict)
                    and current_snapshot.get("e0") is not None
                    and current_snapshot.get("baseline_ref") is not None
                    and current_snapshot.get("session")
                    == owner_at.astimezone(ZoneInfo("America/New_York"))
                    .date()
                    .isoformat()
                ),
                "daily_stop_latched": state["daily_stop_latched"],
                "issue_codes": state["issues"],
                "assessment_sequence": state["watermark"],
                "rebuild_verified": owner.verify_rebuild(),
                "reservation_performed": False,
                "runtime_authorized": False,
            }
            capital = CapitalStageOwner(journal, account_ref)
            capital_at = datetime.now(UTC)
            check_collection_window(args, capital_at)
            capital.assess(
                "read-only-capture:" + str(event.sequence),
                account_sequence=event.sequence,
                calendar_sequence=calendar_event.sequence,
                decision_at=capital_at,
                max_age=timedelta(seconds=60),
            )
            report["capital_stage"] = capital.public_summary() | {
                "rebuild_verified": capital.verify_rebuild(),
            }
        await stream.close()
        report["market"] = {
            "feed": "iex",
            "coverage": "NOT_SIP_EQUIVALENT",
            "diagnostic_symbol": "SPY",
            "subscription_acknowledged": subscribed,
            "assessment_scope": "TRADE_QUOTE_ONLY",
            "raw_records": raw_records,
            "non_price_record_counts": dict(sorted(non_price_types.items())),
            "status_subscription_requested": include_statuses,
            "status_subscription_acknowledged": include_statuses and subscribed,
            "raw_status_record_refs": status_refs,
            "current_trading_status_verified": False,
            "continuous_status_coverage_verified": False,
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
        report["zero_order_intents_verified"] = not journal.read_order_intent_ids()
        check_collection_window(args, datetime.now(UTC))
        return report
    finally:
        if stream is not None:
            await stream.close()
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true")
    parser.add_argument(
        "--observe-risk-owner",
        action="store_true",
        help="Observe risk and capital review state; never reserve or send",
    )
    parser.add_argument("--require-bound-account", action="store_true")
    parser.add_argument("--session-date", type=date.fromisoformat)
    parser.add_argument("--collection-window", choices=("preopen", "open"))
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
    parser.add_argument(
        "--include-statuses",
        action="store_true",
        help=(
            "Also retain raw IEX status messages; "
            "never prove current no-halt or continuity"
        ),
    )
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
