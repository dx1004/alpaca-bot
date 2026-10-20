"""Pure W3 draft coverage computation, never preregistration or source acceptance.

Inputs are canonical trace/review summaries supplied by a separate evidence
adapter, not an append-only scanner log. Repeated identical summaries dedupe;
conflicting summaries cannot become independent cases. This module cannot verify
an exchange calendar, reviewers, source provenance or an accepted release freeze.
It accepts no returns or P&L and grants no operational authority.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

SCENARIOS = (
    "LOST_RESPONSE",
    "DISCONNECT_RECONNECT",
    "PERSISTENCE_FAILURE",
    "CANCEL_FILL_RACE",
    "PARTIAL_FILL_RESTART",
    "BOTH_EXITS_REVERSE_INVENTORY",
)
FORMAL_GATES = (
    "PRE_REGISTRATION_DRAFT_NOT_ACCEPTED",
    "REGISTRATION_CALENDAR_UNVERIFIED",
    "RELEASE_FREEZE_UNVERIFIED",
    "SOURCE_AND_REVIEW_ATTESTATIONS_UNVERIFIED",
)


@dataclass(frozen=True)
class PlannedSession:
    session_id: str
    open_at: datetime
    close_at: datetime


@dataclass(frozen=True)
class DraftPlan:
    window_id: str
    manifest_ref: str
    rubric_ref: str
    enabled_cells: tuple[str, ...]
    sessions: tuple[PlannedSession, ...]


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    status: str  # AVAILABLE, OUTAGE, UNAVAILABLE: recorded downtime is evidence.
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    manifest_ref: str | None
    periods: tuple[Period, ...]
    natural_candidate_ids: tuple[str, ...] | None = None
    census_ref: str | None = None


@dataclass(frozen=True)
class SourceStamp:
    evidence_ref: str
    available_at: datetime | None


@dataclass(frozen=True)
class FidelityReview:
    expected_decision: str | None
    reviewer_ref: str | None
    rubric_ref: str | None
    evidence_ref: str | None


@dataclass(frozen=True)
class CandidateTrace:
    candidate_id: str
    cell: str
    session_id: str
    decision_at: datetime
    manifest_ref: str | None
    origin: str  # NATURAL, ENGINEERING_FIXTURE, RESEARCH_EXPOSED
    decision: str  # ENTRY, NO_ENTRY, UNAVAILABLE, PENDING
    audit_complete: bool
    inputs_complete: bool
    strategy_risk_reached: bool
    sources: tuple[SourceStamp, ...]
    rule_refs: tuple[str, ...]
    review: FidelityReview | None
    enrollment_ref: str | None = None
    lifecycle: str = "NONE"  # NONE, OWNED_FILLED_FLAT, UNRESOLVED
    lifecycle_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioRecord:
    group: str
    result: str  # PASS, FAIL, MISSING
    manifest_ref: str | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class EndState:
    assessed_at: datetime
    reconciled_owned_flat: bool
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class RunEvidence:
    as_of: datetime
    started_at: datetime | None
    sessions: tuple[SessionRecord, ...] = ()
    candidates: tuple[CandidateTrace, ...] = ()
    scenarios: tuple[ScenarioRecord, ...] = ()
    final_state: EndState | None = None
    aborted_at: datetime | None = None
    primary_violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CellCoverage:
    cell: str
    entry_ids: tuple[str, ...]
    no_entry_ids: tuple[str, ...]


@dataclass(frozen=True)
class DraftReport:
    criteria_result: str
    declared_run_state: str
    failure_codes: tuple[str, ...]
    inconclusive_codes: tuple[str, ...]
    cells: tuple[CellCoverage, ...]
    natural_candidate_count: int
    duplicate_candidate_rows: int
    fixture_or_exposed_count: int
    natural_filled_flat_ids: tuple[str, ...]
    covered_sessions: tuple[str, ...]
    missing_sessions: tuple[str, ...]
    planned_microseconds: int
    recorded_microseconds: int | None
    available_microseconds: int | None
    formal_gates: tuple[str, ...] = FORMAL_GATES
    accepted_freeze_verified: bool = False
    source_evidence_verified: bool = False
    completed_formal_report: bool = False
    runtime_authorized: bool = False


def _utc(at: datetime) -> None:
    if type(at) is not datetime or at.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC report clock required")


def _text(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _refs(values: tuple[str, ...]) -> bool:
    return bool(values) and all(_text(value) for value in values)


def _choice(value: str, choices: tuple[str, ...]) -> None:
    if type(value) is not str or value not in choices:
        raise ValueError("unknown draft report category")


def _periods(
    planned: PlannedSession, record: SessionRecord, as_of: datetime
) -> tuple[bool, int | None, int | None]:
    cursor, complete, recorded, available = planned.open_at, True, 0, 0
    for period in sorted(record.periods, key=lambda item: item.start):
        _utc(period.start)
        _utc(period.end)
        _choice(period.status, ("AVAILABLE", "OUTAGE", "UNAVAILABLE"))
        if not (
            planned.open_at <= period.start < period.end <= planned.close_at
            and cursor <= period.start
            and period.end <= as_of
        ) or not _refs(period.evidence_refs):
            return False, None, None
        complete &= cursor == period.start
        elapsed = (period.end - period.start) // timedelta(microseconds=1)
        recorded += elapsed
        available += elapsed if period.status == "AVAILABLE" else 0
        cursor = period.end
    return complete and cursor == planned.close_at, recorded, available


def evaluate_paper_draft(plan: DraftPlan, run: RunEvidence) -> DraftReport:
    """Compute declared-input criteria; even a positive result is not paper PASS.

    Completeness/provenance labels are attestations whose source adapter and
    independent reviewers still need acceptance. Every result retains the formal
    gates above, including a claimed one-session or otherwise unverified calendar.
    """
    _utc(run.as_of)
    if not _text(plan.window_id) or len(set(plan.enabled_cells)) != len(
        plan.enabled_cells
    ):
        raise ValueError("unique declared window/cell identities required")
    if any(not _text(cell) for cell in plan.enabled_cells):
        raise ValueError("nonempty declared cells required")
    planned: dict[str, PlannedSession] = {}
    previous_close: datetime | None = None
    for session in plan.sessions:
        _utc(session.open_at)
        _utc(session.close_at)
        if (
            not _text(session.session_id)
            or session.session_id in planned
            or session.open_at >= session.close_at
            or (previous_close is not None and session.open_at <= previous_close)
        ):
            raise ValueError("unique chronological declared sessions required")
        planned[session.session_id] = session
        previous_close = session.close_at
    failures = {"PRIMARY_VIOLATION:" + value for value in run.primary_violations}
    missing: set[str] = set()
    if not _text(plan.manifest_ref) or not _text(plan.rubric_ref):
        missing.add("PLAN_IDENTITY_MISSING")
    if not plan.enabled_cells:
        missing.add("NO_ENABLED_CELLS")
    if not planned:
        missing.add("NO_PLANNED_SESSIONS")
    if run.started_at is not None:
        _utc(run.started_at)
        if run.started_at > run.as_of:
            raise ValueError("report predates declared start")
        if planned and run.started_at > plan.sessions[0].open_at:
            failures.add("LATE_START_OR_PARTIAL_WINDOW")
    else:
        missing.add("NOT_STARTED")
    if run.aborted_at is not None:
        _utc(run.aborted_at)
        if run.started_at is None or not run.started_at <= run.aborted_at <= run.as_of:
            raise ValueError("abort requires a started run and visible abort clock")
        failures.add("STUDY_ABORTED")

    def identity(ref: str | None, scope: str) -> bool:
        if not _text(ref):
            missing.add(scope + ":MANIFEST_MISSING")
            return False
        if ref != plan.manifest_ref:
            failures.add(scope + ":MIXED_VERSION")
            return False
        return True

    records: dict[str, SessionRecord] = {}
    for record in run.sessions:
        if record.session_id not in planned:
            failures.add("UNPLANNED_SESSION")
            continue
        if record.session_id in records and records[record.session_id] != record:
            failures.add("SESSION_RECORD_CONFLICT")
        records.setdefault(record.session_id, record)
    covered: list[str] = []
    recorded_us: int | None = 0
    available_us: int | None = 0
    for session in plan.sessions:
        observed = records.get(session.session_id)
        if observed is None:
            missing.add("SESSION_EVIDENCE_MISSING:" + session.session_id)
            continue
        complete, recorded, available = _periods(session, observed, run.as_of)
        if identity(observed.manifest_ref, "SESSION") and complete:
            covered.append(session.session_id)
        else:
            missing.add("SESSION_EVIDENCE_INCOMPLETE:" + session.session_id)
        recorded_us = (
            None if recorded_us is None or recorded is None else recorded_us + recorded
        )
        available_us = (
            None
            if available_us is None or available is None
            else available_us + available
        )

    traces: dict[str, CandidateTrace] = {}
    duplicates = 0
    for trace in run.candidates:
        if not _text(trace.candidate_id):
            raise ValueError("stable candidate identity required")
        prior = traces.get(trace.candidate_id)
        if prior is not None:
            duplicates += 1
            if prior != trace:
                failures.add("CANDIDATE_ID_CONFLICT")
        traces.setdefault(trace.candidate_id, trace)
    census: dict[str, str] = {}
    for session in plan.sessions:
        census_record = records.get(session.session_id)
        if (
            census_record is None
            or census_record.natural_candidate_ids is None
            or not _text(census_record.census_ref)
        ):
            missing.add("SESSION_CENSUS_MISSING:" + session.session_id)
            continue
        ids = census_record.natural_candidate_ids
        if len(set(ids)) != len(ids) or any(not _text(cid) for cid in ids):
            failures.add("SESSION_CENSUS_ID_CONFLICT")
        for cid in ids:
            if cid in census and census[cid] != session.session_id:
                failures.add("SESSION_CENSUS_ID_CONFLICT")
            census.setdefault(cid, session.session_id)
    for cid, session_id in census.items():
        found = traces.get(cid)
        if found is None or found.origin != "NATURAL":
            missing.add("CENSUS_TRACE_MISSING:" + cid)
        elif found.session_id != session_id:
            failures.add("CENSUS_TRACE_SESSION_CONFLICT")
    entries: dict[str, list[str]] = {cell: [] for cell in plan.enabled_cells}
    negatives: dict[str, list[str]] = {cell: [] for cell in plan.enabled_cells}
    filled_flat: list[str] = []
    source_times: dict[str, datetime | None] = {}
    natural = fixtures = 0
    for cid, trace in sorted(traces.items()):
        _utc(trace.decision_at)
        _choice(trace.origin, ("NATURAL", "ENGINEERING_FIXTURE", "RESEARCH_EXPOSED"))
        _choice(trace.decision, ("ENTRY", "NO_ENTRY", "UNAVAILABLE", "PENDING"))
        _choice(trace.lifecycle, ("NONE", "OWNED_FILLED_FLAT", "UNRESOLVED"))
        for value in (
            trace.audit_complete,
            trace.inputs_complete,
            trace.strategy_risk_reached,
        ):
            if type(value) is not bool:
                raise ValueError("strict trace completeness flags required")
        if trace.origin != "NATURAL":
            fixtures += 1
            continue
        natural += 1
        if run.started_at is None:
            failures.add("NATURAL_TRACE_WITHOUT_START")
        elif trace.decision_at < run.started_at:
            failures.add("NATURAL_TRACE_BEFORE_DECLARED_START")
        if trace.cell not in entries or trace.session_id not in planned:
            failures.add("UNREGISTERED_CANDIDATE_CELL_OR_SESSION")
            continue
        complete = identity(trace.manifest_ref, "CANDIDATE")
        if cid not in census:
            missing.add("NATURAL_TRACE_CENSUS_UNVERIFIED:" + cid)
            complete = False
        session = planned[trace.session_id]
        if not session.open_at <= trace.decision_at <= session.close_at:
            failures.add("OUTSIDE_DECLARED_SESSION")
            complete = False
        if (
            not trace.audit_complete
            or not _text(trace.enrollment_ref)
            or not trace.sources
            or not _refs(trace.rule_refs)
            or (trace.decision in {"ENTRY", "NO_ENTRY"} and not trace.inputs_complete)
            or (trace.decision == "ENTRY" and not trace.strategy_risk_reached)
            or (trace.lifecycle != "NONE" and not _refs(trace.lifecycle_refs))
            or trace.decision == "PENDING"
            or trace.decision_at > run.as_of
        ):
            complete = False
        if trace.lifecycle == "OWNED_FILLED_FLAT" and trace.decision != "ENTRY":
            failures.add("FILLED_LIFECYCLE_WITHOUT_ENTRY")
            complete = False
        for source in trace.sources:
            if (
                source.evidence_ref in source_times
                and source_times[source.evidence_ref] != source.available_at
            ):
                failures.add("SOURCE_ID_CONFLICT")
            source_times.setdefault(source.evidence_ref, source.available_at)
            if not _text(source.evidence_ref) or source.available_at is None:
                complete = False
            else:
                _utc(source.available_at)
                if source.available_at > trace.decision_at:
                    failures.add("LOOK_AHEAD")
                    complete = False
        review = trace.review
        if (
            review is None
            or not all(
                _text(value)
                for value in (
                    review.reviewer_ref,
                    review.evidence_ref,
                    review.rubric_ref,
                )
            )
            or review.expected_decision is None
        ):
            complete = False
        else:
            _choice(review.expected_decision, ("ENTRY", "NO_ENTRY", "UNAVAILABLE"))
            if review.rubric_ref != plan.rubric_ref:
                failures.add("MIXED_REVIEW_RUBRIC")
                complete = False
            if review.expected_decision != trace.decision:
                failures.add("RULE_MISMATCH")
                complete = False
        if not complete:
            missing.add("CANDIDATE_AUDIT_INCOMPLETE:" + cid)
        witness = (
            complete
            and trace.inputs_complete
            and _refs(trace.rule_refs)
            and run.started_at is not None
        )
        if witness and trace.decision == "ENTRY" and trace.strategy_risk_reached:
            entries[trace.cell].append(cid)
            if trace.lifecycle == "OWNED_FILLED_FLAT" and _refs(trace.lifecycle_refs):
                filled_flat.append(cid)
        elif witness and trace.decision == "NO_ENTRY":
            negatives[trace.cell].append(cid)
        if trace.lifecycle == "UNRESOLVED":
            missing.add("CANDIDATE_LIFECYCLE_UNRESOLVED:" + cid)
    for cell in plan.enabled_cells:
        if not entries[cell]:
            missing.add("NATURAL_ENTRY_MISSING:" + cell)
        if not negatives[cell]:
            missing.add("NATURAL_NO_ENTRY_MISSING:" + cell)
    if not filled_flat:
        missing.add("NATURAL_OWNED_FILLED_FLAT_MISSING")

    scenarios: dict[str, ScenarioRecord] = {}
    for scenario in run.scenarios:
        _choice(scenario.group, SCENARIOS)
        _choice(scenario.result, ("PASS", "FAIL", "MISSING"))
        if scenario.group in scenarios and scenarios[scenario.group] != scenario:
            failures.add("SCENARIO_RECORD_CONFLICT")
        scenarios.setdefault(scenario.group, scenario)
        if scenario.result == "FAIL":
            failures.add("SCENARIO_FAILED:" + scenario.group)
    for group in SCENARIOS:
        outcome = scenarios.get(group)
        if outcome is None or not (
            identity(outcome.manifest_ref, "SCENARIO")
            and outcome.result == "PASS"
            and _refs(outcome.evidence_refs)
        ):
            missing.add("SCENARIO_INCOMPLETE:" + group)
    ended = bool(planned) and run.as_of >= plan.sessions[-1].close_at
    if not ended:
        missing.add("PLANNED_WINDOW_NOT_FINISHED")
    final = run.final_state
    flat = False
    if final is not None:
        _utc(final.assessed_at)
        if type(final.reconciled_owned_flat) is not bool:
            raise ValueError("strict final ownership result required")
        flat = (
            ended
            and plan.sessions[-1].close_at <= final.assessed_at <= run.as_of
            and final.reconciled_owned_flat
            and _refs(final.evidence_refs)
            and not any(
                trace.origin == "NATURAL" and trace.lifecycle == "UNRESOLVED"
                for trace in traces.values()
            )
        )
    if not flat:
        missing.add("FINAL_OWNED_STATE_UNRESOLVED")
    state = (
        "NOT_STARTED"
        if run.started_at is None
        else "ABORTED"
        if run.aborted_at is not None
        else "IN_PROGRESS"
        if not ended
        else "COMPLETED"
        if flat and len(covered) == len(planned)
        else "INCOMPLETE"
    )
    verdict = (
        "FAIL" if failures else "INCONCLUSIVE" if missing else "DRAFT_CRITERIA_MET"
    )
    return DraftReport(
        verdict,
        state,
        tuple(sorted(failures)),
        tuple(sorted(missing)),
        tuple(
            CellCoverage(cell, tuple(entries[cell]), tuple(negatives[cell]))
            for cell in plan.enabled_cells
        ),
        natural,
        duplicates,
        fixtures,
        tuple(filled_flat),
        tuple(covered),
        tuple(
            session.session_id
            for session in plan.sessions
            if session.session_id not in covered
        ),
        sum(
            (session.close_at - session.open_at) // timedelta(microseconds=1)
            for session in plan.sessions
        ),
        recorded_us,
        available_us,
    )
