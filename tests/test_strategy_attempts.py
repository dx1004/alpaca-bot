from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Never

import pytest

from alpaca_ross_bot.config import PaperConfig, PaperCredentials, SecretValue
from alpaca_ross_bot.entry_strategy import EntryDecision, EntryState, Signal
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.paper_order_contract import paper_account_ref
from alpaca_ross_bot.paper_order_http import PaperOrderHttp, PaperOrderHttpError
from alpaca_ross_bot.strategy_attempts import ResearchAttempts
from alpaca_ross_bot.strategy_features import Session, Trend
from alpaca_ross_bot.strategy_risk import DailyRiskState, SizeDecision

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 16)
ACCOUNT = b'{"id":"11111111-1111-4111-8111-111111111111"}'
OPEN = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
NOW = OPEN + timedelta(minutes=1)
DAILY = DailyRiskState(DAY, paper_account_ref(ACCOUNT), Decimal("500"))


def journal_at(tmp_path: Path) -> Journal:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    journal.record_paper_account_verification(
        paper_account_ref(ACCOUNT), ACCOUNT, datetime(2026, 10, 16, tzinfo=UTC)
    )
    return journal


def candidate() -> tuple[EntryDecision, SizeDecision]:
    signal = Signal(
        "signal-1", "TEST", DAY, "trade-1", Fraction(1001, 100), Fraction(9)
    )
    session = Session(
        DAY, OPEN, OPEN + timedelta(hours=6, minutes=30), OPEN, "calendar"
    )
    trend = Trend(
        "TEST",
        session,
        Fraction(10),
        Fraction(9),
        Fraction(8),
        Fraction(10),
        Fraction(9),
        ("research-fixture",),
    )
    state = EntryState(
        "TEST",
        DAY,
        first_seen=True,
        gap_passed=True,
        armed=True,
        last_trade_at=NOW,
        last_decision_at=NOW,
        trend_snapshot=trend,
        timeframe=1,
        pending_signal=signal,
    )
    decision = EntryDecision(state, "RESEARCH_SIGNAL", ("P06:NEW_CROSS",), signal)
    size = SizeDecision(
        paper_account_ref(ACCOUNT),
        DAY,
        Fraction(500),
        1,
        Fraction(1001, 100),
        Fraction(9),
        Fraction(5),
        "FEASIBLE",
    )
    return decision, size


def test_signal_does_not_consume_then_durable_reservation_recovers_after_restart(
    tmp_path: Path,
) -> None:
    journal = journal_at(tmp_path)
    decision, size = candidate()
    attempts = ResearchAttempts(journal, ROOT)
    assert attempts.recover(decision.state) == decision.state
    consumed = attempts.reserve(decision, size, reserved_at=NOW, daily_state=DAILY)
    assert consumed.attempted and consumed.pending_signal is None
    assert consumed.durable_intent_ref is not None
    saved = journal.read_order_intent(consumed.durable_intent_ref)
    assert isinstance(saved, dict)
    assert saved["order_authority"] is False
    assert "request_body" not in saved
    assert not journal.has_order_send_attempt(consumed.durable_intent_ref)
    journal.close()
    journal = journal_at(tmp_path)
    restarted = ResearchAttempts(journal, ROOT)
    assert restarted.recover(EntryState("TEST", DAY)).attempted
    assert (
        restarted.reserve(decision, size, reserved_at=NOW, daily_state=DAILY)
        == consumed
    )
    assert journal.read_order_intent_ids() == (consumed.durable_intent_ref,)
    journal.close()


def test_new_signal_cannot_bypass_same_session_attempt(tmp_path: Path) -> None:
    journal = journal_at(tmp_path)
    attempts = ResearchAttempts(journal, ROOT)
    decision, size = candidate()
    consumed = attempts.reserve(decision, size, reserved_at=NOW, daily_state=DAILY)
    assert decision.signal is not None
    signal = replace(decision.signal, signal_id="different-signal", trade_id="later")
    changed = replace(
        decision,
        state=replace(decision.state, pending_signal=signal),
        signal=signal,
    )
    assert (
        attempts.reserve(changed, size, reserved_at=NOW, daily_state=DAILY) == consumed
    )
    assert len(journal.read_order_intent_ids()) == 1
    journal.close()


