"""Decision-time, supplied-evidence-only optional news context; no trade authority."""

import base64
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .events import EventEnvelope
from .journal import Journal
from .research_spec import load_accepted_research_spec_file
from .scanner_contract import (
    load_accepted_scanner_contract_file,
    validate_sequence_refs,
)


def _json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _read(journal: Journal, ref: int) -> tuple[EventEnvelope, dict[str, Any]]:
    row = journal.read_event(ref)
    if row is None:
        raise ValueError("news reference is missing")
    envelope, raw = row
    if envelope.source_id != "alpaca_news" or envelope.feed != "alpaca_news":
        raise ValueError("news reference source/feed mismatch")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("news reference payload must be an object")
    return envelope, data


def _article(
    journal: Journal, ref: int
) -> tuple[EventEnvelope, dict[str, Any], dict[str, Any]]:
    event, data = _read(journal, ref)
    if data.get("news_eligibility") != "BLOCKED_RESEARCH":
        raise ValueError("news must remain research-blocked")
    if event.event_kind == "NEWS_ARTICLE_VERSION":
        parent_ref = data.get("raw_frame_sequence")
        if type(parent_ref) is not int or parent_ref < 1:
            raise ValueError("invalid raw parent reference")
        row = journal.read_event(parent_ref)
        if row is None:
            raise ValueError("raw parent is missing")
        parent, raw = row
        if (
            parent.source_id not in {"alpaca_news", "alpaca_market_data"}
            or parent.feed != "alpaca_news"
            or parent.event_kind != "RAW_FRAME"
        ):
            raise ValueError("raw parent source/feed/kind mismatch")
        messages = json.loads(raw)
        if not isinstance(messages, list):
            raise ValueError("raw parent must be an array")
        matches = [
            item
            for item in messages
            if isinstance(item, dict)
            and hashlib.sha256(_json(item)).hexdigest() == data.get("fingerprint")
        ]
        if len(matches) != 1 or matches[0].get("T") != "n":
            raise ValueError("article fingerprint does not bind a unique raw record")
        article = matches[0]
        for field in (
            "id",
            "headline",
            "summary",
            "author",
            "source",
            "symbols",
            "created_at",
            "updated_at",
            "content",
            "url",
        ):
            if data.get(field) != article.get(field):
                raise ValueError("normalized article differs from raw record")
        if event.system_first_seen_at != parent.system_first_seen_at:
            raise ValueError("article receipt differs from raw parent")
    elif event.event_kind == "NEWS_REST_ARTICLE_OBSERVATION":
        parent_ref = data.get("parent_page_sequence")
        index = data.get("record_index")
        if (
            type(parent_ref) is not int
            or parent_ref < 1
            or type(index) is not int
            or index < 0
        ):
            raise ValueError("invalid REST parent/index")
        parent, page = _read(journal, parent_ref)
        if parent.event_kind != "REST_RECOVERY_PAGE" or page.get("kind") != "news":
            raise ValueError("REST parent kind mismatch")
        response = json.loads(base64.b64decode(page["raw_body_base64"], validate=True))
        records = response.get("news") if isinstance(response, dict) else None
        if not isinstance(records, list) or index >= len(records):
            raise ValueError("REST article index unavailable")
        article = records[index]
        if not isinstance(article, dict) or hashlib.sha256(
            _json(article)
        ).hexdigest() != data.get("record_sha256"):
            raise ValueError("REST article hash mismatch")
        if event.system_first_seen_at != parent.system_first_seen_at:
            raise ValueError("REST receipt differs from parent")
    else:
        raise ValueError("unsupported news evidence kind")
    if parent.sequence >= event.sequence or event.processed_at < parent.available_at:
        raise ValueError("article precedes parent availability")
    return event, data, article


def capture_optional_news_view(
    journal: Journal,
    *,
    repo_root: Path,
    symbol: str,
    input_refs: tuple[int, ...],
    decision_at: datetime,
    processed_at: datetime,
) -> EventEnvelope:
    """Bind visible source-backed records without selecting a news preference."""
    scanner = load_accepted_scanner_contract_file(
        repo_root / "evidence/general-iex-daily30-screen-v1.json"
    )
    research = load_accepted_research_spec_file(repo_root, scanner)
    validate_sequence_refs(input_refs)
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip():
        raise ValueError("literal symbol is required")
    for at in (decision_at, processed_at):
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError("view clocks must be UTC")
    if processed_at < decision_at:
        raise ValueError("processing precedes decision")
    visible: list[dict[str, object]] = []
    visible_refs: list[int] = []
    for ref in input_refs:
        row = journal.read_event(ref)
        if row is None:
            raise ValueError("news reference is missing")
        # Never expose counts, identities or content of future records.
        if row[0].available_at > decision_at:
            continue
        event, data, article = _article(journal, ref)
        symbols = article.get("symbols")
        if not isinstance(symbols, list) or symbol not in symbols:
            continue
        visible_refs.append(ref)
        visible.append(
            {
                "sequence": ref,
                "transport": event.event_kind,
                "article_id": article.get("id"),
                "provider_symbols": symbols,
                "available_at": event.available_at.isoformat(),
                "local_receipt_at": event.system_first_seen_at.isoformat(),
                "article_first_seen_at": data.get("article_first_seen_at"),
                "version_first_seen_at": data.get("version_first_seen_at"),
                "observed_status": data.get("status", "RETRIEVED_UNVERIFIED"),
                "feed_health_reasons": data.get("feed_health_reasons", "UNKNOWN"),
                "delay_assessment": data.get("delay_assessment", "UNASSESSED"),
                "ticker_mapping": "EXACT_PROVIDER_SYMBOL_ONLY",
                "market_context": "NOT_EVALUATED",
                "causality": "NOT_ASSESSED",
                "content_relation": data.get("content_relation"),
                "provider_created_at": article.get("created_at"),
                "provider_updated_at": article.get("updated_at"),
                "headline": article.get("headline"),
                "context_verification": "UNVERIFIED",
            }
        )
    report = {
        "symbol": symbol,
        "decision_at": decision_at.isoformat(),
        "research_version_id": research.version_id,
        "research_sha256": research.sha256,
        "scanner_sha256": scanner.sha256,
        "governing_rule_ids": ["P02"],
        "input_refs": visible_refs,
        "news_role": "OPTIONAL_NON_VETO",
        "scope": "SUPPLIED_VISIBLE_EVIDENCE_ONLY",
        "affects_scanner_qualification": False,
        "preference_status": "UNVERIFIED",
        "observation_status": (
            "VISIBLE_SUPPLIED_ITEMS_UNVERIFIED"
            if visible
            else "NO_VISIBLE_SUPPLIED_ITEM_UNVERIFIED"
        ),
        "news_eligibility": "BLOCKED_RESEARCH",
        "strategy_eligibility": "BLOCKED_RESEARCH",
        "execution_authority": False,
        "order_authority": False,
        "formal_paper_evaluation": False,
        "records": visible,
    }
    return journal.append_next(
        source_id="optional_news_view",
        feed="alpaca_news",
        event_kind="NEWS_OPTIONAL_PREFERENCE_VIEW",
        payload=_json(report),
        system_first_seen_at=decision_at,
        processed_at=processed_at,
    )
