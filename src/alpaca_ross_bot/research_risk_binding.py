"""Declared reducer-to-risk binding; never source admission or dispatch."""

from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any

from .entry_strategy import EntryDecision, EntryState, Signal
from .journal import JournalError
from .observation_admission import (
    CANDIDATE_VERSION,
    CONTRACT_SHA256,
    ObservationContract,
)
from .observation_admission import (
    VERSION as DATA_CONTRACT_VERSION,
)
from .oco_research import (
    CANDIDATE as OCO_CANDIDATE,
)
from .oco_research import (
    MANIFEST_SHA256 as OCO_SHA256,
)
from .oco_research import (
    VERSION as OCO_VERSION,
)
from .oco_research import OcoResearchContract, load_oco_research_contract
from .research_spec import POLICY_SHA256, RESEARCH_SHA256
from .scanner_contract import ACCEPTED_SHA256
from .strategy_features import COVERAGE, Session, Trend, research_candidate_version
from .strategy_risk import VERSION

RESEARCH_BINDING = "RESEARCH_DECLARED_SIGNAL_NOT_SOURCE_ADMISSION"
OBSERVATION_IDENTITY = {
    "strategy_version": CANDIDATE_VERSION,
    "risk_profile_version": VERSION,
    "parent_research_version": VERSION,
    "data_contract_version": DATA_CONTRACT_VERSION,
    "data_contract_sha256": CONTRACT_SHA256,
    "volume_source_age": "UNKNOWN",
    "volume_source_updated_at": "UNKNOWN",
    "source_admission_qualified": False,
}
OCO_EXECUTION_IDENTITY = {
    "candidate_version": OCO_CANDIDATE,
    "entry_candidate_version": CANDIDATE_VERSION,
    "execution_contract_version": OCO_VERSION,
    "execution_contract_sha256": OCO_SHA256,
    "classification": "DECLARED_PARENT_ENTRY_SHARED_RISK_NOT_SOURCE_ACCEPTANCE",
    "research_semantics_accepted": True,
    "entry_risk_version_binding_accepted": False,
    "source_admission_qualified": False,
    "strategy_plan_qualified": False,
    "runtime_authorized": False,
    "order_authority": False,
}


def _check_execution_binding(declared: dict[str, Any]) -> None:
    if "execution_research_binding" not in declared:
        return
    binding = declared["execution_research_binding"]
    if (
        declared.get("version") != CANDIDATE_VERSION
        or type(binding) is not dict
        or set(binding) != set(OCO_EXECUTION_IDENTITY)
        or any(
            type(binding[key]) is not type(value) or binding[key] != value
            for key, value in OCO_EXECUTION_IDENTITY.items()
        )
    ):
        raise JournalError("OCO research execution binding differs")
    # Rebuild must check the same pinned artifacts as initial opt-in.
    load_oco_research_contract()


def _declared_version(declared: dict[str, Any]) -> str:
    _check_execution_binding(declared)
    version = declared.get("version")
    if version == VERSION and not set(OBSERVATION_IDENTITY) & set(declared):
        return VERSION
    if (
        version == CANDIDATE_VERSION
        and all(
            declared.get(key) == value for key, value in OBSERVATION_IDENTITY.items()
        )
        and declared.get("source_admission_qualified") is False
    ):
        return CANDIDATE_VERSION
    raise JournalError("mixed or unsupported declared candidate/data/risk identity")


def _utc(at: datetime) -> None:
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise JournalError("UTC declared research clock required")


