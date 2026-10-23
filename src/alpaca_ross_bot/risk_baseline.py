"""Source-bound pre-session equity observation; never trading authority."""

import base64
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .journal import Journal
from .order_lifecycle import OrderProjection
from .order_reconciliation import OrderReconciler
from .paper_order_contract import decode_broker_json
from .risk_account_inputs import ENDPOINT, SOURCE, inspect_account_inputs
from .risk_ownership import qualify_ownership

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True, repr=False)
class EquityBaseline:
    account_ref: str
    session_day: date
    equity: Decimal
    session_open: datetime
    observed_at: datetime
    account_sequence: int
    calendar_sequence: int
    frozen_at: datetime | None = None
    runtime_authorized: bool = False
    durability_observed_at: datetime | None = None
    qualified_at: datetime | None = None
    receipt_sequence: int | None = None
    receipt_payload_ref: str | None = None
    rollover_intents: tuple[tuple[str, str], ...] = ()
    rollover_source_refs: tuple[tuple[int, str], ...] = ()


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> None:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise ValueError("baseline clock must be UTC")


def _timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError("calendar timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _utc(parsed)
    return parsed


def _session_interval(
    journal: Journal,
    sequence: int,
    account_ref: str,
    day: date,
    at: datetime,
    max_age: timedelta,
) -> tuple[datetime, datetime]:
    if type(sequence) is not int or sequence < 1:
        raise ValueError("calendar sequence is invalid")
    stored = journal.read_event(sequence)
    if stored is None:
        raise ValueError("calendar evidence is missing")
    event, raw = stored
    if (
        (event.source_id, event.feed, event.event_kind)
        != (SOURCE, "paper", "CALENDAR_INPUT_CAPTURE")
        or not event.visible_at(at)
        or event.external_clock_conflict
    ):
        raise ValueError("calendar source is unavailable")
    body = decode_broker_json(raw)
    if not isinstance(body, dict) or set(body) != {
        "account_ref",
        "endpoint",
        "request",
    }:
        raise ValueError("calendar capture is invalid")
    if body["account_ref"] != account_ref or body["endpoint"] != ENDPOINT:
        raise ValueError("calendar account differs")
    request = body["request"]
    if not isinstance(request, dict) or set(request) != {
        "method",
        "path",
        "status",
        "started_at",
        "completed_at",
        "body",
    }:
        raise ValueError("calendar HTTP capture is invalid")
    if (
        request["method"] != "GET"
        or type(request["status"]) is not int
        or request["status"] != 200
    ):
        raise ValueError("calendar is not a successful GET")
    path = request["path"]
    if type(path) is not str:
        raise ValueError("calendar path is invalid")
    parsed = urlsplit(path)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or parsed.path != "/v2/calendar"
        or set(query) != {"start", "end"}
        or any(len(value) != 1 for value in query.values())
    ):
        raise ValueError("calendar query is invalid")
    start, end = (date.fromisoformat(query[key][0]) for key in ("start", "end"))
    if not start <= day <= end:
        raise ValueError("calendar query excludes session")
    began, ended = (
        _timestamp(request["started_at"]),
        _timestamp(request["completed_at"]),
    )
    if not began <= ended <= event.system_first_seen_at <= at or at - began > max_age:
        raise ValueError("calendar chronology or freshness is invalid")
    if type(request["body"]) is not str:
        raise ValueError("calendar response bytes are missing")
    rows = decode_broker_json(request["body"])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("calendar response is invalid")
    days: set[date] = set()
    sessions: list[tuple[datetime, datetime]] = []
    for row in rows:
        raw_day = row.get("date")
        if type(raw_day) is not str:
            raise ValueError("calendar row date is invalid")
        row_day = date.fromisoformat(raw_day)
        if row_day in days or not start <= row_day <= end:
            raise ValueError("calendar duplicate or outside query")
        days.add(row_day)
        clocks: list[datetime] = []
        for field in ("open", "close"):
            value = row.get(field)
            if type(value) is not str or len(value) != 5 or value[2] != ":":
                raise ValueError("calendar session time is invalid")
            parsed_time = time.fromisoformat(value)
            if parsed_time.tzinfo is not None:
                raise ValueError("calendar session time must be local")
            clocks.append(datetime.combine(row_day, parsed_time, NY).astimezone(UTC))
        if clocks[0] >= clocks[1]:
            raise ValueError("calendar open must precede close")
        if row_day == day:
            sessions.append((clocks[0], clocks[1]))
    if len(sessions) != 1:
        raise ValueError("calendar session is closed or missing")
    return sessions[0]


