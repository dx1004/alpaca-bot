import asyncio
import importlib.util
import json
import subprocess
import sys
from argparse import Namespace
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from types import ModuleType, SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.stream_protocol import ReceivedFrame
from test_account_risk_owner import account_sources

ROOT = Path(__file__).resolve().parents[1]


def collector() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "risk_input_collector", ROOT / "scripts/qualify_risk_inputs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_requires_explicit_connect_before_credentials_or_network() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/qualify_risk_inputs.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "status": "NOT_RUN",
        "runtime_authorized": False,
    }


@pytest.mark.parametrize("owner_mode", ["off", "source_baseline"])
def test_capture_optional_statuses_retains_raw_refs_without_price_or_risk_clearance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner_mode: str
) -> None:
    test_capture_private_account_and_stream_with_redacted_summary(
        tmp_path,
        monkeypatch,
        short_first_page=False,
        freeze_requested=False,
        owner_mode=owner_mode,
        include_statuses=True,
    )


def test_get_record_retains_raw_body_and_only_uses_bounded_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = collector()
    calls = []

    def get(url: str, headers: Mapping[str, str], timeout: float, cap: int) -> bytes:
        calls.append(url)
        assert timeout == 5 and cap == 2_097_152
        return b'{"private":"synthetic"}'

    monkeypatch.setattr(module, "bounded_alpaca_get", get)
    assert module.account_transport_origin() == "INJECTED_EXCHANGE"
    result = module.get_record("https://paper-api.alpaca.markets/v2/account", {})
    assert result["method"] == "GET" and result["status"] == 200
    assert result["body"] == '{"private":"synthetic"}'
    assert calls == ["https://paper-api.alpaca.markets/v2/account"]


def test_transport_origin_inspection_never_calls_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = collector()
    assert module.account_transport_origin() == "BUILTIN_PAPER_HTTPS"
    monkeypatch.setattr(module, "get_record", lambda *_args: None)
    assert module.account_transport_origin() == "INJECTED_EXCHANGE"


