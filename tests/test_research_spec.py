import json
from dataclasses import replace
from pathlib import Path

import pytest

from alpaca_ross_bot.research_spec import (
    RESEARCH_SHA256,
    load_accepted_research_spec,
    load_accepted_research_spec_file,
)
from alpaca_ross_bot.scanner_contract import load_accepted_scanner_contract_file

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / "evidence/hybrid-500-iex-intraday-research-v1.json").read_bytes()
SCANNER = load_accepted_scanner_contract_file(
    ROOT / "evidence/general-iex-daily30-screen-v1.json"
)


def test_accepted_identity_is_research_only() -> None:
    spec = load_accepted_research_spec_file(ROOT, SCANNER)
    assert spec.sha256 == RESEARCH_SHA256
    assert spec.rule_ids == tuple(f"P{n:02}" for n in range(1, 14))
    assert (
        not spec.order_authority
        and not spec.executable_release
        and not spec.formal_paper_evaluation
    )


@pytest.mark.parametrize("change", ["version", "authority", "scanner"])
def test_tampered_research_rejected(change: str) -> None:
    data = json.loads(RAW)
    if change == "version":
        data["version_id"] = "V2"
    elif change == "authority":
        data["acceptance_boundary"]["orders"] = True
    else:
        data["scanner"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        load_accepted_research_spec(json.dumps(data).encode(), SCANNER)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b"\xff", b"", b"[]", b" " * 65537])
def test_malformed_research_rejected(raw: bytes) -> None:
    with pytest.raises(ValueError):
        load_accepted_research_spec(raw, SCANNER)


def test_cross_scanner_contract_rejected() -> None:
    with pytest.raises(ValueError):
        load_accepted_research_spec(RAW, replace(SCANNER, sha256="0" * 64))


def test_policy_section_drift_rejected(tmp_path: Path) -> None:
    (tmp_path / "evidence").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "evidence/hybrid-500-iex-intraday-research-v1.json").write_bytes(RAW)
    policy = (ROOT / "docs/no-leverage-pullback-research.md").read_text()
    (tmp_path / "docs/no-leverage-pullback-research.md").write_text(
        policy.replace(
            "## Decision package: independent intraday adaptation, 2026-10-15",
            "## Decision package: independent intraday adaptation, 2026-10-15\nCHANGED",
        )
    )
    with pytest.raises(ValueError, match="policy section"):
        load_accepted_research_spec_file(tmp_path, SCANNER)


def test_crlf_changed_policy_bytes_rejected(tmp_path: Path) -> None:
    (tmp_path / "evidence").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "evidence/hybrid-500-iex-intraday-research-v1.json").write_bytes(RAW)
    raw_policy = (ROOT / "docs/no-leverage-pullback-research.md").read_bytes()
    changed = raw_policy.replace(b"\n", b"\r\n")
    assert changed != raw_policy
    (tmp_path / "docs/no-leverage-pullback-research.md").write_bytes(changed)
    with pytest.raises(ValueError, match="policy section"):
        load_accepted_research_spec_file(tmp_path, SCANNER)
