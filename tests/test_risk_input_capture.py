import asyncio
import importlib.util
import json
import subprocess
import sys
from argparse import Namespace
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

from alpaca_ross_bot.config import PaperConfig
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.stream_protocol import ReceivedFrame

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
    result = module.get_record("https://paper-api.alpaca.markets/v2/account", {})
    assert result["method"] == "GET" and result["status"] == 200
    assert result["body"] == '{"private":"synthetic"}'
    assert calls == ["https://paper-api.alpaca.markets/v2/account"]


@pytest.mark.parametrize("short_first_page", [False, True])
@pytest.mark.parametrize("freeze_requested", [False, True])
def test_capture_private_account_and_stream_with_redacted_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    short_first_page: bool,
    freeze_requested: bool,
) -> None:
    module = collector()
    private = tmp_path / "private"
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
        at = datetime.now(UTC).isoformat()
        path = url.removeprefix("https://paper-api.alpaca.markets")
        if path.startswith("/v2/orders?"):
            assert path == "/v2/orders?status=open&limit=500&nested=false"
        body: object = []
        if path == "/v2/account":
            body = account
        elif path.startswith("/v2/calendar?"):
            body = [
                {
                    "date": datetime.now(ZoneInfo("America/New_York"))
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
            assert channels == {
                "trades": ("SPY",),
                "quotes": ("SPY",),
                "bars": ("SPY",),
            }
            self.subscribed = True

        async def receive(self) -> ReceivedFrame:
            if self.used:
                raise TimeoutError
            self.used = True
            at = datetime.now(UTC)
            body = json.dumps(
                [
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
            ).encode()
            event = self.journal.append_next(
                source_id="alpaca_market_data",
                feed="iex",
                event_kind="RAW_FRAME",
                payload=body,
                system_first_seen_at=at,
                processed_at=datetime.now(UTC),
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
    )
    report = asyncio.run(module.capture(args))
    assert report["broker_writes_performed"] is False
    assert report["account"]["starting_equity_matches_500"]
    assert report["account"]["pre_session_baseline_frozen"] is False
    assert report["market"]["assessed_records"] == 1
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
