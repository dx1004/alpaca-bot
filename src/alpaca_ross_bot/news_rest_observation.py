"""Replay-safe article references derived from locally retrieved News REST pages."""

import base64
import hashlib
import json
from datetime import datetime, timedelta
from typing import NoReturn

from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal


class RestNewsObservationError(ValueError):
    """A REST news page cannot support the requested local observation."""


def capture_rest_news_page(
    journal: Journal,
    page_sequence: int,
    *,
    processed_at: datetime,
) -> tuple[EventEnvelope, ...]:
    """Append one blocked, retrieval-time observation per REST article.

    The exact response remains authoritative in the parent REST page event.
    Article publication timestamps are deliberately not assigned to the child
    event's source clock: a historical REST response was unavailable locally
    until it was retrieved and processed.
    """
    if type(page_sequence) is not int or page_sequence < 1:
        raise RestNewsObservationError("page sequence must be positive")
    _require_utc(processed_at)
    parent = journal.read_event(page_sequence)
    if parent is None:
        raise RestNewsObservationError("REST news page is missing")
    envelope, payload = parent
    if (
        envelope.source_id != "alpaca_news"
        or envelope.feed != "alpaca_news"
        or envelope.event_kind != "REST_RECOVERY_PAGE"
    ):
        raise RestNewsObservationError("event is not an Alpaca news REST page")
    if processed_at < envelope.available_at:
        raise RestNewsObservationError(
            "processing time precedes local page availability"
        )

    try:
        metadata = json.loads(payload, parse_constant=_reject_constant)
        if not isinstance(metadata, dict) or metadata.get("kind") != "news":
            raise RestNewsObservationError("page metadata is not a news query")
        encoded_body = metadata.get("raw_body_base64")
        if not isinstance(encoded_body, str):
            raise RestNewsObservationError("page lacks its raw response body")
        raw_body = base64.b64decode(encoded_body, validate=True)
        response = json.loads(raw_body, parse_constant=_reject_constant)
    except RestNewsObservationError:
        raise
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestNewsObservationError("stored REST news page is malformed") from exc
    if not isinstance(response, dict) or not isinstance(response.get("news"), list):
        raise RestNewsObservationError("REST response has no news article list")

    observations: list[EventEnvelope] = []
    for record_index, article in enumerate(response["news"]):
        if not isinstance(article, dict):
            raise RestNewsObservationError("REST news record must be an object")
        record_bytes = json.dumps(
            article,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        article_id = article.get("id")
        if not isinstance(article_id, (str, int)) or isinstance(article_id, bool):
            article_id = None
        observation_payload = json.dumps(
            {
                "parent_page_sequence": page_sequence,
                "record_index": record_index,
                "record_sha256": hashlib.sha256(record_bytes).hexdigest(),
                "article_id": article_id,
                "query_start": metadata.get("start"),
                "query_end": metadata.get("end"),
                "page_token": metadata.get("page_token"),
                "page_received_at": metadata.get("received_at"),
                "timing_basis": "LOCAL_RETRIEVAL_AND_PROCESSING_ONLY",
                "news_eligibility": "BLOCKED_RESEARCH",
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        event = journal.append_next_if_state_missing(
            state_key=f"rest_news_observation:{page_sequence}:{record_index}",
            state_value={"record_sha256": hashlib.sha256(record_bytes).hexdigest()},
            source_id="alpaca_news",
            feed="alpaca_news",
            event_kind="NEWS_REST_ARTICLE_OBSERVATION",
            payload=observation_payload,
            system_first_seen_at=envelope.system_first_seen_at,
            processed_at=processed_at,
        )
        if event is not None:
            observations.append(event)
    return tuple(observations)


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise RestNewsObservationError("processing time must be timezone-aware UTC")


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant {value}")
