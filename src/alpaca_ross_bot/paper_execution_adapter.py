"""Source-linked engineering executions, never formal performance enrollment.

Raw account activity order is retained. No execution ordering, fee/day census,
posting finality, native ownership, P&L or release authority is manufactured.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path

from .journal import Journal
from .paper_ledger_sources import (
    LedgerSource,
    ObservedActivity,
    inspect_paper_ledger_sources,
)


@dataclass(frozen=True, repr=False)
class EngineeringExecution:
    execution_id: str
    roundtrip_entry_client_order_id: str
    symbol: str
    side: str
    quantity: Fraction
    price: Fraction
    at: datetime
    original_timestamp: str
    submicrosecond_remainder: Fraction
    canonical_row: bytes
    canonical_row_hash: str
    occurrences: tuple[tuple[int, int], ...]
    activity_source: LedgerSource
    ownership_sources: tuple[LedgerSource, ...]
    origin: str = "ENGINEERING_TEST"


@dataclass(frozen=True, repr=False)
class EngineeringExecutionConversion:
    declared_journal_scope: str
    account_ref: str
    through_sequence: int
    decision_at: datetime
    executions: tuple[EngineeringExecution, ...]
    observations: tuple[ObservedActivity, ...]
    unconverted_observations: tuple[ObservedActivity, ...]
    source_refs: tuple[LedgerSource, ...]
    supplied_order_chronological: bool
    issues: tuple[str, ...]
    classification: str = "OBSERVED_ENGINEERING_EXECUTIONS_NOT_FORMAL_PERFORMANCE"
    journal_scope_authenticated: bool = False
    formal_sample_eligible: bool = False
    source_admission_accepted: bool = False
    activity_finality_accepted: bool = False
    account_day_census_accepted: bool = False
    runtime_authorized: bool = False
    dispatch_authorized: bool = False


def inspect_engineering_executions(
    journal: Journal,
    capture_sequence: int,
    *,
    journal_scope: str,
    account_ref: str,
    decision_at: datetime,
    through_sequence: int,
    max_age: timedelta,
    fee_policy: Path,
) -> EngineeringExecutionConversion:
    """Convert only the canonical reader's explicit engineering assignments.

    Read source evidence afresh at the fixed prefix; accept no caller-built
    ownership result. Duplicate assignments and ambiguous variants stay raw.
    The reader preserves any existing caller transaction without writing.
    """
    observed = inspect_paper_ledger_sources(
        journal,
        capture_sequence,
        journal_scope=journal_scope,
        account_ref=account_ref,
        decision_at=decision_at,
        through_sequence=through_sequence,
        max_age=max_age,
        fee_policy=fee_policy,
    )
    issues = set(observed.issues)
    assignments: dict[str, list[tuple[str, str, tuple[LedgerSource, ...]]]] = {}
    for trip in observed.observed_roundtrips:
        for identity in trip.execution_ids:
            assignments.setdefault(identity, []).append(
                (trip.entry_client_order_id, trip.symbol, trip.source_refs)
            )
    counts: dict[str, int] = {}
    for row in observed.activities:
        if row.activity_id is not None:
            counts[row.activity_id] = counts.get(row.activity_id, 0) + 1
    converted: list[EngineeringExecution] = []
    unconverted: list[ObservedActivity] = []
    for row in observed.activities:
        matches = assignments.get(row.activity_id or "", [])
        if len(matches) > 1:
            issues.add("EXECUTION_ASSIGNMENT_CONFLICT")
        if row.activity_id is not None and counts[row.activity_id] > 1:
            issues.add("EXECUTION_ACTIVITY_VARIANTS_UNRESOLVED")
        if (
            len(matches) != 1
            or counts.get(row.activity_id or "") != 1
            or row.issues
            or row.activity_type != "FILL"
            or row.symbol != matches[0][1]
            or row.side not in {"buy", "sell"}
            or row.quantity is None
            or row.price is None
            or row.parsed_time is None
            or row.transaction_time is None
            or row.submicrosecond_remainder is None
            or row.activity_id is None
        ):
            unconverted.append(row)
            continue
        converted.append(
            EngineeringExecution(
                row.activity_id,
                matches[0][0],
                row.symbol,
                row.side,
                row.quantity,
                row.price,
                row.parsed_time,
                row.transaction_time,
                row.submicrosecond_remainder,
                row.canonical_row,
                row.row_hash,
                row.occurrences,
                row.source,
                matches[0][2],
            )
        )
    keys = [(row.at, row.submicrosecond_remainder) for row in converted]
    chronological = all(
        left <= right for left, right in zip(keys, keys[1:], strict=False)
    )
    if not chronological:
        issues.add("SOURCE_EXECUTION_ORDER_NOT_CHRONOLOGICAL")
    ties: dict[tuple[str, datetime, Fraction], set[str]] = {}
    for execution in converted:
        sides = ties.setdefault(
            (
                execution.roundtrip_entry_client_order_id,
                execution.at,
                execution.submicrosecond_remainder,
            ),
            set(),
        )
        sides.add(execution.side)
        if len(sides) > 1:
            issues.add("OPPOSITE_SIDE_EXECUTION_ORDER_UNRESOLVED")
    if unconverted:
        issues.add("UNCONVERTED_ACTIVITY_OBSERVATIONS_RETAINED")
    issues.add("FORMAL_PERFORMANCE_INPUT_CONVERSION_UNACCEPTED")
    return EngineeringExecutionConversion(
        observed.declared_journal_scope,
        observed.account_ref,
        observed.through_sequence,
        observed.decision_at,
        tuple(converted),
        observed.activities,
        tuple(unconverted),
        observed.source_refs,
        chronological,
        tuple(sorted(issues)),
    )
