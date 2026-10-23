"""Source-backed local reservations, not broker/protection qualification."""

import json
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from alpaca_ross_bot.account_risk_owner import AccountRiskOwner
from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.crossed_stop import CrossedStopJournal
from alpaca_ross_bot.crossed_stop_contract import load_crossed_stop_contract
from alpaca_ross_bot.crossed_stop_preparation import preparation_request
from alpaca_ross_bot.journal import JournalError
from alpaca_ross_bot.oco_journal import OcoJournal
from alpaca_ross_bot.scanner_data import AlpacaScannerData, ScannerDataError
from test_account_risk_owner import SourceAccount, account_sources
from test_observation_candidate_binding import AT, CID, new_decision
from test_oco_entry_evidence import _initial, _prepare
from test_risk_ledger_audit import POLICY


@pytest.mark.parametrize(
    "changes",
    [
        {"asset_sequence": True},
        {"status_sequence": 0},
        {"status_record_index": -1},
        {"max_asset_age": timedelta(0)},
        {"max_status_age": timedelta(microseconds=-1)},
    ],
)
def test_preparation_requires_explicit_exact_research_bounds(
    changes: dict[str, Any],
) -> None:
    values: dict[str, Any] = {
        "asset_sequence": 1,
        "status_sequence": 2,
        "status_record_index": 0,
        "max_asset_age": timedelta(seconds=1),
        "max_status_age": timedelta(seconds=1),
    } | changes
    with pytest.raises(JournalError):
        preparation_request(**values)


