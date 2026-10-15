import json
from dataclasses import replace
from pathlib import Path

import pytest

from alpaca_ross_bot.scanner_contract import (
    ACCEPTED_SHA256,
    RULE_IDS,
    load_accepted_scanner_contract,
    validate_accepted_scanner_contract,
    validate_sequence_refs,
)

RAW = Path("evidence/general-iex-daily30-screen-v1.json").read_bytes()


def test_pinned_contract_has_scanner_only_authority() -> None:
    contract = load_accepted_scanner_contract(RAW)
    assert contract.sha256 == ACCEPTED_SHA256 and contract.rule_ids == RULE_IDS
    assert not contract.order_authority and not contract.formal_paper_evaluation
    assert contract.strategy_eligibility == "BLOCKED_RESEARCH"


@pytest.mark.parametrize("mutation", ["version", "rule", "unknown", "whitespace"])
def test_altered_contract_denied(mutation: str) -> None:
    data = json.loads(RAW)
    if mutation == "version":
        data["profile_id"] = "GENERAL_IEX_DAILY30_SCREEN_V2"
    elif mutation == "rule":
        data["rules"]["rvol_minimum_inclusive"] = "4"
    elif mutation == "unknown":
        data["unknown"] = True
    raw = RAW + b" " if mutation == "whitespace" else json.dumps(data).encode()
    with pytest.raises(ValueError, match="digest"):
        load_accepted_scanner_contract(raw)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b"\xff", b"", b" " * 65537, b"[]"])
def test_invalid_bytes_rejected(raw: bytes) -> None:
    with pytest.raises(ValueError):
        load_accepted_scanner_contract(raw)


def test_forged_typed_contract_rejected() -> None:
    with pytest.raises(ValueError):
        validate_accepted_scanner_contract(
            replace(load_accepted_scanner_contract(RAW), order_authority=True)
        )


@pytest.mark.parametrize("refs", [(0,), (-1,), (True,), (1, 1)])
def test_invalid_refs_rejected(refs: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        validate_sequence_refs(refs)
