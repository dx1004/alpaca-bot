"""Ephemeral risk proofs in one synchronous SQLite transaction scope.

Historical prefixes and successful current consistency checks have separate keys.
Every hit retains storage, account, input-file and mutation checks. Scope owners
must not commit, roll back, or re-enter a transaction before leaving the scope.
No risk eligibility or dispatch authority is cached.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .journal import Journal, JournalError

_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_INPUTS = (
    _ROOT / "evidence/oco-first-execution-research-v1.json",
    _ROOT / "evidence/iex-snapshot-observation-age-research-v1.json",
    _ROOT / "evidence/crossed-stop-residual-exit-research-v1.json",
    _ROOT / "evidence/alpaca-reported-intraday-price-policy-v1.json",
)
_Result = tuple[dict[str, Any] | None, tuple[int, ...]]
_MemoResult = tuple[dict[str, Any] | bool | None, tuple[int, ...]]
_Epoch = tuple[int, int, int, int, int]
_Key = tuple[object, ...]
_DiskInput = tuple[str, str, str]
_PathInput = tuple[str, str]


@dataclass(frozen=True)
class _Cached:
    payload: bytes
    refs: tuple[int, ...]
    dependencies: tuple[_DiskInput, ...]
    resolutions: tuple[_PathInput, ...]


@dataclass
class _Frame:
    files: dict[str, tuple[str, str]]
    paths: dict[str, str] = field(default_factory=dict)


@dataclass
class _Scope:
    journal: Journal
    connection: sqlite3.Connection
    closed: bool = False
    epoch: _Epoch | None = None
    values: dict[_Key, _Cached] = field(default_factory=dict)
    inflight: set[_Key] = field(default_factory=set)
    frames: list[_Frame] = field(default_factory=list)


_ACTIVE: ContextVar[_Scope | None] = ContextVar(
    "causal_risk_verification", default=None
)


def _epoch(journal: Journal) -> _Epoch:
    journal.require_private_order_state()
    connection = journal._db
    if not connection.in_transaction:
        raise JournalError("causal risk memo requires the existing transaction")
    return (
        id(connection),
        connection.total_changes,
        journal.read_evidence_sequence(),
        int(connection.execute("PRAGMA schema_version").fetchone()[0]),
        int(connection.execute("PRAGMA data_version").fetchone()[0]),
    )


def _inputs(paths: tuple[Path, ...]) -> tuple[_DiskInput, ...]:
    # Include original paths as well as resolved targets to detect retargeting.
    return tuple(
        (
            str(path.absolute()),
            str(path.resolve(strict=True)),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in paths
    )


def _resolutions(paths: tuple[Path, ...]) -> tuple[_PathInput, ...]:
    # Scope roots may legitimately not exist yet; resolution does not add a
    # new existence/stat requirement to the original isolation validator.
    return tuple((str(path.absolute()), str(path.resolve())) for path in paths)


def _remember_files(scope: _Scope, inputs: tuple[_DiskInput, ...]) -> None:
    for frame in scope.frames:
        for path, target, digest in inputs:
            value = target, digest
            if path in frame.files and frame.files[path] != value:
                raise JournalError("causal risk input changed during verification")
            frame.files[path] = value


def _remember_paths(scope: _Scope, inputs: tuple[_PathInput, ...]) -> None:
    for frame in scope.frames:
        for path, target in inputs:
            if path in frame.paths and frame.paths[path] != target:
                raise JournalError("causal risk path changed during verification")
            frame.paths[path] = target


def record_path_resolution(journal: Journal, path: Path, expected_target: Path) -> None:
    """Retain a path actually validated by a successful native scope proof."""
    scope = _ACTIVE.get()
    if (
        scope is None
        or scope.closed
        or scope.journal is not journal
        or not scope.frames
    ):
        return
    observed = _resolutions((path,))
    if observed[0][1] != str(expected_target):
        raise JournalError("causal risk scope path changed during verification")
    _remember_paths(scope, observed)


@contextmanager
def causal_risk_verification_scope(journal: Journal) -> Iterator[None]:
    """Enter inside BEGIN; nested same-journal verification shares only this root."""
    _epoch(journal)
    active = _ACTIVE.get()
    if active is not None and not active.closed and active.journal is journal:
        if active.connection is not journal._db:
            raise JournalError("causal risk verification connection changed")
        yield
        return
    scope = _Scope(journal, journal._db)
    token = _ACTIVE.set(scope)
    try:
        yield
    finally:
        scope.closed = True
        scope.values.clear()
        scope.inflight.clear()
        scope.frames.clear()
        _ACTIVE.reset(token)


def memoized_causal_risk_prefix(
    journal: Journal,
    *,
    account_ref: str,
    version: str,
    decision_at: datetime,
    fee_policy: Path,
    through_sequence: int,
    rebuild: Callable[[], _Result],
) -> _Result:
    """Memoize qualified prefixes; keep the reader's basic checks before lookup."""
    state, refs = _memoized_risk_proof(
        journal,
        key=("causal", account_ref, version, decision_at.isoformat(), through_sequence),
        fee_policy=fee_policy,
        rebuild=rebuild,
    )
    if state is not None and not isinstance(state, dict):
        raise JournalError("invalid memoized causal risk result")
    return state, refs


