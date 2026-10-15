"""Offline replay of captured IEX inputs; no network or credential access."""

import argparse
import base64
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from alpaca_ross_bot.adapted_scanner import (
    PROFILE_ID,
    SESSION_SCOPE,
    ClockedPrice,
    ScreenContext,
    ScreenInput,
    evaluate_all,
)
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.replay import ReplayMode, replay_at
from alpaca_ross_bot.scanner_discovery_audit import audit_iex_asset_discovery
from alpaca_ross_bot.scanner_input_audit import DatedDailyVolume, VolumeContract

NY = ZoneInfo("America/New_York")


def stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("clock must be UTC text")
    source = re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.([0-9]{1,9}))?(?:Z|\+00:00)",
        value,
    )
    if source is None:
        raise ValueError("clock must use supported UTC ISO syntax")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("clock must be timezone-aware UTC")
    # datetime floors submicrosecond precision. Round source clocks upward
    # instead, so a +1 ns future observation cannot masquerade as available.
    fraction = source[1] or ""
    if any(digit != "0" for digit in fraction[6:]):
        result += timedelta(microseconds=1)
    return result.astimezone(UTC)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject(value: str) -> None:
    raise ValueError("nonfinite JSON constant")


def strict_json(raw: bytes) -> Any:
    return json.loads(
        raw, parse_float=Decimal, parse_constant=_reject, object_pairs_hook=_unique
    )


def raw_event(
    journal: Journal, sequence: int, *, kind: str, feed: str, decision: datetime
) -> tuple[EventEnvelope, dict[str, Any], Any]:
    if type(sequence) is not int or sequence < 1:
        raise ValueError("invalid raw sequence")
    pair = journal.read_event(sequence)
    if pair is None:
        raise ValueError("missing raw event")
    event, stored = pair
    if (
        event.sequence != sequence
        or event.source_id != "alpaca_scanner_data"
        or event.feed != feed
        or event.event_kind != kind
        or event.payload_ref != "sha256:" + hashlib.sha256(stored).hexdigest()
        or event.source_event_at is not None
        or event.provider_received_at is not None
        or event.available_at > decision
    ):
        raise ValueError("raw envelope provenance or availability mismatch")
    metadata = strict_json(stored)
    if not isinstance(metadata, dict) or metadata.get("request_method") != "GET":
        raise ValueError("invalid raw request metadata")
    requested, received = (
        stamp(metadata.get("requested_at")),
        stamp(metadata.get("received_at")),
    )
    if not (
        requested
        <= received
        == event.system_first_seen_at
        == event.processed_at
        == event.available_at
    ):
        raise ValueError("raw local clock conflict")
    encoded = metadata.get("raw_response_base64")
    if not isinstance(encoded, str):
        raise ValueError("raw encoding invalid")
    raw = base64.b64decode(encoded, validate=True)
    if (
        hashlib.sha256(raw).hexdigest() != metadata.get("response_sha256")
        or type(metadata.get("response_bytes")) is not int
        or len(raw) != metadata["response_bytes"]
    ):
        raise ValueError("raw response hash or length mismatch")
    return event, metadata, strict_json(raw)


def bound_query(metadata: dict[str, Any], host: str, path: str) -> dict[str, str]:
    query, url = metadata.get("query"), metadata.get("request_url")
    if not isinstance(query, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in query.items()
    ):
        raise ValueError("query metadata invalid")
    if not isinstance(url, str):
        raise ValueError("request URL missing")
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if (
        parts.scheme != "https"
        or parts.netloc != host
        or parts.path != path
        or parts.fragment
        or len(dict(pairs)) != len(pairs)
        or dict(pairs) != query
    ):
        raise ValueError("fixed endpoint/query provenance mismatch")
    return dict(query)


def clocked_price(
    symbol: str, value: object, event_time: object, available: datetime
) -> ClockedPrice:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError("price must be a JSON number")
    event = stamp(event_time)
    return ClockedPrice(
        symbol=symbol,
        value=Decimal(value),
        session_date=event.astimezone(NY).date(),
        event_at=event,
        available_at=available,
        source_id="alpaca",
        feed="iex",
        adjustment="raw",
    )


