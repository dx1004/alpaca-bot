"""Point-in-time news-to-IEX context joins without catalyst inference."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal


class NewsMarketContextError(ValueError):
    """Journaled news and IEX evidence cannot be safely joined."""


def capture_news_market_context(
    journal: Journal,
    *,
    article_sequence: int,
    before_sequence: int,
    after_sequence: int,
    processed_at: datetime,
) -> dict[str, object]:
    """Record observed price/volume deltas around local article availability.

    The caller selects the two journaled snapshots. No reaction window,
    threshold, ticker mapping, catalyst judgment, or causal interpretation is
    inferred here. An observation is never strategy-eligible.
    """
    _require_utc(processed_at, "processed_at")
    if min(article_sequence, before_sequence, after_sequence) < 1:
        raise NewsMarketContextError("journal sequences must be positive")
    if len({article_sequence, before_sequence, after_sequence}) != 3:
        raise NewsMarketContextError("news and snapshots must be distinct events")
    if not before_sequence < article_sequence < after_sequence:
        raise NewsMarketContextError("journal sequences must order around the article")

    article_event, article = _read_payload(
        journal, article_sequence, expected_kind="NEWS_ARTICLE_VERSION"
    )
    before_event, before = _read_payload(
        journal, before_sequence, expected_kind="SCANNER_OBSERVATION"
    )
    after_event, after = _read_payload(
        journal, after_sequence, expected_kind="SCANNER_OBSERVATION"
    )
    first_seen = article_event.system_first_seen_at
    if article_event.feed != "alpaca_news":
        raise NewsMarketContextError("article must be from Alpaca news")
    if article.get("news_eligibility") != "BLOCKED_RESEARCH":
        raise NewsMarketContextError("article must remain strategy-blocked")
    if article.get("version_first_seen_at") != first_seen.isoformat():
        raise NewsMarketContextError("article first-seen provenance is inconsistent")
    if before_event.feed != "iex" or after_event.feed != "iex":
        raise NewsMarketContextError("market context must use IEX observations")
    if processed_at < max(
        article_event.available_at, before_event.available_at, after_event.available_at
    ):
        raise NewsMarketContextError("processing precedes required evidence")

    before_at = _utc_string(before.get("as_of"), "before snapshot as_of")
    after_at = _utc_string(after.get("as_of"), "after snapshot as_of")
    if before_at >= first_seen:
        raise NewsMarketContextError("before snapshot must precede article first-seen")
    if after_at <= first_seen:
        raise NewsMarketContextError("after snapshot must follow article first-seen")
    if before_event.available_at > first_seen:
        raise NewsMarketContextError("before snapshot was not locally available yet")
    if after_event.available_at < first_seen:
        raise NewsMarketContextError("after snapshot availability conflicts with news")
    if _coverage(before) != _coverage(after):
        raise NewsMarketContextError("snapshot coverage labels do not match")

    provider_symbols = article.get("symbols")
    if not isinstance(provider_symbols, list) or any(
        not isinstance(symbol, str) or not symbol.strip() for symbol in provider_symbols
    ):
        raise NewsMarketContextError("article provider symbols are malformed")
    before_rows = _rows_by_symbol(before)
    after_rows = _rows_by_symbol(after)
    common_symbols = sorted(set(provider_symbols) & set(before_rows) & set(after_rows))
    context_rows = [
        _measure_symbol(
            symbol,
            before_rows[symbol],
            after_rows[symbol],
        )
        for symbol in common_symbols
    ]

    idempotency_key = hashlib.sha256(
        f"{article_sequence}:{before_sequence}:{after_sequence}".encode()
    ).hexdigest()
    state_key = f"news_market_context:{idempotency_key}"
    if journal.read_state(state_key) is not None:
        return {
            "status": "DUPLICATE",
            "idempotency_key": idempotency_key,
            "context_only": True,
        }

    status = "RECORDED" if context_rows else "NO_COMMON_PROVIDER_SYMBOL"
    report: dict[str, object] = {
        "record_type": "NEWS_MARKET_CONTEXT_OBSERVATION",
        "status": status,
        "article_sequence": article_sequence,
        "article_id": article.get("id"),
        "article_first_seen_at": first_seen.isoformat(),
        "before_sequence": before_sequence,
        "before_as_of": before_at.isoformat(),
        "after_sequence": after_sequence,
        "after_as_of": after_at.isoformat(),
        "pre_observation_age_seconds": (first_seen - before_at).total_seconds(),
        "post_observation_delay_seconds": (after_at - first_seen).total_seconds(),
        "symbol_match_basis": "EXACT_ALPACA_PROVIDER_SYMBOL_ONLY",
        "coverage_label": _coverage(before),
        "causality": "NOT_ASSESSED_TEMPORAL_ASSOCIATION_ONLY",
        "catalyst_qualification": "NOT_ASSESSED",
        "strategy_eligibility": "BLOCKED_RESEARCH",
        "rows": context_rows,
        "idempotency_key": idempotency_key,
    }
    appended = journal.append_next_if_state_missing(
        state_key=state_key,
        state_value={"status": status},
        source_id="news_market_context",
        feed="iex",
        event_kind="NEWS_MARKET_CONTEXT_OBSERVATION",
        payload=_canonical_json(report),
        system_first_seen_at=first_seen,
        processed_at=processed_at,
    )
    if appended is None:
        return {
            "status": "DUPLICATE",
            "idempotency_key": idempotency_key,
            "context_only": True,
        }
    return report


def _read_payload(
    journal: Journal,
    sequence: int,
    *,
    expected_kind: str,
) -> tuple[EventEnvelope, dict[str, object]]:
    event = journal.read_event(sequence)
    if event is None:
        raise NewsMarketContextError("referenced journal event is missing")
    envelope, raw = event
    if envelope.event_kind != expected_kind:
        raise NewsMarketContextError(f"event {sequence} has the wrong kind")
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NewsMarketContextError("journaled evidence is malformed") from exc
    if not isinstance(decoded, dict):
        raise NewsMarketContextError("journaled evidence has an invalid shape")
    return envelope, decoded


def _coverage(snapshot: dict[str, object]) -> str:
    coverage = snapshot.get("coverage_label")
    if coverage != "NOT_SIP_EQUIVALENT":
        raise NewsMarketContextError("snapshot must retain NOT_SIP_EQUIVALENT")
    if snapshot.get("full_market_coverage") is not False:
        raise NewsMarketContextError("IEX snapshot cannot claim full-market coverage")
    if snapshot.get("strategy_eligibility") != "BLOCKED_RESEARCH":
        raise NewsMarketContextError("snapshot must remain strategy-blocked")
    return coverage


def _rows_by_symbol(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise NewsMarketContextError("snapshot rows are malformed")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise NewsMarketContextError("snapshot row is malformed")
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise NewsMarketContextError("snapshot symbol is malformed")
        if symbol in result:
            raise NewsMarketContextError("snapshot symbols are duplicated")
        if row.get("feed") != "iex":
            raise NewsMarketContextError("snapshot row is not IEX")
        if row.get("strategy_eligibility") != "BLOCKED_RESEARCH":
            raise NewsMarketContextError("snapshot row must remain blocked")
        result[symbol] = row
    return result


def _measure_symbol(
    symbol: str,
    before: dict[str, object],
    after: dict[str, object],
) -> dict[str, object]:
    before_price = _decimal(before.get("latest_trade_price"), "before price")
    after_price = _decimal(after.get("latest_trade_price"), "after price")
    before_volume = _integer(before.get("session_volume"), "before volume")
    after_volume = _integer(after.get("session_volume"), "after volume")
    return {
        "symbol": symbol,
        "before_price": str(before_price),
        "after_price": str(after_price),
        "price_delta": str(after_price - before_price),
        "before_session_volume": before_volume,
        "after_session_volume": after_volume,
        "session_volume_delta": after_volume - before_volume,
        "measurement_basis": "RAW_OBSERVED_DELTA_NO_THRESHOLD_OR_CAUSAL_CLAIM",
    }


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise NewsMarketContextError(f"{field} is malformed")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise NewsMarketContextError(f"{field} is malformed") from exc
    if not result.is_finite():
        raise NewsMarketContextError(f"{field} must be finite")
    return result


def _integer(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise NewsMarketContextError(f"{field} must be a nonnegative integer")
    return value


def _utc_string(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise NewsMarketContextError(f"{field} must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NewsMarketContextError(f"{field} must be RFC-3339") from exc
    _require_utc(parsed, field)
    return parsed


def _require_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise NewsMarketContextError(f"{field} must be timezone-aware UTC")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
