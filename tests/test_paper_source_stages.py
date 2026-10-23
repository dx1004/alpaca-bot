import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.events import EventEnvelope
from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.paper_source_stages import inspect_paper_source_stages
from alpaca_ross_bot.research_spec import load_accepted_research_spec
from alpaca_ross_bot.scanner_contract import load_accepted_scanner_contract
from alpaca_ross_bot.scanner_data import AlpacaScannerData
from alpaca_ross_bot.scanner_discovery_audit import audit_scanner_event_census

NOW = datetime(2026, 10, 15, 16, tzinfo=UTC)
SCANNER = load_accepted_scanner_contract(
    Path("evidence/general-iex-daily30-screen-v1.json").read_bytes()
)
RESEARCH = Path("evidence/hybrid-500-iex-intraday-research-v1.json").read_bytes()
SPEC = load_accepted_research_spec(RESEARCH, SCANNER)


def _capture(tmp_path: Path) -> tuple[Journal, dict[str, Any]]:
    journal = Journal(tmp_path / "journal.sqlite3", tmp_path)

    def fetch(
        url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        if "/v2/assets?" in url:
            return json.dumps(
                [
                    {"symbol": s, "class": "us_equity", "status": "active"}
                    for s in ("AAA", "MISSING")
                ]
            ).encode()
        requested = parse_qs(urlsplit(url).query)["symbols"][0].split(",")
        return json.dumps(
            {
                s: {"latestTrade": {"t": NOW.isoformat()}}
                for s in requested
                if s != "MISSING"
            }
        ).encode()

    api = AlpacaScannerData(
        PaperCredentials(SecretValue("synthetic"), SecretValue("synthetic")),
        journal,
        timeout_seconds=1,
        fetch=fetch,
        clock=lambda: NOW,
    )
    run = api.collect_current_asset_iex_snapshots(max_symbols_per_batch=2)
    inputs = [
        run.asset_directory.sequence,
        *(batch.sequence for batch in run.batches),
        run.manifest_sequence,
    ]
    identity = {
        "profile_id": SCANNER.profile_id,
        "contract_sha256": SCANNER.sha256,
        "rule_ids": list(SCANNER.rule_ids),
        "research_version_id": SPEC.version_id,
        "research_spec_sha256": SPEC.sha256,
        "research_rule_ids": list(SPEC.rule_ids),
        "research_evaluation_scope": "P02_SCANNER_ONLY",
    }
    row = dict(
        identity,
        symbol="AAA",
        status="SCREEN_MATCH",
        input_sequence_refs=inputs,
        coverage_label="NOT_SIP_EQUIVALENT",
        full_market_coverage=False,
        order_authority=False,
    )
    return journal, dict(
        identity,
        research_status=SPEC.status,
        decision_at=NOW.isoformat(),
        input_sequences=inputs,
        results=[row],
    )


def _append(journal: Journal, document: dict[str, Any], seconds: int = 2) -> int:
    at = NOW + timedelta(seconds=seconds)
    return journal.append_next(
        source_id="adapted_scanner",
        feed="iex",
        event_kind="ADAPTED_SCREEN_RESULT",
        payload=json.dumps(document).encode(),
        system_first_seen_at=at,
        processed_at=at,
    ).sequence


def _inspect(
    journal: Journal,
    *,
    as_of: datetime = NOW + timedelta(seconds=5),
    horizon: int | None = None,
) -> Any:
    return inspect_paper_source_stages(
        journal,
        journal_scope="SYNTHETIC_DECLARED_SCOPE",
        through_sequence=journal.read_evidence_sequence()
        if horizon is None
        else horizon,
        as_of=as_of,
        scanner_contract=SCANNER,
        research_spec_bytes=RESEARCH,
    )


def test_actual_source_schema_links_reported_result_without_enrollment(
    tmp_path: Path,
) -> None:
    journal, document = _capture(tmp_path)
    sequence = _append(journal, document)
    before = journal.read_evidence_sequence()
    result = _inspect(journal)
    screen = [
        stage for stage in result.stages if stage.stage == "REPORTED_SCREEN_MATCH"
    ]
    assert len(screen) == 1
    assert screen[0].source.sequence == sequence
    assert screen[0].source.available_at == NOW + timedelta(seconds=2)
    assert screen[0].decision_cutoff == NOW
    assert len(screen[0].input_sources) == 3
    assert any(
        stage.symbol == "MISSING" and stage.stage == "SNAPSHOT_MISSING"
        for stage in result.stages
    )
    assert result.natural_candidate_ids is None
    assert not any(
        (
            result.enrollment_accepted,
            result.source_admission_accepted,
            result.session_coverage_accepted,
            result.formal_sample_eligible,
            result.runtime_authorized,
        )
    )
    assert journal.read_evidence_sequence() == before
    journal.close()


def test_retrospective_screen_not_visible_at_its_payload_cutoff(tmp_path: Path) -> None:
    journal, document = _capture(tmp_path)
    _append(journal, document)
    result = _inspect(journal, as_of=NOW)
    assert not any(stage.decision_cutoff is not None for stage in result.stages)
    assert result.discovery.audited_unique_requested_observations == 2
    journal.close()


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("contract", "SCREEN_CONTRACT_IDENTITY_INVALID"),
        ("future", "SCREEN_CLOCK_INVALID"),
        ("missing", "SCREEN_INPUT_ANCESTRY_INVALID"),
        ("lateinput", "SCREEN_INPUT_ANCESTRY_INVALID"),
        ("duplicate", "SCREEN_ROW_SYMBOL_CONFLICT"),
        ("symbol", "SCREEN_ROW_SYMBOL_SOURCE_MISSING"),
        ("rowrefs", "SCREEN_ROW_INPUT_ANCESTRY_INVALID"),
        ("rowcontract", "SCREEN_ROW_CONTRACT_IDENTITY_INVALID"),
        ("scope", "SCREEN_ROW_SCOPE_INVALID"),
    ],
)
def test_invalid_screen_retains_malformed_source_and_original_denominator(
    tmp_path: Path, mutation: str, code: str
) -> None:
    journal, doc = _capture(tmp_path)
    if mutation == "contract":
        doc["research_spec_sha256"] = "0" * 64
    if mutation == "future":
        doc["decision_at"] = (NOW + timedelta(seconds=3)).isoformat()
    if mutation == "missing":
        doc["input_sequences"].append(10000)
    if mutation == "lateinput":
        doc["decision_at"] = (NOW - timedelta(seconds=1)).isoformat()
    if mutation == "duplicate":
        doc["results"].append(dict(doc["results"][0]))
    if mutation == "symbol":
        doc["results"][0]["symbol"] = "OTHER"
    if mutation == "rowrefs":
        doc["results"][0]["input_sequence_refs"] = [999]
    if mutation == "rowcontract":
        doc["results"][0]["rule_ids"] = []
    if mutation == "scope":
        doc["results"][0]["full_market_coverage"] = True
    seq = _append(journal, doc)
    result = _inspect(journal)
    assert any(
        stage.source.sequence == seq and code in stage.issues for stage in result.stages
    )
    assert result.discovery.audited_unique_requested_observations == 2
    journal.close()


