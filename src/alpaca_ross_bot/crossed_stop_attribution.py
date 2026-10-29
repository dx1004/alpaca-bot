"""Durable injected attribution claims, never operational ownership or release.

The account index only unions exact raw execution identities. Every original
reservation basket stays intact, including after partial or terminal reports.
"""

from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from typing import Any, Never, cast

from ._causal_risk_memo import causal_risk_verification_scope
from .crossed_stop_attempts import ORIGIN
from .crossed_stop_capture import AUTHORITY
from .crossed_stop_fills import derive_injected_source_close_fills
from .journal import Journal, JournalError
from .native_oco_requests import _clock, _copy, _digest, _json, _text, _utc
from .oco_journal import _causal_oco_reader
from .oco_research import OcoResearchState, load_oco_research_contract
from .paper_order_contract import decode_broker_json

SOURCE = "ALPACA_SOURCE_CLOSE_ATTRIBUTION"
EVENT_KIND = "SOURCE_CLOSE_ATTRIBUTION"
NAMESPACE = "source_close_attribution"
DEDUPE = "source_close_attribution_evidence"
CLASSIFICATION = "INJECTED_ATTRIBUTION_INDEX_NOT_OPERATIONAL_OWNERSHIP"


class _ConflictRetry(Exception):
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row


def _binding(journal: Journal, initial: OcoResearchState) -> dict[str, Any]:
    return cast(dict[str, Any], _copy(_causal_oco_reader(journal, initial).binding))


def _initial(journal: Journal, binding: dict[str, Any]) -> OcoResearchState:
    initial = OcoResearchState(
        load_oco_research_contract(),
        binding["account_ref"],
        binding["symbol"],
        binding["entry_scope"],
        Fraction(binding["limit"]),
        Fraction(binding["stop"]),
    )
    if _json(_binding(journal, initial)) != _json(binding):
        raise JournalError("attribution initial binding differs")
    return initial


def _key(command: dict[str, Any]) -> str:
    return _json([command["binding"]["entry_scope"], command["identity"]]).decode()


def _command(
    binding: dict[str, Any], identity: str, cid: str, sequences: tuple[int, ...]
) -> dict[str, Any]:
    if (
        type(sequences) is not tuple
        or not sequences
        or any(type(sequence) is not int or sequence < 1 for sequence in sequences)
        or tuple(sorted(set(sequences))) != sequences
    ):
        raise JournalError("strict ordered attribution capture references required")
    return {
        "binding": _copy(binding),
        "identity": _text(identity),
        "client_order_id": _text(cid),
        "capture_sequences": list(sequences),
    }


def _empty(account_ref: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "classification": CLASSIFICATION,
        "account_ref": account_ref,
        "observations": [],
        "operations": {},
        "cid_claims": {},
        "order_claims": {},
        "activity_claims": {},
        "conflicts": [],
        "latched": False,
        "watermark": 0,
        "authority": dict(AUTHORITY),
        "finality": "UNKNOWN",
    }


def _derive(journal: Journal, item: dict[str, Any]) -> dict[str, Any]:
    command = item["command"]
    proof = derive_injected_source_close_fills(
        journal,
        _initial(journal, command["binding"]),
        client_order_id=command["client_order_id"],
        capture_sequences=tuple(command["capture_sequences"]),
        through_sequence=item["source_horizon"],
        decision_at=_clock(item["at"]),
    )
    if (
        proof["component_attributed"] is not True
        or proof["issues"] != []
        or proof["finality"] != "UNKNOWN"
        or proof["origin"] != ORIGIN
        or _json(proof["authority"]) != _json(AUTHORITY)
        or any(value is not False for value in proof["authority"].values())
        or proof["account_ref"] != item["account_ref"]
        or proof["entry_scope"] != command["binding"]["entry_scope"]
        or proof["client_order_id"] != command["client_order_id"]
        or type(proof["source_horizon"]) is not int
        or proof["source_horizon"] != item["source_horizon"]
        or proof["decision_at"] != item["at"]
    ):
        raise JournalError("qualified injected attribution component required")
    return proof


