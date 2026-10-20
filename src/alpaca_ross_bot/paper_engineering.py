"""Isolated engineering recovery; no exposure or protective-exit dispatch."""

import hashlib
import json
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .journal import Journal, JournalError
from .order_reconciliation import OrderReconciler, ReconciliationPolicy
from .paper_order_http import PaperOrderHttp
from .paper_order_recovery import collect_rest_batch


@dataclass(frozen=True, repr=False)
class EngineeringScope:
    experiment_id: str
    account_ref: str
    formal_account_ref: str
    formal_runtime_root: Path

    def validate(self, root: Path) -> None:
        for value in (self.experiment_id, self.account_ref, self.formal_account_ref):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("engineering scope identity is missing")
        if self.account_ref == self.formal_account_ref:
            raise ValueError("engineering and formal accounts must differ")
        formal = self.formal_runtime_root.resolve()
        actual = root.resolve()
        if actual.is_relative_to(formal) or formal.is_relative_to(actual):
            raise ValueError("engineering and formal storage must not overlap")

    def payload(self) -> bytes:
        data = asdict(self)
        data["formal_runtime_root"] = str(self.formal_runtime_root.resolve())
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


class EngineeringHarness:
    """Retain experiment identity and recover all persisted intents without replay.

    The account references must come from separately authenticated account
    evidence. This harness checks the engineering account again before recovery;
    optional formal authentication must use a second API call, never a supplied
    flag or an old sidecar proof. Neither path grants orders or a formal baseline.
    """

    def __init__(
        self, root: Path, scope: EngineeringScope, *, clock: Callable[[], datetime]
    ) -> None:
        scope.validate(root)
        self.root = root.resolve()
        self.clock = clock
        self.scope = scope
        self.check_storage("journal.sqlite3")
        self.journal = Journal.open_private(root / "journal.sqlite3", root)
        try:
            now = self.now()
            payload = scope.payload()
            events = self.journal.read_engineering_events()
            if not events:
                # Never relabel an existing formal or unscoped order journal.
                if (
                    self.journal.read_event(1) is not None
                    or self.journal.read_paper_account_ref() is not None
                    or self.journal.read_order_intent_ids()
                    or self.journal.read_order_events()
                ):
                    raise JournalError("cannot adopt an existing unscoped journal")
                self.journal.append_next_if_state_missing(
                    state_key="engineering_scope",
                    state_value=hashlib.sha256(payload).hexdigest(),
                    source_id="PAPER_ENGINEERING_HARNESS",
                    feed="paper",
                    event_kind="SCOPE",
                    payload=payload,
                    system_first_seen_at=now,
                    processed_at=now,
                )
            self.verify_scope()
        except BaseException:
            self.journal.close()
            raise

    def now(self) -> datetime:
        at = self.clock()
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError("engineering clock must be UTC")
        return at

    def verify_scope(self) -> None:
        self.scope.validate(self.root)
        self.check_storage("journal.sqlite3")
        self.journal.require_private_order_state()
        events = self.journal.read_engineering_events()
        if not events:
            raise JournalError("engineering scope evidence is missing")
        event, raw = events[0]
        if (
            event.event_kind != "SCOPE"
            or event.feed != "paper"
            or raw != self.scope.payload()
            or event.available_at > self.now()
            or self.journal.read_state("engineering_scope")
            != hashlib.sha256(raw).hexdigest()
            or any(item.event_kind == "SCOPE" for item, _ in events[1:])
        ):
            raise JournalError("engineering scope evidence conflicts")
        reference = self.journal.read_paper_account_ref()
        if reference is not None and reference != self.scope.account_ref:
            raise JournalError("engineering account binding conflicts")

    def check_storage(self, name: str) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = self.root / (name + suffix)
            if path.is_symlink():
                raise JournalError("engineering storage must not use aliases")
            if path.exists():
                details = path.stat()
                if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                    raise JournalError("engineering storage must have one link")

    def verify_formal_account(self, api: PaperOrderHttp) -> None:
        """Authenticate the second account in separate protected evidence storage."""
        self.verify_scope()
        name = "formal-account-proof.sqlite3"
        self.check_storage(name)
        proof = Journal.open_private(self.root / name, self.root)
        try:
            api.verify_account(proof, expected_reference=self.scope.formal_account_ref)
        finally:
            proof.close()

    def recover(
        self,
        api: PaperOrderHttp,
        policy: ReconciliationPolicy,
        *,
        formal_api: PaperOrderHttp | None = None,
    ) -> dict[str, object]:
        """One bounded GET-only capture; unknown orders are never submitted again."""
        self.verify_scope()
        if formal_api is not None:
            # A previous sidecar proof is never a substitute for this call.
            self.verify_formal_account(formal_api)
        api.verify_account(self.journal, expected_reference=self.scope.account_ref)
        lifecycle = OrderReconciler(
            self.journal, self.scope.account_ref, clock=self.now
        )
        batch = collect_rest_batch(api, lifecycle, policy, clock=self.now)
        report = lifecycle.apply_rest_batch(batch, policy)
        if not lifecycle.verify_rebuild():
            raise JournalError("engineering recovery rebuild failed")
        # No stream subscription is fabricated. A disconnected capture cannot
        # authorize resumption, and even an empty book is only a REST observation.
        result: dict[str, object] = {
            "status": "RECOVERY_REQUIRED",
            "capture_complete": batch.complete,
            "capture_stable": batch.stable,
            "reconciliation_status": report.status,
            "difference_codes": sorted({row["code"] for row in report.differences}),
            "observed_empty_book": (
                batch.complete
                and batch.stable
                and batch.positions == ()
                and batch.open_orders == ()
            ),
            "formal_account_isolation_authenticated": formal_api is not None,
            # This proves distinct authenticated identities and configured paths,
            # not the formal ledger's baseline, contents or release readiness.
            "formal_starting_state_qualified": False,
            "intent_count": len(self.journal.read_order_intent_ids()),
            "send_attempt_count": sum(
                self.journal.has_order_send_attempt(cid)
                for cid in self.journal.read_order_intent_ids()
            ),
            "rebuild_verified": True,
            "broker_writes_performed": False,
            "runtime_authorized": False,
            "cleanup_accepted": False,
        }
        now = self.now()
        self.journal.append_next(
            source_id="PAPER_ENGINEERING_HARNESS",
            feed="paper",
            event_kind="RECOVERY_ASSESSMENT",
            payload=json.dumps(result, sort_keys=True).encode(),
            system_first_seen_at=now,
            processed_at=now,
        )
        return result

    def close(self) -> None:
        self.journal.close()

    def cancel_entry_once(
        self, api: PaperOrderHttp, client_order_id: str, *, formal_api: PaperOrderHttp
    ) -> None:
        """Explicit owned-entry cancellation only; not flat/cleanup acceptance.

        Never called by recovery or startup. Existing transport independently
        rejects protective sells and persists its one-attempt marker before HTTP.
        """
        self.verify_scope()
        self.verify_formal_account(formal_api)
        api.verify_account(self.journal, expected_reference=self.scope.account_ref)
        api.cancel_engineering_entry_once(self.journal, client_order_id, self.now())
