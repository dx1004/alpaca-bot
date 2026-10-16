"""P06 durable research attempts; intentionally incompatible with broker dispatch."""

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from .entry_strategy import EntryDecision, EntryState, consume_attempt
from .journal import Journal, JournalError
from .research_spec import load_accepted_research_spec_file
from .scanner_contract import load_accepted_scanner_contract_file
from .strategy_features import COVERAGE, VERSION, utc
from .strategy_risk import DailyRiskState, SizeDecision

PURPOSE = "RESEARCH_STRATEGY_INTENT_NO_DISPATCH"


class ResearchAttempts:
    """Bind a single research attempt to account/version/symbol/session.

    The deterministic client ID survives process/state loss. A signal alone does
    not consume an attempt. These intents have no broker request body and cannot
    enter the engineering HTTP adapter. Live input/risk qualification and actual
    strategy dispatch remain separate, unavailable release gates.
    """

    def __init__(self, journal: Journal, repo_root: Path) -> None:
        scanner = load_accepted_scanner_contract_file(
            repo_root / "evidence/general-iex-daily30-screen-v1.json"
        )
        self.spec = load_accepted_research_spec_file(repo_root, scanner)
        journal.require_private_order_state()
        account = journal.read_paper_account_ref()
        if not isinstance(account, str) or not account:
            raise JournalError("P01/P06: verified private account binding required")
        self.journal = journal
        self.account_ref = account

    def _identity(self, state: EntryState) -> tuple[str, dict[str, object]]:
        self.journal.require_private_order_state()
        if (
            self.journal.read_paper_account_ref() != self.account_ref
            or state.version != VERSION
            or state.version != self.spec.version_id
            or type(state.session_day) is not date
            or not isinstance(state.symbol, str)
            or not state.symbol.strip()
            or state.symbol != state.symbol.strip()
        ):
            raise JournalError("P01/P06: attempt account/version/session mismatch")
        identity: dict[str, object] = {
            "paper_account_ref": self.account_ref,
            "purpose": PURPOSE,
            "strategy_version": VERSION,
            "research_sha256": self.spec.sha256,
            "symbol": state.symbol,
            "session_day": state.session_day.isoformat(),
            "coverage": COVERAGE,
            "order_authority": False,
        }
        key = [self.account_ref, VERSION, state.symbol, state.session_day.isoformat()]
        digest = hashlib.sha256(json.dumps(key, separators=(",", ":")).encode())
        return "rs-" + digest.hexdigest()[:45], identity

    def recover(self, state: EntryState) -> EntryState:
        """Recover consumption from the immutable intent, never an editable cache."""
        client_id, expected = self._identity(state)
        saved = self.journal.read_order_intent(client_id)
        if saved is None:
            if state.attempted or state.durable_intent_ref is not None:
                raise JournalError("P06: claimed attempt has no durable intent")
            return state
        if (
            not isinstance(saved, dict)
            or any(saved.get(key) != value for key, value in expected.items())
            or saved.get("order_authority") is not False
            or type(saved.get("quantity")) is not int
            or saved["quantity"] <= 0
            or not isinstance(saved.get("signal_id"), str)
            or not saved["signal_id"]
            or "request_body" in saved
        ):
            raise JournalError("P06: durable attempt identity is invalid")
        return replace(
            state, attempted=True, pending_signal=None, durable_intent_ref=client_id
        )

    def reserve(
        self,
        decision: EntryDecision,
        sizing: SizeDecision,
        *,
        reserved_at: datetime,
        daily_state: DailyRiskState,
    ) -> EntryState:
        """Reserve locally before consuming the pure reducer's allowance.

        An existing attempt always wins, including after a different new signal;
        this does not retry or rewrite an old order. This is a research trace,
        not evidence of accepted live data, account fees or execution capability.
        """
        state = self.recover(decision.state)
        if state.attempted:
            return state
        utc(reserved_at)
        signal = decision.signal
        trend = state.trend_snapshot
        if (
            decision.outcome != "RESEARCH_SIGNAL"
            or decision.order_authority is not False
            or decision.coverage != COVERAGE
            or decision.cancel_working_entry_required is not False
            or state.first_seen is not True
            or state.gap_passed is not True
            or state.armed is not True
            or state.invalidated is not False
            or state.veto_latched is not False
            or trend is None
            or state.last_decision_at is None
            or state.last_trade_at is None
            or reserved_at < state.last_decision_at
            or not timedelta(0)
            <= reserved_at - state.last_trade_at
            <= timedelta(milliseconds=1000)
            or not trend.session.open_at
            <= reserved_at
            < min(trend.session.open_at + timedelta(minutes=60), trend.session.close_at)
            or state.timeframe
            != (1 if reserved_at < trend.session.open_at + timedelta(minutes=15) else 5)
            or signal is None
            or signal != state.pending_signal
            or signal.order_authority is not False
            or signal.version != state.version
            or signal.symbol != state.symbol
            or signal.session_day != state.session_day
            or type(signal.limit_price) is not Fraction
            or type(signal.low_since_open) is not Fraction
            or sizing.version != state.version
            or sizing.account_ref != self.account_ref
            or sizing.session != state.session_day
            or daily_state.account_ref != self.account_ref
            or daily_state.session != state.session_day
            or daily_state.version != VERSION
            or daily_state.coverage != COVERAGE
            or daily_state.runtime_authorized is not False
            or type(daily_state.frozen_equity) is not Decimal
            or not daily_state.frozen_equity.is_finite()
            or daily_state.latched is not False
            or type(daily_state.losing_streak) is not int
            or not 0 <= daily_state.losing_streak < 3
            or type(sizing.frozen_equity) is not Fraction
            or sizing.frozen_equity <= 0
            or sizing.frozen_equity != Fraction(daily_state.frozen_equity)
            or sizing.coverage != COVERAGE
            or sizing.runtime_authorized is not False
            or sizing.reason != "FEASIBLE"
            or type(sizing.quantity) is not int
            or sizing.quantity <= 0
            or type(sizing.limit) is not Fraction
            or type(sizing.stop) is not Fraction
            or sizing.limit != signal.limit_price
            or sizing.stop != Fraction(signal.low_since_open * 100 // 1, 100)
            or not 0 < sizing.stop < sizing.limit
        ):
            raise JournalError("P06/P09/P10: current signal and matching size required")
        client_id, payload = self._identity(state)
        payload.update(
            signal_id=signal.signal_id,
            quantity=sizing.quantity,
            limit=[sizing.limit.numerator, sizing.limit.denominator],
            stop=[sizing.stop.numerator, sizing.stop.denominator],
            reserved_at=reserved_at.isoformat(),
            frozen_equity=[
                sizing.frozen_equity.numerator,
                sizing.frozen_equity.denominator,
            ],
            rule_ids=list(sizing.rule_ids) + ["P06"],
        )
        self.journal.reserve_order_intent(client_id, payload)
        return consume_attempt(state, signal.signal_id, client_id)
