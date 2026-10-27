"""Versioned native ownership inputs for the shared accounting owner.

This internal registry covers reported facts, not strategy, protection or orders.
The risk reducer checks its locked context against the actual preceding risk slot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from ._causal_risk_memo import causal_risk_verification_scope
from .journal import Journal, JournalError
from .paper_order_contract import decode_broker_json

POLICY = "NATIVE_ACCOUNT_RISK_OBSERVATION_V1"
TERMINAL = {"filled", "canceled", "expired", "rejected"}


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass(frozen=True, repr=False)
class NativeRiskRegistry:
    context: dict[str, Any]
    orders: tuple[Any, ...]
    fills: tuple[Any, ...]
    source_refs: tuple[int, ...]
    exit_by_scope: tuple[tuple[str, Fraction], ...]
    final_scopes: tuple[str, ...]
    census_refs: tuple[tuple[str, tuple[tuple[int, str], ...]], ...]
    issues: tuple[str, ...]
    risk_issues: tuple[str, ...]
    origin: str

    def covers_difference(
        self,
        difference: dict[str, str],
        simple_expected: dict[str, Fraction],
        observed: dict[str, Fraction],
    ) -> bool:
        if self.issues:
            return False
        if difference.get("code") == "EXTERNAL_ORDER":
            return difference.get("subject") in {
                order.client_order_id
                for order in self.orders
                if order.status not in TERMINAL
            }
        if difference.get("code") == "POSITION_MISMATCH":
            symbol = difference.get("subject", "")
            native_exit = sum(
                (fill.quantity for fill in self.fills if fill.symbol == symbol),
                Fraction(),
            )
            try:
                return (
                    native_exit > 0
                    and Fraction(difference["expected"])
                    == simple_expected.get(symbol, Fraction())
                    and Fraction(difference["observed"])
                    == observed.get(symbol, Fraction())
                    and simple_expected.get(symbol, Fraction()) - native_exit
                    == observed.get(symbol, Fraction())
                )
            except (ValueError, KeyError):
                return False
        return False

    def covers_batch(self, batch: object, observed: dict[str, Fraction]) -> bool:
        from .native_oco_evidence import _order

        if (
            not isinstance(batch, dict)
            or not isinstance(batch.get("open_orders"), list)
            or not isinstance(batch.get("positions"), list)
        ):
            return False
        try:
            positions = {
                row["symbol"]: Fraction(row["qty"])
                for row in batch["positions"]
                if row["side"] == "long" and row["asset_class"] == "us_equity"
            }
            if positions != observed or len(positions) != len(batch["positions"]):
                return False
            known = {order.order_id: order for order in self.orders}
            seen: set[str] = set()
            for row in batch["open_orders"]:
                if row.get("id") in known:
                    order = _order(row)
                    if (
                        order != known[order.order_id]
                        or order.status in TERMINAL
                        or order.order_id in seen
                    ):
                        return False
                    seen.add(order.order_id)
            return seen == {
                order.order_id for order in self.orders if order.status not in TERMINAL
            }
        except (ValueError, KeyError, TypeError):
            return False


def derive_native_registry(
    journal: Journal,
    context: dict[str, Any],
    *,
    capture_sequence: int,
    decision_at: datetime,
    max_age: timedelta,
    through_sequence: int,
) -> NativeRiskRegistry:
    owns = not journal._db.in_transaction
    if owns:
        journal._db.execute("BEGIN")
    try:
        with causal_risk_verification_scope(journal):
            return _derive_native_registry(
                journal,
                context,
                capture_sequence=capture_sequence,
                decision_at=decision_at,
                max_age=max_age,
                through_sequence=through_sequence,
            )
    finally:
        if owns:
            journal._db.rollback()


def _derive_native_registry(
    journal: Journal,
    context: dict[str, Any],
    *,
    capture_sequence: int,
    decision_at: datetime,
    max_age: timedelta,
    through_sequence: int,
) -> NativeRiskRegistry:
    # Imports are deliberately deferred: entry-census replay depends on the risk
    # owner, while this registry must not recursively instantiate either owner.
    from .native_oco_evidence import _fill, _instant, _order, inspect_native_oco_capture
    from .native_oco_http import native_http_inventory
    from .oco_entry_evidence import (
        _derive_native_entry_census,
        derive_native_owned_entry_census,
    )
    from .oco_exit_evidence import derive_native_exit_census
    from .oco_journal import OcoJournal
    from .oco_research import (
        CANDIDATE,
        MANIFEST_SHA256,
        PARENT,
        PARENT_SHA256,
        VERSION,
        OcoResearchState,
        load_oco_research_contract,
    )
    from .risk_ledger_audit import _activities

    if context.get("policy") != POLICY:
        raise JournalError("versioned native risk context required")
    stored = journal.read_event(capture_sequence)
    if (
        stored is None
        or capture_sequence > through_sequence
        or not stored[0].visible_at(decision_at)
    ):
        raise JournalError("native risk account evidence unavailable")
    capture = decode_broker_json(stored[1])
    if (
        not isinstance(capture, dict)
        or capture.get("schema") != "PAPER_RISK_INPUT_CAPTURE_V2"
        or capture.get("transport_origin")
        not in {"BUILTIN_PAPER_HTTPS", "INJECTED_EXCHANGE"}
    ):
        raise JournalError(
            "native accounting requires V2 origin-qualified account capture"
        )
    origin = str(capture["transport_origin"])
    if capture.get("account_ref") != journal.read_paper_account_ref():
        raise JournalError("native account capture binding differs")
    refs = {capture_sequence}
    issues: set[str] = set()
    risk_issues: set[str] = set()
    orders: dict[str, Any] = {}
    fills: dict[str, Any] = {}
    exits: dict[str, Fraction] = {}
    finals: set[str] = set()
    census_refs: dict[str, tuple[tuple[int, str], ...]] = {}
    episodes = context.get("episodes")
    if type(episodes) is not list or not episodes:
        raise JournalError("native risk episode context missing")
    scoped = {episode["entry_scope"]: episode for episode in episodes}
    if len(scoped) != len(episodes):
        raise JournalError("duplicate native risk episode")
    account = journal.read_paper_account_ref()
    http = native_http_inventory(journal)
    if any(
        row["precall"]["binding"]["entry_scope"] not in scoped
        for row in http
        if row["attempt_ref"]["sequence"] <= through_sequence
    ):
        issues.add("NATIVE_ACCOUNT_SCOPE_UNCOVERED")
    all_events = journal.read_oco_research_events()
    for scope, episode in scoped.items():
        own = []
        for event, raw in all_events:
            if event.sequence > through_sequence:
                continue
            try:
                decoded = json.loads(raw)
                if not isinstance(decoded, dict) or not isinstance(
                    decoded.get("binding"), dict
                ):
                    raise ValueError("OCO object binding required")
                event_scope = decoded["binding"]["entry_scope"]
            except (ValueError, KeyError, TypeError) as exc:
                raise JournalError("native OCO raw event malformed") from exc
            if event_scope == scope:
                own.append((event, decoded, raw))
        if not own:
            if any(row["precall"]["binding"]["entry_scope"] == scope for row in http):
                issues.add("NATIVE_OWNER_SOURCE_MISSING")
            continue
        binding = {
            "account_ref": account,
            "symbol": episode["symbol"],
            "entry_scope": scope,
            "limit": str(Fraction(episode["limit"])),
            "stop": str(Fraction(episode["stop"])),
            "version": VERSION,
            "candidate": CANDIDATE,
            "manifest_sha256": MANIFEST_SHA256,
            "parent_candidate": PARENT,
            "parent_sha256": PARENT_SHA256,
        }
        if (
            binding["account_ref"] != account
            or binding["symbol"] != episode["symbol"]
            or Fraction(binding["limit"]) != Fraction(episode["limit"])
            or Fraction(binding["stop"]) != Fraction(episode["stop"])
        ):
            raise JournalError("native risk frozen entry identity differs")
        initial = OcoResearchState(
            load_oco_research_contract(),
            str(account),
            binding["symbol"],
            scope,
            Fraction(binding["limit"]),
            Fraction(binding["stop"]),
        )
        # Reuse the deterministic model reducer without BIND writes or recursive
        # source-entry/risk requalification. All native receipts below get their
        # own independent source replay; entry facts are anchored to the slot.
        reader = object.__new__(OcoJournal)
        reader.journal, reader.initial, reader.binding = journal, initial, binding
        reader.state_key = "oco_research:" + _json([account, scope]).decode()
        projected: object = None
        first: dict[str, str] = {}
        recorded: dict[str, set[str]] = {}
        expected_cache: dict[str, Any] = {}
        latest: dict[str, tuple[int, str, dict[str, Any]]] = {}
        entry_receipt = None
        originals: dict[str, dict[str, Any]] = {}
        for event, item, raw in own:
            item = reader._decode_operation(event, raw)
            if (
                not event.visible_at(decision_at)
                or event.external_clock_conflict
                or _json(item) != raw
                or item["binding"] != binding
            ):
                raise JournalError("native owner prefix unavailable")
            key = reader._operation_key(item["kind"], item["operation"])
            if item["dedupe_key"] != key:
                raise JournalError("native owner operation identity differs")
            if event.payload_ref in recorded.setdefault(key, set()):
                raise JournalError("duplicate OCO immutable operation evidence")
            recorded[key].add(event.payload_ref)
            conflict = key in first and first[key] != event.payload_ref
            if conflict and item["kind"] in {"BOUND_ENTRY", "BOUND_NATIVE_EXIT"}:
                original = originals[key]
                operation = item["operation"]
                if (
                    operation["outcome"] != "REQUEST_IDENTITY_CONFLICT"
                    or operation["request"] == original["request"]
                    or _json(operation["receipt"]) != _json(original["receipt"])
                    or operation["source_horizon"] != original["source_horizon"]
                ):
                    raise JournalError(
                        "native owner conflict reinterprets source receipt"
                    )
            first.setdefault(key, event.payload_ref)
            originals.setdefault(key, item["operation"])
            expected_cache.setdefault(
                key, {"hash": event.payload_ref, "sequence": event.sequence}
            )
            if item["kind"] == "BOUND_ENTRY" and not conflict:
                entry_receipt = item["operation"]["receipt"]
                operation = item["operation"]
                if not 0 <= operation["source_horizon"] < event.sequence:
                    raise JournalError("native entry causal horizon does not decrease")
                derive_entry = (
                    derive_native_owned_entry_census
                    if operation["request"].get("ownership_policy") == POLICY
                    else _derive_native_entry_census
                )
                derived_entry = derive_entry(
                    journal,
                    initial,
                    decision_at=datetime.fromisoformat(
                        operation["request"]["decision_at"]
                    ),
                    through_sequence=operation["source_horizon"],
                    fee_policy=Path(operation["request"]["fee_policy"]),
                )
                if _json(derived_entry) != _json(entry_receipt):
                    raise JournalError("native source entry differs from causal replay")
                reservation = entry_receipt["reservation"]
                if (
                    reservation["quantity"] != episode["quantity"]
                    or reservation["plan"]["client_order_id"] != scope
                ):
                    raise JournalError(
                        "native entry original positive reservation differs"
                    )
                for sequence, digest in [
                    entry_receipt["reservation_ref"],
                    *entry_receipt["source_refs"],
                    *entry_receipt["risk_refs"],
                ]:
                    source = journal.read_event(sequence)
                    if (
                        source is None
                        or source[0].payload_ref != digest
                        or sequence > through_sequence
                        or not source[0].visible_at(decision_at)
                    ):
                        raise JournalError("native entry source identity unavailable")
                    refs.add(sequence)
            if item["kind"] == "BOUND_NATIVE_EXIT" and not conflict:
                operation = item["operation"]
                request = operation["request"]
                derived = derive_native_exit_census(
                    journal,
                    initial,
                    group_id=request["group_id"],
                    capture_sequence=request["capture_sequence"],
                    decision_at=datetime.fromisoformat(request["decision_at"]),
                    max_age=timedelta(microseconds=request["max_age_microseconds"]),
                    through_sequence=operation["source_horizon"],
                )
                if _json(derived) != _json(operation["receipt"]):
                    raise JournalError(
                        "native ownership receipt differs from source replay"
                    )
                latest[request["group_id"]] = (
                    event.sequence,
                    event.payload_ref,
                    derived,
                )
            projected = reader._reduce(projected, item, conflict, event.sequence)
            refs.add(event.sequence)
        actual_cache: dict[str, Any] = {}
        for key, value in journal.read_oco_research_evidence_cache(
            str(account)
        ).items():
            decoded = json.loads(key)
            if (
                not isinstance(decoded, list)
                or len(decoded) != 3
                or not all(isinstance(part, str) for part in decoded)
                or _json(decoded).decode() != key
                or not isinstance(value, dict)
                or set(value) != {"hash", "sequence"}
                or type(value["sequence"]) is not int
                or value["sequence"] < 1
                or type(value["hash"]) is not str
            ):
                raise JournalError("native owner cache identity malformed")
            if decoded[0] == scope and value["sequence"] <= through_sequence:
                actual_cache[key] = value
        if expected_cache != actual_cache:
            raise JournalError("native owner deduplication differs")
        # A later unrelated receipt cannot alter this pinned prefix, but a full
        # current-prefix observation also verifies the persisted model projection.
        if not any(
            event.sequence > through_sequence
            and json.loads(raw)["binding"]["entry_scope"] == scope
            for event, raw in all_events
        ) and projected != journal.read_state(reader.state_key):
            raise JournalError("native owner projection differs")
        if not isinstance(projected, dict) or entry_receipt is None:
            issues.add("NATIVE_ENTRY_CENSUS_MISSING")
            continue
        model_view = reader._view(projected)
        if model_view.issues:
            risk_issues.update(model_view.issues)
        if episode.get("closed"):
            pinned = {ref[0]: ref[1] for ref in episode["census_refs"]}
            latest = {
                group: row
                for group, row in latest.items()
                if row[0] in pinned and pinned[row[0]] == row[1]
            }
            if len(latest) != len(pinned):
                issues.add("CLOSED_NATIVE_CENSUS_CHANGED")
        group_ids = {group.group_id for group in model_view.model.groups}
        if set(latest) != group_ids:
            issues.add("NATIVE_GROUP_CENSUS_INCOMPLETE")
        total = Fraction()
        terminal = (
            not model_view.issues and bool(group_ids) and set(latest) == group_ids
        )
        scope_refs: list[tuple[int, str]] = []
        for _group, (sequence, digest, receipt) in latest.items():
            scope_refs.append((sequence, digest))
            if (
                receipt["status"] != "BOUND_NATIVE_EXIT_CENSUS"
                or receipt["transport_origin"] != origin
            ):
                issues.add("NATIVE_CENSUS_QUARANTINED_OR_ORIGIN_CONFLICT")
                terminal = False
                continue
            capture_ref = receipt["capture_ref"]
            # Closed episodes use their pinned terminal source proof and fresh
            # account-wide activity/book equality, not an invented fresh GET.
            inspect_at = (
                datetime.fromisoformat(receipt["decision_at"])
                if episode.get("closed")
                else decision_at
            )
            facts = inspect_native_oco_capture(
                journal,
                capture_ref["sequence"],
                account_ref=str(account),
                decision_at=inspect_at,
                max_age=max_age,
                through_sequence=through_sequence,
            )
            if set(facts.issues) - {"GROUP_FILLS_EXCEED_ROOT_REPORTED_QUANTITY"}:
                issues.add("NATIVE_SOURCE_CAPTURE_UNQUALIFIED")
            for ref in (
                receipt["prepared_ref"],
                receipt["attempt_ref"],
                receipt["outcome_ref"],
                capture_ref,
            ):
                refs.add(ref["sequence"])
            for order in facts.orders:
                if order.order_id in orders and orders[order.order_id] != order:
                    raise JournalError("native broker order identity reused")
                orders[order.order_id] = order
                terminal = terminal and order.status in TERMINAL
            for fill in facts.fills:
                if fill.activity_id in fills and fills[fill.activity_id] != fill:
                    issues.add("NATIVE_ACTIVITY_ID_CONFLICT")
                fills[fill.activity_id] = fill
                total += fill.quantity
        exits[scope] = total
        census_refs[scope] = tuple(sorted(scope_refs))
        owned_http = [
            row
            for row in http
            if row["precall"]["binding"]["entry_scope"] == scope
            and row["attempt_ref"]["sequence"] <= through_sequence
        ]
        if {row["precall"]["group_id"] for row in owned_http} != group_ids or any(
            row["outcome"] is None or row["outcome_ref"]["sequence"] > through_sequence
            for row in owned_http
        ):
            terminal = False
            issues.add("NATIVE_HTTP_COMMITMENTS_UNRESOLVED")
        if terminal:
            finals.add(scope)
    # Account-wide rows, never a provider position mark or a filtered fill view,
    # must exactly confirm every accepted native activity and working order.
    rows = _activities(journal, capture_sequence)
    for identity, fill in fills.items():
        row = rows.get(identity)
        fill_order = orders.get(fill.order_id)
        try:
            if (
                row is None
                or fill_order is None
                or _fill(row, fill_order, _instant(decision_at.isoformat())) != fill
            ):
                issues.add("NATIVE_ACCOUNT_ACTIVITY_DISAGREEMENT")
        except ValueError:
            issues.add("NATIVE_ACCOUNT_ACTIVITY_DISAGREEMENT")
    if any(
        row.get("activity_type") == "FILL"
        and row.get("order_id") in orders
        and identity not in fills
        for identity, row in rows.items()
    ):
        issues.add("UNBOUND_OR_LATE_NATIVE_ACTIVITY")
    raw_open: list[Any] = []
    for request in capture["requests"]:
        if request["path"].startswith("/v2/orders?"):
            decoded_open = decode_broker_json(request["body"])
            if not isinstance(decoded_open, list):
                raise JournalError("native account open book malformed")
            raw_open = decoded_open
    native_seen = set()
    for row in raw_open:
        if isinstance(row, dict) and row.get("id") in orders:
            try:
                parsed = _order(row)
                if (
                    parsed != orders[parsed.order_id]
                    or parsed.status in TERMINAL
                    or parsed.order_id in native_seen
                ):
                    issues.add("NATIVE_OPEN_BOOK_DISAGREEMENT")
                native_seen.add(parsed.order_id)
            except ValueError:
                issues.add("NATIVE_OPEN_BOOK_DISAGREEMENT")
    if native_seen != {
        order.order_id for order in orders.values() if order.status not in TERMINAL
    }:
        issues.add("NATIVE_OPEN_BOOK_COVERAGE")
    return NativeRiskRegistry(
        context,
        tuple(orders.values()),
        tuple(fills.values()),
        tuple(sorted(refs)),
        tuple(sorted(exits.items())),
        tuple(sorted(finals)),
        tuple(sorted(census_refs.items())),
        tuple(sorted(issues)),
        tuple(sorted(risk_issues)),
        origin,
    )
