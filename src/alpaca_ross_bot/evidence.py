"""Validated source, strategy-version, and fidelity records."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_-]*\Z")


class EvidenceError(ValueError):
    """Evidence cannot support the declared strategy/version relationship."""


class SourceKind(StrEnum):
    ROSS_FIRST_PARTY = "ROSS_FIRST_PARTY"
    OTHER_TRADER_FIRST_PARTY = "OTHER_TRADER_FIRST_PARTY"
    BROKER_OFFICIAL = "BROKER_OFFICIAL"
    OTHER = "OTHER"


class Fidelity(StrEnum):
    ROSS_DIRECT = "ROSS_DIRECT"
    HYBRID_DIRECT = "HYBRID_DIRECT"
    BROKER_EQUIVALENT = "BROKER_EQUIVALENT"
    ENGINEERING_CONTROL = "ENGINEERING_CONTROL"
    BLOCKED_RESEARCH = "BLOCKED_RESEARCH"


class VersionStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    FROZEN = "FROZEN"


@dataclass(frozen=True)
class Source:
    source_id: str
    kind: SourceKind
    title: str
    url: str
    published_on: date | None
    accessed_on: date
    version_ids: tuple[str, ...]


@dataclass(frozen=True)
class StrategyVersion:
    version_id: str
    status: VersionStatus
    account_stage: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version_id: str
    domain: str
    fidelity: Fidelity
    statement: str
    source_ids: tuple[str, ...]
    equivalence_evidence: str | None
    engineering_basis: str | None
    blocking_reason: str | None


@dataclass(frozen=True)
class EvidenceBundle:
    sources: tuple[Source, ...]
    version: StrategyVersion
    rules: tuple[Rule, ...]

    def validate(self) -> None:
        source_by_id = {source.source_id: source for source in self.sources}
        if len(source_by_id) != len(self.sources):
            raise EvidenceError("duplicate source_id")
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise EvidenceError("duplicate rule_id")
        if self.version.status is VersionStatus.FROZEN and not self.rules:
            raise EvidenceError("a frozen strategy version needs rule records")
        for source_id in self.version.source_ids:
            if source_id not in source_by_id:
                raise EvidenceError("strategy version references an unknown source")
            if self.version.version_id not in source_by_id[source_id].version_ids:
                raise EvidenceError(
                    "strategy version cites a source for another version"
                )
        for rule in self.rules:
            if rule.version_id != self.version.version_id:
                raise EvidenceError("mixed strategy versions are forbidden")
            selected = []
            for source_id in rule.source_ids:
                if source_id not in source_by_id:
                    raise EvidenceError("rule references an unknown source")
                source = source_by_id[source_id]
                if self.version.version_id not in source.version_ids:
                    raise EvidenceError("rule cites a source for another version")
                selected.append(source)
            kinds = {source.kind for source in selected}
            if rule.fidelity is Fidelity.ROSS_DIRECT:
                if (
                    SourceKind.ROSS_FIRST_PARTY not in kinds
                    or SourceKind.OTHER_TRADER_FIRST_PARTY in kinds
                    or rule.equivalence_evidence
                    or rule.engineering_basis
                    or rule.blocking_reason
                ):
                    raise EvidenceError(
                        "Ross direct rule needs a Ross first-party source"
                    )
            elif rule.fidelity is Fidelity.HYBRID_DIRECT:
                if (
                    SourceKind.OTHER_TRADER_FIRST_PARTY not in kinds
                    or SourceKind.ROSS_FIRST_PARTY in kinds
                    or rule.equivalence_evidence
                    or rule.engineering_basis
                    or rule.blocking_reason
                ):
                    raise EvidenceError(
                        "hybrid direct rule needs another trader's first-party source"
                    )
            elif rule.fidelity is Fidelity.BROKER_EQUIVALENT:
                if (
                    not {
                        SourceKind.ROSS_FIRST_PARTY,
                        SourceKind.BROKER_OFFICIAL,
                    }
                    <= kinds
                    or SourceKind.OTHER_TRADER_FIRST_PARTY in kinds
                    or not rule.equivalence_evidence
                    or rule.engineering_basis
                    or rule.blocking_reason
                ):
                    raise EvidenceError("broker mapping needs both sources and proof")
            elif rule.fidelity is Fidelity.ENGINEERING_CONTROL:
                if (
                    not rule.engineering_basis
                    or rule.equivalence_evidence
                    or rule.blocking_reason
                ):
                    raise EvidenceError("engineering control needs its basis")
            elif rule.fidelity is Fidelity.BLOCKED_RESEARCH:
                if (
                    not rule.blocking_reason
                    or rule.equivalence_evidence
                    or rule.engineering_basis
                ):
                    raise EvidenceError("blocked rule needs a reason")


def _mapping(value: object, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EvidenceError(f"{label} has missing or unknown fields")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{label} must be nonempty text")
    return value


def _identifier(value: object, label: str) -> str:
    result = _text(value, label)
    if not _IDENTIFIER.fullmatch(result):
        raise EvidenceError(f"{label} has invalid syntax")
    return result


def _optional_text(value: object, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be a quoted ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an ISO date") from exc


def _ids(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be a list")
    result = tuple(_identifier(item, label) for item in value)
    if len(result) != len(set(result)):
        raise EvidenceError(f"{label} has duplicates")
    return result


def _enum[T: StrEnum](enum_type: type[T], value: object, label: str) -> T:
    try:
        return enum_type(_text(value, label))
    except ValueError as exc:
        raise EvidenceError(f"{label} has an unsupported value") from exc


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(  # type: ignore[override]
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[str, object]:
        self.flatten_mapping(node)
        result: dict[str, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise EvidenceError("YAML field names must be text")
            if key in result:
                raise EvidenceError("duplicate YAML field")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _load_yaml(path: Path) -> object:
    with path.open(encoding="utf-8") as evidence_file:
        try:
            return yaml.load(evidence_file, Loader=_UniqueKeyLoader)
        except yaml.YAMLError as exc:
            raise EvidenceError(f"invalid YAML in {path.name}") from exc


def load_bundle(registry: Path, version: Path, matrix: Path) -> EvidenceBundle:
    """Load the three files as one version-consistent unit."""
    registry_data = _mapping(
        _load_yaml(registry), {"schema_version", "sources"}, "registry"
    )
    version_data = _mapping(
        _load_yaml(version),
        {"schema_version", "version_id", "status", "account_stage", "source_ids"},
        "version",
    )
    matrix_data = _mapping(
        _load_yaml(matrix), {"schema_version", "version_id", "rules"}, "matrix"
    )
    for label, data in (
        ("registry", registry_data),
        ("version", version_data),
        ("matrix", matrix_data),
    ):
        if data["schema_version"] != 1 or type(data["schema_version"]) is not int:
            raise EvidenceError(f"unsupported {label} schema_version")
    if not isinstance(registry_data["sources"], list):
        raise EvidenceError("sources must be a list")
    sources = []
    for raw in registry_data["sources"]:
        item = _mapping(
            raw,
            {
                "source_id",
                "kind",
                "title",
                "url",
                "published_on",
                "accessed_on",
                "version_ids",
            },
            "source",
        )
        published = item["published_on"]
        sources.append(
            Source(
                source_id=_identifier(item["source_id"], "source_id"),
                kind=_enum(SourceKind, item["kind"], "source kind"),
                title=_text(item["title"], "source title"),
                url=_text(item["url"], "source url"),
                published_on=None
                if published is None
                else _date(published, "published_on"),
                accessed_on=_date(item["accessed_on"], "accessed_on"),
                version_ids=_ids(item["version_ids"], "source version_ids"),
            )
        )
    parsed_version = StrategyVersion(
        version_id=_identifier(version_data["version_id"], "version_id"),
        status=_enum(VersionStatus, version_data["status"], "version status"),
        account_stage=_identifier(version_data["account_stage"], "account_stage"),
        source_ids=_ids(version_data["source_ids"], "version source_ids"),
    )
    if matrix_data["version_id"] != parsed_version.version_id:
        raise EvidenceError("matrix has a different strategy version")
    if not isinstance(matrix_data["rules"], list):
        raise EvidenceError("rules must be a list")
    rules = []
    for raw in matrix_data["rules"]:
        item = _mapping(
            raw,
            {
                "rule_id",
                "version_id",
                "domain",
                "fidelity",
                "statement",
                "source_ids",
                "equivalence_evidence",
                "engineering_basis",
                "blocking_reason",
            },
            "rule",
        )
        rules.append(
            Rule(
                rule_id=_identifier(item["rule_id"], "rule_id"),
                version_id=_identifier(item["version_id"], "rule version_id"),
                domain=_identifier(item["domain"], "rule domain"),
                fidelity=_enum(Fidelity, item["fidelity"], "fidelity"),
                statement=_text(item["statement"], "statement"),
                source_ids=_ids(item["source_ids"], "rule source_ids"),
                equivalence_evidence=_optional_text(
                    item["equivalence_evidence"], "equivalence_evidence"
                ),
                engineering_basis=_optional_text(
                    item["engineering_basis"], "engineering_basis"
                ),
                blocking_reason=_optional_text(
                    item["blocking_reason"], "blocking_reason"
                ),
            )
        )
    bundle = EvidenceBundle(tuple(sources), parsed_version, tuple(rules))
    bundle.validate()
    return bundle