def test_failed_durable_write_does_not_consume_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = journal_at(tmp_path)
    attempts = ResearchAttempts(journal, ROOT)
    decision, size = candidate()

    def fail(*_args: object) -> None:
        raise OSError("injected persistence failure")

    monkeypatch.setattr(journal, "reserve_order_intent", fail)
    with pytest.raises(OSError, match="persistence"):
        attempts.reserve(decision, size, reserved_at=NOW, daily_state=DAILY)
    assert not decision.state.attempted
    assert journal.read_order_intent_ids() == ()
    journal.close()


@pytest.mark.parametrize(
    "size",
    [
        replace(candidate()[1], quantity=0),
        replace(candidate()[1], limit=Fraction(11)),
        replace(candidate()[1], stop=Fraction(8)),
        replace(candidate()[1], version="ROSS_SMALL_ACCOUNT_CANDIDATE_V1"),
        replace(candidate()[1], runtime_authorized=True),
        replace(candidate()[1], account_ref="another-account"),
        replace(candidate()[1], session=DAY - timedelta(days=1)),
        replace(candidate()[1], frozen_equity=Fraction(1000)),
    ],
)
def test_invalid_or_cross_version_plan_never_reserves(
    tmp_path: Path, size: SizeDecision
) -> None:
    journal = journal_at(tmp_path)
    attempts = ResearchAttempts(journal, ROOT)
    with pytest.raises(JournalError):
        attempts.reserve(candidate()[0], size, reserved_at=NOW, daily_state=DAILY)
    assert journal.read_order_intent_ids() == ()
    journal.close()


def test_claimed_consumption_without_durable_source_is_rejected(tmp_path: Path) -> None:
    journal = journal_at(tmp_path)
    with pytest.raises(JournalError, match="no durable intent"):
        ResearchAttempts(journal, ROOT).recover(
            replace(candidate()[0].state, attempted=True)
        )
    journal.close()


@pytest.mark.parametrize("veto", ["veto_latched", "invalidated"])
def test_vetoed_signal_never_reserves(tmp_path: Path, veto: str) -> None:
    journal = journal_at(tmp_path)
    decision, size = candidate()
    blocked = (
        replace(decision.state, veto_latched=True)
        if veto == "veto_latched"
        else replace(decision.state, invalidated=True)
    )
    decision = replace(decision, state=blocked)
    with pytest.raises(JournalError):
        ResearchAttempts(journal, ROOT).reserve(
            decision, size, reserved_at=NOW, daily_state=DAILY
        )
    assert journal.read_order_intent_ids() == ()
    journal.close()


def test_expired_signal_cannot_be_reserved(tmp_path: Path) -> None:
    journal = journal_at(tmp_path)
    decision, size = candidate()
    with pytest.raises(JournalError):
        ResearchAttempts(journal, ROOT).reserve(
            decision, size, reserved_at=OPEN + timedelta(hours=1), daily_state=DAILY
        )
    assert journal.read_order_intent_ids() == ()
    journal.close()


def test_research_attempt_cannot_enter_broker_transport(tmp_path: Path) -> None:
    journal = journal_at(tmp_path)
    decision, size = candidate()
    state = ResearchAttempts(journal, ROOT).reserve(
        decision, size, reserved_at=NOW, daily_state=DAILY
    )
    assert state.durable_intent_ref is not None
    config = PaperConfig(
        trading_base_url="https://paper-api.alpaca.markets",
        market_data_base_url="https://data.alpaca.markets",
        api_key_id_env="TEST_KEY",
        api_secret_key_env="TEST_SECRET",
    )

    def no_http(*_args: object) -> Never:
        raise AssertionError("research attempt reached HTTP")

    http = PaperOrderHttp(
        config,
        PaperCredentials(SecretValue("unused-test"), SecretValue("unused-test")),
        paper_account_ref(ACCOUNT),
        timeout_seconds=2,
        max_request_bytes=4096,
        max_response_bytes=4096,
        exchange=no_http,
    )
    with pytest.raises(PaperOrderHttpError):
        http.post_reserved_order_once(journal, state.durable_intent_ref, NOW)
    assert not journal.has_order_send_attempt(state.durable_intent_ref)
    journal.close()


@pytest.mark.parametrize(
    "daily", [replace(DAILY, latched=True), replace(DAILY, losing_streak=3)]
)
def test_daily_veto_blocks_durable_research_reservation(
    tmp_path: Path, daily: DailyRiskState
) -> None:
    journal = journal_at(tmp_path)
    with pytest.raises(JournalError):
        ResearchAttempts(journal, ROOT).reserve(
            *candidate(), reserved_at=NOW, daily_state=daily
        )
    assert journal.read_order_intent_ids() == ()
    journal.close()
