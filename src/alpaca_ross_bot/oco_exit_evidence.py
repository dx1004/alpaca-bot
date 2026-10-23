"""Exact native exit facts; no conflict clearance, protection or risk release."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any

from .journal import Journal, JournalError
from .native_oco_evidence import (
    NativeOrderFact,
    _instant,
    _order,
    _topology,
    inspect_native_oco_capture,
)
from .native_oco_http import native_http_inventory
from .oco_research import OcoResearchState
from .paper_order_contract import decode_broker_json


def derive_native_exit_census(
    journal: Journal,
    initial: OcoResearchState,
    *,
    group_id: str,
    capture_sequence: int,
    decision_at: datetime,
    max_age: timedelta,
    through_sequence: int,
) -> dict[str, Any]:
    """Bind a complete observed native census or retain an explicit quarantine.

    Incomplete captures keep their positive raw facts but never replace a prior
    accepted census with zero. Complete oversells are imported as model incidents.
    """
    facts = inspect_native_oco_capture(
        journal,
        capture_sequence,
        account_ref=initial.account_ref,
        decision_at=decision_at,
        max_age=max_age,
        through_sequence=through_sequence,
    )
    saved = journal.read_event(capture_sequence)
    assert saved is not None
    capture_event, raw = saved
    capture = json.loads(raw)
    if capture.get("schema") != 2:
        raise JournalError("legacy native capture cannot establish exit ownership")
    rows = [
        row
        for row in native_http_inventory(journal)
        if row["precall"]["binding"]["entry_scope"] == initial.entry_scope
        and row["precall"]["group_id"] == group_id
    ]
    if len(rows) != 1:
        raise JournalError("one consumed native HTTP group attempt required")
    row = rows[0]
    pre, outcome = row["precall"], row["outcome"]
    if (
        pre["binding"]["symbol"] != initial.symbol
        or pre["binding"]["account_ref"] != initial.account_ref
    ):
        raise JournalError("native exit attempt scope differs")
    refs = [pre["prepared_ref"], row["attempt_ref"], row["outcome_ref"]]
    for ref in refs:
        if ref is None or ref["sequence"] > through_sequence:
            raise JournalError("successful visible native HTTP outcome required")
        record = journal.read_event(ref["sequence"])
        if (
            record is None
            or record[0].payload_ref != ref["hash"]
            or not record[0].visible_at(decision_at)
        ):
            raise JournalError("native exit source prefix unavailable")
    assert outcome is not None
    if (
        outcome["result_kind"] != "HTTP_RESPONSE"
        or not 200 <= outcome["http_status"] < 300
    ):
        raise JournalError("unresolved native POST cannot establish exit ownership")
    if (
        capture["transport_origin"] != pre["origin"]
        or capture_sequence <= row["outcome_ref"]["sequence"]
        or _instant(capture["started_at"]) < _instant(outcome["completed_at"])
    ):
        raise JournalError("native exit capture origin/chronology differs from POST")
    body = json.loads(pre["request_body"])
    raw_post = base64.b64decode(outcome["body_base64"], validate=True)
    try:
        document = decode_broker_json(raw_post)
        if not isinstance(document, dict) or type(document.get("id")) is not str:
            raise ValueError
        post_orders: tuple[NativeOrderFact, ...]
        if document.get("legs") in (None, []):
            post_orders = (_order(document),)
            post_issues: tuple[str, ...] = ()
        else:
            post_orders, post_issues = _topology(raw_post, document["id"])
    except (ValueError, TypeError) as exc:
        raise JournalError(
            "native POST response is not qualified order evidence"
        ) from exc
    if post_issues or len(post_orders) not in (1, 2):
        raise JournalError("native POST topology cannot establish exit identity")
    post_root = post_orders[0]
    post_stop = post_orders[1] if len(post_orders) == 2 else None
    identity = {
        "root_order_id": post_root.order_id,
        "stop_order_id": post_stop.order_id
        if post_stop is not None
        else (
            facts.orders[1].order_id
            if facts.topology_observed and len(facts.orders) == 2
            else None
        ),
        "broker_group_id": post_root.group_id,
    }

    def root_terms(root: Any) -> bool:
        return (
            root.client_order_id == body["client_order_id"]
            and root.symbol == body["symbol"]
            and root.side == "sell"
            and root.group_role == "take_profit"
            and root.quantity == Fraction(body["qty"])
            and root.time_in_force == body["time_in_force"]
            and root.order_type == "limit"
            and root.observed_order_class == "oco"
            and root.limit_price == Fraction(body["take_profit"]["limit_price"])
            and not root.replaces
            and not root.replaced_by
            and _instant(root.created_at) >= _instant(pre["started_at"])
        )

    def stop_terms(stop: Any) -> bool:
        return (
            stop.order_type == "stop"
            and stop.group_role == "stop_loss"
            and stop.symbol == body["symbol"]
            and stop.side == "sell"
            and stop.time_in_force == body["time_in_force"]
            and stop.group_id == post_root.group_id
            and stop.limit_price is None
            and stop.stop_price == Fraction(body["stop_loss"]["stop_price"])
            and not stop.replaces
            and not stop.replaced_by
            and _instant(stop.created_at) >= _instant(pre["started_at"])
        )

    if not root_terms(post_root) or (
        post_stop is not None and not stop_terms(post_stop)
    ):
        raise JournalError("native POST terms differ from immutable request")
    if any(
        _instant(stamp) > _instant(outcome["completed_at"])
        for order in post_orders
        for stamp in (order.created_at, order.updated_at, order.submitted_at)
        if stamp is not None
    ):
        raise JournalError("native POST source clock exceeds observed response")
    issues = list(facts.issues)
    if post_stop is not None and post_stop.quantity not in {
        post_root.quantity,
        post_root.quantity - post_root.filled_quantity,
    }:
        issues.append("POST_STOP_QUANTITY_RELATION_UNQUALIFIED")
    if len(facts.orders) != 2 or not facts.topology_observed:
        issues.append("NATIVE_EXIT_CAPTURE_TOPOLOGY_UNQUALIFIED")
    elif (
        facts.requested_order_id != post_root.order_id
        or not root_terms(facts.orders[0])
        or not stop_terms(facts.orders[1])
        or any(
            observed.order_id != posted.order_id or observed.group_id != posted.group_id
            for observed, posted in zip(facts.orders, post_orders, strict=False)
        )
    ):
        issues.append("POST_CAPTURE_IDENTITY_OR_TERMS_DIFFER")
    elif any(
        observed.created_at != posted.created_at
        or _instant(observed.updated_at) < _instant(posted.updated_at)
        or observed.filled_quantity < posted.filled_quantity
        for observed, posted in zip(facts.orders, post_orders, strict=False)
    ):
        issues.append("POST_CAPTURE_SOURCE_REGRESSION")
    complete_incidents = {"GROUP_FILLS_EXCEED_ROOT_REPORTED_QUANTITY"}
    allowed = (
        not (set(issues) - complete_incidents)
        and len(facts.orders) == 2
        and all(order.order_id in facts.exhausted_order_ids for order in facts.orders)
    )
    if len(facts.orders) == 2:
        target, stop = facts.orders
        if stop.quantity not in {
            target.quantity,
            target.quantity - target.filled_quantity,
        }:
            issues.append("NATIVE_EXIT_STOP_QUANTITY_RELATION_UNQUALIFIED")
    executions = []
    for fill in facts.fills:
        order = next(
            (order for order in facts.orders if order.order_id == fill.order_id), None
        )
        if (
            order is None
            or _instant(fill.transaction_time) < _instant(order.created_at)
            or _instant(fill.transaction_time) < _instant(pre["started_at"])
        ):
            issues.append("NATIVE_EXIT_FILL_PREDATES_ORDER_OR_PRECALL")
        executions.append(
            {
                "execution_id": "native-fill:"
                + json.dumps(
                    [initial.account_ref, fill.order_id, fill.activity_id],
                    separators=(",", ":"),
                ),
                "activity_id": fill.activity_id,
                "content_hash": fill.content_hash,
                "account_ref": initial.account_ref,
                "symbol": initial.symbol,
                "entry_scope": initial.entry_scope,
                "role": "TARGET" if fill.order_id == post_root.order_id else "STOP",
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "group_id": group_id,
                "transaction_time": fill.transaction_time,
            }
        )
    executions.sort(key=lambda item: item["execution_id"])
    current = {item["activity_id"]: item for item in executions}
    last_source_entry = last_declared_entry = 0
    for event, payload in journal.read_oco_research_events():
        if event.sequence > through_sequence:
            continue
        item = json.loads(payload)
        same_scope = item["binding"]["entry_scope"] == initial.entry_scope
        if (
            same_scope
            and item["kind"] == "EXECUTION"
            and item["operation"]["role"] == "ENTRY"
        ):
            last_declared_entry = event.sequence
        if (
            same_scope
            and item["kind"] == "BOUND_ENTRY"
            and item["operation"]["outcome"] == "BOUND_ENTRY_CENSUS"
        ):
            last_source_entry = event.sequence
        if same_scope and not event.visible_at(decision_at):
            raise JournalError("native exit owner prefix is not decision-visible")
        if same_scope and (
            item["kind"] == "EXECUTION"
            and item["operation"]["role"] != "ENTRY"
            or item["kind"] == "ACKNOWLEDGEMENT"
        ):
            issues.append("DECLARED_NATIVE_EXIT_MIXING")
        if (
            same_scope
            and item["kind"] == "EXECUTION"
            and item["operation"]["execution_id"]
            in {e["execution_id"] for e in executions}
        ):
            issues.append("NATIVE_EXIT_EXECUTION_NAMESPACE_COLLISION")
        if (
            item["kind"] == "BOUND_ENTRY"
            and item["operation"]["outcome"] == "BOUND_ENTRY_CENSUS"
            and any(
                e["execution_id"] in current
                for e in item["operation"]["receipt"]["entry"]["executions"]
            )
        ):
            issues.append("NATIVE_EXIT_RAW_ACTIVITY_COLLIDES_WITH_ENTRY")
        if (
            same_scope
            and item["kind"] == "BOUND_ENTRY"
            and item["operation"]["outcome"] == "BOUND_ENTRY_CENSUS"
            and any(
                e["execution_id"] in {row["execution_id"] for row in executions}
                for e in item["operation"]["receipt"]["entry"]["executions"]
            )
        ):
            issues.append("NATIVE_EXIT_EXECUTION_NAMESPACE_COLLISION")
        if (
            item["kind"] != "BOUND_NATIVE_EXIT"
            or item["operation"]["outcome"] != "BOUND_NATIVE_EXIT_CENSUS"
        ):
            continue
        old = item["operation"]["receipt"]
        if same_scope and old["group_id"] == group_id:
            sticky = {
                "FILL_IDENTITY_CONFLICT",
                "NATIVE_EXIT_FILL_REGRESSION_OR_CONFLICT",
                "NATIVE_EXIT_IDENTITY_CHANGED",
                "NATIVE_EXIT_IDENTITY_REUSED",
                "DECLARED_NATIVE_EXIT_MIXING",
                "POST_CAPTURE_IDENTITY_OR_TERMS_DIFFER",
                "POST_CAPTURE_SOURCE_REGRESSION",
                "NATIVE_EXIT_FILL_PREDATES_ORDER_OR_PRECALL",
                "NATIVE_EXIT_PRIOR_CONFLICT_QUARANTINED",
                "NATIVE_EXIT_EXECUTION_NAMESPACE_COLLISION",
                "NATIVE_EXIT_RAW_ACTIVITY_COLLIDES_WITH_ENTRY",
            }
            if (
                old["status"] != "BOUND_NATIVE_EXIT_CENSUS"
                and set(old["issues"]) & sticky
            ):
                issues.append("NATIVE_EXIT_PRIOR_CONFLICT_QUARANTINED")
            if any(
                old["identity"][name] is not None
                and old["identity"][name] != identity[name]
                for name in identity
            ):
                issues.append("NATIVE_EXIT_IDENTITY_CHANGED")
            if (
                capture_sequence < old["capture_ref"]["sequence"]
                or decision_at.isoformat() < old["decision_at"]
            ):
                issues.append("NATIVE_EXIT_CENSUS_REGRESSION")
            if any(current.get(e["activity_id"]) != e for e in old["executions"]):
                issues.append("NATIVE_EXIT_FILL_REGRESSION_OR_CONFLICT")
        elif (
            (
                {old["identity"]["root_order_id"], old["identity"]["stop_order_id"]}
                - {None}
            )
            & ({identity["root_order_id"], identity["stop_order_id"]} - {None})
            or old["identity"]["broker_group_id"] == post_root.group_id
            or any(e["activity_id"] in current for e in old["executions"])
        ):
            issues.append("NATIVE_EXIT_IDENTITY_REUSED")
    if last_declared_entry > last_source_entry:
        issues.append("DECLARED_ENTRY_AFTER_BOUND_SOURCE")
    accepted = allowed and not (set(issues) - complete_incidents)
    return {
        "schema": 1,
        "status": "BOUND_NATIVE_EXIT_CENSUS"
        if accepted
        else "QUARANTINED_NATIVE_EXIT_CENSUS",
        "classification": "INJECTED_NATIVE_EXIT_TEST_OWNERSHIP"
        if pre["origin"] == "INJECTED_EXCHANGE"
        else "BUILTIN_REPORTED_NATIVE_EXIT_OWNERSHIP",
        "transport_origin": pre["origin"],
        "identity_basis": "POST_ROOT_AND_GET_NESTED_LEG"
        if post_stop is None
        else "POST_NESTED_AND_GET_NESTED",
        "source_classification": "INJECTED_TEST_SOURCE"
        if pre["origin"] == "INJECTED_EXCHANGE"
        else "BUILTIN_REPORTED_SOURCE",
        "broker_ownership_accepted": False,
        "model_exit_census_bound": accepted,
        "source_admission_accepted": False,
        "group_id": group_id,
        "decision_at": decision_at.isoformat(),
        "source_horizon": through_sequence,
        "prepared_ref": pre["prepared_ref"],
        "attempt_ref": row["attempt_ref"],
        "outcome_ref": row["outcome_ref"],
        "capture_ref": {
            "sequence": capture_sequence,
            "hash": capture_event.payload_ref,
        },
        "identity": identity,
        "executions": executions,
        "issues": sorted(set(issues)),
        "source_observation": facts.to_dict(),
        "protection_accepted": False,
        "runtime_authorized": False,
        "risk_release_authorized": False,
        "risk_qualified": False,
    }