def replay_capture(directory: Path, runtime_root: Path) -> dict[str, Any]:
    capture = strict_json((directory / "capture.json").read_bytes())
    if not isinstance(capture, dict) or capture.get("status") != "CAPTURE_COMPLETE":
        raise ValueError("capture incomplete")
    decision = stamp(capture.get("completed_at"))
    journal = Journal(directory / "journal.sqlite3", runtime_root)
    try:
        input_events: dict[int, EventEnvelope] = {}

        def read(
            sequence: int, kind: str, feed: str
        ) -> tuple[EventEnvelope, dict[str, Any], Any]:
            event, metadata, value = raw_event(
                journal, sequence, kind=kind, feed=feed, decision=decision
            )
            input_events[sequence] = event
            return event, metadata, value

        audit = audit_iex_asset_discovery(
            journal,
            manifest_sequence=capture["manifest_sequence"],
            decision_at=decision,
        )
        if not audit.request_coverage_complete:
            raise ValueError("discovery requests incomplete")
        manifest = journal.read_event(audit.manifest_sequence)
        assert manifest is not None
        input_events[audit.manifest_sequence] = manifest[0]
        calendar_event, metadata, calendar = read(
            capture["calendar_sequence"], "ADAPTATION_CALENDAR_RAW_RESPONSE", "calendar"
        )
        query = bound_query(metadata, "paper-api.alpaca.markets", "/v2/calendar")
        today = decision.astimezone(NY).date()
        if (
            set(query) != {"start", "end", "date_type"}
            or query["date_type"] != "TRADING"
            or query["end"] != str(today)
            or date.fromisoformat(query["start"]) > today
            or calendar_event.sequence <= audit.manifest_sequence
            or stamp(metadata["requested_at"]) < manifest[0].available_at
            or stamp(capture["calendar_available_at"]) != calendar_event.available_at
            or not isinstance(calendar, list)
        ):
            raise ValueError("calendar provenance invalid")
        days = tuple(date.fromisoformat(row["date"]) for row in calendar)
        expected = tuple(day for day in days if day < today)[-30:]
        if (
            tuple(sorted(set(days))) != days
            or any(
                day < date.fromisoformat(query["start"]) or day > today for day in days
            )
            or today not in days
            or len(expected) != 30
            or capture["prior_sessions"] != [str(day) for day in expected]
        ):
            raise ValueError("calendar coverage invalid")
        context = ScreenContext(
            decision,
            days,
            calendar_event.available_at,
            audit.catalog_symbols,
            read(
                audit.catalog_sequence, "ASSET_DIRECTORY_RAW_RESPONSE", "asset_master"
            )[0].available_at,
        )
        snapshots: dict[str, tuple[dict[str, Any], datetime]] = {}
        for sequence in audit.snapshot_batch_sequences:
            event, _, values = read(sequence, "IEX_SNAPSHOT_RAW_RESPONSE", "iex")
            snapshots.update(
                {
                    symbol: (value, event.available_at)
                    for symbol, value in values.items()
                }
            )
        eligible: list[str] = []
        for symbol, (snapshot, available) in snapshots.items():
            try:
                trade = snapshot["latestTrade"]
                price = clocked_price(symbol, trade["p"], trade["t"], available)
                if (
                    price.session_date == today
                    and price.value.is_finite()
                    and Decimal(1) <= price.value < Decimal(20)
                    and price.event_at <= available <= decision
                ):
                    eligible.append(symbol)
            except (KeyError, TypeError, ValueError):
                pass
        eligible.sort()
        if capture["price_prefilter_symbols"] != eligible:
            raise ValueError("price prefilter coverage mismatch")
        histories: dict[str, list[DatedDailyVolume]] = {}
        history_clocks: dict[str, datetime] = {}
        queried: list[str] = []
        next_token: str | None = None
        active_query: dict[str, str] | None = None
        seen_tokens: set[str] = set()
        previous_at, previous_sequence = (
            calendar_event.available_at,
            calendar_event.sequence,
        )
        for page in capture["history_pages"]:
            event, metadata, values = read(
                page["sequence"], "ADAPTATION_HISTORY_RAW_RESPONSE", "iex"
            )
            query = bound_query(metadata, "data.alpaca.markets", "/v2/stocks/bars")
            required = {
                "symbols",
                "timeframe",
                "start",
                "end",
                "limit",
                "sort",
                "adjustment",
                "feed",
                "asof",
            }
            symbols = page["symbols"]
            if (
                not isinstance(symbols, list)
                or not symbols
                or any(not isinstance(s, str) or not s for s in symbols)
                or len(set(symbols)) != len(symbols)
                or set(query) - {"page_token"} != required
                or query["symbols"] != ",".join(symbols)
                or query["feed"] != "iex"
                or query["adjustment"] != "raw"
                or query["timeframe"] != "1Day"
                or query["sort"] != "asc"
                or query["limit"] != "10000"
                or query["asof"] != str(today)
                or datetime.fromisoformat(query["start"])
                != datetime.combine(expected[0], time.min, NY)
                or datetime.fromisoformat(query["end"])
                != datetime.combine(today, time.min, NY) - timedelta(microseconds=1)
                or event.sequence <= previous_sequence
                or stamp(metadata["requested_at"]) < previous_at
                or stamp(page["received_at"]) != event.available_at
            ):
                raise ValueError("historical contract or clock mismatch")
            base_query = {
                key: value for key, value in query.items() if key != "page_token"
            }
            if active_query is None:
                if "page_token" in query or set(symbols) & set(queried):
                    raise ValueError(
                        "historical first page or duplicate request invalid"
                    )
                queried.extend(symbols)
                active_query = base_query
                seen_tokens = set()
            elif base_query != active_query or query.get("page_token") != next_token:
                raise ValueError("historical pagination chain mismatch")
            if (
                not isinstance(values, dict)
                or not isinstance(values.get("bars"), dict)
                or "next_page_token" not in values
                or set(values["bars"]) - set(symbols)
            ):
                raise ValueError("historical response shape or identity invalid")
            token = values["next_page_token"]
            if token is not None and (
                not isinstance(token, str) or not token or token in seen_tokens
            ):
                raise ValueError("historical page token invalid or repeated")
            if token is not None:
                seen_tokens.add(token)
            next_token = token
            for symbol in symbols:
                history_clocks[symbol] = event.available_at
            for symbol, rows in values["bars"].items():
                if not isinstance(rows, list):
                    raise ValueError("historical bars must be lists")
                contract = VolumeContract("alpaca", symbol, "iex", "raw", SESSION_SCOPE)
                for row in rows:
                    event_at = stamp(row["t"])
                    day = event_at.astimezone(NY).date()
                    completion = datetime.combine(
                        day + timedelta(days=1), time.min, NY
                    ).astimezone(UTC)
                    histories.setdefault(symbol, []).append(
                        DatedDailyVolume(
                            contract,
                            day,
                            event_at,
                            completion,
                            row["v"],
                            event.available_at,
                            event.available_at,
                        )
                    )
            previous_at, previous_sequence = event.available_at, event.sequence
            if token is None:
                active_query = None
        if active_query is not None or queried != eligible:
            raise ValueError("historical pagination or request coverage incomplete")
        items: list[ScreenInput] = []
        malformed: list[str] = []
        for symbol, (snapshot, available) in snapshots.items():
            try:
                trade = snapshot["latestTrade"]
                price = clocked_price(symbol, trade["p"], trade["t"], available)
                previous = current = None
                if symbol in history_clocks:
                    row = snapshot["prevDailyBar"]
                    previous = clocked_price(symbol, row["c"], row["t"], available)
                    row = snapshot["dailyBar"]
                    event_at = stamp(row["t"])
                    current = DatedDailyVolume(
                        VolumeContract("alpaca", symbol, "iex", "raw", SESSION_SCOPE),
                        event_at.astimezone(NY).date(),
                        event_at,
                        None,
                        row["v"],
                        available,
                        available,
                    )
                items.append(
                    ScreenInput(
                        symbol,
                        price,
                        previous,
                        current,
                        tuple(histories.get(symbol, [])),
                        history_clocks.get(symbol, available),
                        symbol in history_clocks,
                    )
                )
            except (KeyError, TypeError, ValueError):
                malformed.append(symbol)
        results = evaluate_all(context, tuple(items))
        result_rows = []
        for result in results:
            row = asdict(result)
            row["percent_change"] = (
                str(result.percent_change)
                if result.percent_change is not None
                else None
            )
            result_rows.append(row)
        inputs = sorted(input_events)
        payload = json.dumps(
            {
                "profile_id": PROFILE_ID,
                "decision_at": decision.isoformat(),
                "input_sequences": inputs,
                "results": result_rows,
            },
            sort_keys=True,
        ).encode()
        # Preserve the contiguous journal prefix, including intermediate
        # projections. Selected raw input sequence IDs alone contain gaps.
        recorded_events: list[EventEnvelope] = []
        for sequence in range(1, max(inputs) + 1):
            pair = journal.read_event(sequence)
            if pair is None:
                raise ValueError("journal prefix is incomplete")
            recorded_events.append(pair[0])
        decision_frame = replay_at(recorded_events, ReplayMode.PROSPECTIVE, decision)
        decision_frame.require_recorded_timing()
        visible_inputs = sum(
            row.event.sequence in input_events for row in decision_frame.observations
        )
        if visible_inputs != len(input_events):
            raise ValueError("input availability replay failed")
        # The processing clock follows input verification, including prefix
        # replay. Do not backdate a result to when verification started.
        recorded_at = datetime.now(UTC)
        if recorded_at < decision:
            raise ValueError("capture completion is in the future")
        event = journal.append_next(
            source_id="adapted_scanner",
            feed="iex",
            event_kind="ADAPTED_SCREEN_RESULT",
            payload=payload,
            system_first_seen_at=recorded_at,
            processed_at=recorded_at,
        )
        for sequence in range(max(inputs) + 1, event.sequence + 1):
            pair = journal.read_event(sequence)
            if pair is None:
                raise ValueError("journal result prefix is incomplete")
            recorded_events.append(pair[0])
        before = replay_at(
            recorded_events,
            ReplayMode.PROSPECTIVE,
            recorded_at - timedelta(microseconds=1),
        )
        after = replay_at(recorded_events, ReplayMode.PROSPECTIVE, recorded_at)
        after.require_recorded_timing()
        before_derived = sum(
            row.event.sequence == event.sequence for row in before.observations
        )
        after_derived = sum(
            row.event.sequence == event.sequence for row in after.observations
        )
        if before_derived != 0 or after_derived != 1:
            raise ValueError("result availability replay failed")
        summary = {
            "profile_id": PROFILE_ID,
            "decision_at": decision.isoformat(),
            "catalog_count": len(audit.catalog_symbols),
            "requested_count": len(audit.requested_symbols),
            "returned_count": len(audit.returned_symbols),
            "missing_snapshot_count": len(audit.missing_symbols),
            "request_coverage_complete": audit.request_coverage_complete,
            "returned_object_coverage_complete": audit.returned_data_coverage_complete,
            "result_count": len(results),
            "status_counts": dict(Counter(row.status for row in results)),
            "reason_counts": dict(
                Counter(reason for row in results for reason in row.reasons)
            ),
            "screen_match_symbols": [
                row.symbol for row in results if row.status == "SCREEN_MATCH"
            ],
            "malformed_snapshot_symbols": sorted(malformed),
            "result_sequence": event.sequence,
            "result_sha256": hashlib.sha256(payload).hexdigest(),
            "replay_before_result": before_derived,
            "replay_at_result": after_derived,
            "input_events_visible_at_decision": visible_inputs,
            "replay_before_total": len(before.observations),
            "replay_at_total": len(after.observations),
            "eodhd_requests": capture["eodhd_requests"],
            "coverage_label": "NOT_SIP_EQUIVALENT",
            "source_freshness": "NO_EXECUTION_TTL_ACCEPTANCE",
            "strategy_eligibility": "BLOCKED_RESEARCH",
            "order_authority": False,
        }
        (directory / "screen-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n"
        )
        return summary
    finally:
        journal.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--runtime-root", type=Path, default=Path("var"))
    args = parser.parse_args()
    print(json.dumps(replay_capture(args.directory, args.runtime_root), sort_keys=True))
