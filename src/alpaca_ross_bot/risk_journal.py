"""Canonical durable daily-risk ownership; never dispatches broker actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

from .journal import Journal, JournalError
from .strategy_risk import (
    VERSION,
    DailyRiskState,
    FeeBound,
    FeeBounds,
    Reservation,
    RiskAccount,
    RiskLedger,
    SizeDecision,
    capacity,
    size_entry,
    update_daily_stop,
)


def _encode(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _plain(value: object) -> object:
    if isinstance(value, (Decimal, date)):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class RiskInputs:
    """Offline fixture inputs for persistence and arithmetic verification.

    References must name existing immutable local events. A fixture resolver's
    flags do not qualify actual account, fees, fills, or market observations.
    """

    account: RiskAccount
    ledger: RiskLedger
    fees: FeeBounds
    observed_at: datetime
    presession_equity_at: datetime
    session_open: datetime
    input_event_refs: tuple[int, ...]
    reconciled_flat: bool
    no_orphan_orders: bool
    roundtrip_id: str | None = None
    roundtrip_net: Decimal | None = None

    def to_dict(self) -> dict[str, object]:
        result = _plain(asdict(self))
        assert isinstance(result, dict)
        return result

    @classmethod
    def from_dict(cls, value: object) -> RiskInputs:
        if not isinstance(value, dict):
            raise JournalError("invalid risk input record")
        data = dict(value)
        a, ledger_raw, f = (
            dict(data["account"]),
            dict(data["ledger"]),
            dict(data["fees"]),
        )
        for key in ("equity", "spendable"):
            a[key] = Decimal(a[key])
        a["session"] = date.fromisoformat(a["session"])
        for key in ("realized", "unrealized_bid", "accrued_charges"):
            ledger_raw[key] = Decimal(ledger_raw[key])
        ledger_raw["session"] = date.fromisoformat(ledger_raw["session"])
        reservations = []
        for raw in ledger_raw["reservations"]:
            row = dict(raw)
            for key in (
                "limit",
                "stop",
                "bid",
                "pending_entry_fees",
                "pending_stop_fees",
                "open_exit_fees",
            ):
                row[key] = Decimal(row[key])
            reservations.append(Reservation(**row))
        ledger_raw["reservations"] = tuple(reservations)
        f["rows"] = tuple(
            FeeBound(r["quantity"], Decimal(r["entry"]), Decimal(r["stop_exit"]))
            for r in f["rows"]
        )
        data.update(
            account=RiskAccount(**a),
            ledger=RiskLedger(**ledger_raw),
            fees=FeeBounds(**f),
        )
        for key in ("observed_at", "presession_equity_at", "session_open"):
            data[key] = datetime.fromisoformat(data[key])
        data["input_event_refs"] = tuple(data["input_event_refs"])
        if data["roundtrip_net"] is not None:
            data["roundtrip_net"] = Decimal(data["roundtrip_net"])
        result = cls(**data)
        if result.to_dict() != value:
            raise JournalError("noncanonical risk input record")
        return result


class RiskJournal:
    """One account/version owner, serialized by Journal's write transaction.

    The injected resolver is an OFFLINE FIXTURE seam, never a production input
    qualifier. Existing, hash-valid, available event references are necessary
    for fixture provenance but cannot prove account/fee/fill semantics. The
    actual input qualification verifier is unavailable. Every resulting size
    is a non-authorizing arithmetic result, including positive quantities.
    """

    runtime_qualified = False
    qualification_reasons = (
        "ACTUAL_RISK_INPUT_QUALIFIER_UNAVAILABLE",
        "INJECTED_RESOLVER_IS_OFFLINE_FIXTURE_ONLY",
    )

    def __init__(
        self,
        journal: Journal,
        account_ref: str,
        resolve_inputs: Callable[[tuple[int, ...]], RiskInputs] | None = None,
    ) -> None:
        if journal.read_paper_account_ref() != account_ref:
            raise JournalError("risk account is not bound")
        self.journal, self.account_ref, self.resolve_inputs = (
            journal,
            account_ref,
            resolve_inputs,
        )
        self.state_key = "strategy_risk:" + account_ref + ":" + VERSION
        if not self.verify_rebuild():
            raise JournalError("risk cache differs from immutable evidence")

    def view(self) -> dict[str, object] | None:
        raw = self.journal.read_state(self.state_key)
        if raw is not None and not isinstance(raw, dict):
            raise JournalError("invalid risk projection")
        return raw

    @classmethod
    def read_evidence_at(
        cls, journal: Journal, account_ref: str, decision_at: datetime
    ) -> tuple[dict[str, object] | None, tuple[int, ...]]:
        """Read a visible immutable prefix; fixture evidence never grants runtime.

        Construction checks the current projection and deduplication cache, but
        does not call an input resolver or append restart/observation events.
        """
        if type(decision_at) is not datetime or decision_at.utcoffset() != timedelta(0):
            raise ValueError("UTC risk observation clock required")
        reader = cls(journal, account_ref)
        projected: dict[str, object] | None = None
        seen: dict[str, str] = {}
        refs: list[int] = []
        unavailable_prefix = False
        for event, payload in journal.read_strategy_risk_events():
            item = json.loads(payload)
            if (
                not isinstance(item, dict)
                or event.feed != "paper"
                or event.event_kind != "RISK_INPUT"
                or item.get("account_ref") != account_ref
                or item.get("version") != VERSION
            ):
                raise JournalError("risk evidence identity differs")
            if not event.visible_at(decision_at):
                unavailable_prefix = True
                continue
            if unavailable_prefix or event.external_clock_conflict:
                raise JournalError("risk evidence prefix is unavailable")
            at = datetime.fromisoformat(item["at"])
            if at.utcoffset() != timedelta(0) or at != event.system_first_seen_at:
                raise JournalError("risk evidence clock differs")
            inputs = RiskInputs.from_dict(item["inputs"])
            for sequence in inputs.input_event_refs:
                source = journal.read_event(sequence)
                if (
                    source is None
                    or not source[0].visible_at(at)
                    or source[0].external_clock_conflict
                ):
                    raise JournalError("risk input source is unavailable")
                refs.append(sequence)
            key, digest = item["key"], hashlib.sha256(payload).hexdigest()
            conflict = key in seen and seen[key] != digest
            seen.setdefault(key, digest)
            projected = reader._reduce(projected, item, conflict, event.sequence)
            refs.append(event.sequence)
        return projected, tuple(dict.fromkeys(refs))

    def observe(self, event_id: str, refs: tuple[int, ...], at: datetime) -> None:
        self._record(event_id, refs, at, None)

    def reserve(
        self,
        event_id: str,
        refs: tuple[int, ...],
        at: datetime,
        *,
        symbol: str,
        trigger_high: Decimal,
        known_low: Decimal,
        trigger_trade: Decimal,
        ask: Decimal,
    ) -> SizeDecision:
        if type(symbol) is not str or not symbol:
            raise JournalError("reservation symbol is required")
        prices = [trigger_high, known_low, trigger_trade, ask]
        if any(type(p) is not Decimal or not p.is_finite() for p in prices):
            raise JournalError("reservation prices must be finite Decimals")
        self._record(
            event_id, refs, at, {"symbol": symbol, "prices": [str(p) for p in prices]}
        )
        state = self.view()
        assert state is not None
        raw = state["decision"]
        if not isinstance(raw, dict) or raw["event_id"] != event_id:
            raise JournalError("reservation identity conflict")
        daily = self._daily(state["daily"])
        return SizeDecision(
            self.account_ref,
            daily.session,
            Fraction(daily.frozen_equity),
            0 if state["veto"] else raw["quantity"],
            Fraction(raw["limit"]),
            Fraction(raw["stop"]),
            Fraction(raw["capacity"]),
            "EVIDENCE_VETO" if state["veto"] else raw["reason"],
        )

    def _record(
        self, key: str, refs: tuple[int, ...], at: datetime, request: object
    ) -> None:
        if (
            type(key) is not str
            or not key
            or type(refs) is not tuple
            or not refs
            or len(set(refs)) != len(refs)
            or any(type(r) is not int or r <= 0 for r in refs)
        ):
            raise JournalError("immutable risk input identities required")
        if self.resolve_inputs is None:
            raise JournalError("risk input qualification verifier unavailable")
        if at.utcoffset() is None:
            raise JournalError("aware risk clock required")
        prepared: dict[str, object] = {}

        def prepare(_: bytes) -> bytes:
            if not self.verify_rebuild():
                raise JournalError(
                    "risk projection or deduplication differs from evidence"
                )
            assert self.resolve_inputs is not None
            self._check_refs(refs, at)
            inputs = self.resolve_inputs(refs)
            if inputs.input_event_refs != refs:
                raise JournalError("risk input references differ")
            prepared.update(
                key=key,
                account_ref=self.account_ref,
                version=VERSION,
                at=at.isoformat(),
                inputs=inputs.to_dict(),
                request=request,
            )
            return _encode(prepared)

        self.journal.apply_strategy_risk_event(
            account_ref=self.account_ref,
            state_key=self.state_key,
            dedupe_key=json.dumps([VERSION, key], separators=(",", ":")),
            event_kind="RISK_INPUT",
            payload=b"",
            received_at=at,
            prepare_payload=prepare,
            reduce=lambda old, conflict, sequence: self._reduce(
                old, prepared, conflict, sequence
            ),
        )

    def _check_refs(self, refs: tuple[int, ...], at: datetime) -> None:
        if (
            type(refs) is not tuple
            or not refs
            or any(type(sequence) is not int or sequence <= 0 for sequence in refs)
            or len(set(refs)) != len(refs)
        ):
            raise JournalError("immutable risk input identities required")
        for sequence in refs:
            # read_event verifies the immutable payload digest and envelope.
            record = self.journal.read_event(sequence)
            if record is None:
                raise JournalError("referenced risk input event is missing")
            event, _ = record
            if event.system_first_seen_at > at or event.available_at > at:
                raise JournalError("referenced risk input event is not yet available")

    @staticmethod
    def _daily(raw: object) -> DailyRiskState:
        if not isinstance(raw, dict):
            raise JournalError("missing daily risk state")
        if type(raw["session"]) is not str:
            raise JournalError("invalid daily session")
        return DailyRiskState(
            date.fromisoformat(raw["session"]),
            raw["account_ref"],
            Decimal(raw["frozen_equity"]),
            raw["losing_streak"],
            raw["latched"],
            tuple(raw["completed_ids"]),
        )

    def _reduce(
        self, old: object, item: dict[str, object], conflict: bool, sequence: int
    ) -> dict[str, object]:
        if item["account_ref"] != self.account_ref or item["version"] != VERSION:
            raise JournalError("risk account/version mismatch")
        previous = dict(old) if isinstance(old, dict) else None
        if conflict:
            if previous is None:
                raise JournalError("conflict without prior state")
            return previous | {"veto": True, "watermark": sequence}
        inputs = RiskInputs.from_dict(item["inputs"])
        account, ledger = inputs.account, inputs.ledger
        recorded_at = item["at"]
        if not isinstance(recorded_at, str):
            raise JournalError("invalid risk event clock")
        at = datetime.fromisoformat(recorded_at)
        self._check_refs(inputs.input_event_refs, at)
        if (
            account.account_ref != self.account_ref
            or ledger.account_ref != self.account_ref
            or account.version != VERSION
            or account.baseline_verified is not True
        ):
            raise JournalError("input account/version mismatch")
        if any(
            type(v) is not bool
            for v in (inputs.reconciled_flat, inputs.no_orphan_orders)
        ):
            raise JournalError("invalid reconciliation flags")
        if (
            inputs.observed_at.utcoffset() is None
            or inputs.presession_equity_at.utcoffset() is None
            or inputs.session_open.utcoffset() is None
            or inputs.observed_at > at
        ):
            raise JournalError("invalid evidence clock")
        ny = ZoneInfo("America/New_York")
        baseline = inputs.presession_equity_at.astimezone(ny)
        if (
            baseline.date() != account.session
            or inputs.session_open.astimezone(ny).date() != account.session
            or inputs.presession_equity_at >= inputs.session_open
            or inputs.presession_equity_at > inputs.observed_at
        ):
            raise JournalError("verified pre-session equity required")
        if inputs.observed_at.astimezone(ny).date() != account.session:
            raise JournalError("risk observation session mismatch")
        if previous and at < datetime.fromisoformat(previous["at"]):
            return previous | {"veto": True, "watermark": sequence}
        daily = (
            self._daily(previous["daily"])
            if previous
            else DailyRiskState(account.session, self.account_ref, account.equity)
        )
        held = previous["held"] if previous else None
        if (
            previous
            and account.session == daily.session
            and (
                previous["baseline_at"] != inputs.presession_equity_at.isoformat()
                or previous["session_open"] != inputs.session_open.isoformat()
            )
        ):
            raise JournalError("frozen baseline identity changed")
        if account.session != daily.session and held is not None:
            raise JournalError("new session has outstanding risk reservation")
        completed = dict(previous["completed"]) if previous else {}
        roundtrip_id, net = inputs.roundtrip_id, inputs.roundtrip_net
        if roundtrip_id in completed:
            if completed[roundtrip_id] != str(net):
                assert previous is not None
                return previous | {"veto": True, "watermark": sequence}
            roundtrip_id, net = None, None
        # Only a reconciled terminal roundtrip can retire the durable slot.
        if held is not None and inputs.roundtrip_id == held["identity"]:
            if (
                not inputs.reconciled_flat
                or not inputs.no_orphan_orders
                or ledger.reservations
            ):
                raise JournalError("reservation retirement requires reconciled flat")
            held = None
        daily = update_daily_stop(
            daily,
            account,
            ledger,
            reconciled_flat=inputs.reconciled_flat,
            no_orphan_orders=inputs.no_orphan_orders,
            roundtrip_id=roundtrip_id,
            roundtrip_net=net,
            roundtrip_reconciled=roundtrip_id is not None,
        )
        if roundtrip_id is not None:
            completed[roundtrip_id] = str(net)
        veto = bool(previous and previous["veto"])
        decision: object = previous["decision"] if previous else None
        request = item["request"]
        if request is not None:
            if not isinstance(request, dict):
                raise JournalError("invalid reservation request")
            high, low, trade, ask = (Decimal(v) for v in request["prices"])
            decision_obj = size_entry(
                account,
                replace(ledger, daily_stop_latched=daily.latched or veto),
                inputs.fees,
                high,
                low,
                trade,
                ask,
                daily_state=daily,
            )
            if held is not None:
                decision_obj = replace(decision_obj, quantity=0, reason="SLOT_OCCUPIED")
            decision = {
                "event_id": item["key"],
                "quantity": decision_obj.quantity,
                "limit": str(decision_obj.limit),
                "stop": str(decision_obj.stop),
                "capacity": str(decision_obj.capacity),
                "reason": decision_obj.reason,
            }
            if decision_obj.quantity:
                held = {
                    "identity": item["key"],
                    "symbol": request["symbol"],
                    "quantity": decision_obj.quantity,
                }
        capacity(account, ledger)
        return {
            "schema": 1,
            "account_ref": self.account_ref,
            "version": VERSION,
            "daily": _plain(asdict(daily)),
            "completed": completed,
            "held": held,
            "decision": decision,
            "veto": veto,
            "at": item["at"],
            "baseline_at": inputs.presession_equity_at.isoformat(),
            "session_open": inputs.session_open.isoformat(),
            "watermark": sequence,
            "runtime_authorized": False,
            "qualification": "OFFLINE_FIXTURE_ONLY",
        }

    def verify_rebuild(self) -> bool:
        projected: object = None
        expected: dict[str, object] = {}
        seen: dict[str, str] = {}
        for event, payload in self.journal.read_strategy_risk_events():
            item = json.loads(payload)
            if event.feed != "paper" or event.event_kind != "RISK_INPUT":
                raise JournalError("invalid risk event metadata")
            if item["account_ref"] != self.account_ref:
                raise JournalError("foreign risk account evidence")
            if item["version"] != VERSION:
                continue
            key, digest = item["key"], hashlib.sha256(payload).hexdigest()
            conflict = key in seen and seen[key] != digest
            seen.setdefault(key, digest)
            identity = json.dumps([VERSION, key], separators=(",", ":"))
            expected.setdefault(
                identity, {"hash": "sha256:" + digest, "sequence": event.sequence}
            )
            projected = self._reduce(projected, item, conflict, event.sequence)
        actual = {
            k: v
            for k, v in self.journal.read_strategy_risk_evidence_cache(
                self.account_ref
            ).items()
            if json.loads(k)[0] == VERSION
        }
        return self.view() == projected and actual == expected
