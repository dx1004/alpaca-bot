import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alpaca_ross_bot.declared_review import DeclaredReview
from alpaca_ross_bot.journal import Journal, JournalError

NOW = datetime(2026, 10, 22, 16, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def documents() -> tuple[bytes, ...]:
    package = {
        "package_ref": "declared-masked-package",
        "package_digest": DIGEST,
        "rubric_ref": "draft-rubric",
        "rubric_digest": DIGEST,
        "reviewer_ref": "declared-reviewer",
        "decision_at": NOW.isoformat(),
        "journal_scope": "declared-local",
        "through_sequence": 0,
        "source_horizons": {"declared-external-source": 1},
        "source_refs": [
            {
                "journal_scope": "declared-external-source",
                "sequence": 1,
                "payload_ref": DIGEST,
                "available_at": NOW.isoformat(),
            }
        ],
    }
    expected = {
        "verdict": "ELIGIBLE",
        "calculator_ref": "independent-calculator",
        "calculator_version": "declared-v1",
        "calculator_digest": DIGEST,
        "worksheet_ref": "independent-worksheet",
        "reviewer_declaration": {
            "reviewer_ref": "declared-reviewer",
            "independence_statement": "declared independent",
            "conflicts_statement": "declared none",
        },
        "rules": {
            "P08": {
                "value": "limit=1.01",
                "calculation_ref": "independent-worksheet",
                "abstention_reason": None,
            },
            "P09": {
                "value": None,
                "calculation_ref": "worksheet-gap",
                "abstention_reason": "missing independently qualified low",
            },
        },
    }
    observed = {
        "verdict": "ELIGIBLE",
        "rules": {"P08": {"value": "limit=1.02"}, "P09": {"value": "stop=1.00"}},
    }
    observed_bytes = json.dumps(observed, indent=2).encode()
    unmask = {
        "bot_trace_ref": {
            "journal_scope": "declared-bot-source",
            "sequence": 1,
            "payload_ref": DIGEST,
            "available_at": NOW.isoformat(),
        },
        "observed_digest": "sha256:" + hashlib.sha256(observed_bytes).hexdigest(),
    }
    return tuple(
        json.dumps(value, indent=2).encode() for value in (package, expected, unmask)
    ) + (observed_bytes,)


def bound(tmp_path: Path) -> Journal:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "journal.sqlite3", root)
    journal.record_paper_account_verification(
        "synthetic-bound-account", b"synthetic", NOW
    )
    return journal


def test_receipts_order_exact_bytes_retry_reopen_and_prefix(tmp_path: Path) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    docs = documents()
    steps = ("PACKAGE", "EXPECTED", "UNMASK", "COMPARISON")
    receipts = []
    for index, (step, document) in enumerate(zip(steps, docs, strict=True)):
        at = NOW + timedelta(seconds=index)
        receipt = workflow.record("case", step, document, recorded_at=at)
        receipts.append(receipt)
        assert receipt.exact_bytes == document
        assert (
            workflow.record("case", step, document, recorded_at=at + timedelta(days=1))
            == receipt
        )
    view = workflow.view("case")
    assert view.rule_results == (("P08", "MISMATCH"), ("P09", "ABSTAIN"))
    assert view.verdict_result == "MATCH"
    assert not any(
        (
            view.masking_verified,
            view.reviewer_independence_verified,
            view.source_evidence_verified,
            view.natural_enrollment_verified,
            view.formal_sample_eligible,
            view.runtime_authorized,
        )
    )
    historical = workflow.view("case", through_sequence=receipts[1].sequence)
    assert len(historical.receipts) == 2 and historical.rule_results == ()
    journal.close()
    reopened = Journal.open_private(
        tmp_path / "private/journal.sqlite3", tmp_path / "private"
    )
    assert DeclaredReview(reopened, journal_scope="declared-local").view("case") == view
    reopened.close()


@pytest.mark.parametrize(
    "step,index", [("EXPECTED", 1), ("UNMASK", 2), ("COMPARISON", 3)]
)
def test_missing_previous_step_does_not_append(
    tmp_path: Path, step: str, index: int
) -> None:
    journal = bound(tmp_path)
    before = journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="prerequisite"):
        DeclaredReview(journal, journal_scope="declared-local").record(
            "case", step, documents()[index], recorded_at=NOW
        )
    assert journal.read_evidence_sequence() == before
    journal.close()


def test_changed_step_retained_conflict_and_original_seal_immutable(
    tmp_path: Path,
) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    package, expected, unmask, _ = documents()
    workflow.record("case", "PACKAGE", package, recorded_at=NOW)
    original = workflow.record("case", "EXPECTED", expected, recorded_at=NOW)
    with pytest.raises(JournalError, match="conflict"):
        workflow.record("case", "EXPECTED", expected + b" ", recorded_at=NOW)
    view = workflow.view("case")
    assert view.conflict and view.receipts[1] == original
    with pytest.raises(JournalError, match="conflict"):
        workflow.record("case", "UNMASK", unmask, recorded_at=NOW)
    journal.close()


@pytest.mark.parametrize(
    "bad",
    [
        b'{"verdict":"ELIGIBLE","rules":{},"rules":{}}',
        b'{"verdict":"ELIGIBLE","rules":{"P08":{"value":1.000000000000000001,"calculation_ref":"a","abstention_reason":null}}}',
        b'{"verdict":"ELIGIBLE","rules":{"P08":{"value":NaN,"calculation_ref":"a","abstention_reason":null}}}',
    ],
)
def test_unsupported_exact_values_and_duplicate_keys_rejected(
    tmp_path: Path, bad: bytes
) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    workflow.record("case", "PACKAGE", documents()[0], recorded_at=NOW)
    before = journal.read_evidence_sequence()
    with pytest.raises(ValueError):
        workflow.record("case", "EXPECTED", bad, recorded_at=NOW)
    assert journal.read_evidence_sequence() == before
    journal.close()


