"""Private durable incident observations; no broker dispatch or runtime grant."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

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
        if (
            not isinstance(state, dict)
            or (
                set(state) != base
                if state.get("schema") == 1
                else set(state) != base | {"risk"}
            )
            or type(state["schema"]) is not int
            or state["schema"] not in (1, 2)
            or state["policy"] != self.policy.to_dict()
            or type(state["watermark"]) is not int
            or state["watermark"] <= 0
            or state["dispatch_authorized"] is not False
        ):
            raise JournalError("incident projection wrapper is invalid")

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

    def _record(self, item: dict[str, object], at: datetime, key: str) -> None:
        item = dict(item) | {"policy": self.policy.to_dict(), "dedupe_key": key}

        def verify_locked(raw: bytes) -> bytes:
            if not self.verify_rebuild():
                raise JournalError(
                    "incident projection or deduplication differs from evidence"
                )
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

        self.journal.apply_incident_event(
            account_ref=self.policy.account_ref,
            state_key=self.state_key,
            dedupe_key=json.dumps([self.policy.scope_id, key], separators=(",", ":")),
            event_kind=str(item["kind"]),
            payload=_json(item),
            received_at=at,
            prepare_payload=verify_locked,
            reduce=lambda old, conflict, sequence: self._reduce(
                old, item, conflict, sequence
            ),
        )

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
        elif item["kind"] == "OBSERVE":
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
        result: dict[str, object] = {
            "schema": 2 if risk is not None else 1,
            "policy": self.policy.to_dict(),
            "projection": None if projection is None else projection.to_dict(),
            "watermark": sequence,
            "dispatch_authorized": False,
        }
        if risk is not None:
            result["risk"] = risk
        return result

    def verify_rebuild(self) -> bool:
        """Require the cache to include every immutable event for this scope."""
        cached = self.journal.read_state(self.state_key)
        projected: object | None = None
        seen: dict[str, str] = {}
        expected_cache: dict[str, object] = {}
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
