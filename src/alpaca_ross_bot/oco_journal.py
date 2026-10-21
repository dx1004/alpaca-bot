"""Private durable *declared* OCO research operations; never broker authority."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from .journal import Journal, JournalError
from .oco_entry_evidence import derive_entry_census
from .oco_research import (
    CANDIDATE,
    MANIFEST_SHA256,
    PARENT,
    PARENT_SHA256,
    VERSION,
    Execution,
    GroupAllocation,
    LotPortion,
    OcoResearchState,
    TerminalCensus,
    acknowledge_group,
    allocate_group,
    observe_execution,
    reconcile_entry_terminal,
)


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _object(value: object, names: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != names:
        raise JournalError("invalid exact OCO research schema")
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise JournalError("exact OCO research identity required")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise JournalError("exact nonnegative OCO research integer required")
    return value


def _fraction(value: object) -> Fraction:
    if type(value) is not str:
        raise JournalError("canonical OCO rational string required")
    try:
        parsed = Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise JournalError("canonical OCO rational string required") from None
    if str(parsed) != value:
        raise JournalError("canonical OCO rational string required")
    return parsed


def _list(value: object) -> list[Any]:
    if type(value) is not list:
        raise JournalError("exact OCO research list required")
    return value


def _execution(value: object) -> Execution:
    row = _object(
        value,
        {
            "execution_id",
            "account_ref",
            "symbol",
            "entry_scope",
            "role",
            "quantity",
            "price",
            "group_id",
        },
    )
    return Execution(
        _text(row["execution_id"]),
        _text(row["account_ref"]),
        _text(row["symbol"]),
        _text(row["entry_scope"]),
        row["role"],
        _fraction(row["quantity"]),
        _fraction(row["price"]),
        None if row["group_id"] is None else _text(row["group_id"]),
    )


def _execution_dict(execution: Execution) -> dict[str, object]:
    return {
        name: str(value) if type(value) is Fraction else value
        for name, value in (
            ("execution_id", execution.execution_id),
            ("account_ref", execution.account_ref),
            ("symbol", execution.symbol),
            ("entry_scope", execution.entry_scope),
            ("role", execution.role),
            ("quantity", execution.quantity),
            ("price", execution.price),
            ("group_id", execution.group_id),
        )
    }


def _portions(value: object) -> tuple[LotPortion, ...]:
    result = []
    for raw in _list(value):
        row = _object(raw, {"entry_execution_id", "quantity"})
        result.append(
            LotPortion(_text(row["entry_execution_id"]), _fraction(row["quantity"]))
        )
    if tuple(part.entry_execution_id for part in result) != tuple(
        sorted({part.entry_execution_id for part in result})
    ):
        raise JournalError("canonical disjoint OCO portion ordering required")
    return tuple(result)


def _portion_dicts(portions: tuple[LotPortion, ...]) -> list[dict[str, str]]:
    return [
        {"entry_execution_id": p.entry_execution_id, "quantity": str(p.quantity)}
        for p in portions
    ]


@dataclass(frozen=True)
class OcoJournalView:
    model: OcoResearchState
    conflict_keys: tuple[str, ...]
    watermark: int
    entry_binding: dict[str, Any] | None = None

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.model.issues)
                | ({"OPERATION_IDENTITY_CONFLICT"} if self.conflict_keys else set())
            )
        )


class _RecordedConflict(Exception):
    def __init__(self, view: OcoJournalView) -> None:
        self.view = view


class OcoJournal:
    """One immutable account/entry-scope binding; every access verifies replay.

    Raw execution observations remain caller-declared, not source-qualified. The
    private transaction owns only this research projection and deduplication cache.
    """

    def __init__(self, journal: Journal, initial: OcoResearchState) -> None:
        if type(initial) is not OcoResearchState:
            raise JournalError("empty OCO initial state required")
        initial.__post_init__()
        if initial.executions or initial.groups or initial.terminal_censuses:
            raise JournalError("empty OCO initial state required")
        self.journal = journal
        self.initial = initial
        self.binding: dict[str, object] = {
            "account_ref": initial.account_ref,
            "symbol": initial.symbol,
            "entry_scope": initial.entry_scope,
            "limit": str(initial.limit),
            "stop": str(initial.stop),
            "version": VERSION,
            "candidate": CANDIDATE,
            "manifest_sha256": MANIFEST_SHA256,
            "parent_candidate": PARENT,
            "parent_sha256": PARENT_SHA256,
        }
        self.state_key = (
            "oco_research:" + _json([initial.account_ref, initial.entry_scope]).decode()
        )
        self._record("BIND", {}, datetime.now(UTC))

    def _operation_key(
        self, kind: str, operation: object, *, pending: bool = False
    ) -> str:
        if kind == "BIND":
            _object(operation, set())
            identity = "BIND"
        elif kind == "EXECUTION":
            execution = _execution(operation)
            if (execution.account_ref, execution.symbol, execution.entry_scope) != (
                self.initial.account_ref,
                self.initial.symbol,
                self.initial.entry_scope,
            ):
                raise JournalError("OCO execution account/symbol/scope differs")
            identity = execution.execution_id
        elif kind == "ALLOCATION":
            row = _object(operation, {"group_id", "portions"})
            identity = _text(row["group_id"])
            _portions(row["portions"])
        elif kind == "ACKNOWLEDGEMENT":
            row = _object(operation, {"group_id", "acknowledgement_id"})
            identity = _text(row["group_id"])
            _text(row["acknowledgement_id"])
        elif kind == "TERMINAL_CENSUS":
            row = _object(operation, {"census_id", "entry_execution_ids"})
            identity = _text(row["census_id"])
            ids = [_text(value) for value in _list(row["entry_execution_ids"])]
            if ids != sorted(set(ids)):
                raise JournalError("canonical exact OCO terminal census required")
        elif kind == "BOUND_ENTRY":
            row = _object(
                operation, {"request", "source_horizon", "receipt", "outcome"}
            )
            request = _object(row["request"], {"event_id", "decision_at", "fee_policy"})
            identity = _text(request["event_id"])
            stamp = datetime.fromisoformat(_text(request["decision_at"]))
            if (
                stamp.utcoffset() != timedelta(0)
                or stamp.isoformat() != request["decision_at"]
            ):
                raise JournalError("canonical UTC entry-census request required")
            path = Path(_text(request["fee_policy"]))
            if not path.is_absolute() or str(path.resolve()) != request["fee_policy"]:
                raise JournalError("canonical entry-census fee path required")
            if pending:
                if (
                    row["outcome"] != "PENDING_DERIVATION"
                    or row["source_horizon"] is not None
                    or row["receipt"] is not None
                ):
                    raise JournalError("invalid pending entry-census request")
            elif (
                type(row["source_horizon"]) is not int
                or row["source_horizon"] < 0
                or type(row["receipt"]) is not dict
                or row["outcome"]
                not in {"BOUND_ENTRY_CENSUS", "REQUEST_IDENTITY_CONFLICT"}
            ):
                raise JournalError("invalid derived entry-census operation")
        else:
            raise JournalError("unsupported OCO research operation")
        return _json([self.initial.entry_scope, kind, identity]).decode()

    def _model_dict(self, model: OcoResearchState) -> dict[str, object]:
        return {
            "executions": [_execution_dict(e) for e in model.executions],
            "groups": [
                {
                    "group_id": g.group_id,
                    "portions": _portion_dicts(g.portions),
                    "execution_count": g.execution_count,
                    "acknowledgement_id": g.acknowledgement_id,
                }
                for g in model.groups
            ],
            "terminal_censuses": [
                {
                    "census_id": c.census_id,
                    "execution_count": c.execution_count,
                    "entry_execution_ids": list(c.entry_execution_ids),
                }
                for c in model.terminal_censuses
            ],
        }

    def _model(self, value: object) -> OcoResearchState:
        row = _object(value, {"executions", "groups", "terminal_censuses"})
        groups = []
        for raw in _list(row["groups"]):
            group = _object(
                raw, {"group_id", "portions", "execution_count", "acknowledgement_id"}
            )
            groups.append(
                GroupAllocation(
                    _text(group["group_id"]),
                    _portions(group["portions"]),
                    _integer(group["execution_count"]),
                    None
                    if group["acknowledgement_id"] is None
                    else _text(group["acknowledgement_id"]),
                )
            )
        censuses = []
        for raw in _list(row["terminal_censuses"]):
            census = _object(
                raw, {"census_id", "execution_count", "entry_execution_ids"}
            )
            censuses.append(
                TerminalCensus(
                    _text(census["census_id"]),
                    _integer(census["execution_count"]),
                    tuple(
                        _text(identity)
                        for identity in _list(census["entry_execution_ids"])
                    ),
                )
            )
        return OcoResearchState(
            self.initial.contract,
            self.initial.account_ref,
            self.initial.symbol,
            self.initial.entry_scope,
            self.initial.limit,
            self.initial.stop,
            tuple(_execution(raw) for raw in _list(row["executions"])),
            tuple(groups),
            tuple(censuses),
        )

    def _view(self, value: object) -> OcoJournalView:
        base = {"schema", "binding", "model", "conflict_keys", "watermark", "authority"}
        row = _object(
            value,
            base
            | (
                {"entry_binding"}
                if isinstance(value, dict) and value.get("schema") == 2
                else set()
            ),
        )
        if (
            type(row["schema"]) is not int
            or row["schema"] not in (1, 2)
            or row["binding"] != self.binding
        ):
            raise JournalError("OCO frozen binding or cache schema differs")
        conflicts = tuple(_text(key) for key in _list(row["conflict_keys"]))
        if conflicts != tuple(sorted(set(conflicts))):
            raise JournalError("OCO conflict cache differs")
        model = self._model(row["model"])
        if row["authority"] != model.authority:
            raise JournalError("OCO research authority cache differs")
        watermark = _integer(row["watermark"])
        if not watermark:
            raise JournalError("OCO cache watermark missing")
        binding = row.get("entry_binding")
        if row["schema"] == 2 and type(binding) is not dict:
            raise JournalError("source-bound entry projection missing")
        return OcoJournalView(model, conflicts, watermark, binding)

    def _bound_model(
        self,
        model: OcoResearchState,
        operation: dict[str, Any],
        prior: OcoJournalView | None,
    ) -> OcoResearchState:
        receipt = operation["receipt"]
        if prior is not None and prior.entry_binding is not None:
            old = prior.entry_binding
            if (
                receipt["decision_at"] < old["decision_at"]
                or receipt["source_horizon"] < old["source_horizon"]
                or receipt["capture_ref"][0] < old["capture_ref"][0]
                or receipt["reservation_ref"] != old["reservation_ref"]
                or receipt["entry"]["broker_order_id"]
                != old["entry"]["broker_order_id"]
            ):
                raise JournalError(
                    "entry-census source/decision/reservation regression"
                )
        executions = tuple(
            _execution(
                {key: value for key, value in row.items() if key != "transaction_time"}
            )
            for row in receipt["entry"]["executions"]
        )
        census = {execution.execution_id: execution for execution in executions}
        if any(
            census.get(execution.execution_id) != execution
            for execution in model.executions
            if execution.role == "ENTRY"
        ):
            raise JournalError("prior model entry census shrank or changed")
        if (
            not receipt["entry"]["terminal_observed"]
            and model.entry_terminal_reconciled
        ):
            raise JournalError(
                "declared terminal census contradicted by source partial entry"
            )
        for execution in executions:
            model = observe_execution(model, execution)
        if receipt["entry"]["terminal_observed"]:
            census_id = (
                "bound-entry:"
                + _json(
                    [operation["request"]["event_id"], operation["source_horizon"]]
                ).decode()
            )
            model = reconcile_entry_terminal(model, census_id, tuple(sorted(census)))
        return model

    def _reduce(
        self, old: object | None, item: dict[str, Any], conflict: bool, sequence: int
    ) -> dict[str, object]:
        prior = None if old is None else self._view(old)
        kind, operation = item["kind"], item["operation"]
        if prior is None and kind != "BIND":
            raise JournalError("OCO immutable binding missing")
        model = self.initial if prior is None else prior.model
        entry_binding = None if prior is None else prior.entry_binding
        conflicts = set(() if prior is None else prior.conflict_keys)
        if conflict:
            conflicts.add(item["dedupe_key"])
        elif kind == "EXECUTION":
            model = observe_execution(model, _execution(operation))
        elif kind == "ALLOCATION":
            if conflicts:
                raise JournalError(
                    "durable OCO identity conflict blocks new allocation"
                )
            model = allocate_group(
                model, operation["group_id"], _portions(operation["portions"])
            )
        elif kind == "ACKNOWLEDGEMENT":
            model = acknowledge_group(
                model, operation["group_id"], operation["acknowledgement_id"]
            )
        elif kind == "TERMINAL_CENSUS":
            model = reconcile_entry_terminal(
                model, operation["census_id"], tuple(operation["entry_execution_ids"])
            )
        elif kind == "BOUND_ENTRY":
            if (
                operation["outcome"] != "BOUND_ENTRY_CENSUS"
                or not 0 <= operation["source_horizon"] < sequence
            ):
                raise JournalError("entry-census source horizon/outcome differs")
            model = self._bound_model(model, operation, prior)
            entry_binding = operation["receipt"]
        elif kind != "BIND":
            raise JournalError("unsupported OCO research operation")
        result: dict[str, object] = {
            "schema": 1 if entry_binding is None else 2,
            "binding": self.binding,
            "model": self._model_dict(model),
            "conflict_keys": sorted(conflicts),
            "watermark": sequence,
            "authority": model.authority,
        }
        if entry_binding is not None:
            result["entry_binding"] = entry_binding
        return result

    def _verified_replay(
        self,
    ) -> tuple[object | None, dict[str, set[str]], dict[str, dict[str, Any]]]:
        cached = self.journal.read_state(self.state_key)
        projected: object | None = None
        first: dict[str, str] = {}
        recorded: dict[str, set[str]] = {}
        expected_cache: dict[str, object] = {}
        original_operations: dict[str, dict[str, Any]] = {}
        for event, payload in self.journal.read_oco_research_events():
            item = _object(
                json.loads(payload),
                {"schema", "binding", "kind", "operation", "dedupe_key"},
            )
            binding = _object(item["binding"], set(self.binding))
            if binding["account_ref"] != self.initial.account_ref:
                raise JournalError("foreign account in OCO research evidence")
            scope = _text(binding["entry_scope"])
            if (
                event.feed != "paper"
                or event.event_kind != item["kind"]
                or _json(item) != payload
            ):
                raise JournalError("OCO raw event metadata or canonical bytes differ")
            if scope != self.initial.entry_scope:
                continue
            if (
                binding != self.binding
                or type(item["schema"]) is not int
                or item["schema"] != 1
            ):
                raise JournalError("OCO frozen binding differs from immutable evidence")
            key = self._operation_key(item["kind"], item["operation"])
            if item["dedupe_key"] != key:
                raise JournalError("OCO operation deduplication key differs")
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            conflict = key in first and first[key] != digest
            if item["kind"] == "BOUND_ENTRY":
                operation = item["operation"]
                if event.available_at < datetime.fromisoformat(
                    operation["receipt"]["decision_at"]
                ):
                    raise JournalError(
                        "entry-census event predates its receipt decision"
                    )
                if not 0 <= operation["source_horizon"] < event.sequence:
                    raise JournalError(
                        "entry-census source horizon exceeds derived event"
                    )
                if conflict:
                    original = original_operations[key]
                    if (
                        operation["outcome"] != "REQUEST_IDENTITY_CONFLICT"
                        or operation["request"] == original["request"]
                        or _json(operation["receipt"]) != _json(original["receipt"])
                        or operation["source_horizon"] != original["source_horizon"]
                    ):
                        raise JournalError(
                            "entry-census conflict reinterprets original receipt"
                        )
                else:
                    request = operation["request"]
                    receipt = derive_entry_census(
                        self.journal,
                        self.initial,
                        decision_at=datetime.fromisoformat(request["decision_at"]),
                        through_sequence=operation["source_horizon"],
                        fee_policy=Path(request["fee_policy"]),
                    )
                    if operation["outcome"] != "BOUND_ENTRY_CENSUS" or _json(
                        receipt
                    ) != _json(operation["receipt"]):
                        raise JournalError(
                            "entry-census receipt differs from original source replay"
                        )
            if digest in recorded.get(key, set()):
                raise JournalError("duplicate OCO immutable operation evidence")
            first.setdefault(key, digest)
            recorded.setdefault(key, set()).add(digest)
            expected_cache.setdefault(key, {"hash": digest, "sequence": event.sequence})
            original_operations.setdefault(key, item["operation"])
            projected = self._reduce(projected, item, conflict, event.sequence)
        scoped: dict[str, object] = {}
        for key, value in self.journal.read_oco_research_evidence_cache(
            self.initial.account_ref
        ).items():
            identity = _list(json.loads(key))
            if (
                len(identity) != 3
                or any(type(part) is not str for part in identity)
                or _json(identity).decode() != key
            ):
                raise JournalError("invalid OCO deduplication identity")
            if identity[0] == self.initial.entry_scope:
                scoped[key] = value
        if cached is not None:
            self._view(cached)
        if _json(cached) != _json(projected) or _json(scoped) != _json(expected_cache):
            raise JournalError(
                "OCO projection or deduplication differs from immutable evidence"
            )
        return projected, recorded, original_operations

    def _record(
        self, kind: str, operation: dict[str, object], at: datetime
    ) -> OcoJournalView:
        if type(at) is not datetime or at.utcoffset() != timedelta(0):
            raise JournalError("UTC OCO receipt clock required")
        key = self._operation_key(kind, operation, pending=kind == "BOUND_ENTRY")
        item = {
            "schema": 1,
            "binding": self.binding,
            "kind": kind,
            "operation": operation,
            "dedupe_key": key,
        }
        payload = _json(item)
        result: OcoJournalView | None = None

        def prepare(raw: bytes) -> bytes:
            nonlocal result
            projected, recorded, originals = self._verified_replay()
            result = None if projected is None else self._view(projected)
            if kind == "BOUND_ENTRY":
                original = originals.get(key)
                if original is not None:
                    complete = dict(original)
                    if operation["request"] != original["request"]:
                        complete.update(
                            request=operation["request"],
                            outcome="REQUEST_IDENTITY_CONFLICT",
                        )
                else:
                    horizon = self.journal.read_evidence_sequence()
                    request = operation["request"]
                    assert isinstance(request, dict)
                    receipt = derive_entry_census(
                        self.journal,
                        self.initial,
                        decision_at=datetime.fromisoformat(str(request["decision_at"])),
                        through_sequence=horizon,
                        fee_policy=Path(str(request["fee_policy"])),
                    )
                    complete = {
                        "request": request,
                        "source_horizon": horizon,
                        "receipt": receipt,
                        "outcome": "BOUND_ENTRY_CENSUS",
                    }
                item["operation"] = complete
                if at < datetime.fromisoformat(complete["receipt"]["decision_at"]):
                    raise JournalError(
                        "entry-census receive time predates its receipt decision"
                    )
                raw = _json(item)
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            if (
                result is not None
                and digest in recorded.get(key, set())
                and key in result.conflict_keys
            ):
                raise _RecordedConflict(result)
            return raw

        def reduce(old: object | None, conflict: bool, sequence: int) -> object:
            nonlocal result
            updated = self._reduce(old, item, conflict, sequence)
            result = self._view(updated)
            return updated

        try:
            self.journal.apply_oco_research_event(
                account_ref=self.initial.account_ref,
                state_key=self.state_key,
                dedupe_key=key,
                event_kind=kind,
                payload=payload,
                received_at=at,
                prepare_payload=prepare,
                reduce=reduce,
            )
        except _RecordedConflict as duplicate:
            return duplicate.view
        if result is None:
            raise JournalError("OCO binding did not commit")
        return result

    def view(self) -> OcoJournalView:
        return self._record("BIND", {}, datetime.now(UTC))

    def verify_rebuild(self) -> bool:
        self.view()
        return True

    def bind_entry_census(
        self,
        event_id: str,
        decision_at: datetime,
        fee_policy: Path,
        received_at: datetime,
    ) -> OcoJournalView:
        if type(decision_at) is not datetime or decision_at.utcoffset() != timedelta(0):
            raise JournalError("UTC entry-census decision required")
        return self._record(
            "BOUND_ENTRY",
            {
                "request": {
                    "event_id": event_id,
                    "decision_at": decision_at.isoformat(),
                    "fee_policy": str(fee_policy.resolve()),
                },
                "source_horizon": None,
                "receipt": None,
                "outcome": "PENDING_DERIVATION",
            },
            received_at,
        )

    def observe_execution(
        self, execution: Execution, received_at: datetime
    ) -> OcoJournalView:
        if type(execution) is not Execution:
            raise JournalError("exact declared OCO execution required")
        execution.__post_init__()
        return self._record("EXECUTION", _execution_dict(execution), received_at)

    def allocate_group(
        self, group_id: str, portions: tuple[LotPortion, ...], received_at: datetime
    ) -> OcoJournalView:
        if type(portions) is not tuple or any(
            type(part) is not LotPortion for part in portions
        ):
            raise JournalError("immutable exact OCO portions required")
        return self._record(
            "ALLOCATION",
            {
                "group_id": group_id,
                "portions": _portion_dicts(
                    tuple(sorted(portions, key=lambda p: p.entry_execution_id))
                ),
            },
            received_at,
        )

    def acknowledge_group(
        self, group_id: str, acknowledgement_id: str, received_at: datetime
    ) -> OcoJournalView:
        return self._record(
            "ACKNOWLEDGEMENT",
            {"group_id": group_id, "acknowledgement_id": acknowledgement_id},
            received_at,
        )

    def reconcile_entry_terminal(
        self,
        census_id: str,
        entry_execution_ids: tuple[str, ...],
        received_at: datetime,
    ) -> OcoJournalView:
        if type(entry_execution_ids) is not tuple:
            raise JournalError("immutable exact OCO terminal IDs required")
        return self._record(
            "TERMINAL_CENSUS",
            {"census_id": census_id, "entry_execution_ids": list(entry_execution_ids)},
            received_at,
        )