def test_later_step_cannot_backdate_availability_to_decision(tmp_path: Path) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    workflow.record(
        "case", "PACKAGE", documents()[0], recorded_at=NOW + timedelta(seconds=1)
    )
    with pytest.raises(JournalError, match="regressed"):
        workflow.record("case", "EXPECTED", documents()[1], recorded_at=NOW)
    journal.close()


def test_missing_account_binding_does_not_create_one(tmp_path: Path) -> None:
    journal = Journal.open_private(
        tmp_path / "private/journal.sqlite3", tmp_path / "private"
    )
    with pytest.raises(JournalError, match="existing bound"):
        DeclaredReview(journal, journal_scope="declared-local")
    assert journal.read_paper_account_ref() is None
    journal.close()


def test_corrupt_projection_blocks_even_exact_retry(tmp_path: Path) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    package = documents()[0]
    workflow.record("case", "PACKAGE", package, recorded_at=NOW)
    journal._db.execute(
        "UPDATE state SET value_json='{}' WHERE key=?",
        ("declared_review:synthetic-bound-account:case",),
    )
    journal._db.commit()
    before = journal.read_evidence_sequence()
    with pytest.raises(JournalError, match="projection cache"):
        workflow.record("case", "PACKAGE", package, recorded_at=NOW)
    assert journal.read_evidence_sequence() == before
    journal.close()


def test_corrupt_dedupe_cache_blocks_exact_retry(tmp_path: Path) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    package = documents()[0]
    workflow.record("case", "PACKAGE", package, recorded_at=NOW)
    journal._db.execute(
        "DELETE FROM state WHERE key=?",
        ("declared_review_evidence:synthetic-bound-account:case:PACKAGE",),
    )
    journal._db.commit()
    with pytest.raises(JournalError, match="evidence cache"):
        workflow.record("case", "PACKAGE", package, recorded_at=NOW)
    journal.close()


def test_future_local_horizon_refused_but_external_horizon_independent(
    tmp_path: Path,
) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    package = json.loads(documents()[0])
    package["through_sequence"] = 999
    with pytest.raises(JournalError, match="horizon"):
        workflow.record(
            "case", "PACKAGE", json.dumps(package).encode(), recorded_at=NOW
        )
    assert not workflow.view("case").receipts
    journal.close()


def test_comparison_must_match_unmask_digest_and_full_expected_rule_set(
    tmp_path: Path,
) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    docs = documents()
    for step, document in zip(("PACKAGE", "EXPECTED", "UNMASK"), docs[:3], strict=True):
        workflow.record("case", step, document, recorded_at=NOW)
    with pytest.raises(JournalError, match="unmask digest"):
        workflow.record("case", "COMPARISON", docs[3] + b" ", recorded_at=NOW)
    assert workflow.verify_rebuild("case")
    journal.close()


def test_conflict_after_completed_comparison_cannot_report_clean_match(
    tmp_path: Path,
) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    docs = documents()
    for step, document in zip(
        ("PACKAGE", "EXPECTED", "UNMASK", "COMPARISON"), docs, strict=True
    ):
        receipt = workflow.record("case", step, document, recorded_at=NOW)
    original = workflow.view("case", through_sequence=receipt.sequence)
    with pytest.raises(JournalError, match="conflict"):
        workflow.record("case", "EXPECTED", docs[1] + b" ", recorded_at=NOW)
    assert workflow.view("case").verdict_result == "INVALID_CONFLICT"
    assert workflow.view("case", through_sequence=receipt.sequence) == original
    assert workflow.verify_rebuild("case")
    journal.close()


@pytest.mark.parametrize(
    "mutation", ["reviewer", "calculator_digest", "undeclared_field"]
)
def test_expected_metadata_bound_before_unmask(tmp_path: Path, mutation: str) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    package, expected, _, _ = documents()
    workflow.record("case", "PACKAGE", package, recorded_at=NOW)
    value = json.loads(expected)
    if mutation == "reviewer":
        value["reviewer_declaration"]["reviewer_ref"] = "different-reviewer"
    elif mutation == "calculator_digest":
        value["calculator_digest"] = "not-a-digest"
    else:
        value["independence_verified"] = True
    with pytest.raises(JournalError):
        workflow.record("case", "EXPECTED", json.dumps(value).encode(), recorded_at=NOW)
    assert len(workflow.view("case").receipts) == 1
    journal.close()


def test_unmask_cannot_point_to_future_local_sequence(tmp_path: Path) -> None:
    journal = bound(tmp_path)
    workflow = DeclaredReview(journal, journal_scope="declared-local")
    package, expected, unmask, _ = documents()
    workflow.record("case", "PACKAGE", package, recorded_at=NOW)
    workflow.record("case", "EXPECTED", expected, recorded_at=NOW)
    value = json.loads(unmask)
    value["bot_trace_ref"]["journal_scope"] = "declared-local"
    value["bot_trace_ref"]["sequence"] = journal.read_evidence_sequence() + 10
    with pytest.raises(JournalError, match="reference invalid"):
        workflow.record("case", "UNMASK", json.dumps(value).encode(), recorded_at=NOW)
    assert workflow.verify_rebuild("case")
    journal.close()