def _session_open(
    journal: Journal,
    sequence: int,
    account_ref: str,
    day: date,
    at: datetime,
    max_age: timedelta,
) -> datetime:
    return _session_interval(journal, sequence, account_ref, day, at, max_age)[0]


def session_open_from_evidence(
    journal: Journal,
    calendar_sequence: int,
    account_ref: str,
    session_day: date,
    decision_at: datetime,
    max_age: timedelta,
) -> datetime:
    """Read official calendar evidence without granting baseline acceptance."""
    return session_interval_from_evidence(
        journal, calendar_sequence, account_ref, session_day, decision_at, max_age
    )[0]


def session_interval_from_evidence(
    journal: Journal,
    calendar_sequence: int,
    account_ref: str,
    session_day: date,
    decision_at: datetime,
    max_age: timedelta,
) -> tuple[datetime, datetime]:
    """Same validated official evidence; retain early closes and DST explicitly."""
    _utc(decision_at)
    if (
        type(session_day) is not date
        or type(max_age) is not timedelta
        or max_age <= timedelta(0)
    ):
        raise ValueError("calendar session day and positive age required")
    journal.require_private_order_state()
    if journal.read_paper_account_ref() != account_ref:
        raise ValueError("calendar account is not bound")
    return _session_interval(
        journal, calendar_sequence, account_ref, session_day, decision_at, max_age
    )


def _inspect_baseline(
    journal: Journal,
    *,
    account_ref: str,
    account_sequence: int,
    calendar_sequence: int,
    session_day: date,
    decision_at: datetime,
    max_age: timedelta,
    check_retained_intents: bool = True,
) -> EquityBaseline:
    """Inspect immutable flat equity, preserving missing transfer fields as unknown."""
    _utc(decision_at)
    if (
        type(session_day) is not date
        or decision_at.astimezone(NY).date() != session_day
    ):
        raise ValueError("baseline must be observed on the session day")
    if type(max_age) is not timedelta or max_age <= timedelta(0):
        raise ValueError("positive baseline age required")
    opened = _session_open(
        journal, calendar_sequence, account_ref, session_day, decision_at, max_age
    )
    facts = inspect_account_inputs(
        journal,
        account_sequence,
        account_ref=account_ref,
        decision_at=decision_at,
        session_open=opened,
        max_age=max_age,
    )
    stored_account = journal.read_event(account_sequence)
    if stored_account is None:
        raise ValueError("account source disappeared")
    account_body = decode_broker_json(stored_account[1])
    assert isinstance(account_body, dict)  # inspector verified the exact schema
    requests = account_body["requests"]
    assert isinstance(requests, list)
    if any(
        _timestamp(row["started_at"]).astimezone(NY).date() != session_day
        for row in requests
    ):
        raise ValueError("account capture must start on the session day")
    money = dict(facts.money)
    equity = money["equity"]
    if (
        not facts.active_usd_flags_clear
        or equity is None
        or equity <= 0
        or equity != money["cash"]
        or not facts.before_session_open
        or decision_at >= opened
        or facts.observed_at.astimezone(NY).date() != session_day
    ):
        raise ValueError("flat pre-session equity is not established")
    counts = facts.collection_counts
    if [
        (path, count)
        for path, count in counts
        if urlsplit(path).path == "/v2/positions"
    ] != [("/v2/positions", 0)]:
        raise ValueError("empty unfiltered position evidence required")
    orders = [
        (path, count) for path, count in counts if urlsplit(path).path == "/v2/orders"
    ]
    if len(orders) != 1 or orders[0][1] != 0:
        raise ValueError("empty unfiltered open order evidence required")
    query = parse_qs(urlsplit(orders[0][0]).query, keep_blank_values=True)
    if (
        set(query) - {"status", "limit", "nested", "direction"}
        or query.get("status") != ["open"]
        or query.get("limit") != ["500"]
        or query.get("nested") != ["false"]
        or query.get("direction", ["asc"]) != ["asc"]
    ):
        raise ValueError("open order query is incomplete")
    candidate = EquityBaseline(
        account_ref,
        session_day,
        equity,
        opened,
        facts.observed_at,
        account_sequence,
        calendar_sequence,
    )
    intents = journal.read_order_intent_ids() if check_retained_intents else ()
    return (
        _with_rollover_proof(
            journal, candidate, intents, decision_at, max_age, current_inputs=True
        )
        if intents
        else candidate
    )