def _project(
    state: dict[str, Any], item: dict[str, Any], ref: dict[str, Any]
) -> dict[str, Any]:
    state = cast(dict[str, Any], _copy(state))
    command, kind = item["command"], item["kind"]
    key = _key(command)
    row = {"observation_ref": ref, **_copy(item)}
    original = state["operations"].get(key)
    if kind == "CONFLICT":
        if state["latched"]:
            raise JournalError("attribution conflict latch forbids later transitions")
        if (
            original is None
            or item["original_ref"] != original["observation_ref"]
            or original["observation_ref"]["sequence"] > item["source_horizon"]
            or _json(command) == _json(original["command"])
            or any(
                conflict["dedupe_key"] == key
                and conflict["identity_hash"] == item["identity_hash"]
                for conflict in state["conflicts"]
            )
        ):
            raise JournalError("attribution conflict differs from original claim")
        state["conflicts"].append(row)
        state["latched"] = True
    else:
        if original is not None or state["latched"]:
            raise JournalError("attribution identity or account latch already consumed")
        proof = item["proof"]
        cid, order_id = proof["client_order_id"], proof["broker_order_id"]
        frozen = {
            key: proof[key]
            for key in (
                "entry_scope",
                "broker_order_id",
                "original_portions",
                "requested_quantity",
                "handoff_ref",
                "preparation_ref",
                "entry_reservation_ref",
                "emergency_binding_ref",
                "prior_risk_ref",
                "attempt_ref",
                "outcome_ref",
            )
        }
        previous = state["cid_claims"].get(cid)
        if previous is not None and _json(previous["binding"]) != _json(frozen):
            raise JournalError(
                "source CID immutable attribution basket or identity changed"
            )
        order = state["order_claims"].get(order_id)
        if order is not None and order["client_order_id"] != cid:
            raise JournalError("attribution order UUID already claimed")
        executions = {
            execution["activity_id"]: execution for execution in proof["executions"]
        }
        if len(executions) != len(proof["executions"]):
            raise JournalError("duplicate attribution execution identity")
        if previous is not None and not set(previous["activity_ids"]) <= set(
            executions
        ):
            raise JournalError("attribution cumulative execution set regressed")
        for activity, execution in executions.items():
            claim = {
                "client_order_id": cid,
                "broker_order_id": order_id,
                **{
                    key: execution[key]
                    for key in (
                        "content_hash",
                        "quantity",
                        "price",
                        "transaction_time",
                    )
                },
            }
            prior = state["activity_claims"].get(activity)
            if prior is not None and _json(
                {k: v for k, v in prior.items() if k != "first_ref"}
            ) != _json(claim):
                raise JournalError("attribution activity already claimed or changed")
            state["activity_claims"].setdefault(activity, claim | {"first_ref": ref})
        total = sum(
            (
                Fraction(state["activity_claims"][activity]["quantity"])
                for activity in executions
            ),
            Fraction(0),
        )
        if (
            total != Fraction(proof["cumulative_fill_quantity"])
            or not 0 <= total <= Fraction(proof["requested_quantity"])
            or (
                previous is not None
                and total < Fraction(previous["cumulative_fill_quantity"])
            )
        ):
            raise JournalError("attribution cumulative arithmetic differs")
        state["cid_claims"][cid] = {
            "binding": frozen,
            "activity_ids": sorted(executions),
            "cumulative_fill_quantity": str(total),
            "first_ref": ref if previous is None else previous["first_ref"],
            "latest_ref": ref,
        }
        state["order_claims"].setdefault(
            order_id, {"client_order_id": cid, "first_ref": ref}
        )
        state["operations"][key] = row
    state["observations"].append(row)
    state["watermark"] = ref["sequence"]
    return state


