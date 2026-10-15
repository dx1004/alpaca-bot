import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.eodhd_reference import (
    DERIVED_KIND,
    FEED,
    RAW_KIND,
    SOURCE,
    EodhdReferenceError,
    capture_eodhd_reference_raw,
    project_eodhd_reference,
)
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.replay import ReplayMode, replay_at

NOW = datetime(2026, 10, 15, 16, tzinfo=UTC)


class FakeJournal(Journal):
    def __init__(self) -> None:
        self.events: dict[int, tuple[EventEnvelope, bytes]] = {}
        self.states: dict[str, object] = {}

    def append_next(self, **kwargs: Any) -> EventEnvelope:
        payload = kwargs["payload"]
        event = EventEnvelope(
            sequence=len(self.events) + 1,
            source_id=kwargs["source_id"],
            feed=kwargs["feed"],
            event_kind=kwargs["event_kind"],
            payload_ref="sha256:" + hashlib.sha256(payload).hexdigest(),
            source_event_at=kwargs.get("source_event_at"),
            provider_received_at=None,
            system_first_seen_at=kwargs["system_first_seen_at"],
            processed_at=kwargs["processed_at"],
            available_at=kwargs["processed_at"],
        )
        self.events[event.sequence] = (event, payload)
        return event

    def append_next_if_state_missing(self, **kwargs: Any) -> EventEnvelope | None:
        key = kwargs.pop("state_key")
        state = kwargs.pop("state_value")
        if key in self.states:
            return None
        event = self.append_next(**kwargs)
        self.states[key] = state
        return event

    def read_event(self, sequence: int) -> tuple[EventEnvelope, bytes] | None:
        return self.events.get(sequence)


def _response(**changes: object) -> dict[str, object]:
    row = {
        "symbol": "AAPL.US",
        "sharesFloat": 80,
        "sharesOutstanding": 100,
        "marketCap": 5000,
        "currency": "USD",
        "timestamp": int(NOW.timestamp()) - 60,
        "unprojectedPrice": 50,
    }
    return {
        "meta": {"count": 1},
        "data": {"AAPL.US": {**row, **changes}},
        "links": {"next": None},
    }


def _capture(
    journal: Journal,
    raw: bytes,
    *,
    symbols: tuple[str, ...] = ("AAPL.US",),
    received_at: datetime = NOW,
) -> int:
    return capture_eodhd_reference_raw(
        journal,
        raw,
        requested_symbols=symbols,
        requested_at=received_at - timedelta(seconds=1),
        received_at=received_at,
        evidence_context="PUBLIC_DEMO",
    )


def _project(raw: bytes) -> tuple[FakeJournal, dict[str, Any]]:
    journal = FakeJournal()
    seq = _capture(journal, raw)
    result = project_eodhd_reference(
        journal, source_response_sequence=seq, processed_at=NOW
    )
    return journal, json.loads(journal.events[result.sequences[0]][1])