@pytest.mark.parametrize("short_first_page", [False, True])
@pytest.mark.parametrize("freeze_requested", [False, True])
@pytest.mark.parametrize(
    "owner_mode", ["off", "missing_baseline", "source_baseline", "prior_session"]
)
def test_capture_private_account_and_stream_with_redacted_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    short_first_page: bool,
    freeze_requested: bool,
    owner_mode: str,
    include_statuses: bool = False,
    origin_drift: bool = False,
) -> None:
    module = collector()
    if origin_drift:
        origins = iter(("INJECTED_EXCHANGE", "BUILTIN_PAPER_HTTPS"))
        monkeypatch.setattr(module, "account_transport_origin", lambda: next(origins))
    private = tmp_path / "private"
    observed_at = datetime(
        2026, 10, 19 if owner_mode == "prior_session" else 16, 13, 35, tzinfo=UTC
    )

    fixed_clock(module, monkeypatch, observed_at)
    account = {
        "id": "11111111-1111-4111-8111-111111111111",
        "currency": "USD",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
        "multiplier": "1",
        "equity": "500",
        "last_equity": "500",
        "cash": "500",
        "buying_power": "500",
        "non_marginable_buying_power": "500",
        "accrued_fees": "0",
        "pending_transfer_in": "0",
        "pending_transfer_out": "0",
    }

    def credentials(_path: Path, config: PaperConfig) -> dict[str, str]:
        return {
            config.api_key_id_env: "fake-test-key",
            config.api_secret_key_env: "fake-test-secret",
        }

    activity_paths: list[str] = []

    def get_record(url: str, _headers: dict[str, str]) -> dict[str, object]:
        at = observed_at.isoformat()
        path = url.removeprefix("https://paper-api.alpaca.markets")
        if path.startswith("/v2/orders?"):
            assert path == "/v2/orders?status=open&limit=500&nested=false"
        body: object = []
        if path == "/v2/account":
            body = account
        elif path.startswith("/v2/calendar?"):
            body = [
                {
                    "date": observed_at.astimezone(ZoneInfo("America/New_York"))
                    .date()
                    .isoformat(),
                    "open": "09:30",
                    "close": "16:00",
                }
            ]
        elif path == "/v2/account/configurations":
            body = {}
        elif path.startswith("/v2/account/activities?"):
            activity_paths.append(path)
            if short_first_page and len(activity_paths) == 1:
                body = [
                    {
                        "id": "synthetic-page-last",
                        "activity_type": "CSD",
                        "net_amount": "500",
                        "date": at[:10],
                    }
                ]
            if len(activity_paths) == 2:
                assert "page_token=synthetic-page-last" in path
        return {
            "method": "GET",
            "path": path,
            "status": 200,
            "started_at": at,
            "completed_at": at,
            "body": json.dumps(body),
        }

    def metadata(
        _url: str, _headers: dict[str, str], _timeout: int, _cap: int
    ) -> bytes:
        return b'{"@":"Regular Sale","R":"Regular"}'

    class Stream:
        subscribed = False
        gap_unresolved = True

        def __init__(self, journal: Journal) -> None:
            self.journal = journal
            self.used = False

        async def open(self) -> None:
            return None

        async def subscribe(self, channels: object) -> None:
            expected = {
                "trades": ("SPY",),
                "quotes": ("SPY",),
                "bars": ("SPY",),
            }
            if include_statuses:
                expected["statuses"] = ("SPY",)
            assert channels == expected
            self.subscribed = True

        async def receive(self) -> ReceivedFrame:
            if self.used:
                raise TimeoutError
            self.used = True
            at = observed_at
            records: list[dict[str, object]] = [
                {
                    "T": "t",
                    "S": "SPY",
                    "x": "V",
                    "p": 10,
                    "s": 1,
                    "i": 1,
                    "c": ["@"],
                    "z": "B",
                    "t": at.isoformat().replace("+00:00", "Z"),
                }
            ]
            if include_statuses:
                records += [
                    {"T": "s", "S": "SPY", "sc": "T", "t": at.isoformat()},
                    {"T": "s", "S": "SPY", "sc": "H", "t": at.isoformat()},
                    {"T": "b", "S": "SPY", "t": at.isoformat()},
                ]
            body = json.dumps(records).encode()
            event = self.journal.append_next(
                source_id="alpaca_market_data",
                feed="iex",
                event_kind="RAW_FRAME",
                payload=body,
                system_first_seen_at=at,
                processed_at=observed_at,
            )
            return ReceivedFrame(body, at, journal_sequence=event.sequence)

        async def close(self) -> None:
            return None

    def make_stream(*_args: object, **kwargs: object) -> Stream:
        journal = kwargs["journal"]
        assert isinstance(journal, Journal)
        return Stream(journal)

    monkeypatch.setattr(module, "load_paper_credentials", credentials)
    monkeypatch.setattr(module, "get_record", get_record)
    monkeypatch.setattr(module, "bounded_alpaca_get", metadata)
    monkeypatch.setattr(module, "make_observable_stream", make_stream)
    freeze_calls: list[object] = []

    def refuse_baseline(*args: object, **kwargs: object) -> None:
        freeze_calls.append(kwargs)
        raise ValueError("baseline qualification failed")

    monkeypatch.setattr(module, "freeze_baseline", refuse_baseline)
    args = Namespace(
        config=ROOT / "config/paper.example.toml",
        credentials_file=tmp_path / "nonexistent",
        runtime_root=private,
        stream_seconds=1,
        freeze_baseline=freeze_requested,
        observe_risk_owner=owner_mode != "off",
        require_bound_account=owner_mode != "off",
        include_statuses=include_statuses,
    )
    context = (
        account_sources(
            tmp_path,
            monkeypatch,
            freeze=owner_mode in {"source_baseline", "prior_session"},
        )
        if owner_mode != "off"
        else nullcontext()
    )
    with context as seeded:
        if owner_mode == "prior_session":
            assert seeded is not None
            old_source, old_owner = seeded
            old_at = datetime(2026, 10, 16, 13, 35, tzinfo=UTC)
            old_owner.observe("prior-session", old_source.selection(old_at), old_at)
            old_state = old_owner.view()
            assert old_state is not None and old_state["e0"] == "500"
        report = asyncio.run(module.capture(args))
    assert report["broker_writes_performed"] is False
    assert report["account"]["capture_schema"] == "PAPER_RISK_INPUT_CAPTURE_V2"
    assert report["account"]["transport_origin"] == "INJECTED_EXCHANGE"
    assert report["account"]["starting_equity_matches_500"]
    assert report["account"]["pre_session_baseline_frozen"] is False
    assert report["market"]["assessed_records"] == 1
    assert report["market"]["assessment_scope"] == "TRADE_QUOTE_ONLY"
    assert report["market"]["raw_records"] == (4 if include_statuses else 1)
    assert report["market"]["non_price_record_counts"] == (
        {"s": 2, "b": 1} if include_statuses else {}
    )
    assert report["market"]["status_subscription_acknowledged"] is include_statuses
    assert len(report["market"]["raw_status_record_refs"]) == (
        2 if include_statuses else 0
    )
    assert [row[2] for row in report["market"]["raw_status_record_refs"]] == (
        [1, 2] if include_statuses else []
    )
    assert report["market"]["current_trading_status_verified"] is False
    assert report["market"]["continuous_status_coverage_verified"] is False
    assert report["market"]["runtime_eligible"] is False
    assert report["private_integrity_verified"] is True
    assert report["risk_inputs"]["reported_inputs_qualified"] is True
    assert report["risk_inputs"]["bid_valuation_qualified"] is True
    assert report["risk_inputs"]["valued_position_count"] == 0
    assert report["risk_inputs"]["runtime_authorized"] is False
    assert report["account"]["activity_pages"] == (2 if short_first_page else 1)
    assert len(freeze_calls) == int(freeze_requested)
    assert report["ledger"]["baseline_status"] == (
        "BASELINE_QUALIFICATION_FAILED" if freeze_requested else "NOT_REQUESTED"
    )
    public = json.dumps(report)
    assert str(account["id"]) not in public
    assert "fake-test-secret" not in public and "fake-test-key" not in public
    assert "buying_power" not in public
    assert report["zero_order_intents_verified"] is True
    if owner_mode == "off":
        assert "source_risk_owner" not in report
        assert "capital_stage" not in report
        return
    source = report["source_risk_owner"]
    if owner_mode == "source_baseline" and not short_first_page:
        assert source["baseline_present"]
    elif owner_mode in {"missing_baseline", "prior_session"}:
        assert source["baseline_present"] is False
    assert source["source_snapshot_qualified"] == (
        owner_mode == "source_baseline" and not short_first_page
    )
    assert source["reservation_performed"] is False
    assert source["runtime_authorized"] is False and source["rebuild_verified"]
    assert "net" not in source and "e0" not in source
    capital = report["capital_stage"]
    assert capital["runtime_authorized"] is False and capital["rebuild_verified"]
    assert capital["higher_stage"] == "BLOCKED_PENDING_SEPARATE_ACCEPTANCE"
    assert capital["risk_gate"] == "BLOCK_NEW_EXPOSURE"
    assert "reported_equity" not in capital and "reported_multiplier" not in capital
    journal = Journal.open_private(private / "journal.sqlite3", private)
    try:
        captured = journal.read_event(report["account"]["capture_sequence"])
        assert captured is not None
        capture_payload = json.loads(captured[1])
        assert capture_payload["schema"] == "PAPER_RISK_INPUT_CAPTURE_V2"
        assert capture_payload["transport_origin"] == "INJECTED_EXCHANGE"
        events = journal.read_account_risk_events()
        assert len(events) == (2 if owner_mode == "prior_session" else 1)
        assert events[-1][0].event_kind == "OBSERVE"
        raw = json.loads(events[-1][1])
        assert (
            raw["selection"]["capture_sequence"]
            == report["account"]["capture_sequence"]
        )
        assert (
            raw["selection"]["calendar_sequence"]
            == report["account"]["calendar_sequence"]
        )
        assert journal.read_order_intent_ids() == ()
        projection = journal.read_state(
            "account_risk:" + raw["account_ref"] + ":" + raw["version"]
        )
        assert isinstance(projection, dict) and projection["held"] is None
        assert projection["completed"] == {} and projection["used_plan_ids"] == []
        if owner_mode == "prior_session":
            assert projection["e0"] == "500"
            assert projection["session"] == "2026-10-16"
            assert projection["last_snapshot"].get("e0") is None
            assert projection["last_snapshot"]["session"] == "2026-10-19"
        stages = journal.read_capital_stage_events()
        assert len(stages) == 1 and stages[0][0].event_kind == "STAGE_OBSERVATION"
        stage = json.loads(stages[0][1])
        assert (
            stage["selection"]["account_sequence"]
            == report["account"]["capture_sequence"]
        )
        assert (
            stage["selection"]["calendar_sequence"]
            == report["account"]["calendar_sequence"]
        )
    finally:
        journal.close()


