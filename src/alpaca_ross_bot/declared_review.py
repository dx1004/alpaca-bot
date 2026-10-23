"""Durable declared engineering review sequence; no evidence certification."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .journal import Journal, JournalError

STEPS = ("PACKAGE", "EXPECTED", "UNMASK", "COMPARISON")
VERDICTS = ("ELIGIBLE", "INELIGIBLE", "UNAVAILABLE")


def _text(value: object) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise JournalError("review identity/text invalid")
    return value


def _utc(at: datetime) -> None:
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise JournalError("review receipt requires explicit UTC clock")


def _clock(value: object) -> datetime:
    at = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    _utc(at)
    return at


def _hash(value: object) -> str:
    value = _text(value)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise JournalError("review digest invalid")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("duplicate review JSON key")
        result[key] = value
    return result


def _unsupported(value: str) -> None:
    raise JournalError("review numbers must use exact text or integer identities")


def _decode(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= 262144:
        raise JournalError("review document must be bounded bytes")
    value = json.loads(
        raw,
        object_pairs_hook=_object,
        parse_float=_unsupported,
        parse_constant=_unsupported,
    )
    if type(value) is not dict:
        raise JournalError("review document must be an object")
    return value


def _keys(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise JournalError("review fields invalid or unsupported")
    return value


def _package(raw: bytes, at: datetime, *, journal_scope: str, sequence: int) -> None:
    value = _keys(
        _decode(raw),
        {
            "package_ref",
            "package_digest",
            "rubric_ref",
            "rubric_digest",
            "reviewer_ref",
            "decision_at",
            "journal_scope",
            "through_sequence",
            "source_horizons",
            "source_refs",
        },
    )
    for field in ("package_ref", "rubric_ref", "reviewer_ref"):
        _text(value[field])
    _hash(value["package_digest"])
    _hash(value["rubric_digest"])
    decision = _clock(value["decision_at"])
    if (
        decision > at
        or type(value["through_sequence"]) is not int
        or not 0 <= value["through_sequence"] < sequence
        or value["journal_scope"] != journal_scope
    ):
        raise JournalError("declared package horizon/availability invalid")
    refs = value["source_refs"]
    horizons = value["source_horizons"]
    if type(horizons) is not dict or not horizons:
        raise JournalError("scoped source horizons required")
    for scope, horizon in horizons.items():
        _text(scope)
        if (
            type(horizon) is not int
            or horizon < 0
            or (scope == journal_scope and horizon > value["through_sequence"])
        ):
            raise JournalError("scoped source horizon invalid")
    if type(refs) is not list or not refs:
        raise JournalError("declared scoped source references required")
    for ref in refs:
        ref = _keys(ref, {"journal_scope", "sequence", "payload_ref", "available_at"})
        _text(ref["journal_scope"])
        _hash(ref["payload_ref"])
        if (
            type(ref["sequence"]) is not int
            or ref["journal_scope"] not in horizons
            or not 1 <= ref["sequence"] <= horizons[ref["journal_scope"]]
            or _clock(ref["available_at"]) > decision
        ):
            raise JournalError("declared source outside horizon/decision")


def _rules(raw: bytes, *, expected: bool) -> dict[str, Any]:
    fields = {"verdict", "rules"}
    if expected:
        fields |= {
            "calculator_ref",
            "calculator_version",
            "calculator_digest",
            "worksheet_ref",
            "reviewer_declaration",
        }
    value = _keys(_decode(raw), fields)
    if expected:
        for field in ("calculator_ref", "calculator_version", "worksheet_ref"):
            _text(value[field])
        _hash(value["calculator_digest"])
        declaration = _keys(
            value["reviewer_declaration"],
            {"reviewer_ref", "independence_statement", "conflicts_statement"},
        )
        for field in declaration:
            _text(declaration[field])
    if (
        value["verdict"] not in VERDICTS
        or type(value["rules"]) is not dict
        or not value["rules"]
    ):
        raise JournalError("review verdict/rules invalid")
    for rule, row in value["rules"].items():
        if re.fullmatch(r"P(?:0[1-9]|1[0-3])", rule) is None:
            raise JournalError("unsupported review rule")
        fields = (
            {"value", "calculation_ref", "abstention_reason"} if expected else {"value"}
        )
        row = _keys(row, fields)
        if row["value"] is not None:
            _text(row["value"])
        if expected:
            _text(row["calculation_ref"])
            if row["value"] is None:
                _text(row["abstention_reason"])
            elif row["abstention_reason"] is not None:
                raise JournalError("valued expectation cannot also abstain")
    return value


@dataclass(frozen=True, repr=False)
class ReviewReceipt:
    step: str
    sequence: int
    payload_ref: str
    available_at: datetime
    exact_bytes: bytes


@dataclass(frozen=True, repr=False)
class DeclaredReviewView:
    case_id: str
    receipts: tuple[ReviewReceipt, ...]
    conflict: bool
    rule_results: tuple[tuple[str, str], ...]
    verdict_result: str | None
    masking_verified: bool = False
    reviewer_independence_verified: bool = False
    source_evidence_verified: bool = False
    natural_enrollment_verified: bool = False
    formal_sample_eligible: bool = False
    runtime_authorized: bool = False
    classification: str = "DECLARED_ENGINEERING_REVIEW_NOT_MASKING_OR_HOLDOUT_PROOF"


class DeclaredReview:
    def __init__(self, journal: Journal, *, journal_scope: str) -> None:
        journal.require_private_order_state()
        self.journal = journal
        self.journal_scope = _text(journal_scope)
        account_ref = journal.read_paper_account_ref()
        if account_ref is None:
            raise JournalError("declared reviews require an existing bound journal")
        self.account_ref = account_ref

    def view(
        self, case_id: str, *, through_sequence: int | None = None
    ) -> DeclaredReviewView:
        owns_read = not self.journal._db.in_transaction
        if owns_read:
            self.journal._db.execute("BEGIN")
        try:
            return self._view(case_id, through_sequence=through_sequence)
        finally:
            if owns_read:
                self.journal._db.rollback()

    def _view(
        self, case_id: str, *, through_sequence: int | None = None
    ) -> DeclaredReviewView:
        _text(case_id)
        if ":" in case_id or self.journal.read_paper_account_ref() != self.account_ref:
            raise JournalError("review case/account identity invalid")
        horizon = (
            self.journal.read_evidence_sequence()
            if through_sequence is None
            else through_sequence
        )
        if (
            type(horizon) is not int
            or not 0 <= horizon <= self.journal.read_evidence_sequence()
        ):
            raise JournalError("review replay horizon invalid")
        receipts: list[ReviewReceipt] = []
        identities: dict[str, bytes] = {}
        conflict = False
        for event, raw in self.journal.read_declared_review_events():
            if event.sequence > horizon:
                continue
            value = _keys(
                _decode(raw),
                {
                    "schema",
                    "account_ref",
                    "case_id",
                    "dedupe_key",
                    "identity_hash",
                    "step",
                    "document_base64",
                },
            )
            if (
                value["schema"] != 1
                or value["account_ref"] != self.account_ref
                or event.feed != "paper"
                or event.source_event_at is not None
                or event.provider_received_at is not None
            ):
                raise JournalError("review source identity invalid")
            if value["case_id"] != case_id:
                continue
            step = value["step"]
            if (
                step not in STEPS
                or event.event_kind != "DECLARED_REVIEW_" + step
                or value["dedupe_key"] != case_id + ":" + step
            ):
                raise JournalError("review step identity invalid")
            document = base64.b64decode(value["document_base64"], validate=True)
            identity = self._identity(case_id, step, document)
            if hashlib.sha256(identity).hexdigest() != value["identity_hash"]:
                raise JournalError("review semantic identity invalid")
            if step in identities:
                if identities[step] != identity:
                    conflict = True
                else:
                    raise JournalError("duplicated source review receipt")
                continue
            if conflict or step != STEPS[len(receipts)]:
                raise JournalError("review source sequence invalid")
            if receipts and event.available_at < receipts[-1].available_at:
                raise JournalError("review clock regressed")
            self._validate(step, document, event.available_at, receipts, event.sequence)
            identities[step] = identity
            receipts.append(
                ReviewReceipt(
                    step,
                    event.sequence,
                    event.payload_ref,
                    event.available_at,
                    document,
                )
            )
        results: list[tuple[str, str]] = []
        verdict = None
        if len(receipts) == 4:
            expected = _rules(receipts[1].exact_bytes, expected=True)
            observed = _rules(receipts[3].exact_bytes, expected=False)
            verdict = (
                "MATCH" if expected["verdict"] == observed["verdict"] else "MISMATCH"
            )
            for rule, item in expected["rules"].items():
                other = observed["rules"][rule]["value"]
                result = (
                    "ABSTAIN"
                    if item["value"] is None or other is None
                    else "MATCH"
                    if item["value"] == other
                    else "MISMATCH"
                )
                results.append((rule, result))
        if conflict:
            verdict = "INVALID_CONFLICT"
        return DeclaredReviewView(
            case_id, tuple(receipts), conflict, tuple(results), verdict
        )

    @staticmethod
    def _identity(case_id: str, step: str, document: bytes) -> bytes:
        return json.dumps(
            {
                "case_id": case_id,
                "step": step,
                "document_base64": base64.b64encode(document).decode(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def _validate(
        self,
        step: str,
        document: bytes,
        at: datetime,
        receipts: list[ReviewReceipt],
        sequence: int,
    ) -> None:
        if step == "PACKAGE":
            _package(document, at, journal_scope=self.journal_scope, sequence=sequence)
        elif step == "EXPECTED":
            expected = _rules(document, expected=True)
            if (
                expected["reviewer_declaration"]["reviewer_ref"]
                != _decode(receipts[0].exact_bytes)["reviewer_ref"]
            ):
                raise JournalError("expected reviewer differs from declared package")
        elif step == "UNMASK":
            unmask = _keys(_decode(document), {"bot_trace_ref", "observed_digest"})
            _hash(unmask["observed_digest"])
            ref = _keys(
                unmask["bot_trace_ref"],
                {"journal_scope", "sequence", "payload_ref", "available_at"},
            )
            _text(ref["journal_scope"])
            _hash(ref["payload_ref"])
            if (
                type(ref["sequence"]) is not int
                or ref["sequence"] < 1
                or (
                    ref["journal_scope"] == self.journal_scope
                    and ref["sequence"] >= sequence
                )
                or _clock(ref["available_at"]) > at
            ):
                raise JournalError("declared bot trace reference invalid")
        else:
            observed = _rules(document, expected=False)
            expected = _rules(receipts[1].exact_bytes, expected=True)
            if set(observed["rules"]) != set(expected["rules"]):
                raise JournalError("comparison must retain all sealed rule identities")
            if (
                "sha256:" + hashlib.sha256(document).hexdigest()
                != _decode(receipts[2].exact_bytes)["observed_digest"]
            ):
                raise JournalError(
                    "comparison bytes differ from declared unmask digest"
                )

    def record(
        self, case_id: str, step: str, document: bytes, *, recorded_at: datetime
    ) -> ReviewReceipt:
        _text(case_id)
        _utc(recorded_at)
        if ":" in case_id or self.journal._db.in_transaction:
            raise JournalError(
                "review write requires its own transaction and case identity"
            )
        if step not in STEPS:
            raise JournalError("unsupported review step")
        self.journal.require_private_order_state()
        identity = self._identity(case_id, step, document)
        payload = json.dumps(
            {
                "schema": 1,
                "account_ref": self.account_ref,
                "case_id": case_id,
                "dedupe_key": case_id + ":" + step,
                "identity_hash": hashlib.sha256(identity).hexdigest(),
                "step": step,
                "document_base64": base64.b64encode(document).decode(),
            },
            sort_keys=True,
        ).encode()
        state_key = "declared_review:" + self.account_ref + ":" + case_id

        def reduce(cached: object | None, duplicate: bool, sequence: int) -> object:
            current = self.view(case_id)
            sequences = [item.sequence for item in current.receipts]
            projection = {
                "sequences": sequences,
                "conflict": current.conflict,
            }
            if cached is not None and cached != projection:
                raise JournalError("review cache disagrees with immutable evidence")
            if current.conflict:
                raise JournalError("review case has unresolved conflicting receipts")
            if current.receipts and recorded_at < current.receipts[-1].available_at:
                raise JournalError("review clock regressed")
            if duplicate:
                return {"sequences": projection["sequences"], "conflict": True}
            if len(current.receipts) >= 4 or step != STEPS[len(current.receipts)]:
                raise JournalError("review prerequisite receipt missing")
            self._validate(
                step, document, recorded_at, list(current.receipts), sequence
            )
            return {
                "sequences": [*sequences, sequence],
                "conflict": False,
            }

        def prepare(raw: bytes) -> bytes:
            self.verify_rebuild(case_id)
            if self.view(case_id).conflict:
                raise JournalError("review case has unresolved conflicting receipts")
            return raw

        self.journal.apply_declared_review_event(
            account_ref=self.account_ref,
            state_key=state_key,
            dedupe_key=case_id + ":" + step,
            event_kind="DECLARED_REVIEW_" + step,
            payload=payload,
            received_at=recorded_at,
            reduce=reduce,
            identity_payload=identity,
            prepare_payload=prepare,
        )
        result = self.view(case_id)
        if result.conflict:
            raise JournalError("changed review step preserved as conflict")
        return next(item for item in result.receipts if item.step == step)

    def verify_rebuild(self, case_id: str) -> bool:
        owns_read = not self.journal._db.in_transaction
        if owns_read:
            self.journal._db.execute("BEGIN")
        try:
            view = self._view(case_id)
            state_key = "declared_review:" + self.account_ref + ":" + case_id
            expected_projection = (
                {
                    "sequences": [item.sequence for item in view.receipts],
                    "conflict": view.conflict,
                }
                if view.receipts
                else None
            )
            if self.journal.read_state(state_key) != expected_projection:
                raise JournalError(
                    "review projection cache disagrees with immutable evidence"
                )
            expected = {
                case_id + ":" + item.step: {
                    "hash": "sha256:"
                    + hashlib.sha256(
                        self._identity(case_id, item.step, item.exact_bytes)
                    ).hexdigest(),
                    "sequence": item.sequence,
                }
                for item in view.receipts
            }
            actual = {
                key: value
                for key, value in self.journal.read_declared_review_evidence_cache(
                    self.account_ref
                ).items()
                if key.startswith(case_id + ":")
            }
            if actual != expected:
                raise JournalError(
                    "review evidence cache disagrees with immutable evidence"
                )
            return True
        finally:
            if owns_read:
                self.journal._db.rollback()
