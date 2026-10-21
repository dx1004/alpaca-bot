"""Private durable incident observations; no broker dispatch or runtime grant."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

from .incident_execution import derive_execution_incident
from .incident_risk import IncidentRiskRequest, assess_incident_risk
from .incident_state import (
    IncidentObservation,
    IncidentPolicy,
    IncidentProjection,
    IncidentProposal,
    ProposalKind,
    reduce_incident,
)
from .journal import Journal, JournalError


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


class _RecordedExecution(Exception):
    """Roll back an already-recorded request, including a prior conflict."""


def _execution_request(value: object) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"event_id", "decision_at", "fee_policy"}
        or any(type(v) is not str or not v or v != v.strip() for v in value.values())
    ):
        raise JournalError("invalid execution incident request")
    decision = datetime.fromisoformat(value["decision_at"])
    if decision.utcoffset() != timedelta(0):
        raise JournalError("UTC execution incident decision required")
    if str(Path(value["fee_policy"]).resolve()) != value["fee_policy"]:
        raise JournalError("canonical execution incident fee path required")
    return dict(value)


class IncidentJournal:
    """Persist a research-gated incident core without sharing lifecycle events.

    Runtime risk/protection acceptance is deliberately unavailable until its
    independent verifier exists. A caller-supplied flag and hash cannot grant it.
    Every stored record remains non-dispatching, including after a restart.
    """

    def __init__(self, journal: Journal, policy: IncidentPolicy) -> None:
        if policy.runtime_risk_accepted or policy.protection_contract_accepted:
            raise JournalError(
                "runtime risk/protection acceptance verifier is unavailable"
            )
        self.journal = journal
        self.policy = IncidentPolicy.from_dict(policy.to_dict())
        if journal.read_paper_account_ref() != policy.account_ref:
            raise JournalError("incident account is not bound to this journal")
        self.state_key = "incident:" + policy.account_ref + ":" + policy.scope_id
        if not self.verify_rebuild():
            raise JournalError("incident projection differs from immutable evidence")
        self._record({"kind": "RESTART"}, datetime.now(UTC), "restart:" + str(uuid4()))

    def view(self) -> IncidentProjection | None:
        state = self.journal.read_state(self.state_key)
        if state is None:
            return None
        self._check_wrapper(state)
        assert isinstance(state, dict)
        projection = state["projection"]
        return None if projection is None else IncidentProjection.from_dict(projection)

    def _check_wrapper(self, state: object) -> None:
        base = {"schema", "policy", "projection", "watermark", "dispatch_authorized"}
        extra = set()
        if isinstance(state, dict):
            if state.get("schema") == 2:
                extra = {"risk"}
            elif state.get("schema") == 3:
                extra = {"execution_receipt", "execution_quarantine"}
                if "risk" in state:
                    extra.add("risk")
        if (
            not isinstance(state, dict)
            or set(state) != base | extra
            or type(state["schema"]) is not int
            or state["schema"] not in (1, 2, 3)
            or state["policy"] != self.policy.to_dict()
            or type(state["watermark"]) is not int
            or state["watermark"] <= 0
            or state["dispatch_authorized"] is not False
        ):
            raise JournalError("incident projection wrapper is invalid")
        if state["schema"] == 3 and (
            not isinstance(state["execution_receipt"], dict)
            or not isinstance(state["execution_quarantine"], list)
            or any(type(v) is not str for v in state["execution_quarantine"])
            or state["execution_quarantine"]
            != sorted(set(state["execution_quarantine"]))
        ):
            raise JournalError("invalid execution incident projection wrapper")

    def observe_execution(
        self,
        event_id: str,
        decision_at: datetime,
        received_at: datetime,
        *,
        account_risk_fee_policy: Path,
    ) -> dict[str, object]:
        """Atomically derive reported facts; no caller-provided safety flags.

        A retry returns its original receipt, even after a later observation.
        Changed request bytes retain that receipt and latch an identity conflict.
        Quarantine is evidence retention, not allocation or recovery authority.
        """
        for stamp in (decision_at, received_at):
            if type(stamp) is not datetime or stamp.utcoffset() != timedelta(0):
                raise JournalError("UTC execution incident clock required")
        request = _execution_request(
            {
                "event_id": event_id,
                "decision_at": decision_at.isoformat(),
                "fee_policy": str(account_risk_fee_policy.resolve()),
            }
        )
        item = self._record(
            {"kind": "BOUND_EXECUTION", "request": request},
            received_at,
            "execution:" + event_id,
        )
        receipt = item["execution_receipt"]
        assert isinstance(receipt, dict)
        return dict(receipt)

    def observe_risk(
        self,
        event_id: str,
        request: IncidentRiskRequest,
        received_at: datetime,
        *,
        account_risk_fee_policy: Path | None = None,
    ) -> dict[str, object]:
        """Bind risk facts by immutable references; callers cannot submit flags."""
        if not isinstance(event_id, str) or not event_id.strip():
            raise JournalError("risk observation identity required")
        item: dict[str, object] = {
            "kind": "BOUND_RISK",
            "request": request.to_dict(),
            "at": received_at.isoformat(),
        }
        if account_risk_fee_policy is not None:
            item["account_risk_fee_policy"] = str(account_risk_fee_policy.resolve())
        self._record(
            item,
            received_at,
            "risk:" + event_id,
        )
        state = self.journal.read_state(self.state_key)
        if not isinstance(state, dict) or not isinstance(state.get("risk"), dict):
            raise JournalError("bound risk assessment was not committed")
        return dict(state["risk"])

    def observe(
        self, observation: IncidentObservation, received_at: datetime
    ) -> IncidentProjection:
        """Record validated upstream facts; a failed commit yields no proposal."""
        # Strict decoding applies equally to in-process and recovered input.
        observation = IncidentObservation.from_dict(observation.to_dict())
        if (
            observation.account_ref != self.policy.account_ref
            or observation.scope_id != self.policy.scope_id
        ):
            raise JournalError("incident observation account/scope differs")
        self._record(
            {"kind": "OBSERVE", "observation": observation.to_dict()},
            received_at,
            "observe:" + observation.event_id,
        )
        result = self.view()
        if result is None:
            raise JournalError("incident projection was not committed")
        return result

    def _record(
        self, item: dict[str, object], at: datetime, key: str
    ) -> dict[str, object]:
        item = dict(item) | {"policy": self.policy.to_dict(), "dedupe_key": key}

        def verify_locked(raw: bytes) -> bytes:
            if not self.verify_rebuild():
                raise JournalError(
                    "incident projection or deduplication differs from evidence"
                )
            if item["kind"] == "BOUND_EXECUTION":
                prior = None
                recorded: set[bytes] = set()
                for _, payload in self.journal.read_incident_events():
                    row = json.loads(payload)
                    if (
                        row.get("policy") == self.policy.to_dict()
                        and row.get("dedupe_key") == key
                    ):
                        prior = row if prior is None else prior
                        recorded.add(payload)
                request = _execution_request(item["request"])
                if prior is None:
                    horizon = self.journal.read_evidence_sequence()
                    receipt = derive_execution_incident(
                        self.journal,
                        self.policy,
                        event_id=request["event_id"],
                        decision_at=datetime.fromisoformat(request["decision_at"]),
                        through_sequence=horizon,
                        fee_policy=Path(request["fee_policy"]),
                    )
                    item.update(
                        source_horizon=horizon,
                        execution_receipt=receipt,
                        outcome="BOUND_EXECUTION_INCIDENT",
                    )
                else:
                    item.update(
                        source_horizon=prior["source_horizon"],
                        execution_receipt=prior["execution_receipt"],
                        outcome="BOUND_EXECUTION_INCIDENT"
                        if request == prior["request"]
                        else "REQUEST_IDENTITY_CONFLICT",
                    )
                stored_receipt = item["execution_receipt"]
                assert isinstance(stored_receipt, dict)
                receipt_request = _execution_request(stored_receipt["request"])
                if at < datetime.fromisoformat(receipt_request["decision_at"]):
                    raise JournalError("execution incident receipt predates decision")
                raw = _json(item)
                if raw in recorded:
                    raise _RecordedExecution
                return raw
            if item["kind"] == "BOUND_RISK":
                if item.get("account_risk_fee_policy") is not None:
                    item["source_horizon"] = self.journal.read_evidence_sequence()
                item["intent_census"] = [
                    [
                        cid,
                        hashlib.sha256(
                            json.dumps(
                                self.journal.read_order_intent(cid),
                                sort_keys=True,
                                allow_nan=False,
                            ).encode()
                        ).hexdigest(),
                    ]
                    for cid in sorted(self.journal.read_order_intent_ids())
                ]
                item["assessment"] = assess_incident_risk(
                    self.journal,
                    self.policy.account_ref,
                    IncidentRiskRequest.from_dict(item["request"]),
                    datetime.fromisoformat(str(item["at"])),
                    item["intent_census"],
                    None
                    if item.get("account_risk_fee_policy") is None
                    else Path(str(item["account_risk_fee_policy"])),
                    None
                    if item.get("source_horizon") is None
                    else int(str(item["source_horizon"])),
                )
                return _json(item)
            return raw

        try:
            self.journal.apply_incident_event(
                account_ref=self.policy.account_ref,
                state_key=self.state_key,
                dedupe_key=json.dumps(
                    [self.policy.scope_id, key], separators=(",", ":")
                ),
                event_kind=str(item["kind"]),
                payload=_json(item),
                received_at=at,
                prepare_payload=verify_locked,
                reduce=lambda old, conflict, sequence: self._reduce(
                    old, item, conflict, sequence
                ),
            )
        except _RecordedExecution:
            pass
        return item

    def _reduce(
        self, old: object | None, item: dict[str, object], conflict: bool, sequence: int
    ) -> dict[str, object]:
        if old is not None:
            self._check_wrapper(old)
        previous_raw = old["projection"] if isinstance(old, dict) else None
        previous = (
            None if previous_raw is None else IncidentProjection.from_dict(previous_raw)
        )
        risk = old.get("risk") if isinstance(old, dict) else None
        execution_receipt = (
            old.get("execution_receipt") if isinstance(old, dict) else None
        )
        quarantine = set(
            old.get("execution_quarantine", []) if isinstance(old, dict) else []
        )
        if item.get("policy") != self.policy.to_dict():
            raise JournalError("incident policy changed without acceptance")
        if item["kind"] == "RESTART":
            projection = previous
            if projection is not None:
                proposals = projection.proposals
                if not any(row.kind == ProposalKind.RECONCILE for row in proposals):
                    proposals += (IncidentProposal(ProposalKind.RECONCILE),)
                projection = replace(
                    projection,
                    reasons=tuple(
                        sorted(set(projection.reasons) | {"RESTART_REQUIRES_RECOVERY"})
                    ),
                    proposals=proposals,
                )
        elif item["kind"] == "BOUND_RISK":
            if item.get("account_risk_fee_policy") is not None and (
                type(item.get("source_horizon")) is not int
                or not 0 <= int(str(item["source_horizon"])) < sequence
            ):
                raise JournalError("bound risk sequence horizon differs")
            assessment = assess_incident_risk(
                self.journal,
                self.policy.account_ref,
                IncidentRiskRequest.from_dict(item["request"]),
                datetime.fromisoformat(str(item["at"])),
                item["intent_census"],
                None
                if item.get("account_risk_fee_policy") is None
                else Path(str(item["account_risk_fee_policy"])),
                None
                if item.get("source_horizon") is None
                else int(str(item["source_horizon"])),
            )
            if assessment != item["assessment"]:
                raise JournalError("bound risk assessment differs from source replay")
            risk = assessment | {
                "daily_stop_latched": assessment["daily_stop_observed"] is True
                or (isinstance(risk, dict) and risk.get("daily_stop_latched") is True),
                "identity_conflict": conflict
                or (isinstance(risk, dict) and risk.get("identity_conflict") is True),
            }
            projection = previous
            if conflict and projection is not None:
                projection = replace(
                    projection,
                    reasons=tuple(
                        sorted(set(projection.reasons) | {"EVENT_ID_CONFLICT"})
                    ),
                )
        elif item["kind"] == "BOUND_EXECUTION":
            if execution_receipt is None and previous is not None:
                raise JournalError("declared incident scope cannot become source-bound")
            if conflict:
                if previous is None:
                    raise JournalError("execution incident conflict lacks original")
                projection = replace(
                    previous,
                    reasons=tuple(
                        sorted(set(previous.reasons) | {"EVENT_ID_CONFLICT"})
                    ),
                    proposals=tuple(
                        dict.fromkeys(
                            previous.proposals
                            + (
                                IncidentProposal(ProposalKind.RECONCILE),
                                IncidentProposal(ProposalKind.ESCALATE),
                            )
                        )
                    ),
                )
            else:
                receipt = item["execution_receipt"]
                if not isinstance(receipt, dict):
                    raise JournalError("execution incident receipt is invalid")
                observation = IncidentObservation.from_dict(receipt["observation"])
                # Only this atomic writer can attest durability. A failed commit
                # exposes neither this projection nor a successful return.
                observation = replace(observation, durable=True)
                projection = reduce_incident(previous, self.policy, observation)
                execution_receipt = receipt
                quarantine.update(receipt["quarantine_reasons"])
        elif item["kind"] == "OBSERVE":
            if execution_receipt is not None:
                raise JournalError(
                    "source-bound incident scope refuses declared observations"
                )
            observation = IncidentObservation.from_dict(item["observation"])
            projection = reduce_incident(previous, self.policy, observation)
            if conflict and "EVENT_ID_CONFLICT" not in projection.reasons:
                raise JournalError(
                    "incident event identity conflict is not represented"
                )
        else:
            raise JournalError("incident event kind is unsupported")
        if risk is not None:
            if not isinstance(risk, dict) or not isinstance(risk.get("reasons"), list):
                raise JournalError("invalid bound risk projection")
            if projection is not None:
                reasons = {
                    r for r in projection.reasons if not r.startswith("BOUND_RISK_")
                }
                reasons.update(str(r) for r in risk["reasons"])
                if risk.get("ownership_verified") is True:
                    raw_positions = risk.get("owned_positions")
                    if not isinstance(raw_positions, list):
                        raise JournalError("bound risk inventory missing")
                    positions = dict(raw_positions)
                    actual = Fraction(positions.get(self.policy.symbol, "0"))
                    if (
                        projection.expected_quantity is None
                        or projection.observed_position is None
                        or actual != Fraction(projection.expected_quantity)
                        or actual != Fraction(projection.observed_position)
                    ):
                        reasons.add("BOUND_RISK_POSITION_MISMATCH")
                if risk.get("identity_conflict") is True:
                    reasons.add("EVENT_ID_CONFLICT")
                if risk.get("daily_stop_latched") is True:
                    reasons.add("DAILY_STOP_LATCHED")
                if item["kind"] != "BOUND_RISK":
                    reasons.add("BOUND_RISK_REFRESH_REQUIRED")
                proposals = projection.proposals + (
                    IncidentProposal(ProposalKind.RECONCILE),
                )
                if projection.pending_entry_ids:
                    proposals += (
                        IncidentProposal(
                            ProposalKind.CANCEL_ENTRY_REMAINDER,
                            projection.pending_entry_ids,
                        ),
                    )
                projection = replace(
                    projection,
                    reasons=tuple(sorted(reasons)),
                    daily_stop_latched=projection.daily_stop_latched
                    or risk.get("daily_stop_latched") is True,
                    proposals=tuple(dict.fromkeys(proposals)),
                )
        if projection is not None and quarantine:
            projection = replace(
                projection,
                reasons=tuple(
                    sorted(
                        set(projection.reasons) | {"EXECUTION_" + r for r in quarantine}
                    )
                ),
                proposals=tuple(
                    dict.fromkeys(
                        projection.proposals
                        + (
                            IncidentProposal(ProposalKind.RECONCILE),
                            IncidentProposal(ProposalKind.ESCALATE),
                        )
                    )
                ),
            )
        result: dict[str, object] = {
            "schema": 3
            if execution_receipt is not None
            else 2
            if risk is not None
            else 1,
            "policy": self.policy.to_dict(),
            "projection": None if projection is None else projection.to_dict(),
            "watermark": sequence,
            "dispatch_authorized": False,
        }
        if risk is not None:
            result["risk"] = risk
        if execution_receipt is not None:
            result.update(
                execution_receipt=execution_receipt,
                execution_quarantine=sorted(quarantine),
            )
        return result

    def verify_rebuild(self) -> bool:
        """Require the cache to include every immutable event for this scope."""
        cached = self.journal.read_state(self.state_key)
        projected: object | None = None
        seen: dict[str, str] = {}
        expected_cache: dict[str, object] = {}
        execution_originals: dict[str, dict[str, object]] = {}
        execution_payloads: set[bytes] = set()
        for event, payload in self.journal.read_incident_events():
            item = json.loads(payload)
            if event.feed != "paper" or event.event_kind != item.get("kind"):
                raise JournalError("incident event metadata differs from evidence")
            policy = IncidentPolicy.from_dict(item["policy"])
            if policy.account_ref != self.policy.account_ref:
                raise JournalError("foreign account in incident evidence")
            if policy.scope_id != self.policy.scope_id:
                continue
            key = str(item["dedupe_key"])
            fingerprint = hashlib.sha256(payload).hexdigest()
            conflict = key in seen and seen[key] != fingerprint
            if item["kind"] == "BOUND_EXECUTION":
                if (
                    set(item)
                    != {
                        "kind",
                        "request",
                        "source_horizon",
                        "execution_receipt",
                        "outcome",
                        "policy",
                        "dedupe_key",
                    }
                    or _json(item) != payload
                    or type(item["source_horizon"]) is not int
                    or not 0 <= item["source_horizon"] < event.sequence
                    or payload in execution_payloads
                ):
                    raise JournalError("invalid immutable execution incident record")
                request = _execution_request(item["request"])
                if key != "execution:" + request["event_id"]:
                    raise JournalError("execution incident dedupe identity differs")
                receipt_request = _execution_request(
                    item["execution_receipt"]["request"]
                )
                if event.available_at < datetime.fromisoformat(
                    receipt_request["decision_at"]
                ):
                    raise JournalError("immutable execution incident predates decision")
                if conflict:
                    original = execution_originals[key]
                    if (
                        item["outcome"] != "REQUEST_IDENTITY_CONFLICT"
                        or request == original["request"]
                        or item["source_horizon"] != original["source_horizon"]
                        or _json(item["execution_receipt"])
                        != _json(original["execution_receipt"])
                    ):
                        raise JournalError(
                            "execution incident conflict rewrites original"
                        )
                else:
                    receipt = derive_execution_incident(
                        self.journal,
                        self.policy,
                        event_id=request["event_id"],
                        decision_at=datetime.fromisoformat(request["decision_at"]),
                        through_sequence=item["source_horizon"],
                        fee_policy=Path(request["fee_policy"]),
                    )
                    if item["outcome"] != "BOUND_EXECUTION_INCIDENT" or _json(
                        receipt
                    ) != _json(item["execution_receipt"]):
                        raise JournalError(
                            "execution incident differs from source replay"
                        )
                execution_originals.setdefault(key, item)
                execution_payloads.add(payload)
            seen.setdefault(key, fingerprint)
            identity = json.dumps([self.policy.scope_id, key], separators=(",", ":"))
            expected_cache.setdefault(
                identity, {"hash": "sha256:" + fingerprint, "sequence": event.sequence}
            )
            projected = self._reduce(projected, item, conflict, event.sequence)
        scoped_cache: dict[str, object] = {}
        for key, value in self.journal.read_incident_evidence_cache(
            self.policy.account_ref
        ).items():
            identity = json.loads(key)
            if (
                not isinstance(identity, list)
                or len(identity) != 2
                or any(not isinstance(part, str) for part in identity)
            ):
                raise JournalError("incident deduplication identity is invalid")
            if identity[0] == self.policy.scope_id:
                scoped_cache[key] = value
        return cached == projected and expected_cache == scoped_cache
