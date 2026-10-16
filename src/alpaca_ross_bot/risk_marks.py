"""Source-bound reported IEX bids for research valuation, without release authority."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.market_input_checks import _object, assess_market_record

PROFILE = "ALPACA_IEX_REPORTED_REGULAR_BID_V1"
LIMITATIONS = (
    "NOT_SIP_EQUIVALENT",
    "PROVIDER_REPORTED_TIMESTAMP_NOT_EXCHANGE_CLOCK_QUALIFICATION",
    "NO_NBBO_OR_EXECUTABLE_LIQUIDITY_GUARANTEE",
    "METADATA_OBSERVATION_NOT_PROVIDER_CLOCK_OR_ENTRY_POLICY_ACCEPTANCE",
)


def _decode(raw: bytes | str) -> object:
    return json.loads(raw, parse_float=Decimal, object_pairs_hook=_object)


@dataclass(frozen=True, repr=False)
class BidMark:
    symbol: str
    bid: Fraction
    ask: Fraction
    source_refs: tuple[int, ...]
    record_index: int
    profile: str = PROFILE
    observed_valid: bool = True
    limitations: tuple[str, ...] = LIMITATIONS

    @property
    def runtime_authorized(self) -> bool:
        return False

    def long_value(self, quantity: int) -> Fraction:
        """Whole-share long valuation only; never infer a broker position."""
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("positive whole-share long quantity required")
        return quantity * self.bid


def inspect_bid_mark(
    journal: Journal,
    *,
    symbol: str,
    sequence: int,
    record_index: int,
    metadata_sequence: int,
    decision_at: datetime,
    max_metadata_age: timedelta,
) -> BidMark:
    """Validate immutable raw facts and latest locally visible quote/metadata.

    One second is the research age policy, not an Alpaca latency promise.
    Latest means local observation order, not an exchange sequencing guarantee.
    """
    if not symbol or symbol != symbol.strip():
        raise ValueError("exact symbol required")
    if type(metadata_sequence) is not int or metadata_sequence < 1:
        raise ValueError("positive metadata sequence required")
    if max_metadata_age <= timedelta(0):
        raise ValueError("positive metadata age bound required")
    stored = journal.read_event(sequence)
    if stored is None:
        raise ValueError("quote evidence missing")
    event, raw = stored
    if event.external_clock_conflict:
        raise ValueError("quote external clock conflict")
    assessment = assess_market_record(
        journal,
        sequence=sequence,
        record_index=record_index,
        payload_ref=event.payload_ref,
        decision_at=decision_at,
        condition_names={"R": "Regular Market Maker Open"},
    )
    if not assessment.schema_valid or not assessment.measured_fresh:
        raise ValueError("invalid or stale reported quote")
    records = _decode(raw)
    assert isinstance(records, list)
    quote = records[record_index]
    assert isinstance(quote, dict)
    if quote.get("T") != "q" or quote.get("S") != symbol:
        raise ValueError("exact quote symbol required")
    if quote.get("c") != ["R"] or quote.get("z") not in ("A", "B", "C"):
        raise ValueError("reported regular quote condition and tape required")
    for other_event, other_raw in journal.read_market_events():
        if (
            other_event.feed != "iex"
            or other_event.event_kind != "RAW_FRAME"
            or not other_event.visible_at(decision_at)
        ):
            continue
        other_records = _decode(other_raw)
        if not isinstance(other_records, list):
            raise ValueError("ambiguous visible market frame")
        for index, record in enumerate(other_records):
            if (
                isinstance(record, dict)
                and record.get("T") == "q"
                and record.get("S") == symbol
                and (other_event.sequence, index) > (sequence, record_index)
            ):
                raise ValueError("requested quote superseded")
    url = "https://data.alpaca.markets/v2/stocks/meta/conditions/quote?tape=" + str(
        quote["z"]
    )
    metadata = journal.read_event(metadata_sequence)
    if metadata is None:
        raise ValueError("metadata evidence missing")
    meta_event, meta_raw = metadata
    if (
        (meta_event.source_id, meta_event.feed, meta_event.event_kind)
        != ("ALPACA_MARKET_METADATA", "iex", "CONDITION_CODE_METADATA")
        or meta_event.external_clock_conflict
        or not meta_event.visible_at(decision_at)
        or not timedelta(0)
        <= decision_at - meta_event.system_first_seen_at
        <= max_metadata_age
    ):
        raise ValueError("metadata identity, visibility or age invalid")
    payload = _decode(meta_raw)
    if not isinstance(payload, dict) or payload.get("url") != url:
        raise ValueError("metadata exact URL/tape mismatch")
    body = payload.get("body")
    if not isinstance(body, str):
        raise ValueError("raw metadata body required")
    mapping = _decode(body)
    if not isinstance(mapping, dict) or mapping.get("R") != "Regular Market Maker Open":
        raise ValueError("regular quote mapping unqualified")
    for newer, newer_raw in journal.read_market_metadata_events():
        if (
            newer.sequence <= metadata_sequence
            or newer.feed != "iex"
            or newer.event_kind != "CONDITION_CODE_METADATA"
            or not newer.visible_at(decision_at)
        ):
            continue
        newer_payload = _decode(newer_raw)
        if isinstance(newer_payload, dict) and newer_payload.get("url") == url:
            raise ValueError("requested metadata superseded")
    return BidMark(
        symbol,
        Fraction(quote["bp"]),
        Fraction(quote["ap"]),
        (sequence, metadata_sequence),
        record_index,
    )