def asset(source: SourceAccount, at: datetime, **changes: object) -> int:
    def fetch(
        url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        return json.dumps(
            [
                {
                    "symbol": "AAPL",
                    "class": "us_equity",
                    "status": "active",
                    "tradable": True,
                }
                | changes
            ]
        ).encode()

    client = AlpacaScannerData(
        PaperCredentials(SecretValue("synthetic-key"), SecretValue("synthetic-secret")),
        source.journal,
        timeout_seconds=1,
        fetch=fetch,
        clock=lambda: at,
    )
    try:
        return client.collect_active_equity_assets().sequence
    except ScannerDataError:
        # A rejected vendor response remains immutable raw evidence. Verify
        # that preparation cannot bypass the collector's rejection.
        return next(
            event.sequence
            for event, _ in reversed(
                source.journal.read_scanner_events(
                    through_sequence=source.journal.read_evidence_sequence()
                )
            )
            if event.event_kind == "ASSET_DIRECTORY_RAW_RESPONSE"
        )


def status(source: SourceAccount, at: datetime, **changes: object) -> int:
    return source.journal.append_next(
        source_id="alpaca_market_data",
        feed="iex",
        event_kind="RAW_FRAME",
        payload=json.dumps(
            [
                {
                    "T": "s",
                    "S": "AAPL",
                    "sc": "T",
                    "sm": "Trading Resumption",
                    "rc": "",
                    "rm": "",
                    "t": at.isoformat().replace("+00:00", "Z"),
                    "z": "C",
                }
                | changes
            ]
        ).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence


@pytest.fixture
def ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[
    tuple[
        SourceAccount,
        AccountRiskOwner,
        OcoJournal,
        CrossedStopJournal,
        datetime,
        int,
        int,
    ]
]:
    options: dict[str, Any] = getattr(request, "param", {})
    record = OcoJournal._record

    def timed_record(
        self: OcoJournal, kind: str, operation: dict[str, Any], at: datetime
    ) -> Any:
        return record(self, kind, operation, AT if kind == "BIND" else at)

    monkeypatch.setattr(OcoJournal, "_record", timed_record)
    with account_sources(tmp_path, monkeypatch) as (source, risk):
        first = _prepare(source, risk, new_decision())
        owner = OcoJournal(source.journal, _initial())
        owner.bind_entry_census("partial", first, POLICY, first)
        if options.get("opt_in", True):
            risk.bind_emergency_policy(
                "opt-in",
                first,
                client_order_id=CID,
                contract=load_crossed_stop_contract(),
            )
        at = first + timedelta(seconds=1)
        source.order(
            CID,
            at,
            "canceled" if options.get("terminal", True) else "pending_cancel",
            2,
        )
        risk.observe(
            "terminal-source",
            source.selection(
                at,
                cash="480",
                position=2,
                bid="9.49",
                available=options.get("available", "2"),
            ),
            at,
        )
        owner.bind_entry_census("terminal", at, POLICY, at)
        if options.get("group"):
            from alpaca_ross_bot.oco_research import LotPortion

            owner.allocate_group(
                "unknown", (LotPortion("execution-0", Fraction(1)),), at
            )
        emergency = CrossedStopJournal(owner)
        emergency.observe_source("cross", at)
        yield source, risk, owner, emergency, at, asset(source, at), status(source, at)


def prepare(
    ready: tuple[
        SourceAccount,
        AccountRiskOwner,
        OcoJournal,
        CrossedStopJournal,
        datetime,
        int,
        int,
    ],
    **changes: Any,
) -> dict[str, Any]:
    _, _, _, emergency, at, assets, statuses = ready
    values: dict[str, Any] = {
        "asset_sequence": assets,
        "status_sequence": statuses,
        "status_record_index": 0,
        "max_asset_age": timedelta(seconds=1),
        "max_status_age": timedelta(seconds=1),
    } | changes
    return emergency.prepare_source("residual", at, **values)


def test_raw_preparation_reserves_exact_lots_without_send_risk_change_or_no_halt_claim(
    ready: Any,
) -> None:
    source, risk, owner, emergency, at, _, _ = ready
    before_risk = risk.view()
    before_oco = owner.view()
    before_intents = source.journal.read_order_intent_ids()
    state = prepare(ready)
    close = state["closes"]["residual"]
    assert close["quantity"] == "2" and close["portions"] == {"execution-0": "2"}
    assert close["request"]["type"] == "market" and close["request"]["qty"] == "2"
    assert close["attempt"] is None and not close["dispatch_compatible"]
    receipt = close["source_preparation"]
    assert receipt["reported_available_quantity"] == "2"
    for flag in (
        "current_no_halt_proven",
        "continuous_status_coverage_proven",
        "broker_reservation_atomicity_proven",
        "dispatch_authorized",
        "runtime_authorized",
    ):
        assert receipt[flag] is False
    for sequence, digest in receipt["source_refs"]:
        stored = source.journal.read_event(sequence)
        assert stored is not None and stored[0].payload_ref == digest
        assert sequence <= receipt["source_horizon"] and stored[0].visible_at(at)
    assert risk.view() == before_risk and owner.view() == before_oco
    assert source.journal.read_order_intent_ids() == before_intents
    assert CrossedStopJournal(owner).view() == state
    from test_crossed_stop_source import declared

    with pytest.raises(JournalError, match="declared exit"):
        emergency.mark_attempt("residual", "not-authorized", declared(at))


@pytest.mark.parametrize(
    "ready",
    [
        {"opt_in": False},
        {"terminal": False},
        {"available": None},
        {"available": "1"},
        {"available": "3"},
        {"available": "bad"},
    ],
    indirect=True,
)
def test_missing_terminal_opt_in_or_available_quantity_never_reserves(
    ready: Any,
) -> None:
    source, _, _, emergency, _, _, _ = ready
    before = tuple(source.journal._db.iterdump())
    with pytest.raises((JournalError, ValueError)):
        prepare(ready)
    assert tuple(source.journal._db.iterdump()) == before
    assert emergency.view()["closes"] == {}


@pytest.mark.parametrize(
    "change",
    [
        {"tradable": False},
        {"tradable": 1},
        {"status": "inactive"},
        {"symbol": "MSFT"},
        {"class": "crypto"},
    ],
)
def test_asset_eligibility_never_substitutes_for_reported_tradability(
    ready: Any, change: dict[str, object]
) -> None:
    source, _, _, _, at, _, _ = ready
    seq = asset(source, at, **change)
    with pytest.raises((JournalError, ValueError)):
        prepare(ready, asset_sequence=seq)


@pytest.mark.parametrize(
    "change",
    [
        {"sc": "H"},
        {"sc": "Q"},
        {"sc": "P"},
        {"T": "q"},
        {"S": "MSFT"},
        {"z": "A"},
        {"sc": 3},
    ],
)
def test_quote_or_halt_or_quotation_resume_is_not_trading_resume(
    ready: Any, change: dict[str, object]
) -> None:
    source, _, _, _, at, _, _ = ready
    seq = status(source, at, **change)
    with pytest.raises(JournalError):
        prepare(ready, status_sequence=seq)


def test_later_halt_and_asset_capture_invalidate_older_selected_facts(
    ready: Any,
) -> None:
    source, _, _, _, at, _, _ = ready
    status(source, at, sc="H", sm="Trading Halt")
    with pytest.raises(JournalError, match="superseded"):
        prepare(ready)
    seq = status(source, at)
    asset(source, at, tradable=False)
    with pytest.raises(JournalError, match="superseded"):
        prepare(ready, status_sequence=seq)


def test_retry_retains_original_horizon_and_changed_request_latches_conflict(
    ready: Any,
) -> None:
    source, _, owner, emergency, at, _, _ = ready
    original = prepare(ready)
    rows = source.journal.read_crossed_stop_events()
    status(source, at + timedelta(seconds=60), sc="H")
    assert prepare(ready) == original
    assert source.journal.read_crossed_stop_events() == rows
    conflict = prepare(ready, max_status_age=timedelta(seconds=2))
    assert conflict["conflicts"] and conflict["closes"] == original["closes"]
    rows = source.journal.read_crossed_stop_events()
    assert prepare(ready, max_status_age=timedelta(seconds=2)) == conflict
    assert source.journal.read_crossed_stop_events() == rows
    assert CrossedStopJournal(owner).view() == conflict


@pytest.mark.parametrize("ready", [{"group": True}], indirect=True)
def test_unknown_declared_group_is_not_promoted_to_source_ownership(ready: Any) -> None:
    _, _, owner, emergency, _, _, _ = ready
    before = owner.view()
    with pytest.raises(JournalError, match="exit ownership"):
        prepare(ready)
    assert owner.view() == before and emergency.view()["closes"] == {}


@pytest.mark.parametrize("sent", [False, True])
def test_post_snapshot_local_sell_commitment_blocks_new_preparation(
    ready: Any, sent: bool
) -> None:
    source, _, _, emergency, at, _, _ = ready
    source.intent("racing-sell", at, side="sell", order_type="market", qty=2, sent=sent)
    before = tuple(source.journal._db.iterdump())
    with pytest.raises(JournalError, match="census changed"):
        prepare(ready)
    assert tuple(source.journal._db.iterdump()) == before
    assert emergency.view()["closes"] == {}


def test_unsent_bound_exit_is_not_available_for_emergency_reallocation(
    ready: Any,
) -> None:
    source, risk, _, emergency, at, _, _ = ready
    source.intent(
        "unsent-stop", at, side="sell", order_type="stop", price="9.50", sent=False
    )
    risk.bind_exit("bind-existing-protection", at, "unsent-stop", "STOP")
    before = tuple(source.journal._db.iterdump())
    with pytest.raises(JournalError):
        prepare(ready)
    assert tuple(source.journal._db.iterdump()) == before
    assert emergency.view()["closes"] == {}


def test_preparation_retry_preserves_original_local_census_after_later_commitment(
    ready: Any,
) -> None:
    source, _, owner, _, at, _, _ = ready
    original = prepare(ready)
    saved = source.journal.read_order_intent(CID)
    assert saved is not None
    body = json.loads(saved["request_body"])
    body.update(client_order_id="later-other-symbol", symbol="MSFT")
    source.journal.reserve_order_intent(
        "later-other-symbol", saved | {"request_body": json.dumps(body)}
    )
    assert prepare(ready) == original
    assert CrossedStopJournal(owner).view() == original


def test_source_preparation_storage_failure_rolls_back_reservation(
    ready: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, _, emergency, _, _, _ = ready
    before = tuple(source.journal._db.iterdump())
    append = source.journal._append_locked

    def fail(*args: Any, **kwargs: Any) -> Any:
        if args and args[0].event_kind == "SOURCE_PREPARE":
            raise JournalError("synthetic source reservation storage failure")
        return append(*args, **kwargs)

    monkeypatch.setattr(source.journal, "_append_locked", fail)
    with pytest.raises(JournalError, match="synthetic source reservation"):
        prepare(ready)
    assert tuple(source.journal._db.iterdump()) == before
    assert emergency.view()["closes"] == {}


def test_source_reservation_interlocks_later_simple_intent_and_send_writers(
    ready: Any,
) -> None:
    source, _, _, _, at, _, _ = ready
    original = prepare(ready)
    before = tuple(source.journal._db.iterdump())
    with pytest.raises(JournalError, match="reservation blocks"):
        source.intent(
            "double-allocated-sell", at, side="sell", order_type="market", qty=2
        )
    with pytest.raises(JournalError, match="reservation blocks"):
        source.journal.mark_order_send_attempt(CID, at)
    saved = source.journal.read_order_intent(CID)
    assert saved is not None
    for symbol in ("aapl", " AAPL ", "00000000-0000-0000-0000-000000000001"):
        body = json.loads(saved["request_body"])
        body.update(client_order_id="alias-sell", symbol=symbol)
        with pytest.raises(JournalError):
            source.journal.reserve_order_intent(
                "alias-sell", saved | {"request_body": json.dumps(body)}
            )
    assert tuple(source.journal._db.iterdump()) == before
    assert prepare(ready) == original


def test_source_fact_age_and_future_timestamps_cannot_be_declared_fresh(
    ready: Any,
) -> None:
    source, _, _, emergency, at, _, _ = ready
    for stamp in (at - timedelta(seconds=2), at + timedelta(microseconds=1)):
        seq = status(source, at, t=stamp.isoformat().replace("+00:00", "Z"))
        before = tuple(source.journal._db.iterdump())
        with pytest.raises(JournalError, match="stale or future"):
            prepare(ready, status_sequence=seq)
        assert tuple(source.journal._db.iterdump()) == before
    seq = status(source, at)
    old_asset = asset(source, at - timedelta(seconds=2))
    with pytest.raises(JournalError, match="observation age"):
        prepare(ready, status_sequence=seq, asset_sequence=old_asset)
    assert emergency.view()["closes"] == {}
