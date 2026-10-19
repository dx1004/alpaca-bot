"""Synthetic network responses run through the actual read-only source collector."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from alpaca_ross_bot.config import PaperCredentials, SecretValue
from alpaca_ross_bot.journal import Journal
from alpaca_ross_bot.observation_admission import (
    CANDIDATE_VERSION,
    CONTRACT_SHA256,
    PATH,
    ObservationContract,
    admit_completed_sweep,
    admit_snapshot_observation,
    load_observation_contract,
)
from alpaca_ross_bot.research_spec import RESEARCH_SHA256
from alpaca_ross_bot.scanner_contract import ACCEPTED_SHA256
from alpaca_ross_bot.scanner_data import AlpacaScannerData, IexAssetSnapshotRun
from test_scanner_discovery_audit import JournalView

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 19, 16, tzinfo=UTC)


class ReadView(JournalView):
    def read_evidence_sequence(self) -> int:
        return self.journal.read_evidence_sequence()


class Source:
    def __init__(self, tmp_path: Path, *, missing: bool = False) -> None:
        self.at, self.missing = NOW, missing
        self.daily: dict[str, object] = {"v": 500, "t": "2026-10-19T04:00:00Z"}
        root = tmp_path / "runtime"
        self.journal = Journal(root / "journal.sqlite3", root)
        self.client = AlpacaScannerData(
            PaperCredentials(
                SecretValue("synthetic-key"), SecretValue("synthetic-secret")
            ),
            self.journal,
            timeout_seconds=1,
            fetch=self.fetch,
            clock=lambda: self.at,
        )

    def fetch(
        self, url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> bytes:
        if "/v2/assets?" in url:
            return json.dumps(
                [
                    {"symbol": symbol, "class": "us_equity", "status": "active"}
                    for symbol in ("AAA", "BBB")
                ]
            ).encode()
        symbols = parse_qs(urlsplit(url).query)["symbols"][0].split(",")
        return json.dumps(
            {
                symbol: {
                    "latestTrade": {"p": 10, "t": NOW.isoformat()},
                    "dailyBar": self.daily,
                }
                for symbol in symbols
                if not (self.missing and symbol == "BBB")
            }
        ).encode()

    def sweep(self) -> IexAssetSnapshotRun:
        return self.client.collect_current_asset_iex_snapshots(max_symbols_per_batch=1)


def contract() -> ObservationContract:
    return load_observation_contract((ROOT / PATH).read_bytes())


def test_actual_collector_completed_manifest_qualifies_only_new_age_component(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path)
    try:
        run = source.sweep()
        result = admit_completed_sweep(
            source.journal,
            contract(),
            manifest_sequence=run.manifest_sequence,
            decision_at=NOW,
        )
        assert (
            result.request_coverage_complete and result.returned_data_coverage_complete
        )
        assert tuple(item.symbol for item in result.members) == ("AAA", "BBB")
        for member in result.members:
            assert member.observation_age_component_qualified
            assert member.volume == 500 and member.daily_bucket_day == NOW.date()
            assert member.acquisition_started_at == NOW
            assert (
                member.volume_source_updated_at == member.volume_source_age == "UNKNOWN"
            )
            assert member.candidate_version == CANDIDATE_VERSION
            assert not member.runtime_authorized and not member.strategy_eligible
            assert (
                not member.order_authority and member.coverage == "NOT_SIP_EQUIVALENT"
            )
            assert "SOURCE_CONDITION_POLICY" in member.unresolved_gates
            assert (
                "ENTRY_RISK_VERSION_BINDING_FOR_NEW_CANDIDATE"
                in member.unresolved_gates
            )
            assert {
                run.manifest_sequence,
                run.asset_directory.sequence,
                member.raw_sequence,
            } == {seq for seq, _ in member.source_refs}
        assert not result.runtime_authorized
    finally:
        source.journal.close()


@pytest.mark.parametrize(
    "offset, qualified",
    [
        (timedelta(seconds=300), True),
        (timedelta(seconds=300, microseconds=1), False),
    ],
)
def test_inclusive_300_second_page_boundary(
    tmp_path: Path, offset: timedelta, qualified: bool
) -> None:
    source = Source(tmp_path)
    try:
        batch = source.client.collect_iex_snapshot_batch(("AAA",))
        result = admit_snapshot_observation(
            source.journal,
            contract(),
            symbol="AAA",
            raw_sequence=batch.sequence,
            decision_at=NOW + offset,
        )
        assert result.observation_age_component_qualified is qualified
        assert result.acquisition_started_at == NOW
    finally:
        source.journal.close()


def test_new_identical_response_is_new_acquisition_but_old_page_stays_old(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path)
    try:
        old = source.client.collect_iex_snapshot_batch(("AAA",))
        source.at = NOW + timedelta(seconds=301)
        new = source.client.collect_iex_snapshot_batch(("AAA",))
        before, after = (
            admit_snapshot_observation(
                source.journal,
                contract(),
                symbol="AAA",
                raw_sequence=batch.sequence,
                decision_at=source.at,
            )
            for batch in (old, new)
        )
        assert not before.observation_age_component_qualified
        assert after.observation_age_component_qualified
        assert before.volume == after.volume == 500
        digests: list[str] = []
        for batch in (old, new):
            record = source.journal.read_event(batch.sequence)
            assert record is not None
            digests.append(json.loads(record[1])["response_sha256"])
        assert digests[0] == digests[1]
        assert before.source_refs != after.source_refs
        assert after.volume_source_age == "UNKNOWN"
    finally:
        source.journal.close()


def test_catalog_acquisition_age_cannot_be_refreshed_by_late_sweep_completion(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path)
    try:
        run = source.sweep()
        at = NOW + timedelta(seconds=301)
        saved = source.journal.read_event(run.manifest_sequence)
        assert saved is not None
        event, raw = saved
        view = ReadView(
            source.journal,
            {
                run.manifest_sequence: (
                    replace(
                        event, system_first_seen_at=at, processed_at=at, available_at=at
                    ),
                    raw,
                )
            },
        )
        result = admit_completed_sweep(
            view,
            contract(),
            manifest_sequence=run.manifest_sequence,
            decision_at=at,
            through_sequence=run.manifest_sequence,
        )
        assert result.request_coverage_complete
        assert all(
            not member.observation_age_component_qualified for member in result.members
        )
        assert all(member.acquisition_started_at == NOW for member in result.members)
    finally:
        source.journal.close()


def test_late_page_does_not_hide_stale_catalog_in_completed_discovery(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path)
    try:
        original = source.fetch

        def delayed(
            url: str, headers: Mapping[str, str], timeout: float, max_bytes: int
        ) -> bytes:
            if "/snapshots?" in url:
                source.at = NOW + timedelta(seconds=301)
            return original(url, headers, timeout, max_bytes)

        source.client._fetch = delayed
        run = source.sweep()
        result = admit_completed_sweep(
            source.journal,
            contract(),
            manifest_sequence=run.manifest_sequence,
            decision_at=source.at,
        )
        assert result.request_coverage_complete
        assert all(member.acquisition_started_at == NOW for member in result.members)
        assert all(
            not member.observation_age_component_qualified for member in result.members
        )
    finally:
        source.journal.close()


def test_derived_processing_delay_does_not_reset_acquisition_and_reclocked_copy_refused(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path)
    try:
        run = source.sweep()
        seq = run.observations[0].sequence
        assert seq is not None
        original = source.journal.read_event(seq)
        assert original is not None
        event, raw = original
        at = NOW + timedelta(seconds=100)
        view = ReadView(
            source.journal,
            {
                seq: (
                    replace(event, processed_at=at, available_at=at),
                    raw,
                )
            },
        )
        delayed = admit_snapshot_observation(
            view,
            contract(),
            symbol="AAA",
            raw_sequence=run.batches[0].sequence,
            observation_sequence=seq,
            decision_at=at,
            through_sequence=run.manifest_sequence,
        )
        assert delayed.observation_age_component_qualified
        assert delayed.acquisition_started_at == NOW
        copied = source.journal.append_next(
            source_id="alpaca_iex",
            feed="iex",
            event_kind="IEX_SYMBOL_SNAPSHOT_OBSERVATION",
            payload=raw,
            system_first_seen_at=at,
            processed_at=at,
        )
        with pytest.raises(ValueError, match="cache cannot change"):
            admit_snapshot_observation(
                source.journal,
                contract(),
                symbol="AAA",
                raw_sequence=run.batches[0].sequence,
                observation_sequence=copied.sequence,
                decision_at=at,
            )
    finally:
        source.journal.close()


@pytest.mark.parametrize(
    "daily,reason",
    [
        ({"v": True, "t": "2026-10-19T04:00:00Z"}, "CURRENT_DAILY_VOLUME"),
        ({"v": -1, "t": "2026-10-19T04:00:00Z"}, "CURRENT_DAILY_VOLUME"),
        ({"v": 500, "t": "2026-10-16T04:00:00Z"}, "CURRENT_NY_DAY"),
        ({"v": 500, "t": "2026-10-19T04:00:00"}, "CURRENT_NY_DAY"),
    ],
)
def test_raw_volume_and_current_ny_bucket_required(
    tmp_path: Path, daily: dict[str, object], reason: str
) -> None:
    source = Source(tmp_path)
    try:
        source.daily = daily
        batch = source.client.collect_iex_snapshot_batch(("AAA",))
        result = admit_snapshot_observation(
            source.journal,
            contract(),
            symbol="AAA",
            raw_sequence=batch.sequence,
            decision_at=NOW,
        )
        assert not result.observation_age_component_qualified
        assert any(reason in item for item in result.reasons)
    finally:
        source.journal.close()


def test_requested_coverage_is_not_returned_coverage_or_missing_symbol_admission(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path, missing=True)
    try:
        run = source.sweep()
        result = admit_completed_sweep(
            source.journal,
            contract(),
            manifest_sequence=run.manifest_sequence,
            decision_at=NOW,
        )
        assert (
            result.request_coverage_complete
            and not result.returned_data_coverage_complete
        )
        assert result.missing_symbols == ("BBB",)
        assert tuple(member.symbol for member in result.members) == ("AAA",)
        missing = admit_snapshot_observation(
            source.journal,
            contract(),
            symbol="BBB",
            raw_sequence=run.batches[1].sequence,
            decision_at=NOW,
        )
        assert missing.reasons == ("SNAPSHOT_NOT_RETURNED",)
    finally:
        source.journal.close()


def test_same_clock_later_sequence_is_not_visible_through_saved_horizon(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path)
    try:
        first = source.client.collect_iex_snapshot_batch(("AAA",))
        horizon = source.journal.read_evidence_sequence()
        later = source.client.collect_iex_snapshot_batch(("AAA",))
        with pytest.raises(ValueError, match="horizon"):
            admit_snapshot_observation(
                source.journal,
                contract(),
                symbol="AAA",
                raw_sequence=later.sequence,
                decision_at=NOW,
                through_sequence=horizon,
            )
        assert admit_snapshot_observation(
            source.journal,
            contract(),
            symbol="AAA",
            raw_sequence=first.sequence,
            decision_at=NOW,
            through_sequence=horizon,
        ).observation_age_component_qualified
    finally:
        source.journal.close()


@pytest.mark.parametrize("sequence", [True, -1])
def test_boolean_or_negative_sequence_refs_refused(
    tmp_path: Path, sequence: object
) -> None:
    source = Source(tmp_path)
    try:
        with pytest.raises(ValueError, match="horizon"):
            admit_snapshot_observation(
                source.journal,
                contract(),
                symbol="AAA",
                raw_sequence=cast(int, sequence),
                decision_at=NOW,
            )
    finally:
        source.journal.close()


def test_original_parent_contract_hashes_and_runtime_boundaries_unchanged() -> None:
    raw = (ROOT / PATH).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CONTRACT_SHA256
    body = json.loads(raw)
    assert body["parent"]["research_sha256"] == RESEARCH_SHA256
    assert (
        hashlib.sha256(
            (ROOT / "evidence/hybrid-500-iex-intraday-research-v1.json").read_bytes()
        ).hexdigest()
        == RESEARCH_SHA256
    )
    assert (
        hashlib.sha256(
            (ROOT / "evidence/general-iex-daily30-screen-v1.json").read_bytes()
        ).hexdigest()
        == ACCEPTED_SHA256
    )
    assert body["authority"]["runtime_authorized"] is False
    with pytest.raises(ValueError, match="pinned"):
        load_observation_contract(raw + b" ")


def test_future_horizon_is_not_an_immutable_current_source_boundary(
    tmp_path: Path,
) -> None:
    source = Source(tmp_path)
    try:
        batch = source.client.collect_iex_snapshot_batch(("AAA",))
        with pytest.raises(ValueError, match="current immutable"):
            admit_snapshot_observation(
                source.journal,
                contract(),
                symbol="AAA",
                raw_sequence=batch.sequence,
                decision_at=NOW,
                through_sequence=source.journal.read_evidence_sequence() + 1,
            )
    finally:
        source.journal.close()
