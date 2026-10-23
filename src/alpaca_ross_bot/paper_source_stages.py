"""Immutable scanner-to-report stage inventory, never natural enrollment.

Reported screen outcomes are source observations, not recomputed rule verdicts.
Raw calendar/history bytes are authenticated here, not their strategy semantics.
"""

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime

from .events import EventEnvelope
from .journal import Journal, JournalError
from .research_spec import load_accepted_research_spec
from .scanner_contract import (
    AcceptedScannerContract,
    validate_accepted_scanner_contract,
)
from .scanner_discovery_audit import (
    _clock,
    _json,
    _raw,
    _symbols,
    _utc,
    audit_iex_asset_discovery,
    audit_scanner_event_census,
)


@dataclass(frozen=True)
class StageSource:
    declared_journal_scope: str
    sequence: int
    payload_ref: str
    available_at: datetime


@dataclass(frozen=True)
class SourceStage:
    source: StageSource
    symbol: str | None
    stage: str
    input_sources: tuple[StageSource, ...] = ()
    decision_cutoff: datetime | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopedDiscoverySummary:
    """Existing denominator results with exclusively scoped outward references.

    Per-manifest tuples contain source, requested/returned/missing counts and
    existing audit reasons; they are not new symbol/candidate denominators.
    """

    declared_journal_scope: str
    event_sources: tuple[StageSource, ...]
    catalog_event_count: int
    snapshot_event_count: int
    manifest_event_count: int
    manifest_counts: tuple[tuple[StageSource, int, int, int, tuple[str, ...]], ...]
    audit_errors: tuple[tuple[StageSource, str, str], ...]
    orphan_catalog_sources: tuple[StageSource, ...]
    orphan_snapshot_sources: tuple[StageSource, ...]
    excluded_kind_sources: tuple[tuple[StageSource, str], ...]
    audited_unique_requested_observations: int
    audited_unique_returned_observations: int
    audited_unique_missing_observations: int
    coverage_label: str = "NOT_SIP_EQUIVALENT"


@dataclass(frozen=True)
class PaperSourceStages:
    declared_journal_scope: str
    through_sequence: int
    as_of: datetime
    discovery: ScopedDiscoverySummary
    stages: tuple[SourceStage, ...]
    origin: str = "UNVERIFIED_SOURCE_OBSERVATIONS_NOT_FORMAL_ENROLLMENT"
    natural_candidate_ids: None = None
    enrollment_accepted: bool = False
    source_admission_accepted: bool = False
    session_coverage_accepted: bool = False
    formal_sample_eligible: bool = False
    runtime_authorized: bool = False
    journal_scope_authenticated: bool = False


def _stamp(event: EventEnvelope, journal_scope: str) -> StageSource:
    return StageSource(
        journal_scope, event.sequence, event.payload_ref, event.available_at
    )