def memoized_current_risk_verification(
    journal: Journal,
    *,
    account_ref: str,
    version: str,
    state_key: str,
    fee_policy: Path,
    verifier: tuple[object, ...],
    rebuild: Callable[[], bool],
) -> bool:
    """Reuse only consistency True, never a risk state or qualification decision."""
    scope = _ACTIVE.get()
    if scope is None or scope.closed or scope.journal is not journal:
        return rebuild()
    if journal.read_paper_account_ref() != account_ref:
        raise JournalError("source risk account is not bound")
    verified, _ = _memoized_risk_proof(
        journal,
        key=("current", account_ref, version, state_key, verifier),
        fee_policy=fee_policy,
        rebuild=lambda: (rebuild(), ()),
    )
    return verified is True


def _memoized_risk_proof(
    journal: Journal,
    *,
    key: _Key,
    fee_policy: Path,
    rebuild: Callable[[], _MemoResult],
) -> _MemoResult:
    scope = _ACTIVE.get()
    if scope is None or scope.closed or scope.journal is not journal:
        return rebuild()
    if scope.connection is not journal._db:
        raise JournalError("causal risk verification connection changed")
    dependencies = _inputs((fee_policy, *_CONTRACT_INPUTS))
    epoch = _epoch(journal)
    if scope.epoch != epoch:
        if scope.inflight:
            raise JournalError("causal risk source changed during verification")
        scope.values.clear()
        scope.epoch = epoch
    key = (*key, dependencies, epoch)
    _remember_files(scope, dependencies)
    cached = scope.values.get(key)
    if cached is not None:
        current = _inputs(tuple(Path(item[0]) for item in cached.dependencies))
        paths = _resolutions(tuple(Path(item[0]) for item in cached.resolutions))
        if _epoch(journal) != epoch:
            scope.values.clear()
            raise JournalError("causal risk source changed during verification")
        if current == cached.dependencies and paths == cached.resolutions:
            _remember_files(scope, current)
            _remember_paths(scope, paths)
            state = json.loads(cached.payload)
            if state is not None and not isinstance(state, (dict, bool)):
                raise JournalError("invalid memoized risk proof")
            return state, cached.refs
        del scope.values[key]
    if key in scope.inflight:
        raise JournalError("causal risk verification dependency cycle")
    scope.inflight.add(key)
    frame = _Frame({path: (target, digest) for path, target, digest in dependencies})
    scope.frames.append(frame)
    try:
        state, refs = rebuild()
        closure = tuple((path, *frame.files[path]) for path in sorted(frame.files))
        paths = tuple((path, frame.paths[path]) for path in sorted(frame.paths))
        current = _inputs(tuple(Path(item[0]) for item in closure))
        resolved = _resolutions(tuple(Path(item[0]) for item in paths))
        if current != closure or resolved != paths or _epoch(journal) != epoch:
            scope.values.clear()
            raise JournalError("causal risk source changed during verification")
        # Preserve insertion order as well as values; consumers can construct
        # canonical arrays by iterating these dictionaries.
        payload = json.dumps(state, separators=(",", ":"), allow_nan=False).encode()
        copied = json.loads(payload)
        if copied is not None and not isinstance(copied, (dict, bool)):
            raise JournalError("invalid risk proof result")
        _remember_files(scope, closure)
        _remember_paths(scope, paths)
        verified = (
            state is True
            or state is None
            or (
                isinstance(state, dict)
                and state.get("source_snapshot_qualified") is True
                and isinstance(state.get("last_snapshot"), dict)
                and state["last_snapshot"].get("qualified") is True
                and state.get("veto") is False
            )
        )
        if verified:
            scope.values[key] = _Cached(payload, tuple(refs), closure, paths)
        return copied, tuple(refs)
    finally:
        scope.inflight.discard(key)
        scope.frames.pop()