@pytest.mark.parametrize("status", ["RULE_NOT_MET", "INPUT_UNAVAILABLE", "invented"])
def test_reported_status_never_becomes_entry_or_negative_strategy_example(
    tmp_path: Path, status: str
) -> None:
    journal, doc = _capture(tmp_path)
    doc["results"][0]["status"] = status
    _append(journal, doc)
    result = _inspect(journal)
    assert any(
        stage.stage
        == ("UNMAPPED_SCREEN_STATUS" if status == "invented" else "REPORTED_" + status)
        for stage in result.stages
    )
    assert result.natural_candidate_ids is None
    journal.close()


@pytest.mark.parametrize("conflict", [False, True])
def test_multiple_source_results_retained_and_repetition_flagged(
    tmp_path: Path, conflict: bool
) -> None:
    journal, doc = _capture(tmp_path)
    _append(journal, doc)
    if conflict:
        doc["results"][0]["status"] = "RULE_NOT_MET"
    _append(journal, doc, seconds=3)
    result = _inspect(journal)
    rows = [stage for stage in result.stages if stage.decision_cutoff is not None]
    assert len(rows) == 2
    assert (
        "CONFLICTING_SCREEN_OBSERVATION" if conflict else "REPEATED_SCREEN_OBSERVATION"
    ) in rows[-1].issues
    journal.close()