def test_capture_and_projection_keep_source_fields_untrusted_and_separate() -> None:
    raw = json.dumps(_response()).encode()
    journal, payload = _project(raw)
    parent = json.loads(journal.events[1][1])
    assert base64.b64decode(parent["raw_response_base64"]) == raw
    assert parent["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert payload["float_shares"] == 80
    assert payload["shares_outstanding"] == 100
    assert payload["market_cap_usd"] == 5000
    assert (
        payload["float_effective_at"] is None and payload["float_revision_at"] is None
    )
    assert payload["trusted_float_shares"] is None
    assert payload["float_freshness"] == "UNVERIFIED"
    assert payload["entitlement"] == "UNVERIFIED_FOR_ACTUAL_USER"
    assert payload["evidence_context"] == "PUBLIC_DEMO"
    assert payload["strategy_eligibility"] == "BLOCKED_RESEARCH"
    assert payload["full_market_coverage"] is False
    assert "unprojectedPrice" not in payload
    assert journal.events[2][0].source_event_at == NOW - timedelta(seconds=60)


@pytest.mark.parametrize(
    "raw",
    [
        b"invalid",
        b"[]",
        b'{"meta":{},"meta":{}}',
        b'{"meta":{"count":0},"data":{},"links":{"next":NaN}}',
        b'{"meta":{"count":0},"data":{},"links":{"next":Infinity}}',
    ],
)
def test_invalid_bytes_are_retained_before_projection_rejects(raw: bytes) -> None:
    journal = FakeJournal()
    seq = _capture(journal, raw)
    with pytest.raises(EodhdReferenceError):
        project_eodhd_reference(journal, source_response_sequence=seq, processed_at=NOW)
    assert len(journal.events) == 1
    assert (
        base64.b64decode(json.loads(journal.events[1][1])["raw_response_base64"]) == raw
    )


@pytest.mark.parametrize("field", ["source_id", "feed", "event_kind", "payload_ref"])
def test_raw_envelope_tamper_is_rejected(field: str) -> None:
    journal = FakeJournal()
    _capture(journal, json.dumps(_response()).encode())
    event, raw = journal.events[1]
    if field == "source_id":
        changed = replace(event, source_id="tampered")
    elif field == "feed":
        changed = replace(event, feed="tampered")
    elif field == "event_kind":
        changed = replace(event, event_kind="tampered")
    else:
        changed = replace(event, payload_ref="tampered")
    journal.events[1] = (changed, raw)
    with pytest.raises(EodhdReferenceError, match="provenance"):
        project_eodhd_reference(journal, source_response_sequence=1, processed_at=NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("response_sha256", "0" * 64),
        ("response_bytes", True),
        ("response_bytes", 0),
        ("raw_response_base64", "?"),
        ("received_at", (NOW - timedelta(seconds=1)).isoformat()),
    ],
)
def test_raw_metadata_tamper_is_independently_rejected(
    field: str, value: object
) -> None:
    journal = FakeJournal()
    _capture(journal, json.dumps(_response()).encode())
    event, stored = journal.events[1]
    metadata = {**json.loads(stored), field: value}
    changed = json.dumps(metadata).encode()
    journal.events[1] = (
        replace(event, payload_ref="sha256:" + hashlib.sha256(changed).hexdigest()),
        changed,
    )
    with pytest.raises(EodhdReferenceError):
        project_eodhd_reference(journal, source_response_sequence=1, processed_at=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"meta": {"count": True}},
        {"meta": {"count": 2}},
        {"links": {"next": "nextpage"}},
        {"data": [1]},
        {"data": {"AAPL.US": {"symbol": "OTHER.US"}}},
        {"data": {"OTHER.US": {"symbol": "OTHER.US"}}},
    ],
)
def test_shape_count_pagination_and_unrequested_rows_reject(
    changes: dict[str, object],
) -> None:
    journal = FakeJournal()
    _capture(journal, json.dumps({**_response(), **changes}).encode())
    with pytest.raises(EodhdReferenceError):
        project_eodhd_reference(journal, source_response_sequence=1, processed_at=NOW)
    assert len(journal.events) == 1


@pytest.mark.parametrize("data", [[], {}])
def test_all_skipped_or_missing_requested_symbols_are_explicit(data: object) -> None:
    _, payload = _project(
        json.dumps(
            {"meta": {"count": 0}, "data": data, "links": {"next": None}}
        ).encode()
    )
    assert payload["float_status"] == "MISSING"
    assert payload["diagnostics"] == ["MISSING_NOT_STRATEGY_REJECTED"]
    assert payload["trusted_float_shares"] is None


@pytest.mark.parametrize("floating", [None, True, False, 0, -1, "80", 80.0])
def test_unavailable_float_never_falls_back_to_outstanding_or_cap(
    floating: object,
) -> None:
    _, payload = _project(json.dumps(_response(sharesFloat=floating)).encode())
    assert payload["float_shares"] is None
    assert payload["shares_outstanding"] == 100
    assert payload["float_status"] == "UNAVAILABLE"
    assert "SHARESFLOAT_UNAVAILABLE" in payload["diagnostics"]


def test_absent_float_field_cannot_fall_back_to_other_fields() -> None:
    response = _response()
    data = response["data"]
    assert isinstance(data, dict)
    data["AAPL.US"].pop("sharesFloat")
    _, payload = _project(json.dumps(response).encode())
    assert payload["float_shares"] is None
    assert payload["shares_outstanding"] == 100


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sharesOutstanding", True),
        ("sharesOutstanding", 0),
        ("marketCap", True),
        ("marketCap", 5000.5),
        ("marketCap", -1),
    ],
)
def test_outstanding_and_market_cap_require_positive_json_integers(
    field: str, value: object
) -> None:
    _, payload = _project(json.dumps(_response(**{field: value})).encode())
    assert payload["float_shares"] == 80
    assert f"{field.upper()}_UNAVAILABLE" in payload["diagnostics"]
    if field == "marketCap":
        assert payload["market_cap_usd"] is None


@pytest.mark.parametrize("timestamp", [None, True, "1790000000", 1.1, 10**30])
def test_invalid_provider_clock_cannot_become_source_event(timestamp: object) -> None:
    journal, payload = _project(json.dumps(_response(timestamp=timestamp)).encode())
    assert "PROVIDER_TIMESTAMP_INVALID" in payload["diagnostics"]
    assert journal.events[2][0].source_event_at is None
    assert payload["trusted_float_shares"] is None


def test_future_snapshot_is_retained_as_diagnostic_not_event_time() -> None:
    future = int(NOW.timestamp()) + 1
    journal, payload = _project(json.dumps(_response(timestamp=future)).encode())
    assert payload["provider_snapshot_at"] == (NOW + timedelta(seconds=1)).isoformat()
    assert payload["provider_timestamp_seconds"] == future
    assert "PROVIDER_TIMESTAMP_FUTURE" in payload["diagnostics"]
    assert journal.events[2][0].source_event_at is None


