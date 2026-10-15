"""Transactional, append-only local event journal."""

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from alpaca_ross_bot.events import EventEnvelope, EventError


class JournalError(ValueError):
    """The journal cannot preserve the requested state transition."""


@dataclass(frozen=True)
class OrderHttpObservation:
    request_kind: str
    result_kind: str
    observed_at: datetime
    http_status: int | None = None
    body: bytes | None = None


class Journal:
    """Own event evidence and local state in one SQLite transaction."""

    @classmethod
    def open_private(cls, path: Path, runtime_root: Path) -> "Journal":
        """Create owner-only state without relaxing or chmodding existing paths."""
        root = runtime_root.resolve()
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise JournalError("journal path must be inside the runtime root")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        details = root.stat()
        if details.st_uid != os.getuid() or details.st_mode & 0o077:
            raise JournalError("private journal filesystem permissions are unavailable")
        resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(resolved, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        database = resolved.stat()
        if (
            not stat.S_ISREG(database.st_mode)
            or database.st_uid != os.getuid()
            or database.st_mode & 0o077
        ):
            raise JournalError("existing order journal storage is not private")
        journal = cls(resolved, root)
        try:
            journal.require_private_order_state()
        except Exception:
            journal.close()
            raise
        return journal

    def __init__(self, path: Path, runtime_root: Path) -> None:
        root = runtime_root.resolve()
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise JournalError("journal path must be inside the runtime root")
        self._runtime_root = root
        self._path = resolved
        root.mkdir(parents=True, exist_ok=True)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(resolved, timeout=5)
        try:
            self._migrate()
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
        except Exception:
            self._db.close()
            raise

    def _migrate(self) -> None:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version == 5:
                self._db.commit()
                return
            if version not in (0, 1, 2, 3, 4):
                raise JournalError("unsupported journal schema version")
            if version == 0:
                self._db.execute(
                    """CREATE TABLE events (
                    sequence INTEGER PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    payload_ref TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    source_event_at TEXT,
                    provider_received_at TEXT,
                    system_first_seen_at TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    available_at TEXT NOT NULL
                    )"""
                )
                self._db.execute(
                    "CREATE TABLE state ("
                    "key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
                )
                for action in ("UPDATE", "DELETE"):
                    self._db.execute(
                        f"CREATE TRIGGER events_no_{action.lower()} "
                        f"BEFORE {action} ON events BEGIN "
                        "SELECT RAISE(ABORT, 'events are append-only'); END"
                    )
            if version in (0, 1):
                self._db.execute(
                    """CREATE TABLE order_intents (
                        client_order_id TEXT PRIMARY KEY,
                        intent_json TEXT NOT NULL
                    )"""
                )
                for action in ("UPDATE", "DELETE"):
                    self._db.execute(
                        f"CREATE TRIGGER order_intents_no_{action.lower()} "
                        f"BEFORE {action} ON order_intents BEGIN "
                        "SELECT RAISE(ABORT, 'order intents are immutable'); END"
                    )
            if version in (0, 1, 2):
                self._db.execute(
                    """CREATE TABLE order_send_attempts (
                        client_order_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        FOREIGN KEY (client_order_id)
                            REFERENCES order_intents(client_order_id)
                    )"""
                )
                for action in ("UPDATE", "DELETE"):
                    self._db.execute(
                        f"CREATE TRIGGER order_send_attempts_no_{action.lower()} "
                        f"BEFORE {action} ON order_send_attempts BEGIN "
                        "SELECT RAISE(ABORT, 'send attempts are immutable'); END"
                    )
            if version < 4:
                self._migrate_http_observations()
            self._db.execute(
                """CREATE TABLE paper_account_verifications (
                    sequence INTEGER PRIMARY KEY,
                    account_ref TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    body BLOB NOT NULL
                )"""
            )
            for action in ("UPDATE", "DELETE"):
                self._db.execute(
                    f"CREATE TRIGGER paper_account_verifications_no_{action.lower()} "
                    f"BEFORE {action} ON paper_account_verifications BEGIN "
                    "SELECT RAISE(ABORT, 'account verifications are immutable'); END"
                )
            self._db.execute("PRAGMA user_version=5")
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _migrate_http_observations(self) -> None:
        self._db.execute(
            """CREATE TABLE order_http_observations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL,
                    request_kind TEXT NOT NULL,
                    result_kind TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    http_status INTEGER,
                    body BLOB,
                    FOREIGN KEY (client_order_id)
                        REFERENCES order_send_attempts(client_order_id)
            )"""
        )
        for action in ("UPDATE", "DELETE"):
            self._db.execute(
                f"CREATE TRIGGER order_http_observations_no_{action.lower()} "
                f"BEFORE {action} ON order_http_observations BEGIN "
                "SELECT RAISE(ABORT, 'HTTP observations are immutable'); END"
            )

    def append(
        self,
        event: EventEnvelope,
        payload: bytes,
        state_updates: Mapping[str, object] | None = None,
    ) -> None:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._append_locked(event, payload, state_updates)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def append_next(
        self,
        *,
        source_id: str,
        feed: str,
        event_kind: str,
        payload: bytes,
        system_first_seen_at: datetime,
        processed_at: datetime,
        provider_received_at: datetime | None = None,
        source_event_at: datetime | None = None,
        state_updates: Mapping[str, object] | None = None,
    ) -> EventEnvelope:
        """Assign the next durable sequence and append one observation atomically."""
        try:
            self._db.execute("BEGIN IMMEDIATE")
            sequence = self._db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events"
            ).fetchone()[0]
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            event = EventEnvelope(
                sequence=sequence,
                source_id=source_id,
                feed=feed,
                event_kind=event_kind,
                payload_ref=digest,
                source_event_at=source_event_at,
                provider_received_at=provider_received_at,
                system_first_seen_at=system_first_seen_at,
                processed_at=processed_at,
                available_at=processed_at,
            )
            self._append_locked(event, payload, state_updates)
            self._db.commit()
            return event
        except Exception:
            self._db.rollback()
            raise

    def append_next_if_state_missing(
        self,
        *,
        state_key: str,
        state_value: object,
        additional_state_updates: Mapping[str, object] | None = None,
        source_id: str,
        feed: str,
        event_kind: str,
        payload: bytes,
        system_first_seen_at: datetime,
        processed_at: datetime,
        source_event_at: datetime | None = None,
        provider_received_at: datetime | None = None,
    ) -> EventEnvelope | None:
        """Atomically append once per state key; return None after a prior insert."""
        if not state_key.strip():
            raise JournalError("state key must be nonempty")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            exists = self._db.execute(
                "SELECT 1 FROM state WHERE key=?", (state_key,)
            ).fetchone()
            if exists is not None:
                self._db.rollback()
                return None
            sequence = self._db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events"
            ).fetchone()[0]
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            event = EventEnvelope(
                sequence=sequence,
                source_id=source_id,
                feed=feed,
                event_kind=event_kind,
                payload_ref=digest,
                source_event_at=source_event_at,
                provider_received_at=provider_received_at,
                system_first_seen_at=system_first_seen_at,
                processed_at=processed_at,
                available_at=processed_at,
            )
            updates = dict(additional_state_updates or {})
            if state_key in updates:
                raise JournalError("conditional state key is duplicated")
            updates[state_key] = state_value
            self._append_locked(event, payload, updates)
            self._db.commit()
            return event
        except Exception:
            self._db.rollback()
            raise

    def _append_locked(
        self,
        event: EventEnvelope,
        payload: bytes,
        state_updates: Mapping[str, object] | None,
    ) -> None:
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if event.payload_ref != digest:
            raise JournalError("event payload reference does not match payload")
        next_sequence = self._db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events"
        ).fetchone()[0]
        if event.sequence != next_sequence:
            raise JournalError("event sequence is not the next journal sequence")
        self._db.execute(
            """INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.sequence,
                event.source_id,
                event.feed,
                event.event_kind,
                event.payload_ref,
                payload,
                _timestamp(event.source_event_at),
                _timestamp(event.provider_received_at),
                _timestamp(event.system_first_seen_at),
                _timestamp(event.processed_at),
                _timestamp(event.available_at),
            ),
        )
        for key, value in (state_updates or {}).items():
            if not key.strip():
                raise JournalError("state key must be nonempty")
            encoded = json.dumps(value, sort_keys=True, allow_nan=False)
            self._db.execute(
                """INSERT INTO state(key, value_json) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (key, encoded),
            )

    def read_event(self, sequence: int) -> tuple[EventEnvelope, bytes] | None:
        row = self._db.execute(
            "SELECT * FROM events WHERE sequence=?", (sequence,)
        ).fetchone()
        if row is None:
            return None
        payload = bytes(row[5])
        if row[4] != "sha256:" + hashlib.sha256(payload).hexdigest():
            raise JournalError("stored event payload failed integrity check")
        try:
            event = EventEnvelope(
                sequence=row[0],
                source_id=row[1],
                feed=row[2],
                event_kind=row[3],
                payload_ref=row[4],
                source_event_at=_parse(row[6]),
                provider_received_at=_parse(row[7]),
                system_first_seen_at=_parse_required(row[8]),
                processed_at=_parse_required(row[9]),
                available_at=_parse_required(row[10]),
            )
        except (EventError, ValueError) as exc:
            raise JournalError("stored event envelope failed validation") from exc
        return event, payload

    def read_state(self, key: str) -> object | None:
        row = self._db.execute(
            "SELECT value_json FROM state WHERE key=?", (key,)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def apply_order_event(
        self,
        *,
        account_ref: str,
        state_key: str,
        dedupe_key: str,
        event_kind: str,
        payload: bytes,
        received_at: datetime,
        reduce: Callable[[object | None, bool, int], object],
        identity_payload: bytes | None = None,
        prepare_payload: Callable[[bytes], bytes] | None = None,
    ) -> EventEnvelope | None:
        """Atomically deduplicate, preserve evidence and replace its projection.

        The reducer executes under the SQLite write lock. Reuse of a stable key
        with different bytes is preserved as a conflict, never as a new fill.
        """
        self.require_private_order_state()
        if not state_key.startswith("order_lifecycle:") or not dedupe_key:
            raise JournalError("invalid lifecycle state or deduplication key")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            if self.read_paper_account_ref() != account_ref:
                raise JournalError("lifecycle account is not bound to this journal")
            if prepare_payload is not None:
                payload = prepare_payload(payload)
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            identity_hash = (
                "sha256:"
                + hashlib.sha256(
                    payload if identity_payload is None else identity_payload
                ).hexdigest()
            )
            key = "order_evidence:" + account_ref + ":" + dedupe_key
            previous_evidence = self.read_state(key)
            if (
                isinstance(previous_evidence, dict)
                and previous_evidence.get("hash") == identity_hash
            ):
                self._db.rollback()
                return None
            sequence = self._db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM events"
            ).fetchone()[0]
            projected = reduce(
                self.read_state(state_key), previous_evidence is not None, sequence
            )
            event = EventEnvelope(
                sequence=sequence,
                source_id="ALPACA_ORDER_LIFECYCLE",
                feed="paper",
                event_kind=event_kind,
                payload_ref=digest,
                source_event_at=None,
                provider_received_at=None,
                system_first_seen_at=received_at,
                processed_at=received_at,
                available_at=received_at,
            )
            updates = {state_key: projected}
            if previous_evidence is None:
                updates[key] = {"hash": identity_hash, "sequence": sequence}
            self._append_locked(event, payload, updates)
            self._db.commit()
            return event
        except Exception:
            self._db.rollback()
            raise

    def read_order_events(
        self, *, through_sequence: int | None = None
    ) -> tuple[tuple[EventEnvelope, bytes], ...]:
        """Read immutable private lifecycle evidence at a fixed watermark."""
        self.require_private_order_state()
        sequences = self._db.execute(
            "SELECT sequence FROM events WHERE source_id='ALPACA_ORDER_LIFECYCLE' "
            "AND (? IS NULL OR sequence<=?) ORDER BY sequence",
            (through_sequence, through_sequence),
        ).fetchall()
        result = []
        for (sequence,) in sequences:
            item = self.read_event(sequence)
            if item is None:
                raise JournalError("lifecycle evidence disappeared")
            result.append(item)
        return tuple(result)

    def reserve_order_intent(
        self, client_order_id: str, intent: Mapping[str, object]
    ) -> None:
        """Durably reserve one client ID before any external submission.

        This local primitive does not authorize or submit an order. The eventual
        adapter must reconcile a reserved ID before attempting a network retry.
        """
        if not client_order_id or not client_order_id.strip():
            raise JournalError("client order ID must be nonempty")
        self.require_private_order_state()
        if not intent:
            raise JournalError("order intent must be nonempty")
        encoded = json.dumps(intent, sort_keys=True, allow_nan=False)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            bound = self._db.execute(
                "SELECT account_ref FROM paper_account_verifications LIMIT 1"
            ).fetchone()
            if (
                bound is not None
                and "paper_account_ref" in intent
                and intent["paper_account_ref"] != bound[0]
            ):
                raise JournalError("order intent differs from bound paper account")
            self._db.execute(
                "INSERT INTO order_intents(client_order_id, intent_json) VALUES (?, ?)",
                (client_order_id, encoded),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise JournalError("client order ID is already reserved") from exc
        except Exception:
            self._db.rollback()
            raise

    def read_order_intent(self, client_order_id: str) -> object | None:
        row = self._db.execute(
            "SELECT intent_json FROM order_intents WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def require_private_order_state(self) -> None:
        """Reject account/order state stored outside an owner-only local path."""
        root = self._runtime_root.stat()
        database = self._path.stat()
        if (
            not stat.S_ISDIR(root.st_mode)
            or not stat.S_ISREG(database.st_mode)
            or root.st_uid != os.getuid()
            or database.st_uid != os.getuid()
            or root.st_mode & 0o077
            or database.st_mode & 0o077
        ):
            raise JournalError("order journal storage is not private")

    def record_paper_account_verification(
        self, account_ref: str, body: bytes, verified_at: datetime
    ) -> None:
        """Bind one journal to one authenticated account; reject account changes."""
        self.require_private_order_state()
        if not account_ref.strip() or not body:
            raise JournalError("account verification evidence is missing")
        if verified_at.tzinfo is None or verified_at.utcoffset() != timedelta(0):
            raise JournalError("account verification timestamp must be UTC")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                "SELECT account_ref FROM paper_account_verifications LIMIT 1"
            ).fetchone()
            if row is not None and row[0] != account_ref:
                raise JournalError("paper account changed for this journal")
            if row is None:
                for (encoded,) in self._db.execute(
                    "SELECT intent_json FROM order_intents"
                ):
                    intent = json.loads(encoded)
                    if (
                        isinstance(intent, dict)
                        and "paper_account_ref" in intent
                        and intent["paper_account_ref"] != account_ref
                    ):
                        raise JournalError("paper account differs from reserved intent")
            self._db.execute(
                "INSERT INTO paper_account_verifications"
                "(account_ref, verified_at, body) VALUES (?, ?, ?)",
                (account_ref, verified_at.isoformat(), body),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def read_paper_account_ref(self) -> str | None:
        self.require_private_order_state()
        row = self._db.execute(
            "SELECT account_ref FROM paper_account_verifications LIMIT 1"
        ).fetchone()
        return None if row is None else str(row[0])

    def read_order_intent_ids(self) -> tuple[str, ...]:
        self.require_private_order_state()
        return tuple(
            row[0]
            for row in self._db.execute(
                "SELECT client_order_id FROM order_intents ORDER BY client_order_id"
            )
        )

    def mark_order_send_attempt(
        self, client_order_id: str, started_at: datetime
    ) -> None:
        """Commit the single allowed send attempt before handing bytes to transport.

        Even an uncertain lookup or a restarted process cannot clear this marker.
        A later paper adapter must never POST when this marker already exists.
        """
        if not client_order_id or not client_order_id.strip():
            raise JournalError("client order ID must be nonempty")
        if started_at.tzinfo is None or started_at.utcoffset() != timedelta(0):
            raise JournalError("send attempt timestamp must be UTC")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            cursor = self._db.execute(
                """INSERT INTO order_send_attempts(client_order_id, started_at)
                SELECT ?, ? WHERE EXISTS (
                    SELECT 1 FROM order_intents WHERE client_order_id=?
                )""",
                (client_order_id, started_at.isoformat(), client_order_id),
            )
            if cursor.rowcount != 1:
                raise JournalError("order intent must be reserved before sending")
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise JournalError("order send was already attempted") from exc
        except Exception:
            self._db.rollback()
            raise

    def has_order_send_attempt(self, client_order_id: str) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM order_send_attempts WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            is not None
        )

    def record_order_http_observation(
        self, client_order_id: str, observation: OrderHttpObservation
    ) -> None:
        """Append transport evidence; never turn it into retry authority."""
        self.require_private_order_state()
        if observation.request_kind not in ("POST", "GET"):
            raise JournalError("invalid order HTTP request kind")
        if (
            observation.observed_at.tzinfo is None
            or observation.observed_at.utcoffset() != timedelta(0)
        ):
            raise JournalError("order HTTP observation timestamp must be UTC")
        if observation.result_kind == "HTTP_RESPONSE":
            if (
                type(observation.http_status) is not int
                or not 100 <= observation.http_status <= 599
                or not isinstance(observation.body, bytes)
            ):
                raise JournalError("invalid order HTTP response evidence")
        elif observation.result_kind == "TRANSPORT_UNKNOWN":
            if observation.http_status is not None or observation.body is not None:
                raise JournalError("unknown order outcome cannot have a response")
        else:
            raise JournalError("invalid order HTTP result kind")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            cursor = self._db.execute(
                """INSERT INTO order_http_observations(
                    client_order_id, request_kind, result_kind, observed_at,
                    http_status, body
                ) SELECT ?, ?, ?, ?, ?, ? WHERE EXISTS (
                    SELECT 1 FROM order_send_attempts WHERE client_order_id=?
                )""",
                (
                    client_order_id,
                    observation.request_kind,
                    observation.result_kind,
                    observation.observed_at.isoformat(),
                    observation.http_status,
                    observation.body,
                    client_order_id,
                ),
            )
            if cursor.rowcount != 1:
                raise JournalError("order send attempt must precede HTTP evidence")
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def read_order_http_observations(
        self, client_order_id: str
    ) -> tuple[OrderHttpObservation, ...]:
        self.require_private_order_state()
        rows = self._db.execute(
            """SELECT request_kind, result_kind, observed_at, http_status, body
            FROM order_http_observations WHERE client_order_id=? ORDER BY sequence""",
            (client_order_id,),
        ).fetchall()
        return tuple(
            OrderHttpObservation(
                request_kind=row[0],
                result_kind=row[1],
                observed_at=datetime.fromisoformat(row[2]),
                http_status=row[3],
                body=row[4],
            )
            for row in rows
        )

    def close(self) -> None:
        self._db.close()


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _parse_required(value: str | None) -> datetime:
    if value is None:
        raise JournalError("stored event is missing a required timestamp")
    return datetime.fromisoformat(value)
