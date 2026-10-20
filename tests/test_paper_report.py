"""Synthetic declared records test draft arithmetic, never natural source proof."""

import inspect
import json
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.paper_report import (
    FORMAL_GATES,
    SCENARIOS,
    CandidateTrace,
    DraftPlan,
    DraftReport,
    EndState,
    FidelityReview,
    Period,
    PlannedSession,
    RunEvidence,
    ScenarioRecord,
    SessionRecord,
    SourceStamp,
    evaluate_paper_draft,
)

ROOT = Path(__file__).parents[1]


def examples() -> tuple[DraftPlan, RunEvidence]:
    """Reuse the draft expected dates; this deliberately supplies no accepted freeze."""
    raw = json.loads(
        (ROOT / "evidence/w3-paper-calendar-20261026-20261120.json").read_bytes()
    )
    sessions = tuple(
        PlannedSession(
            row["date"],
            datetime.fromisoformat(row["open_utc"]),
            datetime.fromisoformat(row["close_utc"]),
        )
        for row in raw["sessions"]
    )
    plan = DraftPlan(
        "synthetic-declared-window",
        "test-manifest",
        "test-rubric",
        ("setup/1m", "setup/5m"),
        sessions,
    )
    records = tuple(
        SessionRecord(
            session.session_id,
            plan.manifest_ref,
            (
                Period(
                    session.open_at,
                    session.close_at,
                    "AVAILABLE",
                    ("session-" + session.session_id,),
                ),
            ),
        )
        for session in sessions
    )
    traces = []
    for cell in plan.enabled_cells:
        for decision in ("ENTRY", "NO_ENTRY"):
            cid = cell + ":" + decision
            at = sessions[0].open_at + timedelta(minutes=5)
            traces.append(
                CandidateTrace(
                    cid,
                    cell,
                    sessions[0].session_id,
                    at,
                    plan.manifest_ref,
                    "NATURAL",
                    decision,
                    True,
                    True,
                    decision == "ENTRY",
                    (SourceStamp(cid + ":raw", at),),
                    ("declared-rule",),
                    FidelityReview(
                        decision,
                        "test-reviewer-reference",
                        plan.rubric_ref,
                        cid + ":review",
                    ),
                    enrollment_ref=cid + ":enrollment",
                    lifecycle="OWNED_FILLED_FLAT" if decision == "ENTRY" else "NONE",
                    lifecycle_refs=(cid + ":lifecycle",) if decision == "ENTRY" else (),
                )
            )
    records = tuple(
        replace(
            record,
            natural_candidate_ids=tuple(
                trace.candidate_id
                for trace in traces
                if trace.session_id == record.session_id
            ),
            census_ref="test-census:" + record.session_id,
        )
        for record in records
    )
    run = RunEvidence(
        sessions[-1].close_at,
        sessions[0].open_at,
        records,
        tuple(traces),
        tuple(
            ScenarioRecord(group, "PASS", plan.manifest_ref, (group + ":test-case",))
            for group in SCENARIOS
        ),
        EndState(sessions[-1].close_at, True, ("test-final-reconciliation",)),
    )
    return plan, run


def test_all_declared_draft_criteria_met_is_never_formal_pass_or_freeze() -> None:
    plan, run = examples()
    report = evaluate_paper_draft(plan, run)
    assert report.criteria_result == "DRAFT_CRITERIA_MET"
    assert report.declared_run_state == "COMPLETED"
    assert report.failure_codes == report.inconclusive_codes == ()
    assert (
        report.natural_candidate_count == 4 and len(report.natural_filled_flat_ids) == 2
    )
    assert all(
        len(cell.entry_ids) == len(cell.no_entry_ids) == 1 for cell in report.cells
    )
    assert len(report.covered_sessions) == 20 and report.missing_sessions == ()
    assert report.planned_microseconds == 20 * 390 * 60 * 1_000_000
    assert (
        report.recorded_microseconds
        == report.available_microseconds
        == report.planned_microseconds
    )
    assert report.formal_gates == FORMAL_GATES
    assert not report.accepted_freeze_verified and not report.source_evidence_verified
    assert not report.completed_formal_report and not report.runtime_authorized