class SourceCloseAttributions:
    def __init__(self, journal: Journal, initial: OcoResearchState) -> None:
        self._initialize(journal, initial)
        self.view()

    def _initialize(self, journal: Journal, initial: OcoResearchState) -> None:
        self.journal, self.initial = journal, initial
        self.binding = _binding(journal, initial)
        self.account_ref = initial.account_ref
        self.state_key = NAMESPACE + ":" + self.account_ref

    def view(self) -> dict[str, Any]:
        return self._read(None, None)

    def read_verified_at(
        self, through_sequence: int, decision_at: datetime
    ) -> dict[str, Any]:
        if (
            type(through_sequence) is not int
            or not 0 <= through_sequence <= self.journal.read_evidence_sequence()
        ):
            raise JournalError("bounded attribution horizon required")
        return self._read(through_sequence, _utc(decision_at))

    def _read(self, horizon: int | None, at: datetime | None) -> dict[str, Any]:
        owns = not self.journal._db.in_transaction
        if owns:
            self.journal._db.execute("BEGIN")
        try:
            with causal_risk_verification_scope(self.journal):
                return self._replay(horizon, at)
        except JournalError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            raise JournalError("attribution journal evidence is malformed") from exc
        finally:
            if owns:
                self.journal._db.rollback()

    def _replay(self, horizon: int | None, at: datetime | None) -> dict[str, Any]:
        if self.journal.read_paper_account_ref() != self.account_ref:
            raise JournalError("attribution account differs")
        projected = _empty(self.account_ref)
        first: dict[str, dict[str, Any]] = {}
        previous: datetime | None = None
        unavailable = False
        fields = {
            "schema",
            "classification",
            "account_ref",
            "origin",
            "authority",
            "kind",
            "at",
            "source_horizon",
            "command",
            "dedupe_key",
            "identity_hash",
            "proof",
            "original_ref",
        }
        for event, raw in self.journal._read_private_events(SOURCE, horizon):
            if at is not None and not event.visible_at(at):
                unavailable = True
                continue
            if unavailable:
                raise JournalError("attribution prefix is not decision-visible")
            item = decode_broker_json(raw)
            if type(item) is not dict or set(item) != fields:
                raise JournalError("exact attribution event schema required")
            recorded = _clock(item["at"])
            command = item["command"]
            if type(command) is not dict or set(command) != {
                "binding",
                "identity",
                "client_order_id",
                "capture_sequences",
            }:
                raise JournalError("exact attribution command required")
            rebuilt = _command(
                _binding(self.journal, _initial(self.journal, command["binding"])),
                command["identity"],
                command["client_order_id"],
                tuple(command["capture_sequences"]),
            )
            if (
                _json(command) != _json(rebuilt)
                or _json(item) != raw
                or type(item["schema"]) is not int
                or item["schema"] != 1
                or item["classification"] != CLASSIFICATION
                or item["origin"] != ORIGIN
                or item["account_ref"] != self.account_ref
                or _json(item["authority"]) != _json(AUTHORITY)
                or any(value is not False for value in item["authority"].values())
                or item["kind"] not in {"OBSERVE", "CONFLICT"}
                or event.feed != "paper"
                or event.event_kind != EVENT_KIND
                or event.payload_ref != _digest(raw)
                or event.source_event_at is not None
                or event.provider_received_at is not None
                or event.external_clock_conflict
                or event.system_first_seen_at != recorded
                or event.processed_at != recorded
                or event.available_at != recorded
                or (previous is not None and recorded < previous)
                or type(item["source_horizon"]) is not int
                or item["source_horizon"] != event.sequence - 1
                or item["dedupe_key"] != _key(command)
                or item["identity_hash"]
                != _digest(_json(command)).removeprefix("sha256:")
            ):
                raise JournalError(
                    "attribution event identity, clock or origin differs"
                )
            previous = recorded
            if item["kind"] == "OBSERVE":
                if item["original_ref"] is not None or _json(
                    _derive(self.journal, item)
                ) != _json(item["proof"]):
                    raise JournalError("attribution original proof differs")
            elif item["proof"] is not None:
                raise JournalError(
                    "attribution conflict cannot carry an accepted proof"
                )
            ref = {"sequence": event.sequence, "hash": event.payload_ref}
            projected = _project(projected, item, ref)
            first.setdefault(
                item["dedupe_key"],
                {"sequence": event.sequence, "hash": "sha256:" + item["identity_hash"]},
            )
        actual = {}
        for key, value in self.journal._read_evidence_cache(
            DEDUPE, self.account_ref
        ).items():
            if (
                type(value) is not dict
                or set(value) != {"sequence", "hash"}
                or type(value["sequence"]) is not int
                or value["sequence"] < 1
                or type(value["hash"]) is not str
            ):
                raise JournalError("attribution first-reference map is malformed")
            if horizon is not None and value["sequence"] > horizon:
                continue
            source = self.journal.read_event(value["sequence"])
            if source is None:
                raise JournalError("attribution first-reference evidence is missing")
            if at is not None and not source[0].visible_at(at):
                continue
            actual[key] = value
        if _json(actual) != _json(first):
            raise JournalError("attribution first-reference map differs")
        if horizon is None:
            cached = self.journal.read_state(self.state_key)
            if (first and _json(cached) != _json(projected)) or (
                not first and cached is not None
            ):
                raise JournalError(
                    "attribution projection differs from immutable evidence"
                )
        return cast(dict[str, Any], _copy(projected))

    def observe(
        self,
        identity: str,
        at: datetime,
        *,
        client_order_id: str,
        capture_sequences: tuple[int, ...],
    ) -> dict[str, Any]:
        recorded = _utc(at)
        command = _command(self.binding, identity, client_order_id, capture_sequences)
        if self.journal._db.in_transaction:
            raise JournalError("attribution writer refuses caller transaction")
        item: dict[str, Any] = {}
        before: dict[str, Any] = {}
        result: dict[str, Any] = {}

        def prepare(_: bytes) -> bytes:
            nonlocal before, result
            with causal_risk_verification_scope(self.journal):
                before = self._replay(None, None)
                key = _key(command)
                original = before["operations"].get(key)
                if original is not None and _json(original["command"]) == _json(
                    command
                ):
                    if recorded < _clock(original["at"]):
                        raise JournalError("attribution retry cannot be retrodated")
                    result = original
                    return _json(
                        {k: v for k, v in original.items() if k != "observation_ref"}
                    )
                for conflict in before["conflicts"]:
                    if conflict["dedupe_key"] == key and _json(
                        conflict["command"]
                    ) == _json(command):
                        if recorded < _clock(conflict["at"]):
                            raise JournalError(
                                "attribution conflict retry cannot be retrodated"
                            )
                        raise _ConflictRetry(conflict)
                if before["latched"]:
                    raise JournalError(
                        "attribution account conflict latch blocks new observations"
                    )
                if before["observations"] and recorded < _clock(
                    before["observations"][-1]["at"]
                ):
                    raise JournalError(
                        "new attribution observation cannot be retrodated"
                    )
                item.update(
                    schema=1,
                    classification=CLASSIFICATION,
                    account_ref=self.account_ref,
                    origin=ORIGIN,
                    authority=dict(AUTHORITY),
                    kind="OBSERVE" if original is None else "CONFLICT",
                    at=recorded.isoformat(),
                    source_horizon=self.journal.read_evidence_sequence(),
                    command=command,
                    dedupe_key=key,
                    identity_hash=_digest(_json(command)).removeprefix("sha256:"),
                    proof=None,
                    original_ref=None
                    if original is None
                    else original["observation_ref"],
                )
                if original is None:
                    item["proof"] = _derive(self.journal, item)
                return _json(item)

        def reduce(old: object, conflict: bool, sequence: int) -> dict[str, Any]:
            nonlocal result
            if (
                (old is None) != (not before["observations"])
                or (old is not None and _json(old) != _json(before))
                or conflict != (item["kind"] == "CONFLICT")
            ):
                raise JournalError("attribution locked projection or identity changed")
            ref = {"sequence": sequence, "hash": _digest(_json(item))}
            projected = _project(before, item, ref)
            result = projected["observations"][-1]
            return projected

        try:
            self.journal._apply_private_event(
                account_ref=self.account_ref,
                state_key=self.state_key,
                dedupe_key=_key(command),
                event_kind=EVENT_KIND,
                payload=b"{}",
                received_at=recorded,
                reduce=reduce,
                identity_payload=_json(command),
                namespace=NAMESPACE,
                source_id=SOURCE,
                dedupe_namespace=DEDUPE,
                prepare_payload=prepare,
            )
        except _ConflictRetry as retry:
            return cast(dict[str, Any], _copy(retry.row))
        except JournalError:
            if self.journal._db.in_transaction:
                self.journal._db.rollback()
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            if self.journal._db.in_transaction:
                self.journal._db.rollback()
            raise JournalError("attribution observation evidence is malformed") from exc
        except BaseException:
            # Journal's generic helper handles Exception, while this writer also
            # owns cleanup after process-level interruption inside its callback.
            if self.journal._db.in_transaction:
                self.journal._db.rollback()
            raise
        return cast(dict[str, Any], _copy(result))


class _ReadOnlyAttributions(SourceCloseAttributions):
    def observe(self, *args: Any, **kwargs: Any) -> Never:
        raise JournalError("bounded attribution reader cannot write")

    def view(self) -> Never:
        raise JournalError("bounded attribution reader requires an explicit horizon")


def read_source_close_attributions_at(
    journal: Journal,
    initial: OcoResearchState,
    *,
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    reader = object.__new__(_ReadOnlyAttributions)
    reader._initialize(journal, initial)
    return reader.read_verified_at(through_sequence, decision_at)