class CorruptView(Journal):
    def __init__(self, original: Journal, missing: bool = False) -> None:
        self.original, self.missing = original, missing

    def read_evidence_sequence(self) -> int:
        return self.original.read_evidence_sequence()

    def read_event(self, sequence: int) -> tuple[EventEnvelope, bytes] | None:
        pair = self.original.read_event(sequence)
        assert pair is not None
        if sequence != 1:
            return pair
        if self.missing:
            return None
        return replace(
            pair[0], payload_ref="sha256:" + hashlib.sha256(b"changed").hexdigest()
        ), pair[1]


@pytest.mark.parametrize("missing", [False, True])
def test_prefix_corruption_is_hard_failure(tmp_path: Path, missing: bool) -> None:
    journal, _ = _capture(tmp_path)
    with pytest.raises(JournalError):
        _inspect(CorruptView(journal, missing))
    journal.close()


@pytest.mark.parametrize("horizon", [-1, True, 10000])
def test_invalid_horizon_rejected(tmp_path: Path, horizon: int) -> None:
    journal, _ = _capture(tmp_path)
    with pytest.raises(ValueError, match="PREFIX_HORIZON_INVALID"):
        _inspect(journal, horizon=horizon)
    journal.close()


def test_unfinalized_raw_batch_retained_as_orphan_without_fabricating_negative(
    tmp_path: Path,
) -> None:
    journal, doc = _capture(tmp_path)
    raw_sequence = doc["input_sequences"][1]
    result = _inspect(journal, horizon=raw_sequence)
    rows = [stage for stage in result.stages if stage.source.sequence == raw_sequence]
    assert len(rows) == 2
    assert all("ORPHAN_RAW_SNAPSHOT" in row.issues for row in rows)
    assert {row.stage for row in rows} == {"OBSERVED", "SNAPSHOT_MISSING"}
    assert not result.discovery.manifest_counts
    journal.close()


def test_invalid_later_row_marks_entire_source_result_rejected(tmp_path: Path) -> None:
    journal, doc = _capture(tmp_path)
    doc["results"].append(dict(doc["results"][0]))
    _append(journal, doc)
    result = _inspect(journal)
    assert not any(stage.stage.startswith("REPORTED_") for stage in result.stages)
    assert any(
        stage.stage == "REJECTED_SCREEN_ROW"
        and "SCREEN_EVENT_MALFORMED" in stage.issues
        for stage in result.stages
    )
    journal.close()


def test_screen_manifest_cannot_omit_its_raw_dependencies(tmp_path: Path) -> None:
    journal, doc = _capture(tmp_path)
    doc["input_sequences"] = [doc["input_sequences"][-1]]
    _append(journal, doc)
    result = _inspect(journal)
    assert any(
        "SCREEN_MANIFEST_ANCESTRY_INVALID" in stage.issues for stage in result.stages
    )
    journal.close()