def test_a_claimed_one_day_calendar_never_becomes_four_week_acceptance() -> None:
    plan, run = examples()
    day = plan.sessions[0]
    report = evaluate_paper_draft(
        replace(plan, sessions=(day,)),
        replace(
            run,
            as_of=day.close_at,
            sessions=run.sessions[:1],
            final_state=EndState(day.close_at, True, ("test-final",)),
        ),
    )
    assert report.criteria_result == "DRAFT_CRITERIA_MET"
    assert "REGISTRATION_CALENDAR_UNVERIFIED" in report.formal_gates
    assert not report.completed_formal_report and not report.accepted_freeze_verified


def test_returns_and_pnl_are_not_evaluator_inputs_or_primary_fields() -> None:
    assert tuple(inspect.signature(evaluate_paper_draft).parameters) == ("plan", "run")
    for cls in (DraftPlan, RunEvidence, CandidateTrace, DraftReport):
        assert not any(
            any(word in field.name.lower() for word in ("profit", "pnl", "return"))
            for field in fields(cls)
        )


@pytest.mark.parametrize("origin", ["ENGINEERING_FIXTURE", "RESEARCH_EXPOSED"])
def test_fixture_and_previously_exposed_cases_cannot_supply_natural_witnesses(
    origin: str,
) -> None:
    plan, run = examples()
    report = evaluate_paper_draft(
        plan,
        replace(
            run,
            candidates=tuple(replace(trace, origin=origin) for trace in run.candidates),
        ),
    )
    assert report.criteria_result == "INCONCLUSIVE"
    assert report.natural_candidate_count == 0 and report.fixture_or_exposed_count == 4
    assert not report.natural_filled_flat_ids
    assert all(not cell.entry_ids and not cell.no_entry_ids for cell in report.cells)


def test_exact_repeat_scans_do_not_expand_distinct_candidate_denominators() -> None:
    plan, run = examples()
    report = evaluate_paper_draft(plan, replace(run, candidates=run.candidates * 4))
    assert report.criteria_result == "DRAFT_CRITERIA_MET"
    assert report.natural_candidate_count == 4 and report.duplicate_candidate_rows == 12
    assert all(
        len(cell.entry_ids) == len(cell.no_entry_ids) == 1 for cell in report.cells
    )


def test_candidate_identity_cannot_be_counted_again_in_another_cell() -> None:
    plan, run = examples()
    changed = replace(run.candidates[0], cell=plan.enabled_cells[1])
    report = evaluate_paper_draft(
        plan, replace(run, candidates=run.candidates[:2] + (changed, run.candidates[3]))
    )
    assert report.criteria_result == "FAIL"
    assert "CANDIDATE_ID_CONFLICT" in report.failure_codes
    assert report.natural_candidate_count == 3 and report.duplicate_candidate_rows == 1
    assert report.cells[1].entry_ids == ()


@pytest.mark.parametrize(
    "missing_field", ["review", "reviewer", "rubric", "evidence", "abstention"]
)
def test_missing_or_abstaining_independent_review_never_counts_as_a_witness(
    missing_field: str,
) -> None:
    plan, run = examples()
    case = run.candidates[0]
    assert case.review is not None
    review = (
        None
        if missing_field == "review"
        else replace(
            case.review,
            **{
                "reviewer": {"reviewer_ref": None},
                "rubric": {"rubric_ref": None},
                "evidence": {"evidence_ref": None},
                "abstention": {"expected_decision": None},
            }[missing_field],
        )
    )
    report = evaluate_paper_draft(
        plan,
        replace(run, candidates=(replace(case, review=review),) + run.candidates[1:]),
    )
    assert report.criteria_result == "INCONCLUSIVE" and not report.failure_codes
    assert report.cells[0].entry_ids == ()


