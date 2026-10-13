from dataclasses import replace
from pathlib import Path

import pytest

from alpaca_ross_bot.evidence import (
    EvidenceBundle,
    EvidenceError,
    Fidelity,
    Rule,
    Source,
    SourceKind,
    StrategyVersion,
    VersionStatus,
    load_bundle,
)


def test_candidate_inventory_is_valid_but_not_executable() -> None:
    bundle = load_bundle(
        Path("evidence/source-registry.yaml"),
        Path("evidence/strategy-versions/ROSS_SMALL_ACCOUNT_CANDIDATE_V1.yaml"),
        Path("evidence/fidelity-matrix.yaml"),
    )
    assert bundle.version.status is VersionStatus.CANDIDATE
    assert len(bundle.rules) == 11
    assert all(rule.fidelity is Fidelity.BLOCKED_RESEARCH for rule in bundle.rules)


def test_hybrid_candidate_is_separate_and_not_executable() -> None:
    bundle = load_bundle(
        Path("evidence/source-registry.yaml"),
        Path("evidence/strategy-versions/HYBRID_500_IEX_CANDIDATE_V1.yaml"),
        Path("evidence/hybrid-fidelity-matrix.yaml"),
    )
    assert bundle.version.status is VersionStatus.CANDIDATE
    assert len(bundle.rules) == 11
    assert all(
        source.kind is SourceKind.OTHER_TRADER_FIRST_PARTY
        for source in bundle.sources
        if source.source_id in bundle.version.source_ids
    )
    assert all(rule.fidelity is Fidelity.BLOCKED_RESEARCH for rule in bundle.rules)