def test_unknown_derived_and_screen_kinds_explicitly_unmapped(tmp_path: Path) -> None:
    journal, _ = _capture(tmp_path)
    journal.append_next(
        source_id="adapted_scanner",
        feed="iex",
        event_kind="UNKNOWN",
        payload=b"{}",
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    result = _inspect(journal)
    assert any(stage.stage == "UNMAPPED_SCREEN_EVENT" for stage in result.stages)
    assert any(stage.stage == "UNMAPPED_DERIVED_EVENT" for stage in result.stages)
    journal.close()


@pytest.mark.parametrize("source", ["foreign", "alpaca_scanner_data"])
def test_wrong_screen_source_is_hard_identity_failure(
    tmp_path: Path, source: str
) -> None:
    journal, document = _capture(tmp_path)
    journal.append_next(
        source_id=source,
        feed="iex",
        event_kind="ADAPTED_SCREEN_RESULT",
        payload=json.dumps(document).encode(),
        system_first_seen_at=NOW,
        processed_at=NOW,
    )
    with pytest.raises(ValueError, match="SCREEN_EVENT_IDENTITY_INVALID"):
        _inspect(journal)
    journal.close()


def test_contract_inputs_are_verified_against_pinned_bytes(tmp_path: Path) -> None:
    journal, _ = _capture(tmp_path)
    with pytest.raises(ValueError):
        inspect_paper_source_stages(
            journal,
            journal_scope="SYNTHETIC_DECLARED_SCOPE",
            through_sequence=journal.read_evidence_sequence(),
            as_of=NOW,
            scanner_contract=replace(SCANNER, sha256="0" * 64),
            research_spec_bytes=RESEARCH,
        )
    with pytest.raises(ValueError):
        inspect_paper_source_stages(
            journal,
            journal_scope="SYNTHETIC_DECLARED_SCOPE",
            through_sequence=journal.read_evidence_sequence(),
            as_of=NOW,
            scanner_contract=SCANNER,
            research_spec_bytes=RESEARCH + b" ",
        )
    journal.close()


@pytest.mark.parametrize("scope", ["", " ", " trailing", "line\nbreak", None, 42])
def test_invalid_declared_journal_scope_rejected(tmp_path: Path, scope: Any) -> None:
    journal, _ = _capture(tmp_path)
    with pytest.raises(ValueError, match="DECLARED_JOURNAL_SCOPE_INVALID"):
        inspect_paper_source_stages(
            journal,
            journal_scope=scope,
            through_sequence=journal.read_evidence_sequence(),
            as_of=NOW,
            scanner_contract=SCANNER,
            research_spec_bytes=RESEARCH,
        )
    journal.close()


def test_scoped_source_refs_remain_distinct_without_authenticating_namespaces(
    tmp_path: Path,
) -> None:
    journal, _ = _capture(tmp_path)
    left = _inspect(journal)
    right = inspect_paper_source_stages(
        journal,
        journal_scope="OTHER_DECLARED_SCOPE",
        through_sequence=journal.read_evidence_sequence(),
        as_of=NOW,
        scanner_contract=SCANNER,
        research_spec_bytes=RESEARCH,
    )
    assert left.stages[0].source != right.stages[0].source
    assert left.stages[0].source.sequence == right.stages[0].source.sequence
    assert left.stages[0].source.payload_ref == right.stages[0].source.payload_ref
    assert left.discovery != right.discovery
    assert left.discovery.event_sources[0] != right.discovery.event_sources[0]
    summary = right.discovery
    refs = (
        *summary.event_sources,
        *(row[0] for row in summary.manifest_counts),
        *(row[0] for row in summary.audit_errors),
        *summary.orphan_catalog_sources,
        *summary.orphan_snapshot_sources,
        *(row[0] for row in summary.excluded_kind_sources),
    )
    assert refs and all(
        ref.declared_journal_scope == "OTHER_DECLARED_SCOPE" for ref in refs
    )
    legacy = audit_scanner_event_census(
        journal, through_sequence=journal.read_evidence_sequence(), decision_at=NOW
    )
    assert (
        summary.audited_unique_requested_observations
        == legacy.audited_unique_requested_observations
    )
    assert (
        summary.audited_unique_returned_observations
        == legacy.audited_unique_returned_observations
    )
    assert (
        summary.audited_unique_missing_observations
        == legacy.audited_unique_missing_observations
    )
    assert summary.manifest_counts[0][1:4] == (2, 1, 1)
    assert not hasattr(summary, "manifest_audits") and not hasattr(
        summary, "event_refs"
    )
    assert (
        not left.journal_scope_authenticated and not right.journal_scope_authenticated
    )
    assert all(
        stage.source.declared_journal_scope == "OTHER_DECLARED_SCOPE"
        for stage in right.stages
    )
    journal.close()


def test_empty_result_keeps_source_event_without_claiming_zero_candidates(
    tmp_path: Path,
) -> None:
    journal, doc = _capture(tmp_path)
    doc["results"] = []
    seq = _append(journal, doc)
    result = _inspect(journal)
    rows = [stage for stage in result.stages if stage.source.sequence == seq]
    assert len(rows) == 1 and rows[0].stage == "EMPTY_SCREEN_ROWS"
    assert rows[0].source.available_at == NOW + timedelta(seconds=2)
    assert rows[0].input_sources
    assert "NOT_A_ZERO_CANDIDATE_CENSUS" in rows[0].issues
    assert result.natural_candidate_ids is None
    journal.close()