@pytest.mark.parametrize(
    "field", ["audit_complete", "inputs_complete", "strategy_risk_reached"]
)
def test_missing_inputs_or_unreached_strategy_risk_are_not_entry_witnesses(
    field: str,
) -> None:
    plan, run = examples()
    original = run.candidates[0]
    case = (
        replace(original, audit_complete=False)
        if field == "audit_complete"
        else replace(original, inputs_complete=False)
        if field == "inputs_complete"
        else replace(original, strategy_risk_reached=False)
    )
    report = evaluate_paper_draft(
        plan, replace(run, candidates=(case,) + run.candidates[1:])
    )
    assert report.criteria_result == "INCONCLUSIVE" and report.cells[0].entry_ids == ()


def test_missing_data_is_not_a_no_entry_setup_example() -> None:
    plan, run = examples()
    negative = run.candidates[1]
    assert negative.review is not None
    missing = replace(
        negative,
        decision="UNAVAILABLE",
        inputs_complete=False,
        review=replace(negative.review, expected_decision="UNAVAILABLE"),
    )
    report = evaluate_paper_draft(
        plan, replace(run, candidates=(run.candidates[0], missing) + run.candidates[2:])
    )
    assert report.criteria_result == "INCONCLUSIVE"
    assert report.cells[0].no_entry_ids == () and not report.failure_codes


def test_fidelity_failure_takes_precedence_over_missing_window_and_coverage() -> None:
    plan, run = examples()
    case = run.candidates[0]
    assert case.review is not None
    mismatch = replace(case, review=replace(case.review, expected_decision="NO_ENTRY"))
    report = evaluate_paper_draft(
        plan, replace(run, sessions=(), candidates=(mismatch,))
    )
    assert report.criteria_result == "FAIL" and "RULE_MISMATCH" in report.failure_codes
    assert report.missing_sessions and report.inconclusive_codes


@pytest.mark.parametrize("change", ["unknown", "future", "conflict"])
def test_source_availability_cannot_be_invented_or_conflict(change: str) -> None:
    plan, run = examples()
    case = run.candidates[0]
    sources = (
        SourceStamp(
            "shared-source",
            None if change == "unknown" else case.decision_at + timedelta(seconds=1),
        ),
    )
    changed = replace(case, sources=sources)
    cases = (changed,) + run.candidates[1:]
    if change == "conflict":
        cases = (
            changed,
            replace(
                cases[1], sources=(SourceStamp("shared-source", case.decision_at),)
            ),
        ) + cases[2:]
    report = evaluate_paper_draft(plan, replace(run, candidates=cases))
    assert report.criteria_result == ("INCONCLUSIVE" if change == "unknown" else "FAIL")
    if change == "future":
        assert "LOOK_AHEAD" in report.failure_codes
    elif change == "conflict":
        assert "SOURCE_ID_CONFLICT" in report.failure_codes


def test_recorded_outage_remains_in_denominator_without_inventing_uptime_sla() -> None:
    plan, run = examples()
    session = plan.sessions[0]
    outage_start = session.open_at + timedelta(hours=3)
    outage_end = outage_start + timedelta(minutes=30)
    record = replace(
        run.sessions[0],
        periods=(
            Period(session.open_at, outage_start, "AVAILABLE", ("before-outage",)),
            Period(outage_start, outage_end, "OUTAGE", ("recorded-outage",)),
            Period(outage_end, session.close_at, "AVAILABLE", ("after-outage",)),
        ),
    )
    report = evaluate_paper_draft(
        plan, replace(run, sessions=(record,) + run.sessions[1:])
    )
    assert report.criteria_result == "DRAFT_CRITERIA_MET"
    assert report.recorded_microseconds == report.planned_microseconds
    assert (
        report.available_microseconds
        == report.planned_microseconds - 30 * 60 * 1_000_000
    )


