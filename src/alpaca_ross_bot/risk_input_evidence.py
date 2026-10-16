"""Join reported funds, owned inventory and bid evidence without order authority."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from .journal import Journal
from .risk_marks import BidMark, inspect_bid_mark
from .risk_ownership import OwnershipFacts, qualify_ownership


@dataclass(frozen=True, repr=False)
class RiskInputEvidence:
    ownership: OwnershipFacts
    marks: tuple[BidMark, ...]
    source_refs: tuple[tuple[int, str], ...]
    total_bid_value: Fraction | None
    issues: tuple[str, ...]
    limitations: tuple[str, ...]

    @property
    def reported_inputs_qualified(self) -> bool:
        return (
            self.ownership.reported_available_funds is not None
            and self.ownership.ownership_verified
            and self.total_bid_value is not None
            and not self.issues
        )

    @property
    def runtime_authorized(self) -> bool:
        return False

    def public_summary(self) -> dict[str, object]:
        return self.ownership.public_summary() | {
            "reported_inputs_qualified": self.reported_inputs_qualified,
            "bid_valuation_qualified": self.total_bid_value is not None,
            "valued_position_count": len(self.marks),
            "issues": self.issues,
            "limitations": self.limitations,
            "runtime_authorized": False,
        }


def inspect_risk_inputs(
    journal: Journal,
    capture_sequence: int,
    *,
    account_ref: str,
    decision_at: datetime,
    session_open: datetime,
    max_age: timedelta,
    mark_refs: tuple[tuple[str, int, int, int], ...] = (),
    max_metadata_age: timedelta,
) -> RiskInputEvidence:
    """Accept references, never caller-supplied positions or qualification flags.

    Each mark reference is (symbol, raw frame sequence, index, metadata sequence).
    Empty, source-verified inventory has value zero without an invented quote.
    A positive result is bounded observation qualification, not E0, strategy,
    fee-ledger, protective-order or executable-release acceptance.
    """
    ownership = qualify_ownership(
        journal,
        capture_sequence,
        account_ref=account_ref,
        decision_at=decision_at,
        session_open=session_open,
        max_age=max_age,
    )
    if type(mark_refs) is not tuple:
        raise ValueError("immutable mark references required")
    by_symbol: dict[str, tuple[int, int, int]] = {}
    for row in mark_refs:
        if (
            type(row) is not tuple
            or len(row) != 4
            or type(row[0]) is not str
            or not row[0].strip()
            or any(type(v) is not int or v < 0 for v in row[1:])
            or row[1] == 0
            or row[3] == 0
            or row[0] in by_symbol
        ):
            raise ValueError("invalid or duplicate mark reference")
        by_symbol[row[0]] = row[1:]
    positions = dict(ownership.owned_long_positions)
    if set(by_symbol) - set(positions):
        raise ValueError("mark reference is outside source-owned inventory")
    issues = set(ownership.issues)
    limitations = set(ownership.limitations) | {
        "E0_AND_ACCOUNTING_LEDGER_REQUIRE_SEPARATE_ACCEPTANCE",
        "NOT_STRATEGY_PROTECTION_OR_EXECUTABLE_RELEASE_ACCEPTANCE",
        "REVALIDATE_INPUTS_UNDER_RUNTIME_OWNER_BEFORE_ACTION",
    }
    marks: list[BidMark] = []
    refs = set(ownership.source_refs)
    total = Fraction(0)
    for symbol, quantity in positions.items():
        if quantity.denominator != 1 or quantity <= 0:
            issues.add("NONWHOLE_OWNED_POSITION_UNQUALIFIED")
            continue
        if symbol not in by_symbol:
            issues.add("OWNED_POSITION_BID_EVIDENCE_MISSING")
            continue
        sequence, index, metadata = by_symbol[symbol]
        mark = inspect_bid_mark(
            journal,
            symbol=symbol,
            sequence=sequence,
            record_index=index,
            metadata_sequence=metadata,
            decision_at=decision_at,
            max_metadata_age=max_metadata_age,
        )
        marks.append(mark)
        refs.update(mark.source_refs)
        limitations.update(mark.limitations)
        total += mark.long_value(quantity.numerator)
    evidence_refs: list[tuple[int, str]] = []
    for sequence in sorted(refs):
        stored = journal.read_event(sequence)
        if stored is None or not stored[0].visible_at(decision_at):
            raise ValueError("risk input reference is unavailable")
        evidence_refs.append((sequence, stored[0].payload_ref))
    value = (
        total if ownership.ownership_verified and len(marks) == len(positions) else None
    )
    return RiskInputEvidence(
        ownership,
        tuple(marks),
        tuple(evidence_refs),
        value,
        tuple(sorted(issues)),
        tuple(sorted(limitations)),
    )