def test_float_greater_than_outstanding_and_currency_conflicts_are_diagnostics() -> (
    None
):
    _, payload = _project(
        json.dumps(_response(sharesFloat=101, currency="EUR")).encode()
    )
    assert "FLOAT_EXCEEDS_OUTSTANDING" in payload["diagnostics"]
    assert "MARKET_CAP_CURRENCY_NOT_USD" in payload["diagnostics"]
    assert payload["market_cap_reported"] == 5000
    assert payload["market_cap_usd"] is None


def test_projection_is_idempotent_per_raw_sequence_and_symbol() -> None:
    journal = FakeJournal()
    response = _response()
    data = response["data"]
    assert isinstance(data, dict)
    data["BRK-B.US"] = {"symbol": "BRK-B.US"}
    response["meta"] = {"count": 2}
    _capture(journal, json.dumps(response).encode(), symbols=("AAPL.US", "BRK-B.US"))
    first = project_eodhd_reference(
        journal, source_response_sequence=1, processed_at=NOW
    )
    again = project_eodhd_reference(
        journal, source_response_sequence=1, processed_at=NOW + timedelta(seconds=2)
    )
    assert first.sequences == (2, 3) and first.symbol_count == 2
    assert again.sequences == () and again.status == "DUPLICATE"
    assert len(journal.states) == 2 and len(journal.events) == 3
    assert json.loads(journal.events[3][1])["symbol"] == "BRK-B.US"


def test_partial_missing_symbols_get_independent_observations() -> None:
    journal = FakeJournal()
    _capture(
        journal, json.dumps(_response()).encode(), symbols=("AAPL.US", "MISSING.US")
    )
    result = project_eodhd_reference(
        journal, source_response_sequence=1, processed_at=NOW
    )
    assert result.sequences == (2, 3)
    assert json.loads(journal.events[2][1])["float_shares"] == 80
    missing = json.loads(journal.events[3][1])
    assert missing["symbol"] == "MISSING.US" and missing["float_status"] == "MISSING"


def test_actual_journal_transactional_projection_is_compatible(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "runtime" / "journal.sqlite3", tmp_path / "runtime")
    try:
        _capture(journal, json.dumps(_response()).encode())
        first = project_eodhd_reference(
            journal, source_response_sequence=1, processed_at=NOW
        )
        again = project_eodhd_reference(
            journal, source_response_sequence=1, processed_at=NOW
        )
        assert first.sequences == (2,) and again.sequences == ()
        assert journal.read_event(3) is None
        event = journal.read_event(2)
        assert event is not None and event[0].feed == FEED
    finally:
        journal.close()


def test_delayed_projection_and_later_identical_capture_keep_replay_clocks() -> None:
    journal = FakeJournal()
    raw = json.dumps(_response()).encode()
    _capture(journal, raw)
    derived_at = NOW + timedelta(seconds=2)
    project_eodhd_reference(
        journal, source_response_sequence=1, processed_at=derived_at
    )
    later = _capture(journal, raw, received_at=NOW + timedelta(seconds=5))
    result = project_eodhd_reference(
        journal, source_response_sequence=later, processed_at=NOW + timedelta(seconds=6)
    )
    assert later == 3 and result.sequences == (4,)
    assert journal.events[4][0].system_first_seen_at == NOW + timedelta(seconds=5)
    events = [event for event, _ in journal.events.values()]
    before = replay_at(events, ReplayMode.PROSPECTIVE, NOW + timedelta(seconds=1))
    after = replay_at(events, ReplayMode.PROSPECTIVE, derived_at)
    assert [item.event.event_kind for item in before.observations] == [RAW_KIND]
    assert [item.event.event_kind for item in after.observations] == [
        RAW_KIND,
        DERIVED_KIND,
    ]


def test_invalid_request_identity_cannot_smuggle_a_query_or_duplicate() -> None:
    journal = FakeJournal()
    for symbols in (("AAPL.US?api_token=secret",), ("AAPL.US", "AAPL.US")):
        with pytest.raises(EodhdReferenceError, match="identities") as error:
            _capture(journal, b"{}", symbols=symbols)
        assert "secret" not in str(error.value)
    assert not journal.events


def test_clock_order_rejects_before_capture_or_projection() -> None:
    journal = FakeJournal()
    with pytest.raises(EodhdReferenceError, match="clocks"):
        capture_eodhd_reference_raw(
            journal,
            b"{}",
            requested_symbols=("AAPL.US",),
            requested_at=NOW,
            received_at=NOW - timedelta(seconds=1),
            evidence_context="PUBLIC_DEMO",
        )
    _capture(journal, json.dumps(_response()).encode())
    with pytest.raises(EodhdReferenceError, match="processing"):
        project_eodhd_reference(
            journal, source_response_sequence=1, processed_at=NOW - timedelta(seconds=1)
        )
    assert (
        journal.events[1][0].source_id == SOURCE and journal.events[1][0].feed == FEED
    )
