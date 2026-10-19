"""Durable source-bound capital research observations, never stage activation."""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .journal import Journal, JournalError
from .paper_order_contract import decode_broker_json
from .risk_account_inputs import inspect_account_inputs
from .risk_baseline import read_baseline, session_open_from_evidence
from .strategy_risk import VERSION

SOURCE = "ALPACA_CAPITAL_STAGE"
POLICY = "USER_2000_RESEARCH_REVIEW_V1"
VERSIONS = {
    "capital_policy_version": POLICY,
    "strategy_version": VERSION,
    "risk_profile_version": VERSION,
    "account_identity_version": "ALPACA_PAPER_UUID_BINDING_V1",
    "broker_capability_version": "ALPACA_REPORTED_EQUITY_CAPABILITIES_V1",
}


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _utc(at: datetime) -> None:
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise JournalError("UTC capital observation clock required")


class CapitalStageOwner:
    """Own research history without changing strategy, risk, or broker permissions.

    Current reported USD equity controls only the user's $2,000 research-review
    threshold. E0 is recorded independently. Multiplier and buying power are
    observed facts, not borrowed leverage or instrument entitlement. No higher
    operational stage or separate leveraged paper acceptance exists here.
    """

    def __init__(self, journal: Journal, account_ref: str) -> None:
        if journal.read_paper_account_ref() != account_ref:
            raise JournalError("capital observation account is not bound")
        self.journal, self.account_ref = journal, account_ref
        self.state_key = "capital_stage:" + account_ref + ":" + POLICY
        if not self.verify_rebuild():
            raise JournalError("capital cache differs from immutable evidence")

    def view(self) -> dict[str, Any] | None:
        raw = self.journal.read_state(self.state_key)
        if raw is not None and (
            not isinstance(raw, dict)
            or raw.get("account_ref") != self.account_ref
            or raw.get("versions") != VERSIONS
            or raw.get("runtime_authorized") is not False
        ):
            raise JournalError("capital projection identity differs")
        return raw

    def _observation(self, item: dict[str, Any]) -> dict[str, object]:
        at = datetime.fromisoformat(item["at"])
        _utc(at)
        selection, horizon = item["selection"], item["source_horizon"]
        if (
            type(horizon) is not int
            or horizon < 0
            or not isinstance(selection, dict)
            or set(selection) != {"account_sequence", "calendar_sequence", "max_age_us"}
            or any(type(value) is not int or value < 1 for value in selection.values())
            or max(selection["account_sequence"], selection["calendar_sequence"])
            > horizon
        ):
            raise JournalError("capital source selection is invalid")
        age = timedelta(microseconds=selection["max_age_us"])
        day = at.astimezone(ZoneInfo("America/New_York")).date()
        opened = session_open_from_evidence(
            self.journal, selection["calendar_sequence"], self.account_ref, day, at, age
        )
        facts = inspect_account_inputs(
            self.journal,
            selection["account_sequence"],
            account_ref=self.account_ref,
            decision_at=at,
            session_open=opened,
            max_age=age,
        )
        stored = self.journal.read_event(selection["account_sequence"])
        assert stored is not None
        envelope = decode_broker_json(stored[1])
        assert isinstance(envelope, dict) and isinstance(envelope["requests"], list)
        account = next(
            decode_broker_json(row["body"])
            for row in envelope["requests"]
            if row["path"] == "/v2/account"
        )
        assert isinstance(account, dict)
        baseline = read_baseline(
            self.journal,
            account_ref=self.account_ref,
            session_day=day,
            decision_at=at,
            through_sequence=horizon,
        )
        money = dict(facts.money)
        equity, power = money["equity"], money["buying_power"]
        current = (
            equity >= Decimal("2000")
            if account.get("currency") == "USD" and equity is not None and equity > 0
            else None
        )
        multiplier = account.get("multiplier")
        reasons = [
            "W3_02_RUNTIME_RISK_ACCEPTANCE_SEPARATE",
            "LEVERAGED_ROSS_VERSION_AND_RISK_UNACCEPTED",
            "SEPARATE_1X_VS_LEVERAGED_PAPER_EVIDENCE_UNACCEPTED",
            "INSTRUMENT_MARGIN_ENTITLEMENT_UNVERIFIED",
        ]
        if not facts.active_usd_flags_clear:
            reasons.append("ACCOUNT_FLAGS_UNQUALIFIED")
        if current is None:
            reasons.append("CURRENT_USD_EQUITY_UNQUALIFIED")
        if baseline is None:
            reasons.append("PRESESSION_E0_UNAVAILABLE")
        if type(multiplier) is not str or multiplier not in {"1", "2", "4"}:
            reasons.append("REPORTED_MULTIPLIER_UNQUALIFIED")
        if power is None:
            reasons.append("REPORTED_BUYING_POWER_UNAVAILABLE")
        if money["cash"] is not None and money["cash"] < 0:
            reasons.append("NEGATIVE_CASH_REQUIRES_RECONCILIATION")
        refs = {selection["account_sequence"], selection["calendar_sequence"]}
        if baseline is not None:
            assert baseline.receipt_sequence is not None
            receipt = self.journal.read_event(baseline.receipt_sequence)
            assert receipt is not None
            receipt_body = decode_broker_json(receipt[1])
            assert isinstance(receipt_body, dict)
            refs.update(
                {
                    baseline.account_sequence,
                    baseline.calendar_sequence,
                    baseline.receipt_sequence,
                    int(receipt_body["baseline_sequence"]),
                    *(seq for seq, _ in baseline.rollover_source_refs),
                }
            )
        references: list[list[object]] = []
        for sequence in sorted(refs):
            source = self.journal.read_event(sequence)
            if source is None or sequence > horizon or not source[0].visible_at(at):
                raise JournalError("capital source proof is unavailable")
            references.append([sequence, source[0].payload_ref])
        return {
            "capital_stage": "UNQUALIFIED"
            if current is None or not facts.active_usd_flags_clear
            else (
                "LEVERAGED_VERSION_REVIEW_ONLY" if current else "NO_BORROWING_RESEARCH"
            ),
            "current_equity_threshold_reached": current,
            "e0_threshold_reached": None
            if baseline is None
            else baseline.equity >= 2000,
            "reported_equity": None if equity is None else str(equity),
            "reported_buying_power": None if power is None else str(power),
            "reported_multiplier": multiplier if type(multiplier) is str else None,
            "broker_entitlement": "UNVERIFIED",
            "higher_stage": "BLOCKED_PENDING_SEPARATE_ACCEPTANCE",
            "separate_leveraged_paper_acceptance": "UNACCEPTED",
            "risk_gate": "BLOCK_NEW_EXPOSURE",
            "borrowed_leverage_authorized": False,
            "strategy_switch_authorized": False,
            "reasons": sorted(reasons),
            "source_refs": references,
            "runtime_authorized": False,
        }

    def _reduce(
        self, old: object, item: dict[str, Any], conflict: bool, sequence: int
    ) -> dict[str, Any]:
        if (
            set(item)
            != {
                "key",
                "account_ref",
                "versions",
                "kind",
                "at",
                "selection",
                "source_horizon",
                "observation",
            }
            or conflict
            or item.get("account_ref") != self.account_ref
            or item.get("versions") != VERSIONS
            or type(item.get("source_horizon")) is not int
            or not 0 <= item["source_horizon"] < sequence
        ):
            raise JournalError("capital evidence identity or horizon conflicts")
        snapshot = self._observation(item)
        if item.get("observation") != snapshot:
            raise JournalError("capital observation differs from source evidence")
        previous = old if isinstance(old, dict) else None
        if previous is not None and (
            item["at"] < previous["at"]
            or item["selection"]["account_sequence"] < previous["account_sequence"]
        ):
            raise JournalError("capital observations cannot move backwards")
        current = snapshot["current_equity_threshold_reached"]
        known = None if previous is None else previous["last_known_threshold"]
        downgraded = known is True and current is False
        return {
            "schema": 1,
            "account_ref": self.account_ref,
            "versions": dict(VERSIONS),
            "at": item["at"],
            "watermark": sequence,
            "account_sequence": item["selection"]["account_sequence"],
            "last_known_threshold": known if current is None else current,
            "review_withdrawn": downgraded,
            "downgrade_count": (0 if previous is None else previous["downgrade_count"])
            + int(downgraded),
            "previous_sequence": None if previous is None else previous["watermark"],
            "observation": snapshot,
            "runtime_authorized": False,
        }

    def assess(
        self,
        event_id: str,
        *,
        account_sequence: int,
        calendar_sequence: int,
        decision_at: datetime,
        max_age: timedelta,
    ) -> dict[str, Any]:
        _utc(decision_at)
        if type(event_id) is not str or not event_id.strip():
            raise JournalError("capital event identity required")
        if type(max_age) is not timedelta or max_age <= timedelta(0):
            raise JournalError("positive capital source age required")
        item: dict[str, Any] = {
            "key": event_id,
            "account_ref": self.account_ref,
            "versions": dict(VERSIONS),
            "kind": "STAGE_OBSERVATION",
            "at": decision_at.isoformat(),
            "selection": {
                "account_sequence": account_sequence,
                "calendar_sequence": calendar_sequence,
                "max_age_us": max_age // timedelta(microseconds=1),
            },
        }

        def prepare(_: bytes) -> bytes:
            if not self.verify_rebuild():
                raise JournalError("capital cache or deduplication differs")
            for _event, raw in self.journal.read_capital_stage_events():
                saved = json.loads(raw)
                if saved["key"] == event_id:
                    caller = {
                        k: v
                        for k, v in saved.items()
                        if k not in {"observation", "source_horizon"}
                    }
                    if caller != item:
                        raise JournalError("capital retry command differs")
                    item.clear()
                    item.update(saved)
                    return raw
            item["source_horizon"] = self.journal.read_evidence_sequence()
            item["observation"] = self._observation(item)
            return _bytes(item)

        self.journal.apply_capital_stage_event(
            account_ref=self.account_ref,
            state_key=self.state_key,
            dedupe_key=event_id,
            event_kind="STAGE_OBSERVATION",
            payload=b"",
            received_at=decision_at,
            prepare_payload=prepare,
            reduce=lambda old, conflict, seq: self._reduce(old, item, conflict, seq),
        )
        state = self.view()
        assert state is not None
        return state

    def public_summary(self) -> dict[str, object]:
        state = self.view()
        if state is None:
            return {"observed": False, "runtime_authorized": False}
        observation = state["observation"]
        return {
            "observed": True,
            "versions": dict(VERSIONS),
            "capital_stage": observation["capital_stage"],
            "higher_stage": observation["higher_stage"],
            "risk_gate": observation["risk_gate"],
            "downgrade_count": state["downgrade_count"],
            "runtime_authorized": False,
        }

    def verify_rebuild(self) -> bool:
        projected: object = None
        expected: dict[str, object] = {}
        for event, raw in self.journal.read_capital_stage_events():
            item = decode_broker_json(raw)
            if not isinstance(item, dict) or (
                event.feed != "paper"
                or event.event_kind != "STAGE_OBSERVATION"
                or item.get("kind") != event.event_kind
                or item.get("at") != event.available_at.isoformat()
                or event.external_clock_conflict
                or type(item.get("key")) is not str
                or item["key"] in expected
            ):
                raise JournalError("capital immutable event identity differs")
            expected[item["key"]] = {
                "hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "sequence": event.sequence,
            }
            projected = self._reduce(projected, item, False, event.sequence)
        return self.view() == projected and (
            self.journal.read_capital_stage_evidence_cache(self.account_ref) == expected
        )
