"""Pinned scanner-only acceptance artifact; grants no trading authority."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ACCEPTED_SHA256 = "5ce35ecf7e4869dfda490c5e54b0d17d35c47b4d29cd641ccf2d14f87d8bc88f"
RULE_IDS = (
    "V1_PRICE_1_INCLUSIVE_20_EXCLUSIVE",
    "V1_RVOL_IEX_RAW_DAILY30_GE5",
    "V1_RANK_EXACT_CHANGE_SYMBOL_TIE",
    "V1_DISCOVERY_CURRENT_ACTIVE_US_EQUITY",
    "V1_NEWS_FLOAT_NONVETO_PREFERENCES",
    "V1_AS_OBSERVED_NY_DAY_AVAILABILITY",
)


@dataclass(frozen=True)
class AcceptedScannerContract:
    profile_id: str
    classification: str
    discovery_scope: str
    sha256: str
    rule_ids: tuple[str, ...]
    strategy_eligibility: str = "BLOCKED_RESEARCH"
    order_authority: bool = False
    formal_paper_evaluation: bool = False


_ACCEPTED = AcceptedScannerContract(
    "GENERAL_IEX_DAILY30_SCREEN_V1",
    "USER_ACCEPTED_ADAPTATION_NOT_ROSS_REPLICA",
    "ALPACA_CURRENT_ACTIVE_US_EQUITY_CATALOG",
    ACCEPTED_SHA256,
    RULE_IDS,
)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate contract JSON key")
        result[key] = value
    return result


def load_accepted_scanner_contract(raw: bytes) -> AcceptedScannerContract:
    """Accept only exact reviewed V1 bytes, strict UTF-8 and unique-key JSON.

    The digest binds all nested schema/rules/authority fields, including fields
    not projected into this compact runtime identity. Whitespace changes require
    review and repinning too. No user-provided digest can override this pin.
    """
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 65536:
        raise ValueError("contract bytes must be nonempty and bounded")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("contract must be strict UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("contract root must be an object")
    if hashlib.sha256(raw).hexdigest() != ACCEPTED_SHA256:
        raise ValueError("contract bytes do not match accepted V1 digest")
    data = cast(dict[str, object], document)
    if data.get("profile_id") != _ACCEPTED.profile_id:
        raise ValueError("unsupported scanner contract version")
    return _ACCEPTED


def load_accepted_scanner_contract_file(path: Path) -> AcceptedScannerContract:
    with path.open("rb") as handle:
        return load_accepted_scanner_contract(handle.read(65537))


def validate_accepted_scanner_contract(contract: AcceptedScannerContract) -> None:
    if type(contract) is not AcceptedScannerContract or contract != _ACCEPTED:
        raise ValueError("unsupported or altered scanner contract")


def validate_sequence_refs(refs: tuple[int, ...]) -> None:
    if not isinstance(refs, tuple) or any(
        type(ref) is not int or ref <= 0 for ref in refs
    ):
        raise ValueError("input sequence refs must be positive integer tuple")
    if len(set(refs)) != len(refs):
        raise ValueError("input sequence refs must be unique")