@pytest.mark.parametrize("fault", ["missing_session", "gap", "overlap", "missing_ref"])
def test_unobserved_or_conflicting_session_periods_are_not_complete(fault: str) -> None:
    plan, run = examples()
    record = run.sessions[0]
    period = record.periods[0]
    if fault == "missing_session":
        records = run.sessions[1:]
    elif fault == "gap":
        records = (
            replace(
                record,
                periods=(replace(period, start=period.start + timedelta(minutes=1)),),
            ),
        ) + run.sessions[1:]
    elif fault == "overlap":
        records = (replace(record, periods=(period, period)),) + run.sessions[1:]
    else:
        records = (
            replace(record, periods=(replace(period, evidence_refs=()),)),
        ) + run.sessions[1:]
    report = evaluate_paper_draft(plan, replace(run, sessions=records))
    assert report.criteria_result == "INCONCLUSIVE"
    assert report.declared_run_state == "INCOMPLETE" and report.missing_sessions
    if fault in {"overlap", "missing_ref"}:
        assert report.recorded_microseconds is None


@pytest.mark.parametrize(
    "fault",
    ["missing", "unknown", "before_end", "open_inventory", "unresolved_lifecycle"],
)
def test_final_owned_state_never_completes_on_missing_or_uncertain_cleanup(
    fault: str,
) -> None:
    plan, run = examples()
    final = run.final_state
    assert final is not None
    if fault == "missing":
        changed = replace(run, final_state=None)
    elif fault == "unknown":
        changed = replace(run, final_state=replace(final, evidence_refs=()))
    elif fault == "before_end":
        changed = replace(
            run,
            final_state=replace(
                final, assessed_at=final.assessed_at - timedelta(seconds=1)
            ),
        )
    elif fault == "open_inventory":
        changed = replace(run, final_state=replace(final, reconciled_owned_flat=False))
    else:
        changed = replace(
            run,
            candidates=(replace(run.candidates[0], lifecycle="UNRESOLVED"),)
            + run.candidates[1:],
        )
    report = evaluate_paper_draft(plan, changed)
    assert (
        report.criteria_result == "INCONCLUSIVE"
        and report.declared_run_state == "INCOMPLETE"
    )
    assert "FINAL_OWNED_STATE_UNRESOLVED" in report.inconclusive_codes


def test_not_started_in_progress_and_aborted_are_distinct_even_without_samples() -> (
    None
):
    plan, run = examples()
    unopened = RunEvidence(plan.sessions[0].open_at - timedelta(seconds=1), None)
    waiting = evaluate_paper_draft(plan, unopened)
    assert (
        waiting.declared_run_state == "NOT_STARTED"
        and waiting.criteria_result == "INCONCLUSIVE"
    )
    started = replace(
        unopened, as_of=plan.sessions[0].open_at, started_at=plan.sessions[0].open_at
    )
    active = evaluate_paper_draft(plan, started)
    assert (
        active.declared_run_state == "IN_PROGRESS"
        and active.criteria_result == "INCONCLUSIVE"
    )
    aborted = evaluate_paper_draft(plan, replace(started, aborted_at=started.as_of))
    assert aborted.declared_run_state == "ABORTED" and aborted.criteria_result == "FAIL"


def test_not_started_cannot_count_claimed_natural_cases() -> None:
    plan, run = examples()
    report = evaluate_paper_draft(plan, replace(run, started_at=None))
    assert (
        report.declared_run_state == "NOT_STARTED" and report.criteria_result == "FAIL"
    )
    assert all(not cell.entry_ids and not cell.no_entry_ids for cell in report.cells)
    assert "NATURAL_TRACE_WITHOUT_START" in report.failure_codes


def test_late_start_or_behavior_breach_cannot_be_repaired_by_full_later_records() -> (
    None
):
    plan, run = examples()
    late = evaluate_paper_draft(
        plan, replace(run, started_at=plan.sessions[0].open_at + timedelta(seconds=1))
    )
    assert (
        late.criteria_result == "FAIL"
        and "LATE_START_OR_PARTIAL_WINDOW" in late.failure_codes
    )
    breach = evaluate_paper_draft(
        plan, replace(run, primary_violations=("UNAUTHORIZED_ACTION",))
    )
    assert (
        breach.criteria_result == "FAIL"
        and "PRIMARY_VIOLATION:UNAUTHORIZED_ACTION" in breach.failure_codes
    )