def test_origin_drift_cannot_commit_account_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="transport changed"):
        test_capture_private_account_and_stream_with_redacted_summary(
            tmp_path,
            monkeypatch,
            short_first_page=False,
            freeze_requested=False,
            owner_mode="off",
            origin_drift=True,
        )
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    try:
        assert (
            journal._db.execute(
                "SELECT COUNT(*) FROM events WHERE source_id='ALPACA_PAPER_RISK_HTTP' "
                "AND event_kind='ACCOUNT_INPUT_CAPTURE'"
            ).fetchone()[0]
            == 0
        )
        assert journal.read_order_intent_ids() == ()
    finally:
        journal.close()


def fixed_clock(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, at: datetime
) -> None:
    def now(tz: tzinfo | None = None) -> datetime:
        return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(
        module,
        "datetime",
        SimpleNamespace(now=now, fromisoformat=datetime.fromisoformat),
    )


def arguments(tmp_path: Path, **changes: object) -> Namespace:
    values = {
        "config": ROOT / "config/paper.example.toml",
        "credentials_file": tmp_path / "nonexistent-credentials",
        "runtime_root": tmp_path / "private",
        "stream_seconds": 1,
        "freeze_baseline": False,
        "observe_risk_owner": True,
        "require_bound_account": True,
        "session_date": None,
        "collection_window": None,
    }
    return Namespace(**(values | changes))