def _refs(value: object) -> tuple[int, ...]:
    if (
        type(value) is not list
        or not value
        or any(type(item) is not int or item < 1 for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("INPUT_REFERENCES_INVALID")
    return tuple(value)


def inspect_paper_source_stages(
    journal: Journal,
    *,
    journal_scope: str,
    through_sequence: int,
    as_of: datetime,
    scanner_contract: AcceptedScannerContract,
    research_spec_bytes: bytes,
) -> PaperSourceStages:
    """Read a contiguous fixed prefix; preserve unknowns and result availability.

    No journal/global candidate IDs are manufactured. Every StageSource retains
    the exact caller-declared namespace; its ownership/authenticity is unproven.
    All existing archives stay unenrolled, regardless of the reported outcomes.
    """
    _utc(as_of)
    if (
        type(journal_scope) is not str
        or not journal_scope.strip()
        or journal_scope != journal_scope.strip()
        or any(ord(char) < 32 for char in journal_scope)
    ):
        raise ValueError("DECLARED_JOURNAL_SCOPE_INVALID")
    validate_accepted_scanner_contract(scanner_contract)
    spec = load_accepted_research_spec(research_spec_bytes, scanner_contract)
    if (
        type(through_sequence) is not int
        or not 0 <= through_sequence <= journal.read_evidence_sequence()
    ):
        raise ValueError("PREFIX_HORIZON_INVALID")
    prefix: dict[int, EventEnvelope] = {}
    payloads: dict[int, bytes] = {}
    for sequence in range(1, through_sequence + 1):
        pair = journal.read_event(sequence)
        if pair is None:
            raise JournalError("PREFIX_EVENT_MISSING")
        event, raw = pair
        if event.sequence != sequence or event.payload_ref != (
            "sha256:" + hashlib.sha256(raw).hexdigest()
        ):
            raise JournalError("PREFIX_EVENT_INTEGRITY_INVALID")
        prefix[sequence], payloads[sequence] = event, raw
    census = audit_scanner_event_census(
        journal, through_sequence=through_sequence, decision_at=as_of
    )
    stages: list[SourceStage] = []
    for event in prefix.values():
        if event.available_at > as_of:
            continue
        if event.event_kind == "ADAPTED_SCREEN_RESULT" and (
            event.source_id != "adapted_scanner" or event.feed != "iex"
        ):
            raise ValueError("SCREEN_EVENT_IDENTITY_INVALID")
        if event.source_id == "alpaca_scanner_data":
            issues = tuple(
                code for seq, _, code in census.audit_errors if seq == event.sequence
            )
            if event.event_kind == "IEX_SNAPSHOT_RAW_RESPONSE":
                try:
                    _, metadata, response = _raw(
                        journal, event.sequence, event.event_kind, "iex", as_of
                    )
                    requested = _symbols(metadata.get("requested_symbols"))
                    if type(response) is not dict or set(response) - set(requested):
                        raise ValueError("SNAPSHOT_SYMBOL_MAPPING_INVALID")
                    orphan = any(
                        seq == event.sequence for seq, _ in census.orphan_snapshot_refs
                    )
                    for symbol in requested:
                        stages.append(
                            SourceStage(
                                _stamp(event, journal_scope),
                                symbol,
                                "OBSERVED"
                                if symbol in response
                                else "SNAPSHOT_MISSING",
                                issues=issues
                                + (("ORPHAN_RAW_SNAPSHOT",) if orphan else ()),
                            )
                        )
                except ValueError as exc:
                    stages.append(
                        SourceStage(
                            _stamp(event, journal_scope),
                            None,
                            "MALFORMED",
                            issues=(str(exc),),
                        )
                    )
            else:
                stages.append(
                    SourceStage(
                        _stamp(event, journal_scope),
                        None,
                        "SOURCE_EVENT",
                        issues=issues,
                    )
                )
        elif event.event_kind == "ADAPTED_SCREEN_RESULT":
            result_start = len(stages)
            try:
                document = _json(payloads[event.sequence])
                if type(document) is not dict:
                    raise ValueError("SCREEN_DOCUMENT_INVALID")
                expected = {
                    "profile_id": scanner_contract.profile_id,
                    "contract_sha256": scanner_contract.sha256,
                    "rule_ids": list(scanner_contract.rule_ids),
                    "research_version_id": spec.version_id,
                    "research_spec_sha256": spec.sha256,
                    "research_rule_ids": list(spec.rule_ids),
                    "research_evaluation_scope": "P02_SCANNER_ONLY",
                }
                if any(document.get(key) != value for key, value in expected.items()):
                    raise ValueError("SCREEN_CONTRACT_IDENTITY_INVALID")
                if document.get("research_status") != spec.status:
                    raise ValueError("SCREEN_RESEARCH_STATUS_INVALID")
                cutoff = _clock(document.get("decision_at"))
                if cutoff > event.system_first_seen_at or event.external_clock_conflict:
                    raise ValueError("SCREEN_CLOCK_INVALID")
                inputs = _refs(document.get("input_sequences"))
                manifest_batches: set[int] = set()
                manifest_catalogs: set[int] = set()
                for sequence in inputs:
                    parent = prefix.get(sequence)
                    if (
                        parent is None
                        or sequence >= event.sequence
                        or parent.available_at > cutoff
                    ):
                        raise ValueError("SCREEN_INPUT_ANCESTRY_INVALID")
                    if parent.event_kind == "IEX_ASSET_SNAPSHOT_SCAN_MANIFEST":
                        audit = audit_iex_asset_discovery(
                            journal, manifest_sequence=sequence, decision_at=cutoff
                        )
                        if not audit.request_coverage_complete or not {
                            audit.catalog_sequence,
                            *audit.snapshot_batch_sequences,
                        } <= set(inputs):
                            raise ValueError("SCREEN_MANIFEST_ANCESTRY_INVALID")
                        manifest_batches.update(audit.snapshot_batch_sequences)
                        manifest_catalogs.add(audit.catalog_sequence)
                    else:
                        feeds = {
                            "ASSET_DIRECTORY_RAW_RESPONSE": "asset_master",
                            "IEX_SNAPSHOT_RAW_RESPONSE": "iex",
                            "ADAPTATION_HISTORY_RAW_RESPONSE": "iex",
                            "ADAPTATION_CALENDAR_RAW_RESPONSE": "calendar",
                        }
                        if parent.event_kind not in feeds:
                            raise ValueError("SCREEN_INPUT_KIND_UNMAPPED")
                        _raw(
                            journal,
                            sequence,
                            parent.event_kind,
                            feeds[parent.event_kind],
                            cutoff,
                        )
                if not manifest_catalogs:
                    raise ValueError("SCREEN_MANIFEST_MISSING")
                rows = document.get("results")
                if type(rows) is not list:
                    raise ValueError("SCREEN_ROWS_INVALID")
                if not rows:
                    stages.append(
                        SourceStage(
                            _stamp(event, journal_scope),
                            None,
                            "EMPTY_SCREEN_ROWS",
                            tuple(_stamp(prefix[seq], journal_scope) for seq in inputs),
                            cutoff,
                            ("NO_SCREEN_ROWS_RECORDED", "NOT_A_ZERO_CANDIDATE_CENSUS"),
                        )
                    )
                symbols: set[str] = set()
                for row in rows:
                    if type(row) is not dict:
                        raise ValueError("SCREEN_ROW_INVALID")
                    row_symbol = row.get("symbol")
                    if (
                        type(row_symbol) is not str
                        or not row_symbol.strip()
                        or row_symbol != row_symbol.strip()
                        or row_symbol in symbols
                    ):
                        raise ValueError("SCREEN_ROW_SYMBOL_CONFLICT")
                    symbols.add(row_symbol)
                    if (
                        any(
                            row.get(key) != value
                            for key, value in expected.items()
                            if key != "research_evaluation_scope"
                        )
                        or row.get("research_evaluation_scope") != "P02_SCANNER_ONLY"
                    ):
                        raise ValueError("SCREEN_ROW_CONTRACT_IDENTITY_INVALID")
                    if (
                        row.get("coverage_label") != "NOT_SIP_EQUIVALENT"
                        or row.get("full_market_coverage") is not False
                        or row.get("order_authority") is not False
                    ):
                        raise ValueError("SCREEN_ROW_SCOPE_INVALID")
                    refs = _refs(row.get("input_sequence_refs"))
                    if not set(refs) <= set(inputs):
                        raise ValueError("SCREEN_ROW_INPUT_ANCESTRY_INVALID")
                    snapshot_sources = []
                    for sequence in refs:
                        parent = prefix[sequence]
                        if parent.event_kind == "IEX_SNAPSHOT_RAW_RESPONSE":
                            _, _, response = _raw(
                                journal, sequence, parent.event_kind, "iex", cutoff
                            )
                            if sequence not in manifest_batches:
                                raise ValueError("SCREEN_SNAPSHOT_OUTSIDE_MANIFEST")
                            if type(response) is dict and row_symbol in response:
                                snapshot_sources.append(sequence)
                    if not snapshot_sources:
                        raise ValueError("SCREEN_ROW_SYMBOL_SOURCE_MISSING")
                    status = row.get("status")
                    known = type(status) is str and status in (
                        "SCREEN_MATCH",
                        "RULE_NOT_MET",
                        "INPUT_UNAVAILABLE",
                    )
                    stages.append(
                        SourceStage(
                            _stamp(event, journal_scope),
                            row_symbol,
                            "REPORTED_" + str(status)
                            if known
                            else "UNMAPPED_SCREEN_STATUS",
                            tuple(_stamp(prefix[seq], journal_scope) for seq in refs),
                            cutoff,
                            (
                                "REPORTED_OUTCOME_NOT_RECOMPUTED",
                                "HISTORY_CALENDAR_SEMANTICS_UNVERIFIED",
                            )
                            + (() if known else ("UNKNOWN_SCREEN_STATUS",)),
                        )
                    )
            except ValueError as exc:
                for index in range(result_start, len(stages)):
                    stages[index] = replace(
                        stages[index],
                        stage="REJECTED_SCREEN_ROW",
                        issues=stages[index].issues
                        + ("SCREEN_EVENT_MALFORMED", str(exc)),
                    )
                stages.append(
                    SourceStage(
                        _stamp(event, journal_scope),
                        None,
                        "MALFORMED_SCREEN",
                        issues=(str(exc),),
                    )
                )
        elif event.source_id == "alpaca_iex":
            stages.append(
                SourceStage(
                    _stamp(event, journal_scope),
                    None,
                    "UNMAPPED_DERIVED_EVENT",
                    issues=("DERIVED_EVENT_NOT_USED_AS_SCREEN_INPUT",),
                )
            )
        elif event.source_id == "adapted_scanner":
            stages.append(
                SourceStage(
                    _stamp(event, journal_scope),
                    None,
                    "UNMAPPED_SCREEN_EVENT",
                    issues=("SCREEN_EVENT_KIND_UNMAPPED",),
                )
            )
    # Multiple recorded results are retained as observations, never extra
    # candidates. Flag repeated decision/input identities rather than overwrite.
    seen: dict[tuple[object, ...], SourceStage] = {}
    for index, stage in enumerate(stages):
        if stage.decision_cutoff is None:
            continue
        key = stage.symbol, stage.decision_cutoff, stage.input_sources
        prior = seen.get(key)
        if prior is not None:
            code = (
                "REPEATED_SCREEN_OBSERVATION"
                if prior.source.payload_ref == stage.source.payload_ref
                else "CONFLICTING_SCREEN_OBSERVATION"
            )
            stages[index] = replace(stage, issues=stage.issues + (code,))
        else:
            seen[key] = stage
    discovery = ScopedDiscoverySummary(
        journal_scope,
        tuple(_stamp(prefix[seq], journal_scope) for seq, _, _ in census.event_refs),
        census.catalog_event_count,
        census.snapshot_event_count,
        census.manifest_event_count,
        tuple(
            (
                _stamp(prefix[audit.manifest_sequence], journal_scope),
                len(audit.requested_symbols),
                len(audit.returned_symbols),
                len(audit.missing_symbols),
                audit.reasons,
            )
            for audit in census.manifest_audits
        ),
        tuple(
            (_stamp(prefix[seq], journal_scope), kind, code)
            for seq, kind, code in census.audit_errors
        ),
        tuple(
            _stamp(prefix[seq], journal_scope) for seq, _ in census.orphan_catalog_refs
        ),
        tuple(
            _stamp(prefix[seq], journal_scope) for seq, _ in census.orphan_snapshot_refs
        ),
        tuple(
            (_stamp(prefix[seq], journal_scope), kind)
            for seq, _, kind in census.excluded_kind_refs
        ),
        census.audited_unique_requested_observations,
        census.audited_unique_returned_observations,
        census.audited_unique_missing_observations,
    )
    return PaperSourceStages(
        journal_scope, through_sequence, as_of, discovery, tuple(stages)
    )