@pytest.mark.parametrize("fault", ["missing", "failed", "mixed_release", "conflict"])
def test_all_six_engineering_groups_require_same_declared_release_evidence(
    fault: str,
) -> None:
    plan, run = examples()
    first = run.scenarios[0]
    if fault == "missing":
        scenarios = run.scenarios[1:]
    elif fault == "failed":
        scenarios = (replace(first, result="FAIL"),) + run.scenarios[1:]
    elif fault == "mixed_release":
        scenarios = (replace(first, manifest_ref="other-release"),) + run.scenarios[1:]
    else:
        scenarios = run.scenarios + (replace(first, evidence_refs=("changed-result",)),)
    report = evaluate_paper_draft(plan, replace(run, scenarios=scenarios))
    assert report.criteria_result == ("INCONCLUSIVE" if fault == "missing" else "FAIL")


def test_complete_negative_examples_without_natural_fill_still_inconclusive() -> None:
    plan, run = examples()
    report = evaluate_paper_draft(
        plan,
        replace(
            run,
            candidates=tuple(
                replace(case, lifecycle="NONE", lifecycle_refs=())
                for case in run.candidates
            ),
        ),
    )
    assert report.criteria_result == "INCONCLUSIVE"
    assert "NATURAL_OWNED_FILLED_FLAT_MISSING" in report.inconclusive_codes


@pytest.mark.parametrize(
    "fault", ["empty_cells", "empty_sessions", "empty_manifest", "empty_rubric"]
)
def test_missing_declaration_cannot_vacuously_satisfy_draft_criteria(
    fault: str,
) -> None:
    plan, run = examples()
    changed = (
        replace(plan, enabled_cells=())
        if fault == "empty_cells"
        else replace(plan, sessions=())
        if fault == "empty_sessions"
        else replace(plan, manifest_ref="")
        if fault == "empty_manifest"
        else replace(plan, rubric_ref="")
    )
    report = evaluate_paper_draft(changed, run)
    assert report.criteria_result != "DRAFT_CRITERIA_MET"


def test_naive_clock_and_duplicate_or_reordered_planned_sessions_are_rejected() -> None:
    plan, run = examples()
    with pytest.raises(ValueError, match="UTC"):
        evaluate_paper_draft(plan, replace(run, as_of=run.as_of.replace(tzinfo=None)))
    with pytest.raises(ValueError, match="chronological"):
        evaluate_paper_draft(replace(plan, sessions=plan.sessions * 2), run)
    with pytest.raises(ValueError, match="chronological"):
        evaluate_paper_draft(
            replace(plan, sessions=tuple(reversed(plan.sessions))), run
        )


@pytest.mark.parametrize(
    "fault",
    ["unknown", "missing_ref", "omitted_trace", "extra_trace", "conflicting_session"],
)
def test_declared_session_census_is_not_missing_zero_or_selective_trace_list(
    fault: str,
) -> None:
    plan, run = examples()
    record = run.sessions[0]
    if fault == "unknown":
        changed = replace(
            run,
            sessions=(replace(record, natural_candidate_ids=None),) + run.sessions[1:],
        )
    elif fault == "missing_ref":
        changed = replace(
            run, sessions=(replace(record, census_ref=None),) + run.sessions[1:]
        )
    elif fault == "omitted_trace":
        changed = replace(run, candidates=run.candidates[1:])
    elif fault == "extra_trace":
        changed = replace(
            run,
            sessions=(replace(record, natural_candidate_ids=()),) + run.sessions[1:],
        )
    else:
        extra = replace(
            run.sessions[1], natural_candidate_ids=(run.candidates[0].candidate_id,)
        )
        changed = replace(run, sessions=(record, extra) + run.sessions[2:])
    report = evaluate_paper_draft(plan, changed)
    assert report.criteria_result == (
        "FAIL" if fault == "conflicting_session" else "INCONCLUSIVE"
    )