def _with_rollover_proof(
    journal: Journal,
    candidate: EquityBaseline,
    intent_ids: tuple[str, ...],
    decision_at: datetime,
    max_age: timedelta,
    *,
    current_inputs: bool = False,
    source_horizon: int | None = None,
) -> EquityBaseline:
    """Retain lifetime intent history while proving a fresh terminal flat book."""
    if not intent_ids or tuple(sorted(set(intent_ids))) != intent_ids:
        raise ValueError("rollover intent census is invalid")
    ownership = qualify_ownership(
        journal,
        candidate.account_sequence,
        account_ref=candidate.account_ref,
        decision_at=decision_at,
        session_open=candidate.session_open,
        max_age=max_age,
        _intent_ids=intent_ids,
        _source_horizon=source_horizon,
    )
    state, lifecycle_refs = OrderReconciler.read_evidence_at(
        journal,
        candidate.account_ref,
        decision_at,
        through_sequence=source_horizon,
    )
    orders = state.get("orders")
    if (
        not ownership.ownership_verified
        or ownership.issues
        or ownership.owned_long_positions
        or not isinstance(orders, dict)
        or set(orders) != set(intent_ids)
    ):
        raise ValueError("rollover requires independently owned flat inventory")
    for row in orders.values():
        if not isinstance(row, dict) or OrderProjection.from_dict(
            row
        ).state.value not in {
            "FILLED",
            "CANCELED",
            "EXPIRED",
            "REJECTED",
        }:
            raise ValueError("rollover requires all retained orders terminal")
    last = journal.read_event(int(str(state["watermark"])))
    assert last is not None  # ownership verifier requires the latest REST source
    source = decode_broker_json(last[1])
    assert isinstance(source, dict) and isinstance(source["intents"], dict)
    fingerprints: list[tuple[str, str]] = []
    for cid in intent_ids:
        intent = journal.read_order_intent(cid)
        if not isinstance(intent, dict):
            raise ValueError("rollover retained intent is missing")
        if current_inputs:
            current = {
                "intent": intent,
                "sent": journal.has_order_send_attempt(cid),
                "http_history": [
                    {
                        "status": row.http_status,
                        "body": base64.b64encode(row.body).decode(),
                    }
                    for row in journal.read_order_http_observations(cid)
                    if row.body is not None and row.http_status in (200, 201)
                ],
            }
            if current != source["intents"].get(cid):
                raise ValueError("rollover local inputs changed after reconciliation")
        digest = hashlib.sha256(
            json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        fingerprints.append((cid, "sha256:" + digest))
    refs: list[tuple[int, str]] = []
    for sequence in sorted(set(ownership.source_refs + lifecycle_refs)):
        stored = journal.read_event(sequence)
        if (
            stored is None
            or not stored[0].visible_at(decision_at)
            or stored[0].external_clock_conflict
        ):
            raise ValueError("rollover source proof is unavailable")
        refs.append((sequence, stored[0].payload_ref))
    return replace(
        candidate,
        rollover_intents=tuple(fingerprints),
        rollover_source_refs=tuple(refs),
    )


def inspect_baseline(
    journal: Journal,
    *,
    account_ref: str,
    account_sequence: int,
    calendar_sequence: int,
    session_day: date,
    decision_at: datetime,
    max_age: timedelta,
) -> EquityBaseline:
    return _inspect_baseline(
        journal,
        account_ref=account_ref,
        account_sequence=account_sequence,
        calendar_sequence=calendar_sequence,
        session_day=session_day,
        decision_at=decision_at,
        max_age=max_age,
    )


def _key(account_ref: str, day: date) -> str:
    return "risk_baseline:" + account_ref + ":" + day.isoformat()


def _payload(
    candidate: EquityBaseline, frozen_at: datetime, max_age: timedelta
) -> dict[str, object]:
    body: dict[str, object] = {
        "account_ref": candidate.account_ref,
        "session_day": candidate.session_day.isoformat(),
        "equity": str(candidate.equity),
        "session_open": candidate.session_open.isoformat(),
        "observed_at": candidate.observed_at.isoformat(),
        "account_sequence": candidate.account_sequence,
        "calendar_sequence": candidate.calendar_sequence,
        "frozen_at": frozen_at.isoformat(),
        "max_age_us": max_age // timedelta(microseconds=1),
        "runtime_authorized": False,
    }
    if candidate.rollover_intents:
        body["rollover_proof"] = {
            "profile": "FLAT_OWNED_TERMINAL_LIFECYCLE_V1",
            "intents": [list(item) for item in candidate.rollover_intents],
            "source_refs": [list(item) for item in candidate.rollover_source_refs],
        }
    return body


def _read_candidate(
    journal: Journal, *, account_ref: str, session_day: date
) -> tuple[EquityBaseline, int, str] | None:
    """Rebuild projection from immutable freeze and original HTTP source events."""
    state = journal.read_state(_key(account_ref, session_day))
    matching = []
    for event, raw in journal.read_risk_baseline_events():
        body = decode_broker_json(raw)
        if isinstance(body, dict) and (
            body.get("account_ref"),
            body.get("session_day"),
        ) == (account_ref, session_day.isoformat()):
            matching.append((event, body))
    if state is None and not matching:
        return None
    if len(matching) != 1 or json.dumps(state, sort_keys=True) != json.dumps(
        matching[0][1], sort_keys=True
    ):
        raise ValueError("baseline projection differs from immutable evidence")
    event, body = matching[0]
    frozen_at = _timestamp(body.get("frozen_at"))
    if (
        event.feed != "paper"
        or event.event_kind != "PRESESSION_E0"
        or event.external_clock_conflict
        or event.system_first_seen_at != frozen_at
        or event.processed_at != frozen_at
        or event.available_at != frozen_at
    ):
        raise ValueError("baseline freeze chronology differs")
    if (
        type(body.get("account_sequence")) is not int
        or type(body.get("calendar_sequence")) is not int
        or type(body.get("max_age_us")) is not int
        or not 0 < body["account_sequence"] < event.sequence
        or not 0 < body["calendar_sequence"] < event.sequence
    ):
        raise ValueError("baseline source references are invalid")
    rebuilt = _inspect_baseline(
        journal,
        account_ref=account_ref,
        account_sequence=body["account_sequence"],
        calendar_sequence=body["calendar_sequence"],
        session_day=session_day,
        decision_at=frozen_at,
        max_age=timedelta(microseconds=body["max_age_us"]),
        check_retained_intents=False,
    )
    if "rollover_proof" in body:
        proof = body["rollover_proof"]
        if not isinstance(proof, dict) or set(proof) != {
            "profile",
            "intents",
            "source_refs",
        }:
            raise ValueError("baseline rollover proof is invalid")
        intents = proof["intents"]
        if (
            not isinstance(intents, list)
            or not intents
            or any(
                not isinstance(item, list)
                or len(item) != 2
                or any(type(value) is not str or not value for value in item)
                for item in intents
            )
        ):
            raise ValueError("baseline rollover intent proof is invalid")
        source_refs = proof["source_refs"]
        if (
            not isinstance(source_refs, list)
            or not source_refs
            or any(
                not isinstance(item, list)
                or len(item) != 2
                or type(item[0]) is not int
                or not 0 < item[0] < event.sequence
                or type(item[1]) is not str
                or not item[1]
                for item in source_refs
            )
        ):
            raise ValueError("baseline rollover source proof is invalid")
        rebuilt = _with_rollover_proof(
            journal,
            rebuilt,
            tuple(item[0] for item in intents),
            frozen_at,
            timedelta(microseconds=body["max_age_us"]),
            source_horizon=max(item[0] for item in source_refs),
        )
    if _payload(rebuilt, frozen_at, timedelta(microseconds=body["max_age_us"])) != body:
        raise ValueError("baseline source reconstruction differs")
    return (replace(rebuilt, frozen_at=frozen_at), event.sequence, event.payload_ref)


def _receipt_key(account_ref: str, session_day: date) -> str:
    return "risk_baseline_receipt:" + account_ref + ":" + session_day.isoformat()


def _read_receipt(
    journal: Journal,
    candidate: EquityBaseline,
    sequence: int,
    payload_ref: str,
) -> EquityBaseline | None:
    state = journal.read_state(
        _receipt_key(candidate.account_ref, candidate.session_day)
    )
    matches = []
    for event, raw in journal.read_risk_baseline_receipt_events():
        body = decode_broker_json(raw)
        if isinstance(body, dict) and (
            body.get("account_ref"),
            body.get("session_day"),
        ) == (candidate.account_ref, candidate.session_day.isoformat()):
            matches.append((event, body))
    if state is None and not matches:
        return None
    if len(matches) != 1 or json.dumps(state, sort_keys=True) != json.dumps(
        matches[0][1], sort_keys=True
    ):
        raise ValueError("baseline durability receipt projection differs")
    event, body = matches[0]
    observed = _timestamp(body.get("observed_durable_at"))
    expected = {
        "account_ref": candidate.account_ref,
        "session_day": candidate.session_day.isoformat(),
        "baseline_sequence": sequence,
        "baseline_payload_ref": payload_ref,
        "observed_durable_at": observed.isoformat(),
        "runtime_authorized": False,
    }
    if (
        json.dumps(body, sort_keys=True) != json.dumps(expected, sort_keys=True)
        or type(body.get("baseline_sequence")) is not int
        or candidate.frozen_at is None
        or not candidate.frozen_at <= observed < candidate.session_open
        or observed.astimezone(NY).date() != candidate.session_day
        or event.sequence <= sequence
        or event.external_clock_conflict
        or not observed
        <= event.system_first_seen_at
        <= event.processed_at
        <= event.available_at
    ):
        raise ValueError(
            "baseline durability receipt does not prove pre-session persistence"
        )
    return replace(
        candidate,
        durability_observed_at=observed,
        qualified_at=event.available_at,
        receipt_sequence=event.sequence,
        receipt_payload_ref=event.payload_ref,
    )


def read_baseline(
    journal: Journal,
    *,
    account_ref: str,
    session_day: date,
    decision_at: datetime | None = None,
    through_sequence: int | None = None,
) -> EquityBaseline | None:
    """Accept only a first commit observed durable before the official open.

    A receipt may be committed later: it records when the already committed
    candidate was observed. A missing receipt never retrospectively proves E0.
    With a decision cutoff, a not-yet-qualified candidate is unavailable; source
    and projection corruption still raises instead of becoming a valid E0. A
    sequence horizon also excludes later commits sharing the decision clock.
    """
    if decision_at is not None:
        _utc(decision_at)
    if through_sequence is not None and (
        type(through_sequence) is not int or through_sequence < 0
    ):
        raise ValueError("baseline sequence horizon must be a nonnegative integer")
    pending = _read_candidate(journal, account_ref=account_ref, session_day=session_day)
    if pending is None:
        orphan = journal.read_state(_receipt_key(account_ref, session_day)) is not None
        for _, raw in journal.read_risk_baseline_receipt_events():
            body = decode_broker_json(raw)
            if isinstance(body, dict) and (
                body.get("account_ref"),
                body.get("session_day"),
            ) == (account_ref, session_day.isoformat()):
                orphan = True
        if orphan:
            raise ValueError("baseline receipt has no candidate")
        return None
    if (
        decision_at is not None
        and pending[0].frozen_at is not None
        and pending[0].frozen_at > decision_at
    ) or (through_sequence is not None and pending[1] > through_sequence):
        return None
    result = _read_receipt(journal, *pending)
    if result is None:
        if decision_at is not None or through_sequence is not None:
            return None
        raise ValueError("baseline candidate lacks pre-session durability receipt")
    if (
        decision_at is not None
        and result.qualified_at is not None
        and result.qualified_at > decision_at
    ) or (
        through_sequence is not None
        and result.receipt_sequence is not None
        and result.receipt_sequence > through_sequence
    ):
        return None
    return result


def freeze_baseline(
    journal: Journal,
    *,
    account_ref: str,
    account_sequence: int,
    calendar_sequence: int,
    session_day: date,
    max_age: timedelta,
) -> EquityBaseline:
    """Freeze once using the actual local clock; cannot create an intraday E0."""
    existing = _read_candidate(
        journal, account_ref=account_ref, session_day=session_day
    )
    if existing is not None and (
        existing[0].account_sequence,
        existing[0].calendar_sequence,
    ) != (account_sequence, calendar_sequence):
        raise ValueError("frozen baseline cannot be replaced")
    if existing is not None:
        accepted = _read_receipt(journal, *existing)
        if accepted is not None:
            return accepted

    def prepare() -> tuple[bytes, object, datetime]:
        now = _now()
        candidate = inspect_baseline(
            journal,
            account_ref=account_ref,
            account_sequence=account_sequence,
            calendar_sequence=calendar_sequence,
            session_day=session_day,
            decision_at=now,
            max_age=max_age,
        )
        body = _payload(candidate, now, max_age)
        return json.dumps(body, sort_keys=True).encode(), body, now

    journal.append_next_if_state_missing(
        state_key=_key(account_ref, session_day),
        state_value=None,
        source_id="ALPACA_RISK_BASELINE",
        feed="paper",
        event_kind="PRESESSION_E0",
        payload=b"",
        system_first_seen_at=_now(),
        processed_at=_now(),
        prepare_under_lock=prepare,
    )
    pending = _read_candidate(journal, account_ref=account_ref, session_day=session_day)
    if pending is None or (
        pending[0].account_sequence,
        pending[0].calendar_sequence,
    ) != (account_sequence, calendar_sequence):
        raise ValueError("concurrent baseline differs")
    candidate, sequence, payload_ref = pending
    observed = _now()
    _utc(observed)
    if (
        candidate.frozen_at is None
        or not candidate.frozen_at <= observed < candidate.session_open
    ):
        raise ValueError("candidate commit was not observed before session open")
    receipt = {
        "account_ref": account_ref,
        "session_day": session_day.isoformat(),
        "baseline_sequence": sequence,
        "baseline_payload_ref": payload_ref,
        "observed_durable_at": observed.isoformat(),
        "runtime_authorized": False,
    }

    def prepare_receipt() -> tuple[bytes, object, datetime]:
        recorded = _now()
        _utc(recorded)
        if recorded < observed:
            raise ValueError("durability receipt clock moved backwards")
        return json.dumps(receipt, sort_keys=True).encode(), receipt, recorded

    journal.append_next_if_state_missing(
        state_key=_receipt_key(account_ref, session_day),
        state_value=None,
        source_id="ALPACA_RISK_BASELINE_RECEIPT",
        feed="paper",
        event_kind="E0_DURABILITY_OBSERVED",
        payload=b"",
        system_first_seen_at=observed,
        processed_at=observed,
        prepare_under_lock=prepare_receipt,
    )
    result = read_baseline(journal, account_ref=account_ref, session_day=session_day)
    if result is None:
        raise ValueError("baseline durability receipt is missing")
    return result