def fake_credentials(_path: Path, config: PaperConfig) -> dict[str, str]:
    return {
        config.api_key_id_env: "synthetic-key",
        config.api_secret_key_env: "synthetic-secret",
    }


@pytest.mark.parametrize(
    ("window", "hour", "minute", "valid"),
    [
        ("preopen", 13, 14, False),
        ("preopen", 13, 15, True),
        ("preopen", 13, 24, True),
        ("preopen", 13, 25, False),
        ("open", 13, 29, False),
        ("open", 13, 30, True),
        ("open", 13, 39, True),
        ("open", 13, 40, False),
    ],
)
def test_declared_new_york_window_start_inclusive_end_exclusive(
    tmp_path: Path, window: str, hour: int, minute: int, valid: bool
) -> None:
    module = collector()
    args = arguments(
        tmp_path, session_date=date(2026, 10, 19), collection_window=window
    )
    at = datetime(2026, 10, 19, hour, minute, tzinfo=UTC)
    if valid:
        module.check_collection_window(args, at)
    else:
        with pytest.raises(ValueError, match="outside"):
            module.check_collection_window(args, at)


@pytest.mark.parametrize(
    ("day", "window", "at", "freeze", "reason"),
    [
        (
            date(2026, 10, 20),
            "open",
            datetime(2026, 10, 19, 13, 35, tzinfo=UTC),
            False,
            "outside",
        ),
        (date(2026, 10, 19), "open", datetime(2026, 10, 19, 13, 35), False, "UTC"),
        (
            date(2026, 10, 19),
            "open",
            datetime(2026, 10, 19, 13, 35, tzinfo=UTC),
            True,
            "cannot create",
        ),
        (
            None,
            "open",
            datetime(2026, 10, 19, 13, 35, tzinfo=UTC),
            False,
            "both session",
        ),
        (
            date(2026, 10, 19),
            None,
            datetime(2026, 10, 19, 13, 35, tzinfo=UTC),
            False,
            "both session",
        ),
    ],
)
def test_window_rejects_wrong_date_naive_clock_open_freeze_and_unpaired_flags(
    tmp_path: Path,
    day: date | None,
    window: str | None,
    at: datetime,
    freeze: bool,
    reason: str,
) -> None:
    module = collector()
    args = arguments(
        tmp_path, session_date=day, collection_window=window, freeze_baseline=freeze
    )
    with pytest.raises(ValueError, match=reason):
        module.check_collection_window(args, at)


@pytest.mark.parametrize("cause", ["early", "late", "date", "open_freeze"])
def test_invalid_collection_window_never_reads_credentials_or_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    module = collector()
    at = datetime(2026, 10, 19, 13, 35, tzinfo=UTC)
    if cause == "early":
        at -= timedelta(minutes=6)
    elif cause == "late":
        at += timedelta(minutes=5)
    elif cause == "date":
        at += timedelta(days=1)
    fixed_clock(module, monkeypatch, at)
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        calls.append("unexpected credential or network access")
        raise AssertionError(calls[-1])

    monkeypatch.setattr(module, "load_paper_credentials", forbidden)
    monkeypatch.setattr(module, "get_record", forbidden)
    monkeypatch.setattr(module, "make_observable_stream", forbidden)
    args = arguments(
        tmp_path,
        config=tmp_path / "does-not-exist.toml",
        session_date=date(2026, 10, 19),
        collection_window="open",
        freeze_baseline=cause == "open_freeze",
    )
    with pytest.raises(ValueError, match="outside|cannot create"):
        asyncio.run(module.capture(args))
    assert calls == [] and not args.runtime_root.exists()


