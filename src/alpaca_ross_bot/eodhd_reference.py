"""Journal supplied EODHD reference bytes; never grant float/trading eligibility."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from alpaca_ross_bot.journal import Journal

SOURCE = "eodhd_live_v2_reference"
FEED = "eodhd_reference"
RAW_KIND = "EODHD_REFERENCE_RAW_RESPONSE"
DERIVED_KIND = "EODHD_REFERENCE_OBSERVATION"
type EvidenceContext = Literal["PUBLIC_DEMO", "USER_SUPPLIED_BYTES"]


class EodhdReferenceError(ValueError):
    """Supplied reference evidence or its journal provenance is invalid."""


@dataclass(frozen=True)
class ReferenceProjectionResult:
    source_response_sequence: int
    sequences: tuple[int, ...]
    symbol_count: int
    status: str


def capture_eodhd_reference_raw(
    journal: Journal,
    raw: bytes,
    *,
    requested_symbols: tuple[str, ...],
    requested_at: datetime,
    received_at: datetime,
    evidence_context: EvidenceContext,
) -> int:
    """Capture before parse, without accepting a URL, token or network client."""
    _symbols(requested_symbols)
    _utc(requested_at)
    _utc(received_at)
    if requested_at > received_at or evidence_context not in (
        "PUBLIC_DEMO",
        "USER_SUPPLIED_BYTES",
    ):
        raise EodhdReferenceError("request clocks or evidence context invalid")
    metadata = {
        "requested_symbols": requested_symbols,
        "requested_at": requested_at.isoformat(),
        "received_at": received_at.isoformat(),
        "evidence_context": evidence_context,
        "raw_response_base64": base64.b64encode(raw).decode("ascii"),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
    }
    return journal.append_next(
        source_id=SOURCE,
        feed=FEED,
        event_kind=RAW_KIND,
        payload=json.dumps(metadata, sort_keys=True, allow_nan=False).encode(),
        system_first_seen_at=received_at,
        processed_at=received_at,
    ).sequence


def project_eodhd_reference(
    journal: Journal, *, source_response_sequence: int, processed_at: datetime
) -> ReferenceProjectionResult:
    """Independently verify raw provenance and project once per raw row identity.

    Provider snapshot time is not float effective/revision time. Null, invalid
    and missing values stay unavailable; outstanding shares never replace float.
    Later captures of identical bytes have distinct receipt and replay clocks.
    """
    _utc(processed_at)
    if type(source_response_sequence) is not int or source_response_sequence < 1:
        raise EodhdReferenceError("invalid raw reference sequence")
    event = journal.read_event(source_response_sequence)
    if event is None:
        raise EodhdReferenceError("raw reference response missing")
    envelope, stored = event
    if (
        envelope.sequence != source_response_sequence
        or envelope.source_id != SOURCE
        or envelope.feed != FEED
        or envelope.event_kind != RAW_KIND
        or envelope.source_event_at is not None
        or envelope.provider_received_at is not None
        or envelope.payload_ref != "sha256:" + hashlib.sha256(stored).hexdigest()
        or processed_at < envelope.available_at
    ):
        raise EodhdReferenceError("raw reference provenance or processing invalid")
    metadata = _object(stored)
    requested = metadata.get("requested_symbols")
    if not isinstance(requested, list):
        raise EodhdReferenceError("raw requested symbols missing")
    _symbols(tuple(requested))
    request_time = _local_time(metadata.get("requested_at"))
    received = _local_time(metadata.get("received_at"))
    context = metadata.get("evidence_context")
    if (
        context not in ("PUBLIC_DEMO", "USER_SUPPLIED_BYTES")
        or request_time > received
        or received != envelope.system_first_seen_at
        or received != envelope.processed_at
        or received != envelope.available_at
    ):
        raise EodhdReferenceError("raw reference request metadata invalid")
    try:
        encoded = metadata["raw_response_base64"]
        if not isinstance(encoded, str):
            raise ValueError
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise EodhdReferenceError("raw reference encoding invalid") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if (
        digest != metadata.get("response_sha256")
        or type(metadata.get("response_bytes")) is not int
        or len(raw) != metadata["response_bytes"]
    ):
        raise EodhdReferenceError("raw reference digest or size mismatch")
    response = _object(raw)
    meta, rows, links = (
        response.get("meta"),
        response.get("data"),
        response.get("links"),
    )
    if rows == []:
        rows = {}  # documented all-skipped response shape; no zero-filled values
    if (
        not isinstance(meta, dict)
        or type(meta.get("count")) is not int
        or not isinstance(rows, dict)
        or meta["count"] != len(rows)
        or not isinstance(links, dict)
        or "next" not in links
        or links["next"] is not None
    ):
        raise EodhdReferenceError("reference shape, count or pagination invalid")
    for symbol, row in rows.items():
        if (
            symbol not in requested
            or not isinstance(row, dict)
            or row.get("symbol") != symbol
        ):
            raise EodhdReferenceError("reference row identity mismatch")
    projections = [
        (symbol, *_reference_fields(rows.get(symbol), received)) for symbol in requested
    ]
    sequences: list[int] = []
    for symbol, fields, snapshot_at, valid_clock in projections:
        payload = {
            **fields,
            "symbol": symbol,
            "source_response_sequence": source_response_sequence,
            "source_response_sha256": digest,
            "requested_at": request_time.isoformat(),
            "received_at": received.isoformat(),
            "processed_at": processed_at.isoformat(),
            "available_at": processed_at.isoformat(),
            "evidence_context": context,
            "provider_snapshot_at": None
            if snapshot_at is None
            else snapshot_at.isoformat(),
            "provider_time_semantics": "SNAPSHOT_NOT_FLOAT_EFFECTIVE_OR_REVISION_TIME",
            "float_effective_at": None,
            "float_revision_at": None,
            "float_freshness": "UNVERIFIED",
            "trusted_float_shares": None,
            "entitlement": "UNVERIFIED_FOR_ACTUAL_USER",
            "full_market_coverage": False,
            "scope": "EXPLICIT_REQUESTED_SYMBOLS_ONLY",
            "strategy_eligibility": "BLOCKED_RESEARCH",
        }
        appended = journal.append_next_if_state_missing(
            state_key=f"eodhd_reference:{source_response_sequence}:{symbol}",
            state_value={
                "source_response_sequence": source_response_sequence,
                "symbol": symbol,
            },
            source_id=SOURCE,
            feed=FEED,
            event_kind=DERIVED_KIND,
            payload=json.dumps(payload, sort_keys=True, allow_nan=False).encode(),
            system_first_seen_at=received,
            processed_at=processed_at,
            source_event_at=snapshot_at if valid_clock else None,
        )
        if appended is not None:
            sequences.append(appended.sequence)
    return ReferenceProjectionResult(
        source_response_sequence,
        tuple(sequences),
        len(requested),
        "RECORDED" if sequences else "DUPLICATE",
    )


def _reference_fields(
    row: dict[str, Any] | None, received: datetime
) -> tuple[dict[str, object], datetime | None, bool]:
    if row is None:
        return (
            {
                "diagnostics": ["MISSING_NOT_STRATEGY_REJECTED"],
                "float_status": "MISSING",
            },
            None,
            False,
        )
    diagnostics: list[str] = []
    values: dict[str, object] = {}
    for name, source_field in (
        ("float_shares", "sharesFloat"),
        ("shares_outstanding", "sharesOutstanding"),
        ("market_cap_reported", "marketCap"),
    ):
        value = row.get(source_field)
        valid = type(value) is int and value > 0
        values[name] = value if valid else None
        if not valid:
            diagnostics.append(f"{source_field.upper()}_UNAVAILABLE")
    floating, outstanding = values["float_shares"], values["shares_outstanding"]
    if (
        isinstance(floating, int)
        and isinstance(outstanding, int)
        and floating > outstanding
    ):
        diagnostics.append("FLOAT_EXCEEDS_OUTSTANDING")
    currency = row.get("currency")
    values["currency"] = (
        currency if isinstance(currency, str) and currency.strip() else None
    )
    values["market_cap_usd"] = (
        values["market_cap_reported"] if currency == "USD" else None
    )
    if values["market_cap_reported"] is not None and currency != "USD":
        diagnostics.append("MARKET_CAP_CURRENCY_NOT_USD")
    timestamp = row.get("timestamp")
    snapshot_at: datetime | None = None
    if type(timestamp) is int:
        try:
            snapshot_at = datetime.fromtimestamp(timestamp, UTC)
        except (ValueError, OverflowError, OSError):
            pass
    valid_clock = snapshot_at is not None and snapshot_at <= received
    if snapshot_at is None:
        diagnostics.append("PROVIDER_TIMESTAMP_INVALID")
    elif not valid_clock:
        diagnostics.append("PROVIDER_TIMESTAMP_FUTURE")
    values.update(
        {
            "provider_timestamp_seconds": timestamp if type(timestamp) is int else None,
            "diagnostics": diagnostics,
            "float_status": "SOURCE_VALUE_UNVERIFIED"
            if floating is not None
            else "UNAVAILABLE",
        }
    )
    return values, snapshot_at, valid_clock


def _symbols(values: tuple[object, ...]) -> None:
    if (
        not values
        or any(
            not isinstance(symbol, str)
            or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]*\.US", symbol) is None
            for symbol in values
        )
        or len(set(values)) != len(values)
    ):
        raise EodhdReferenceError(
            "requested US reference identities invalid or duplicated"
        )


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise EodhdReferenceError("reference clocks require UTC")


def _local_time(value: object) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        result = datetime.fromisoformat(value)
        _utc(result)
        return result
    except ValueError as exc:
        raise EodhdReferenceError("reference local clock invalid") from exc


def _object(raw: bytes) -> dict[str, Any]:
    try:
        result = json.loads(
            raw,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_pairs,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise EodhdReferenceError("reference evidence is not strict JSON") from exc
    if not isinstance(result, dict):
        raise EodhdReferenceError("reference evidence must be an object")
    return result


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON constant")