def test_duplicate_yaml_field_is_rejected(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text("schema_version: 1\nsources: []\nsources: []\n")
    with pytest.raises(EvidenceError, match="duplicate YAML field"):
        load_bundle(
            registry,
            Path("evidence/strategy-versions/ROSS_SMALL_ACCOUNT_CANDIDATE_V1.yaml"),
            Path("evidence/fidelity-matrix.yaml"),
        )


def test_mixed_version_rule_is_rejected() -> None:
    bundle = EvidenceBundle(
        sources=(),
        version=StrategyVersion("VERSION_A", VersionStatus.CANDIDATE, "STAGE_A", ()),
        rules=(
            Rule(
                "RULE_001",
                "VERSION_B",
                "NEWS",
                Fidelity.BLOCKED_RESEARCH,
                "News quality is not yet specified",
                (),
                None,
                None,
                "Source semantics pending",
            ),
        ),
    )
    with pytest.raises(EvidenceError, match="mixed strategy versions"):
        bundle.validate()


def test_direct_rule_requires_first_party_source() -> None:
    from datetime import date

    source = Source(
        "ALPACA_DOC_001",
        SourceKind.BROKER_OFFICIAL,
        "Alpaca doc",
        "https://docs.alpaca.markets/example",
        None,
        date(2026, 9, 28),
        ("VERSION_A",),
    )
    bundle = EvidenceBundle(
        sources=(source,),
        version=StrategyVersion("VERSION_A", VersionStatus.CANDIDATE, "STAGE_A", ()),
        rules=(
            Rule(
                "RULE_001",
                "VERSION_A",
                "ENTRY",
                Fidelity.ROSS_DIRECT,
                "Example entry",
                ("ALPACA_DOC_001",),
                None,
                None,
                None,
            ),
        ),
    )
    with pytest.raises(EvidenceError, match="Ross first-party"):
        bundle.validate()


def test_other_trader_cannot_be_labeled_ross_direct() -> None:
    from datetime import date

    source = Source(
        "OTHER_TRADER_001",
        SourceKind.OTHER_TRADER_FIRST_PARTY,
        "Other trader",
        "https://example.org",
        None,
        date(2026, 10, 6),
        ("HYBRID_A",),
    )
    version = StrategyVersion(
        "HYBRID_A", VersionStatus.CANDIDATE, "USD_500_1X_PAPER", ()
    )
    direct_rule = Rule(
        "RULE_001",
        "HYBRID_A",
        "ENTRY",
        Fidelity.ROSS_DIRECT,
        "Other trader's entry",
        ("OTHER_TRADER_001",),
        None,
        None,
        None,
    )
    with pytest.raises(EvidenceError, match="Ross first-party"):
        EvidenceBundle((source,), version, (direct_rule,)).validate()
    ross_source = replace(
        source,
        source_id="ROSS_001",
        kind=SourceKind.ROSS_FIRST_PARTY,
    )
    mixed_rule = replace(direct_rule, source_ids=("ROSS_001", "OTHER_TRADER_001"))
    with pytest.raises(EvidenceError, match="Ross first-party"):
        EvidenceBundle((source, ross_source), version, (mixed_rule,)).validate()


def test_hybrid_direct_requires_other_trader_first_party_source() -> None:
    from datetime import date

    source = Source(
        "OTHER_TRADER_001",
        SourceKind.OTHER_TRADER_FIRST_PARTY,
        "Other trader",
        "https://example.org",
        None,
        date(2026, 10, 6),
        ("HYBRID_A",),
    )
    version = StrategyVersion(
        "HYBRID_A", VersionStatus.CANDIDATE, "USD_500_1X_PAPER", ()
    )
    rule = Rule(
        "RULE_001",
        "HYBRID_A",
        "ENTRY",
        Fidelity.HYBRID_DIRECT,
        "Other trader's documented entry",
        ("OTHER_TRADER_001",),
        None,
        None,
        None,
    )
    EvidenceBundle((source,), version, (rule,)).validate()
    with pytest.raises(EvidenceError, match="other trader's first-party"):
        EvidenceBundle((source,), version, (replace(rule, source_ids=()),)).validate()
    ross_source = replace(
        source,
        source_id="ROSS_001",
        kind=SourceKind.ROSS_FIRST_PARTY,
    )
    mixed_rule = replace(rule, source_ids=("ROSS_001", "OTHER_TRADER_001"))
    with pytest.raises(EvidenceError, match="other trader's first-party"):
        EvidenceBundle((source, ross_source), version, (mixed_rule,)).validate()


def test_rule_cannot_cite_source_for_another_version() -> None:
    from datetime import date

    source = Source(
        "ROSS_001",
        SourceKind.ROSS_FIRST_PARTY,
        "Ross",
        "https://example.org",
        None,
        date(2026, 9, 28),
        ("VERSION_B",),
    )
    bundle = EvidenceBundle(
        sources=(source,),
        version=StrategyVersion("VERSION_A", VersionStatus.CANDIDATE, "STAGE_A", ()),
        rules=(
            Rule(
                "RULE_001",
                "VERSION_A",
                "ENTRY",
                Fidelity.ROSS_DIRECT,
                "Example entry",
                ("ROSS_001",),
                None,
                None,
                None,
            ),
        ),
    )
    with pytest.raises(EvidenceError, match="source for another version"):
        bundle.validate()


def test_broker_mapping_needs_explicit_proof() -> None:
    from datetime import date

    sources = (
        Source(
            "ROSS_001",
            SourceKind.ROSS_FIRST_PARTY,
            "Ross",
            "https://example.org",
            None,
            date(2026, 9, 28),
            ("VERSION_A",),
        ),
        Source(
            "BROKER_001",
            SourceKind.BROKER_OFFICIAL,
            "Broker",
            "https://example.org",
            None,
            date(2026, 9, 28),
            ("VERSION_A",),
        ),
    )
    bundle = EvidenceBundle(
        sources=sources,
        version=StrategyVersion("VERSION_A", VersionStatus.CANDIDATE, "STAGE_A", ()),
        rules=(
            Rule(
                "RULE_001",
                "VERSION_A",
                "EXECUTION",
                Fidelity.BROKER_EQUIVALENT,
                "Example mapping",
                ("ROSS_001", "BROKER_001"),
                None,
                None,
                None,
            ),
        ),
    )
    with pytest.raises(EvidenceError, match="both sources and proof"):
        bundle.validate()
    other_source = replace(
        sources[0],
        source_id="OTHER_TRADER_001",
        kind=SourceKind.OTHER_TRADER_FIRST_PARTY,
    )
    mixed_rule = replace(
        bundle.rules[0],
        source_ids=("ROSS_001", "BROKER_001", "OTHER_TRADER_001"),
        equivalence_evidence="Documented broker mapping",
    )
    with pytest.raises(EvidenceError, match="both sources and proof"):
        EvidenceBundle(
            (*sources, other_source), bundle.version, (mixed_rule,)
        ).validate()