def test_missing_required_journal_refuses_before_credentials_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = collector()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("credentials or network must not be used")

    monkeypatch.setattr(module, "load_paper_credentials", forbidden)
    monkeypatch.setattr(module, "get_record", forbidden)
    with pytest.raises(ValueError, match="existing bound"):
        asyncio.run(module.capture(arguments(tmp_path)))
    assert not (tmp_path / "private").exists()


def test_empty_existing_journal_refuses_before_any_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = collector()
    root = tmp_path / "private"
    Journal.open_private(root / "journal.sqlite3", root).close()
    monkeypatch.setattr(module, "load_paper_credentials", fake_credentials)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network must not be used for an unbound journal")

    monkeypatch.setattr(module, "get_record", forbidden)
    with pytest.raises(ValueError, match="identity must already be bound"):
        asyncio.run(module.capture(arguments(tmp_path)))
    journal = Journal.open_private(root / "journal.sqlite3", root)
    try:
        assert journal.read_paper_account_ref() is None
        assert journal.read_account_risk_events() == ()
    finally:
        journal.close()


def test_observed_account_identity_mismatch_preserves_original_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = collector()
    monkeypatch.setattr(module, "load_paper_credentials", fake_credentials)
    calls: list[str] = []

    def foreign_account(url: str, _headers: dict[str, str]) -> dict[str, object]:
        calls.append(url)
        assert url == "https://paper-api.alpaca.markets/v2/account"
        return {
            "body": json.dumps(
                {
                    "id": "99999999-9999-4999-8999-999999999999",
                    "status": "ACTIVE",
                    "currency": "USD",
                }
            )
        }

    monkeypatch.setattr(module, "get_record", foreign_account)
    with account_sources(tmp_path, monkeypatch) as (source, _owner):
        original_ref = source.journal.read_paper_account_ref()
        original_baseline = source.journal.read_risk_baseline_events()
        original_receipts = source.journal.read_risk_baseline_receipt_events()
        original_lifecycle = source.journal.read_order_events()
        with pytest.raises(JournalError, match="account|bound"):
            asyncio.run(module.capture(arguments(tmp_path)))
        assert source.journal.read_paper_account_ref() == original_ref
        assert source.journal.read_risk_baseline_events() == original_baseline
        assert source.journal.read_risk_baseline_receipt_events() == original_receipts
        assert source.journal.read_order_events() == original_lifecycle
        assert source.journal.read_account_risk_events() == ()
        assert source.journal.read_order_intent_ids() == ()
    assert len(calls) == 1