@pytest.mark.parametrize("side", ["before_open", "after_close"])
def test_outside_own_declared_session_cannot_count_a_natural_witness(side: str) -> None:
    plan, run = examples()
    session = plan.sessions[0]
    at = (
        session.open_at - timedelta(seconds=1)
        if side == "before_open"
        else session.close_at + timedelta(seconds=1)
    )
    case = replace(
        run.candidates[0],
        decision_at=at,
        sources=(SourceStamp("out-of-session-source", at),),
    )
    report = evaluate_paper_draft(
        plan, replace(run, candidates=(case,) + run.candidates[1:])
    )
    assert report.criteria_result == "FAIL"
    assert "OUTSIDE_DECLARED_SESSION" in report.failure_codes
    assert report.cells[0].entry_ids == ()


@pytest.mark.parametrize("missing_evidence", ["rule", "lifecycle"])
def test_extra_natural_case_must_be_auditable_after_all_quotas_are_met(
    missing_evidence: str,
) -> None:
    plan, run = examples()
    extra = replace(
        run.candidates[0],
        candidate_id="extra-entry",
        rule_refs=() if missing_evidence == "rule" else ("rule",),
        lifecycle_refs=() if missing_evidence == "lifecycle" else ("lifecycle",),
    )
    record = run.sessions[0]
    assert record.natural_candidate_ids is not None
    record = replace(
        record,
        natural_candidate_ids=record.natural_candidate_ids + (extra.candidate_id,),
    )
    report = evaluate_paper_draft(
        plan,
        replace(
            run,
            sessions=(record,) + run.sessions[1:],
            candidates=run.candidates + (extra,),
        ),
    )
    assert report.criteria_result == "INCONCLUSIVE"
    assert "CANDIDATE_AUDIT_INCOMPLETE:extra-entry" in report.inconclusive_codes


def test_no_entry_verdict_cannot_claim_owned_filled_lifecycle() -> None:
    plan, run = examples()
    case = replace(
        run.candidates[1],
        lifecycle="OWNED_FILLED_FLAT",
        lifecycle_refs=("contradictory-order-trace",),
    )
    report = evaluate_paper_draft(
        plan, replace(run, candidates=(run.candidates[0], case) + run.candidates[2:])
    )
    assert report.criteria_result == "FAIL"
    assert "FILLED_LIFECYCLE_WITHOUT_ENTRY" in report.failure_codes


@pytest.mark.parametrize("decision", ["ENTRY", "NO_ENTRY"])
def test_extra_decision_with_missing_inputs_is_inconclusive_even_after_quotas(
    decision: str,
) -> None:
    plan, run = examples()
    original = run.candidates[0 if decision == "ENTRY" else 1]
    extra = replace(
        original, candidate_id="extra-incomplete-inputs", inputs_complete=False
    )
    record = run.sessions[0]
    assert record.natural_candidate_ids is not None
    record = replace(
        record,
        natural_candidate_ids=record.natural_candidate_ids + (extra.candidate_id,),
    )
    report = evaluate_paper_draft(
        plan,
        replace(
            run,
            sessions=(record,) + run.sessions[1:],
            candidates=run.candidates + (extra,),
        ),
    )
    assert report.criteria_result == "INCONCLUSIVE" and not report.failure_codes
    assert (
        "CANDIDATE_AUDIT_INCOMPLETE:extra-incomplete-inputs"
        in report.inconclusive_codes
    )
    assert all(
        len(cell.entry_ids) == len(cell.no_entry_ids) == 1 for cell in report.cells
    )


def test_extra_entry_requires_strategy_risk_decision_after_other_quotas_are_met() -> (
    None
):
    plan, run = examples()
    extra = replace(
        run.candidates[0],
        candidate_id="extra-unreached-risk",
        strategy_risk_reached=False,
    )
    record = run.sessions[0]
    assert record.natural_candidate_ids is not None
    record = replace(
        record,
        natural_candidate_ids=record.natural_candidate_ids + (extra.candidate_id,),
    )
    report = evaluate_paper_draft(
        plan,
        replace(
            run,
            sessions=(record,) + run.sessions[1:],
            candidates=run.candidates + (extra,),
        ),
    )
    assert report.criteria_result == "INCONCLUSIVE" and not report.failure_codes
    assert (
        "CANDIDATE_AUDIT_INCOMPLETE:extra-unreached-risk" in report.inconclusive_codes
    )
