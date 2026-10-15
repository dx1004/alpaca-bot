"""Pinned pending-validation research identity, never execution authority."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .scanner_contract import ACCEPTED_SHA256, AcceptedScannerContract

RESEARCH_SHA256 = "acefeee493533686c2595fbc8347f0db0c03630c5bfdf1749ffa56c72b995c9a"
POLICY_PATH = "docs/no-leverage-pullback-research.md"
START = "## Decision package: independent intraday adaptation, 2026-10-15"
END = "## Validation sequence and acceptance evidence"
POLICY_SHA256 = "a92b174d7b6fb5e94d408021aca75895853dabeb74417191751825cb3ddf57cf"


@dataclass(frozen=True)
class AcceptedResearchSpec:
    version_id: str
    sha256: str
    scanner_sha256: str
    classification: str
    rule_ids: tuple[str, ...]
    status: str = "USER_ACCEPTED_FROZEN_RESEARCH_SPECIFICATION"
    executable_release: bool = False
    order_authority: bool = False
    formal_paper_evaluation: bool = False


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate research specification key")
        result[key] = value
    return result


def load_accepted_research_spec(
    raw: bytes, scanner: AcceptedScannerContract
) -> AcceptedResearchSpec:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 65536:
        raise ValueError("research specification bytes must be bounded")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("research specification must be UTF-8 JSON") from exc
    if (
        not isinstance(document, dict)
        or hashlib.sha256(raw).hexdigest() != RESEARCH_SHA256
    ):
        raise ValueError("research specification differs from accepted digest")
    from .scanner_contract import validate_accepted_scanner_contract

    validate_accepted_scanner_contract(scanner)
    if (
        document["scanner"]["sha256"] != scanner.sha256
        or scanner.sha256 != ACCEPTED_SHA256
    ):
        raise ValueError("research specification scanner link mismatch")
    return AcceptedResearchSpec(
        document["version_id"],
        RESEARCH_SHA256,
        scanner.sha256,
        document["classification"],
        tuple(document["policy"]["rule_ids"]),
    )


def load_accepted_research_spec_file(
    repo_root: Path, scanner: AcceptedScannerContract
) -> AcceptedResearchSpec:
    """Resolve only fixed repository paths and verify the complete policy section."""
    root = repo_root.resolve()
    artifact = root / "evidence/hybrid-500-iex-intraday-research-v1.json"
    policy = root / POLICY_PATH
    if not policy.resolve().is_relative_to(
        root
    ) or not artifact.resolve().is_relative_to(root):
        raise ValueError("research files must remain inside repository")
    with artifact.open("rb") as handle:
        spec = load_accepted_research_spec(handle.read(65537), scanner)
    text = policy.read_bytes().decode("utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("research policy section headings changed")
    start, end = text.index(START), text.index(END)
    if (
        end <= start
        or hashlib.sha256(text[start:end].encode()).hexdigest() != POLICY_SHA256
    ):
        raise ValueError("research policy section differs from accepted digest")
    return spec
