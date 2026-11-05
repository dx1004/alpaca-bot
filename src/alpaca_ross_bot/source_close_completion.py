"""Accepted as-of source accounting completion; no broker or protection authority."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from .journal import Journal, JournalError

VERSION = "SOURCE_CLOSE_ASOF_ACCOUNTING_COMPLETION_V1"
MANIFEST_SHA256 = "62096035b048b1a0fc95eb24cb7863b67b37169a3eaf3b0536e9fb81ba189735"
_MANIFEST = (
    Path(__file__).resolve().parents[2] / "evidence/source-close-completion-v1.json"
)


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def completion_identity() -> dict[str, Any]:
    from .crossed_stop_contract import load_crossed_stop_contract
    from .oco_research import MANIFEST_SHA256 as EXECUTION_SHA256
    from .oco_research import VERSION as EXECUTION_VERSION

    try:
        raw = _MANIFEST.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
            raise ValueError
        doc = json.loads(raw)
        parent = load_crossed_stop_contract().identity
        if (
            doc["version"] != VERSION
            or doc["parent_emergency_version"] != parent["version"]
            or doc["parent_emergency_sha256"] != parent["manifest_sha256"]
            or doc["parent_execution_version"] != EXECUTION_VERSION
            or doc["parent_execution_sha256"] != EXECUTION_SHA256
            or doc["accounting_semantics_accepted"] is not True
            or any(flag is not False for flag in doc["authority"].values())
        ):
            raise ValueError
        return dict(doc, manifest_sha256=MANIFEST_SHA256)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise JournalError(
            "pinned accepted source completion contract required"
        ) from exc


def require_completion_contract(journal: Journal, horizon: int | None = None) -> None:
    """Check current pinned bytes before memoized completion evidence is reused."""
    if any(
        event.event_kind == "COMPLETE_SOURCE"
        and (horizon is None or event.sequence <= horizon)
        for event, _ in journal.read_account_risk_events()
    ):
        completion_identity()


def _execution_identity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in row.items() if key != "raw_refs"} for row in rows
    ]


def _ordinary_basis(
    journal: Journal,
    client_ids: tuple[str, ...],
    *,
    capture_sequence: int,
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    from .native_oco_evidence import _tagged
    from .order_reconciliation import OrderReconciler
    from .paper_order_contract import decode_broker_json
    from .risk_account_inputs import KIND as ACCOUNT_KIND
    from .risk_account_inputs import SOURCE as ACCOUNT_SOURCE
    from .source_risk_registry import _account_activities

    stored = journal.read_event(capture_sequence)
    if (
        stored is None
        or capture_sequence > through_sequence
        or not stored[0].visible_at(decision_at)
        or (stored[0].source_id, stored[0].feed, stored[0].event_kind)
        != (ACCOUNT_SOURCE, "paper", ACCOUNT_KIND)
        or stored[0].payload_ref != "sha256:" + hashlib.sha256(stored[1]).hexdigest()
    ):
        raise JournalError("ordinary completion basis account capture unavailable")
    capture = decode_broker_json(stored[1])
    if (
        type(capture) is not dict
        or capture.get("schema") != "PAPER_RISK_INPUT_CAPTURE_V2"
        or capture.get("transport_origin") != "INJECTED_EXCHANGE"
        or capture.get("account_ref") != journal.read_paper_account_ref()
    ):
        raise JournalError("ordinary completion basis account identity differs")
    lifecycle, refs = OrderReconciler.read_evidence_at(
        journal,
        capture["account_ref"],
        decision_at,
        through_sequence=through_sequence,
    )
    ordinary = lifecycle["orders"]
    if type(ordinary) is not dict or not client_ids:
        raise JournalError("canonical ordinary completion order basis required")
    frozen: dict[str, Any] = {}
    for cid in client_ids:
        row = ordinary.get(cid)
        if row is None:
            raise JournalError("original completed ordinary order missing")
        frozen[cid] = {
            key: row[key]
            for key in (
                "account_ref",
                "client_order_id",
                "broker_order_id",
                "symbol",
                "side",
                "state",
                "raw_status",
            )
        } | {
            "quantity": str(Fraction(row["quantity"])),
            "filled_quantity": str(Fraction(row["filled_quantity"])),
        }
    broker_ids = {
        row["broker_order_id"] for row in frozen.values() if row["broker_order_id"]
    }
    activities = {
        identity: {
            "broker_order_id": row["order_id"],
            "content_hash": "sha256:"
            + hashlib.sha256(_bytes(_tagged(row))).hexdigest(),
        }
        for identity, row in _account_activities(capture).items()
        if row.get("order_id") in broker_ids
    }
    return {
        "orders": frozen,
        "activities": activities,
        "account_capture_ref": [stored[0].sequence, stored[0].payload_ref],
        "lifecycle_refs": list(refs),
    }


def verify_closed_ordinary_basis(
    journal: Journal,
    frozen: dict[str, Any],
    *,
    capture_sequence: int,
    through_sequence: int,
    decision_at: datetime,
) -> None:
    current = _ordinary_basis(
        journal,
        tuple(sorted(frozen["orders"])),
        capture_sequence=capture_sequence,
        through_sequence=through_sequence,
        decision_at=decision_at,
    )
    if (
        current["orders"] != frozen["orders"]
        or current["activities"] != frozen["activities"]
    ):
        raise JournalError("completed entry or simple exit accounting basis changed")


def derive_source_close_completion(
    journal: Journal,
    state: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    """Internal reducer proof from its canonical preceding state and fresh snapshot."""
    from .crossed_stop_attribution import (
        _initial,
        read_source_http_close_attributions_at,
    )
    from .crossed_stop_consumption import read_source_close_consumptions_at
    from .risk_account_inputs import KIND as ACCOUNT_KIND
    from .risk_account_inputs import SOURCE as ACCOUNT_SOURCE
    from .source_risk_registry import POLICY

    identity = completion_identity()
    if (
        type(through_sequence) is not int
        or not 0 <= through_sequence <= journal.read_evidence_sequence()
        or type(decision_at) is not datetime
        or decision_at.utcoffset() != timedelta(0)
        or state.get("watermark", through_sequence + 1) > through_sequence
    ):
        raise JournalError("bounded canonical source completion prefix required")
    held = state.get("held")
    if (
        not isinstance(held, dict)
        or not held.get("source_close_bindings")
        or type(held.get("quantity")) is not int
        or held["quantity"] <= 0
        or "quantity_incident" in held
        or state.get("veto") is not False
        or set(state.get("issues", []))
        - {"SOURCE_CLOSE_OWNERSHIP_PENDING", "DAILY_STOP_LATCHED"}
        or state.get("_source_policy") != POLICY
        or snapshot.get("qualified") is not True
        or snapshot.get("flat") is not True
        or snapshot.get("positions") != {}
        or snapshot.get("net") is None
        or snapshot.get("fee_document_ref") is None
        or snapshot.get("issues")
    ):
        raise JournalError(
            "qualified whole source slot and flat account required for completion"
        )
    scope = held["client_order_id"]
    orders = snapshot["orders"]
    entry = orders.get(scope)
    if (
        entry is None
        or entry["state"] not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
        or any(
            order["state"] not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
            for order in orders.values()
        )
        or snapshot["session"] != state["session"]
        or snapshot["e0"] != state["e0"]
        or snapshot["baseline_ref"] != state["baseline_ref"]
    ):
        raise JournalError(
            "terminal original entry and unchanged accounting baseline required"
        )
    filled = Fraction(entry["filled_quantity"])
    if filled <= 0 or filled.denominator != 1:
        raise JournalError("positive whole entry execution required")
    captures = [
        event.sequence
        for event, _ in journal._read_private_events(ACCOUNT_SOURCE, through_sequence)
        if event.event_kind == ACCOUNT_KIND and event.visible_at(decision_at)
    ]
    if not captures or max(captures) != snapshot["selection"]["capture_sequence"]:
        raise JournalError("newest full account capture required for completion")
    source = snapshot.get("source")
    if not isinstance(source, dict):
        raise JournalError("source account union required for completion")
    episode = next(
        (
            row
            for row in source["context"]["episodes"]
            if row["entry_scope"] == scope and not row.get("closed")
        ),
        None,
    )
    if (
        episode is None
        or episode["source_close_bindings"] != held["source_close_bindings"]
    ):
        raise JournalError("original source completion basket required")
    initial = _initial(journal, episode["binding"])
    close_state = read_source_close_consumptions_at(
        journal, initial, through_sequence=through_sequence, decision_at=decision_at
    )
    if close_state["conflicts"]:
        raise JournalError("source consumption conflict prevents completion")
    index = read_source_http_close_attributions_at(
        journal, initial, through_sequence=through_sequence, decision_at=decision_at
    )
    if index["latched"] or index["conflicts"]:
        raise JournalError("source attribution conflict prevents completion")
    claims: dict[str, Any] = {}
    source_quantity = Fraction()
    for cid, basket in held["source_close_bindings"].items():
        claim = index["cid_claims"].get(cid)
        if claim is None or cid in orders:
            raise JournalError("canonical separate source claim required")
        operation = next(
            row
            for row in index["observations"]
            if row["observation_ref"] == claim["latest_ref"]
        )
        proof = operation["proof"]
        facts = [row for row in source["orders"] if row["client_order_id"] == cid]
        quantity = Fraction(proof["requested_quantity"])
        if (
            len(facts) != 1
            or facts[0]["status"] != "filled"
            or facts[0]["broker_order_id"] != proof["broker_order_id"]
            or Fraction(facts[0]["filled_quantity"]) != quantity
            or Fraction(proof["cumulative_fill_quantity"]) != quantity
            or proof["original_portions"] != basket["portions"]
        ):
            raise JournalError("every source order must be fully filled and attributed")
        close = next(
            (
                row
                for row in close_state["closes"].values()
                if row.get("source_intent_binding", {})
                .get("intent", {})
                .get("request_body")
                and json.loads(row["source_intent_binding"]["intent"]["request_body"])[
                    "client_order_id"
                ]
                == cid
            ),
            None,
        )
        if (
            close is None
            or Fraction(close["filled"]) != quantity
            or close["portions"] != basket["portions"]
        ):
            raise JournalError("canonical full original source consumption required")
        receipts = close.get("source_consumptions", {})
        matching = {
            key: receipt
            for key, receipt in receipts.items()
            if receipt["attribution_ref"] == claim["latest_ref"]
            and receipt["executions"] == proof["executions"]
            and receipt["attempt_ref"] == proof["attempt_ref"]
            and receipt["outcome_ref"] == proof["outcome_ref"]
            and receipt["original_portions"] == basket["portions"]
            and Fraction(receipt["cumulative_fill_quantity"]) == quantity
            and close["attempt"] == receipt["attempt_id"]
        }
        consumed = [
            (event, item["data"]["receipt"])
            for event, raw in journal.read_crossed_stop_events()
            if event.sequence <= through_sequence
            and event.visible_at(decision_at)
            and (item := json.loads(raw)).get("kind") == "SOURCE_CONSUME"
            and item["data"]["identity"] in matching
            and item["data"]["receipt"] == matching[item["data"]["identity"]]
        ]
        if not consumed:
            raise JournalError(
                "canonical source consume event required before completion"
            )
        event, receipt = consumed[-1]
        if receipt["client_order_id"] != cid or receipt["entry_scope"] != scope:
            raise JournalError("source consumption scope differs")
        claims[cid] = {
            "broker_order_id": proof["broker_order_id"],
            "requested_quantity": proof["requested_quantity"],
            "original_portions": proof["original_portions"],
            "executions": proof["executions"],
            "attempt_ref": proof["attempt_ref"],
            "outcome_ref": proof["outcome_ref"],
            "attribution_ref": claim["latest_ref"],
            "consumption_ref": [event.sequence, event.payload_ref],
        }
        source_quantity += quantity
    native = snapshot.get("native")
    native_quantity = (
        Fraction()
        if native is None
        else Fraction(native["exit_by_scope"].get(scope, "0"))
    )
    native_episode = None
    if native is not None and native["context"]["active_scope"] == scope:
        if (
            scope not in native["final_scopes"]
            or native["risk_issues"]
            or not native["census_refs"].get(scope)
        ):
            raise JournalError("complete native census and final source scope required")
        native_episode = dict(
            next(
                row
                for row in native["context"]["episodes"]
                if row["entry_scope"] == scope
            ),
            closed=True,
            census_refs=native["census_refs"][scope],
        )
    simple_quantity = sum(
        (
            Fraction(orders[cid]["filled_quantity"])
            for cid in held["exits"]
            if cid in orders
        ),
        Fraction(),
    )
    if (
        filled != source_quantity + native_quantity + simple_quantity
        or Fraction(source["exit_by_scope"].get(scope, "0")) != source_quantity
    ):
        raise JournalError("exact simple native source execution union required")
    ordinary_basis = _ordinary_basis(
        journal,
        tuple(sorted({scope, *(cid for cid in held["exits"] if cid in orders)})),
        capture_sequence=snapshot["selection"]["capture_sequence"],
        through_sequence=through_sequence,
        decision_at=decision_at,
    )
    return {
        "schema": 1,
        "contract": identity,
        "ordinary_basis": ordinary_basis,
        "account_ref": initial.account_ref,
        "entry_scope": scope,
        "source_horizon": through_sequence,
        "decision_at": decision_at.isoformat(),
        "account_selection": snapshot["selection"],
        "source_refs": snapshot["source_refs"],
        "fee_document_ref": snapshot["fee_document_ref"],
        "source_context": episode,
        "native_context": native_episode,
        "claims": claims,
        "entry_filled_quantity": str(filled),
        "source_exit_quantity": str(source_quantity),
        "native_exit_quantity": str(native_quantity),
        "simple_exit_quantity": str(simple_quantity),
        "net": str(Fraction(snapshot["net"]) - Fraction(held["net_at_start"])),
        "as_of_accounting_complete": True,
        "authority": identity["authority"],
    }


def verify_closed_source_episode(
    journal: Journal,
    episode: dict[str, Any],
    *,
    through_sequence: int,
    decision_at: datetime,
) -> dict[str, Any]:
    """A caller's closed boolean cannot bypass source freshness."""
    from .account_risk_owner import AccountRiskOwner
    from .crossed_stop_attribution import _initial
    from .oco_journal import _causal_oco_reader
    from .strategy_risk import VERSION as RISK_VERSION

    identity = completion_identity()
    ref = episode.get("completion_ref")
    if (
        type(ref) is not list
        or len(ref) != 2
        or type(ref[0]) is not int
        or not 0 < ref[0] <= through_sequence
    ):
        raise JournalError("canonical earlier source completion reference required")
    saved = journal.read_event(ref[0])
    if saved is None:
        raise JournalError("source completion reference unavailable")
    event, raw = saved
    item = json.loads(raw)
    if (
        event.source_id != "ALPACA_ACCOUNT_RISK"
        or event.feed != "paper"
        or event.event_kind != "COMPLETE_SOURCE"
        or not event.visible_at(decision_at)
        or event.payload_ref != ref[1]
        or event.payload_ref != "sha256:" + hashlib.sha256(raw).hexdigest()
        or _bytes(item) != raw
        or item.get("version") != RISK_VERSION
        or item.get("completion_contract") != identity
        or item.get("completion_proof", {}).get("contract") != identity
        or episode.get("completion_contract") != identity
    ):
        raise JournalError("source completion canonical identity differs")
    initial = _initial(journal, episode["binding"])
    view = _causal_oco_reader(journal, initial).read_verified_at(
        event.sequence, datetime.fromisoformat(item["at"])
    )
    if view is None or view.entry_binding is None:
        raise JournalError("source completion original entry unavailable")
    state, _ = AccountRiskOwner._read_native_causal_evidence_at(
        journal,
        initial.account_ref,
        datetime.fromisoformat(item["at"]),
        Path(view.entry_binding["fee_policy"]),
        through_sequence=event.sequence,
    )
    completed = None if state is None else state["completed"].get(initial.entry_scope)
    if (
        completed is None
        or completed.get("source_context") != episode
        or completed.get("completion_ref") != ref
    ):
        raise JournalError("verified canonical completed source slot required")
    return cast(dict[str, Any], item["completion_proof"])


def verify_closed_source_claim(proof: dict[str, Any], frozen: dict[str, Any]) -> None:
    if any(
        proof[key] != frozen[key]
        for key in (
            "broker_order_id",
            "requested_quantity",
            "original_portions",
            "attempt_ref",
            "outcome_ref",
        )
    ) or _execution_identity(proof["executions"]) != _execution_identity(
        frozen["executions"]
    ):
        raise JournalError("completed source execution identity changed")
