"""Journal-backed SIP-derived top-list observations, never Ross qualification."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlencode

from alpaca_ross_bot.journal import Journal

type RankingKind = Literal["most_actives", "movers"]
type RankingBy = Literal["volume", "trades"]

SCREENER_FEED = "alpaca_sip_screener"
SCHEMA_SOURCE = (
    "https://github.com/alpacahq/alpaca-py/blob/v0.44.0/alpaca/data/models/screener.py"
)


class RankingObservationError(ValueError):
    """A top-list response or its provenance is invalid."""


@dataclass(frozen=True)
class RankingObservationResult:
    source_response_sequence: int
    sequence: int | None
    symbol_count: int
    status: str


def ranking_request(
    kind: object, *, top: object, by: object = None
) -> tuple[str, dict[str, str]]:
    """Require explicit list size and endpoint-specific ranking metric."""
    if kind not in ("most_actives", "movers"):
        raise RankingObservationError("unsupported ranking kind")
    cap = 100 if kind == "most_actives" else 50
    if type(top) is not int or not 1 <= top <= cap:
        raise RankingObservationError("ranking top is outside documented bounds")
    if kind == "most_actives":
        if not isinstance(by, str) or by not in ("volume", "trades"):
            raise RankingObservationError("most-actives requires volume or trades")
        query = {"by": by, "top": str(top)}
        path = "most-actives"
    else:
        if by is not None:
            raise RankingObservationError("movers does not accept a by metric")
        query = {"top": str(top)}
        path = "movers"
    return (
        f"https://data.alpaca.markets/v1beta1/screener/stocks/{path}?"
        f"{urlencode(query)}",
        query,
    )


def journal_sip_ranking_observation(
    journal: Journal, *, source_response_sequence: int, processed_at: datetime
) -> RankingObservationResult:
    """Project exact journal bytes once; preserve provider order and clocks.

    The timestamp is the provider's ranking computation time, not a trade
    timestamp or the first time the symbol was eligible. Absence from these
    limited lists is never evidence of rejection from the market universe.
    """
    _utc(processed_at)
    event = journal.read_event(source_response_sequence)
    if event is None:
        raise RankingObservationError("raw ranking response is missing")
    envelope, stored = event
    if (
        envelope.source_id != "alpaca_scanner_data"
        or envelope.feed != SCREENER_FEED
        or envelope.event_kind != "SIP_SCREENER_RAW_RESPONSE"
        or processed_at < envelope.available_at
    ):
        raise RankingObservationError("raw ranking provenance or processing is invalid")
    metadata = _object(stored)
    kind = metadata.get("ranking_kind")
    url, query = ranking_request(kind, top=metadata.get("top"), by=metadata.get("by"))
    if metadata.get("request_url") != url or metadata.get("query") != query:
        raise RankingObservationError("ranking request provenance does not match")
    try:
        encoded = metadata["raw_response_base64"]
        if not isinstance(encoded, str):
            raise ValueError("invalid raw encoding")
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise RankingObservationError("raw ranking bytes are unavailable") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != metadata.get("response_sha256") or len(raw) != metadata.get(
        "response_bytes"
    ):
        raise RankingObservationError("raw ranking digest or size does not match")
    response = _object(raw)
    raw_updated = response.get("last_updated")
    computed_at = _provider_time(raw_updated)
    fractional = str(raw_updated).split(".", 1)
    submicroseconds = len(fractional) == 2 and any(
        digit != "0" for digit in re.split(r"Z|\+", fractional[1])[0][6:]
    )
    clock_conflict = computed_at > envelope.system_first_seen_at or (
        computed_at == envelope.system_first_seen_at and submicroseconds
    )
    if clock_conflict:
        raise RankingObservationError("future ranking computation cannot be projected")
    groups = ("most_actives",) if kind == "most_actives" else ("gainers", "losers")
    if kind == "movers" and response.get("market_type") != "stocks":
        raise RankingObservationError("movers market type must be stocks")
    rows: list[dict[str, object]] = []
    for group in groups:
        records = response.get(group)
        if not isinstance(records, list) or len(records) > int(query["top"]):
            raise RankingObservationError("ranking list is missing or exceeds request")
        seen: set[str] = set()
        for rank, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise RankingObservationError("ranking row must be an object")
            symbol = record.get("symbol")
            if (
                not isinstance(symbol, str)
                or not symbol.strip()
                or symbol != symbol.strip()
                or symbol in seen
            ):
                raise RankingObservationError(
                    "ranking symbols are invalid or duplicated"
                )
            seen.add(symbol)
            fields = (
                ("volume", "trade_count")
                if kind == "most_actives"
                else ("percent_change", "change", "price")
            )
            values = {field: _number(record.get(field), field) for field in fields}
            rows.append(
                {"symbol": symbol, "list": group, "provider_rank": rank, **values}
            )
    payload = {
        "ranking_kind": kind,
        "ranking_by": metadata.get("by"),
        "requested_top_per_list": int(query["top"]),
        "rows": rows,
        "source_response_sequence": source_response_sequence,
        "source_response_sha256": digest,
        "schema_source": SCHEMA_SOURCE,
        "provider_last_updated_raw": raw_updated,
        "provider_computed_at": computed_at.isoformat(),
        "provider_time_semantics": "RANKING_COMPUTATION_NOT_SYMBOL_TRADE_TIME",
        "timestamp_precision": "RAW_RETAINED_ENVELOPE_MICROSECOND_FLOOR",
        "source_timestamp_status": "RECORDED_NOT_FRESHNESS_VALIDATED",
        "coverage_label": "LIMITED_SIP_DERIVED_RANKING_NOT_FULL_MARKET_SCANNER",
        "scope": "PROVIDER_TOP_LIST_ONLY",
        "full_market_coverage": False,
        "omitted_symbol_meaning": "NOT_OBSERVED_NOT_REJECTED",
        "session_status": "UNVERIFIED",
        "premarket_gap_rank": "UNAVAILABLE",
        "movers_reset_semantics": (
            "PREVIOUS_MARKET_DAY_BEFORE_MARKET_OPEN_RESET"
            if kind == "movers"
            else "NOT_APPLICABLE_SESSION_BOUNDARY_UNVERIFIED"
        ),
        "float_status": "MISSING",
        "rvol_status": "UNAVAILABLE_DO_NOT_MIX_SIP_RANK_VALUES_WITH_IEX",
        "iex_enrichment_coverage_label": "NOT_SIP_EQUIVALENT",
        "strategy_eligibility": "BLOCKED_RESEARCH",
    }
    appended = journal.append_next_if_state_missing(
        state_key=f"sip_ranking_observation:{source_response_sequence}",
        state_value={"source_response_sequence": source_response_sequence},
        source_id="alpaca_sip_screener",
        feed=SCREENER_FEED,
        event_kind="SIP_RANKING_OBSERVATION",
        payload=json.dumps(payload, sort_keys=True, allow_nan=False).encode(),
        system_first_seen_at=envelope.system_first_seen_at,
        processed_at=processed_at,
        source_event_at=computed_at,
    )
    return RankingObservationResult(
        source_response_sequence,
        None if appended is None else appended.sequence,
        len(rows),
        "DUPLICATE" if appended is None else "RECORDED",
    )


def _object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_pairs,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise RankingObservationError("ranking evidence is not strict JSON") from exc
    if not isinstance(value, dict):
        raise RankingObservationError("ranking evidence must be an object")
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON constant")


def _number(value: object, field: str) -> str:
    if type(value) is not int and not isinstance(value, Decimal):
        raise RankingObservationError("ranking metric must be a JSON number")
    numeric = Decimal(str(value))
    if (
        not numeric.is_finite()
        or (field in ("volume", "trade_count") and numeric < 0)
        or (field == "price" and numeric <= 0)
    ):
        raise RankingObservationError("ranking metric is outside valid bounds")
    return str(numeric)


def _provider_time(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|\+00:00)",
        value,
    ):
        raise RankingObservationError("ranking computation timestamp must be UTC")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RankingObservationError(
            "ranking computation timestamp is invalid"
        ) from exc


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise RankingObservationError("processing timestamp must be UTC")
