"""Pinned independent emergency policy identity; no risk or broker authority."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .oco_research import (
    CANDIDATE as PARENT_CANDIDATE,
)
from .oco_research import (
    MANIFEST_SHA256 as PARENT_SHA256,
)
from .oco_research import (
    VERSION as PARENT_VERSION,
)
from .oco_research import load_oco_research_contract

VERSION = "CROSSED_STOP_RESIDUAL_EXIT_RESEARCH_V1"
CANDIDATE = "HYBRID_500_IEX_CROSSED_STOP_RESEARCH_V1"
MANIFEST_SHA256 = "6f5c6fe8796e9abad2f88edcee46d13e337f762765f45f6ba08c6959e01fc0b1"
_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, repr=False)
class CrossedStopContract:
    raw: bytes = field(repr=False)
    parent: bytes = field(repr=False)

    def __post_init__(self) -> None:
        for raw, expected in (
            (self.raw, MANIFEST_SHA256),
            (self.parent, PARENT_SHA256),
        ):
            if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("pinned emergency policy and OCO parent required")
        document, parent = json.loads(self.raw), json.loads(self.parent)
        authority = document["authority"]
        if (
            document["version"] != VERSION
            or document["candidate_version"] != CANDIDATE
            or document["parent_contract_sha256"] != PARENT_SHA256
            or parent["version"] != PARENT_VERSION
            or parent["candidate_version"] != PARENT_CANDIDATE
            or authority["research_semantics_accepted"] is not True
            or any(
                value is not False
                for key, value in authority.items()
                if key != "research_semantics_accepted"
            )
        ):
            raise ValueError("incompatible emergency research contract")

    @property
    def identity(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "version": VERSION,
            "candidate_version": CANDIDATE,
            "manifest_sha256": MANIFEST_SHA256,
            "parent_execution_version": PARENT_VERSION,
            "parent_execution_sha256": PARENT_SHA256,
            "classification": (
                "EXPLICIT_SAME_SLOT_RESEARCH_OPT_IN_NOT_RUNTIME_ACCEPTANCE"
            ),
            "research_semantics_accepted": True,
            "entry_risk_version_binding_accepted": False,
            "source_admission_qualified": False,
            "broker_exit_qualified": False,
            "runtime_authorized": False,
            "order_authority": False,
        }


def load_crossed_stop_contract() -> CrossedStopContract:
    parent = load_oco_research_contract()
    return CrossedStopContract(
        (_ROOT / "evidence/crossed-stop-residual-exit-research-v1.json").read_bytes(),
        parent.manifest_bytes,
    )