def _declared_research_plan(
    client_order_id: str, declared: dict[str, Any], at: datetime
) -> dict[str, str]:
    """Check persisted declaration consistency; never qualify its source inputs."""
    version = _declared_version(declared)
    if (
        declared.get("classification") != RESEARCH_BINDING
        or declared.get("research_sha256") != RESEARCH_SHA256
        or declared.get("policy_sha256") != POLICY_SHA256
        or declared.get("scanner_sha256") != ACCEPTED_SHA256
        or declared.get("coverage") != COVERAGE
        or declared.get("outcome") != "RESEARCH_SIGNAL"
        or declared.get("order_authority") is not False
        or declared.get("signal_order_authority") is not False
        or declared.get("cancel_working_entry_required") is not False
        or any(
            declared.get(key) is not True
            for key in ("first_seen", "gap_passed", "armed")
        )
        or any(
            declared.get(key) is not False
            for key in ("attempted", "invalidated", "veto_latched")
        )
        or declared.get("durable_intent_ref") is not None
        or declared.get("pending_signal_id") != declared.get("signal_id")
        or declared.get("trend_version") != version
        or declared.get("trend_symbol") != declared.get("symbol")
    ):
        raise JournalError("declared research signal/state identity differs")
    for key in ("symbol", "signal_id", "trade_id"):
        value = declared.get(key)
        if type(value) is not str or not value.strip() or value != value.strip():
            raise JournalError("declared research signal identity is missing")
    try:
        session = declared["session"]
        if not isinstance(session, dict):
            raise ValueError("session is missing")
        calendar = Session(
            date.fromisoformat(session["day"]),
            datetime.fromisoformat(session["open_at"]),
            datetime.fromisoformat(session["close_at"]),
            datetime.fromisoformat(session["available_at"]),
            session["calendar_ref"],
        )
        traded, decided = (
            datetime.fromisoformat(declared[key])
            for key in ("last_trade_at", "last_decision_at")
        )
        _utc(traded)
        _utc(decided)
        limit, low = (Fraction(declared[key]) for key in ("limit", "low_since_open"))
        if any(
            str(value) != declared[key]
            for value, key in ((limit, "limit"), (low, "low_since_open"))
        ):
            raise ValueError("noncanonical exact signal price")
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise JournalError(
            "declared research signal clock or price is invalid"
        ) from exc
    _utc(at)
    if (
        declared.get("session_day") != calendar.day.isoformat()
        or declared["signal_id"]
        != f"{version}:{calendar.day}:{declared['symbol']}:{declared['trade_id']}"
        or not traded <= decided <= at
        or not timedelta(0) <= at - traded <= timedelta(seconds=1)
        or not calendar.open_at
        <= at
        < min(calendar.open_at + timedelta(minutes=60), calendar.close_at)
        or calendar.available_at > decided
        or type(declared.get("timeframe")) is not int
        or declared["timeframe"]
        != (1 if at < calendar.open_at + timedelta(minutes=15) else 5)
        or limit <= 0
        or low <= 0
        or (limit * 100).denominator != 1
    ):
        raise JournalError("declared research signal is not current in its session")
    stop = Fraction(low * 100 // 1, 100)

    def cents(value: Fraction) -> str:
        raw = int(value * 100)
        return f"{raw // 100}.{raw % 100:02d}"

    if not 0 < stop < limit:
        raise JournalError("declared research stop topology is invalid")
    return {
        "client_order_id": client_order_id,
        "symbol": declared["symbol"],
        "limit": cents(limit),
        "stop": cents(stop),
    }


def _research_declaration(
    decision: EntryDecision,
    *,
    observation_contract: ObservationContract | None = None,
    oco_contract: OcoResearchContract | None = None,
) -> dict[str, Any]:
    version = research_candidate_version(observation_contract)
    if type(decision) is not EntryDecision or type(decision.state) is not EntryState:
        raise JournalError("current declared research decision required")
    state, signal = decision.state, decision.signal
    trend = state.trend_snapshot
    if (
        type(signal) is not Signal
        or state.pending_signal != signal
        or type(trend) is not Trend
        or type(trend.session) is not Session
        or state.version != version
        or signal.version != version
        or signal.symbol != state.symbol
        or signal.session_day != state.session_day
        or type(state.session_day) is not date
        or type(signal.limit_price) is not Fraction
        or type(signal.low_since_open) is not Fraction
        or state.last_trade_at is None
        or state.last_decision_at is None
    ):
        raise JournalError("current matching pending research signal required")
    for stamp in (state.last_trade_at, state.last_decision_at):
        _utc(stamp)
    declared = {
        "classification": RESEARCH_BINDING,
        "version": state.version,
        "research_sha256": RESEARCH_SHA256,
        "policy_sha256": POLICY_SHA256,
        "scanner_sha256": ACCEPTED_SHA256,
        "coverage": decision.coverage,
        "outcome": decision.outcome,
        "order_authority": decision.order_authority,
        "signal_order_authority": signal.order_authority,
        "cancel_working_entry_required": decision.cancel_working_entry_required,
        "symbol": state.symbol,
        "session_day": state.session_day.isoformat(),
        "signal_id": signal.signal_id,
        "pending_signal_id": state.pending_signal.signal_id,
        "trade_id": signal.trade_id,
        "limit": str(signal.limit_price),
        "low_since_open": str(signal.low_since_open),
        "first_seen": state.first_seen,
        "gap_passed": state.gap_passed,
        "armed": state.armed,
        "invalidated": state.invalidated,
        "attempted": state.attempted,
        "veto_latched": state.veto_latched,
        "durable_intent_ref": state.durable_intent_ref,
        "last_trade_at": state.last_trade_at.isoformat(),
        "last_decision_at": state.last_decision_at.isoformat(),
        "timeframe": state.timeframe,
        "trend_symbol": trend.symbol,
        "trend_version": trend.version,
        "feature_refs": list(trend.evidence_refs),
        "declared_reasons": list(decision.reasons),
        "session": {
            "day": trend.session.day.isoformat(),
            "open_at": trend.session.open_at.isoformat(),
            "close_at": trend.session.close_at.isoformat(),
            "available_at": trend.session.available_at.isoformat(),
            "calendar_ref": trend.session.calendar_ref,
        },
    }
    if version == CANDIDATE_VERSION:
        declared.update(OBSERVATION_IDENTITY)
    if oco_contract is not None:
        if (
            type(oco_contract) is not OcoResearchContract
            or version != CANDIDATE_VERSION
        ):
            raise JournalError(
                "OCO research requires its explicit parent entry contract"
            )
        oco_contract.__post_init__()
        declared["execution_research_binding"] = dict(OCO_EXECUTION_IDENTITY)
        _check_execution_binding(declared)
    return declared
