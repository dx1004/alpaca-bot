"""Append-only provenance capture for Alpaca news messages.

This records provider facts and conservative duplicate/revision observations.
It does not classify catalysts or make any article strategy-eligible.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from alpaca_ross_bot.feed_health import FeedHealth, FeedKind
from alpaca_ross_bot.journal import Journal


class NewsEvidenceError(ValueError):
    """A news frame cannot be safely interpreted as evidence."""


def capture_news_frame(
    journal: Journal,
    raw: str | bytes,
    *,
    first_seen_at: datetime,
    processed_at: datetime,
    health: FeedHealth,
    health_now: datetime,
    max_heartbeat_age_seconds: float,
) -> tuple[dict[str, object], ...]:
    """Journal article versions without inferring catalyst or freshness rules.

    ``raw`` should already have been recorded as a WebSocket ``RAW_FRAME`` by
    the stream client. Provider timestamps remain metadata; local first-seen
    time controls replay availability. Every returned record is blocked.
    """
    for name, value in (
        ("first_seen_at", first_seen_at),
        ("processed_at", processed_at),
        ("health_now", health_now),
    ):
        _require_utc(value, name)
    if processed_at < first_seen_at:
        raise NewsEvidenceError("processing cannot precede system first-seen")
    if not isfinite(max_heartbeat_age_seconds) or max_heartbeat_age_seconds <= 0:
        raise ValueError("heartbeat age must be positive")
    if health.kind is not FeedKind.NEWS:
        raise NewsEvidenceError("news evidence requires the news feed health state")
    health_reasons = health.reasons(
        now=health_now,
        max_heartbeat_age=_seconds(max_heartbeat_age_seconds),
        max_data_age=None,
    )
    messages = _decode_frame(raw)
    results: list[dict[str, object]] = []
    for message in messages:
        try:
            record = _article_record(message)
        except NewsEvidenceError as exc:
            payload = json.dumps(
                {
                    "status": "MALFORMED",
                    "eligibility": "BLOCKED",
                    "reason": str(exc),
                    "raw_message": message,
                    "first_seen_at": first_seen_at.isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            journal.append_next(
                source_id="alpaca_news",
                feed="alpaca_news",
                event_kind="NEWS_REJECTED",
                payload=payload,
                system_first_seen_at=first_seen_at,
                processed_at=processed_at,
            )
            results.append({"status": "MALFORMED", "eligibility": "BLOCKED"})
            continue

        article_id = record["id"]
        article_key = f"news_article:{article_id}"
        article_state = journal.read_state(article_key)
        fingerprint = hashlib.sha256(_canonical_json(message)).hexdigest()
        fingerprint_key = f"news_fingerprint:{fingerprint}"
        duplicate_state = journal.read_state(fingerprint_key)
        original_first_seen = (
            article_state["first_seen_at"]
            if isinstance(article_state, dict)
            and isinstance(article_state.get("first_seen_at"), str)
            else first_seen_at.isoformat()
        )
        if duplicate_state is not None:
            relation = "EXACT_CONTENT_REPEAT"
        elif article_state is None:
            relation = "INITIAL_VERSION"
        elif (
            isinstance(article_state, dict)
            and article_state.get("created_at") != record["created_at"]
        ):
            relation = "CONFLICTING_CREATED_AT"
        else:
            relation = "REVISION_UNVERIFIED"

        created_at = _parse_timestamp(_string_field(record, "created_at"), "created_at")
        updated_at = _parse_timestamp(_string_field(record, "updated_at"), "updated_at")
        timestamp_conflict = (
            record["timestamp_conflict"]
            or created_at > first_seen_at
            or updated_at > first_seen_at
        )
        created_age_seconds = (first_seen_at - created_at).total_seconds()
        age_band = _headline_age_band(created_age_seconds)
        record["timestamp_conflict"] = timestamp_conflict
        if health_reasons:
            status = "FEED_UNHEALTHY"
        elif relation == "CONFLICTING_CREATED_AT" or timestamp_conflict:
            status = "CONFLICTING"
        elif relation == "EXACT_CONTENT_REPEAT":
            status = "DUPLICATE_OR_REPRINT"
        elif relation == "REVISION_UNVERIFIED":
            status = "REVISION_UNVERIFIED"
        else:
            status = "RECEIVED_UNVERIFIED"

        payload_record: dict[str, object] = {
            **record,
            "fingerprint": fingerprint,
            "content_relation": relation,
            "status": status,
            "feed_health_reasons": [reason.value for reason in health_reasons],
            "article_first_seen_at": original_first_seen,
            "version_first_seen_at": first_seen_at.isoformat(),
            "provider_created_lag_seconds": (
                first_seen_at - created_at
            ).total_seconds(),
            "provider_created_age_band": age_band,
            "age_band_basis": "alpaca_created_at_first_seen_display_approximation",
            "age_band_boundary_convention": "exact_source_boundaries_unresolved",
            "delay_assessment": "UNASSESSED_NO_APPROVED_PATH_THRESHOLD",
            "news_eligibility": "BLOCKED_RESEARCH",
            "market_reaction": "NOT_EVALUATED",
            "ticker_mapping": "PROVIDER_SYMBOLS_ONLY",
        }
        serialized = json.dumps(
            payload_record,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        state_updates: dict[str, object] = {
            fingerprint_key: {"first_seen_at": first_seen_at.isoformat()}
        }
        if article_state is None:
            state_updates[article_key] = {
                "created_at": record["created_at"],
                "first_seen_at": first_seen_at.isoformat(),
            }
        journal.append_next(
            source_id="alpaca_news",
            feed="alpaca_news",
            event_kind="NEWS_ARTICLE_VERSION",
            payload=serialized,
            system_first_seen_at=first_seen_at,
            processed_at=processed_at,
            state_updates=state_updates,
        )
        results.append(payload_record)
    return tuple(results)


def _decode_frame(raw: str | bytes) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise NewsEvidenceError("news frame is not strict JSON") from exc
    if not isinstance(decoded, list) or not decoded:
        raise NewsEvidenceError("news frame must be a nonempty message array")
    messages: list[dict[str, Any]] = []
    for item in decoded:
        if not isinstance(item, dict) or item.get("T") != "n":
            raise NewsEvidenceError("frame contains a non-news message")
        messages.append(item)
    return messages


def _article_record(message: Mapping[str, Any]) -> dict[str, object]:
    article_id = message.get("id")
    if type(article_id) is not int or article_id < 1:
        raise NewsEvidenceError("article id must be a positive integer")
    text_fields = (
        "headline",
        "summary",
        "author",
        "source",
        "created_at",
        "updated_at",
    )
    for field in text_fields:
        if not isinstance(message.get(field), str) or not message[field].strip():
            raise NewsEvidenceError(f"article field {field} is missing or invalid")
    symbols = message.get("symbols")
    if not isinstance(symbols, list) or any(
        not isinstance(symbol, str) or not symbol.strip() for symbol in symbols
    ):
        raise NewsEvidenceError("symbols must be an array of nonempty strings")
    for field in ("content", "url"):
        value = message.get(field)
        if value is not None and not isinstance(value, str):
            raise NewsEvidenceError(f"article field {field} has an invalid type")
    created = _parse_timestamp(message["created_at"], "created_at")
    updated = _parse_timestamp(message["updated_at"], "updated_at")
    return {
        "id": article_id,
        "headline": message["headline"],
        "summary": message["summary"],
        "author": message["author"],
        "created_at": message["created_at"],
        "updated_at": message["updated_at"],
        "content": message.get("content"),
        "url": message.get("url"),
        "symbols": list(symbols),
        "source": message["source"],
        "timestamp_conflict": updated < created,
    }


def _canonical_json(message: Mapping[str, Any]) -> bytes:
    return json.dumps(
        message,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NewsEvidenceError(f"{field} is not RFC-3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NewsEvidenceError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _string_field(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise NewsEvidenceError(f"article field {field} is missing or invalid")
    return value


def _headline_age_band(age_seconds: float) -> str:
    if age_seconds < 0:
        return "FUTURE_TIMESTAMP_CONFLICT"
    if age_seconds in (2 * 60 * 60, 12 * 60 * 60, 24 * 60 * 60):
        return "BOUNDARY_UNRESOLVED"
    if age_seconds < 2 * 60 * 60:
        return "UNDER_2H"
    if age_seconds < 12 * 60 * 60:
        return "2_TO_12H"
    if age_seconds < 24 * 60 * 60:
        return "12_TO_24H"
    return "OVER_24H"


def _require_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset != timedelta(0):
        raise NewsEvidenceError(f"{field} must be timezone-aware UTC")


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=value)


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")