def test_declared_window_requires_fresh_official_nine_thirty_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = collector()
    at = datetime(2026, 10, 19, 13, 35, tzinfo=UTC)
    fixed_clock(module, monkeypatch, at)
    monkeypatch.setattr(module, "load_paper_credentials", fake_credentials)
    calls: list[str] = []

    def get_record(url: str, _headers: dict[str, str]) -> dict[str, object]:
        calls.append(url)
        path = url.removeprefix("https://paper-api.alpaca.markets")
        if path == "/v2/account":
            body: object = {
                "id": "11111111-1111-4111-8111-111111111111",
                "status": "ACTIVE",
                "currency": "USD",
            }
        else:
            assert path.startswith("/v2/calendar?")
            body = [{"date": "2026-10-19", "open": "10:00", "close": "16:00"}]
        return {"body": json.dumps(body)}

    monkeypatch.setattr(module, "get_record", get_record)
    with account_sources(tmp_path, monkeypatch) as (source, _owner):
        baseline = source.journal.read_risk_baseline_events()
        args = arguments(tmp_path, session_date=at.date(), collection_window="open")
        with pytest.raises(ValueError, match="official session open differs"):
            asyncio.run(module.capture(args))
        assert source.journal.read_risk_baseline_events() == baseline
        assert source.journal.read_account_risk_events() == ()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "expires_at",
    [
        "before_freeze",
        "before_metadata",
        "during_metadata",
        "before_assessment",
        "before_owner",
        "before_capital",
        "before_success",
    ],
)
def test_window_expiry_during_capture_blocks_later_actions_and_public_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expires_at: str
) -> None:
    module = collector()
    preopen = expires_at == "before_freeze"
    within = datetime(2026, 10, 19, 13, 24 if preopen else 39, 59, tzinfo=UTC)
    expired = within + timedelta(seconds=1)
    current = [within]

    def now(tz: tzinfo | None = None) -> datetime:
        return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(
        module,
        "datetime",
        SimpleNamespace(now=now, fromisoformat=datetime.fromisoformat),
    )
    monkeypatch.setattr(module, "load_paper_credentials", fake_credentials)
    calls: list[str] = []

    def get_record(url: str, _headers: dict[str, str]) -> dict[str, object]:
        path = url.removeprefix("https://paper-api.alpaca.markets")
        calls.append(path)
        body: object = []
        if path == "/v2/account":
            body = {
                "id": "11111111-1111-4111-8111-111111111111",
                "status": "ACTIVE",
                "currency": "USD",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
                "cash": "500",
                "equity": "500",
                "buying_power": "500",
                "non_marginable_buying_power": "500",
                "multiplier": "1",
            }
        elif path.startswith("/v2/calendar?"):
            body = [{"date": "2026-10-19", "open": "09:30", "close": "16:00"}]
        elif path == "/v2/account/configurations":
            body = {}
        result = {
            "method": "GET",
            "path": path,
            "status": 200,
            "started_at": within.isoformat(),
            "completed_at": within.isoformat(),
            "body": json.dumps(body),
        }
        if path.startswith("/v2/account/activities?") and expires_at in {
            "before_freeze",
            "before_metadata",
        }:
            current[0] = expired
        return result

    def metadata(*_args: object) -> bytes:
        assert current[0] == within
        calls.append("metadata")
        if expires_at == "during_metadata":
            current[0] = expired
        return b'{"R":"Regular Market Maker Open"}'

    def no_freeze(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("baseline freeze must not run after window expiry")

    class Stream:
        subscribed = False
        gap_unresolved = True

        async def open(self) -> None:
            calls.append("stream-open")

        async def subscribe(self, _channels: object) -> None:
            self.subscribed = True

        async def receive(self) -> ReceivedFrame:
            if expires_at == "before_assessment":
                current[0] = expired
            raise TimeoutError

        async def close(self) -> None:
            if expires_at == "before_success":
                current[0] = expired

    monkeypatch.setattr(module, "get_record", get_record)
    monkeypatch.setattr(module, "bounded_alpaca_get", metadata)
    monkeypatch.setattr(module, "freeze_baseline", no_freeze)
    monkeypatch.setattr(
        module, "make_observable_stream", lambda *_args, **_kwargs: Stream()
    )
    original_append = Journal._append_locked

    def append_and_advance_clock(
        self: Journal,
        event: EventEnvelope,
        payload: bytes,
        state_updates: Mapping[str, object] | None,
    ) -> None:
        original_append(self, event, payload, state_updates)
        if (
            expires_at == "before_owner" and event.source_id == "ALPACA_RISK_INPUTS"
        ) or (
            expires_at == "before_capital" and event.source_id == "ALPACA_ACCOUNT_RISK"
        ):
            current[0] = expired

    monkeypatch.setattr(Journal, "_append_locked", append_and_advance_clock)
    with account_sources(tmp_path, monkeypatch) as (source, _owner):
        original = source.journal.read_risk_baseline_events()
        args = arguments(
            tmp_path,
            session_date=date(2026, 10, 19),
            collection_window="preopen" if preopen else "open",
            freeze_baseline=preopen,
        )
        with pytest.raises(ValueError, match="outside the declared"):
            asyncio.run(module.capture(args))
        assert source.journal.read_risk_baseline_events() == original
        events = source.journal.read_account_risk_events()
        assert len(events) == int(expires_at in {"before_capital", "before_success"})
        assert all(event.available_at < expired for event, _ in events)
        stages = source.journal.read_capital_stage_events()
        assert len(stages) == int(expires_at == "before_success")
        assert all(event.available_at < expired for event, _ in stages)
        assert source.journal.read_order_intent_ids() == ()
    if expires_at in {"before_freeze", "before_metadata"}:
        assert "metadata" not in calls and "stream-open" not in calls
    elif expires_at == "during_metadata":
        assert calls.count("metadata") == 1 and "stream-open" not in calls
    else:
        assert calls.count("metadata") == 6 and "stream-open" in calls
