"""Synthetic durable daily-risk ownership; no broker requests."""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from alpaca_ross_bot.journal import Journal, JournalError
from alpaca_ross_bot.risk_journal import RiskInputs, RiskJournal
from alpaca_ross_bot.strategy_risk import FeeBound, FeeBounds, RiskAccount, RiskLedger

D = Decimal
ACCOUNT = "sha256:" + "a" * 64
AT = datetime(2026, 10, 16, 14, tzinfo=UTC)
BASE = AT.replace(hour=12)
REFS = (1,)


def inputs() -> RiskInputs:
    account = RiskAccount(
        D(500),
        D(500),
        date(2026, 10, 16),
        ACCOUNT,
        equity_verified=True,
        funds_verified=True,
        account_eligible=True,
        symbol_tradable=True,
        one_x_verified=True,
        baseline_verified=True,
    )
    ledger = RiskLedger(
        ACCOUNT,
        account.session,
        D(0),
        D(0),
        D(0),
        verified=True,
        marks_verified=True,
        cashflows_verified=True,
    )
    fees = FeeBounds(
        tuple(FeeBound(q, D(0), D(0)) for q in range(1, 501)),
        "synthetic-fee-bounds",
        True,
    )
    return RiskInputs(
        account,
        ledger,
        fees,
        AT,
        BASE,
        AT.replace(hour=13, minute=30),
        REFS,
        True,
        True,
    )


def setup(tmp_path: Path) -> tuple[Journal, RiskJournal, list[RiskInputs]]:
    root = tmp_path / "private"
    journal = Journal.open_private(root / "risk.sqlite3", root)
    journal.record_paper_account_verification(ACCOUNT, b"synthetic", AT)
    event = journal.append_next(
        source_id="SYNTHETIC_OFFLINE_RISK_FIXTURE",
        feed="paper",
        event_kind="OFFLINE_RISK_FIXTURE",
        payload=b"synthetic fixture only",
        system_first_seen_at=BASE,
        processed_at=BASE,
    )
    assert event.sequence == REFS[0]
    facts = [inputs()]
    owner = RiskJournal(journal, ACCOUNT, lambda refs: facts[0])
    return journal, owner, facts


def view(owner: RiskJournal) -> dict[str, Any]:
    value = owner.view()
    assert value is not None
    return cast(dict[str, Any], value)


def reserve(owner: RiskJournal, key: str = "entry-1", symbol: str = "ABC") -> int:
    return owner.reserve(
        key,
        REFS,
        AT,
        symbol=symbol,
        trigger_high=D("9.99"),
        known_low=D("9.50"),
        trigger_trade=D(10),
        ask=D(10),
    ).quantity


def test_restart_preserves_baseline_clock_reservation_and_blocks_second_symbol(
    tmp_path: Path,
) -> None:
    journal, owner, facts = setup(tmp_path)
    assert reserve(owner) == 5
    before = owner.view()
    restarted = RiskJournal(journal, ACCOUNT, lambda refs: facts[0])
    assert restarted.view() == before and restarted.verify_rebuild()
    assert reserve(restarted, "entry-2", "XYZ") == 0
    assert view(restarted)["held"]["identity"] == "entry-1"


def test_exact_duplicate_is_idempotent_and_changed_duplicate_latches_veto(
    tmp_path: Path,
) -> None:
    journal, owner, facts = setup(tmp_path)
    owner.observe("obs", REFS, AT)
    count = len(journal.read_strategy_risk_events())
    owner.observe("obs", REFS, AT)
    assert len(journal.read_strategy_risk_events()) == count
    facts[0] = replace(facts[0], ledger=replace(facts[0].ledger, realized=D(-1)))
    owner.observe("obs", REFS, AT)
    assert view(owner)["veto"] is True
    facts[0] = inputs()
    owner.observe("recovery", REFS, AT)
    assert reserve(owner) == 0 and owner.verify_rebuild()


def test_changed_reservation_reuse_cannot_return_original_positive_decision(
    tmp_path: Path,
) -> None:
    _, owner, facts = setup(tmp_path)
    assert reserve(owner) == 5
    facts[0] = replace(inputs(), ledger=replace(inputs().ledger, realized=D(-1)))
    assert reserve(owner) == 0
    assert view(owner)["held"]["identity"] == "entry-1"
    assert view(owner)["veto"] is True


@pytest.mark.parametrize("delta", [0, 1, 6])
def test_intraday_baseline_refused(tmp_path: Path, delta: int) -> None:
    _, owner, facts = setup(tmp_path)
    facts[0] = replace(
        facts[0],
        presession_equity_at=AT.replace(hour=13, minute=30) + timedelta(minutes=delta),
    )
    with pytest.raises(JournalError, match="pre-session"):
        owner.observe("obs", REFS, AT)
    assert owner.view() is None


def test_baseline_identity_and_equity_cannot_change(tmp_path: Path) -> None:
    _, owner, facts = setup(tmp_path)
    owner.observe("initial", REFS, AT)
    facts[0] = replace(facts[0], presession_equity_at=BASE + timedelta(seconds=1))
    with pytest.raises(JournalError, match="baseline identity"):
        owner.observe("new", REFS, AT)
    facts[0] = replace(inputs(), account=replace(inputs().account, equity=D(600)))
    with pytest.raises(ValueError, match="equity mismatch"):
        owner.observe("equity", REFS, AT)


def test_three_losses_latch_and_roundtrip_net_cannot_be_rewritten(
    tmp_path: Path,
) -> None:
    _, owner, facts = setup(tmp_path)
    for i in range(3):
        facts[0] = replace(inputs(), roundtrip_id=f"roundtrip-{i}", roundtrip_net=D(-1))
        owner.observe(f"obs-{i}", REFS, AT)
    assert view(owner)["daily"]["latched"] is True
    assert view(owner)["daily"]["losing_streak"] == 3
    facts[0] = replace(inputs(), roundtrip_id="roundtrip-0", roundtrip_net=D(10))
    owner.observe("changed-net", REFS, AT)
    assert view(owner)["veto"] is True
    assert view(owner)["completed"]["roundtrip-0"] == "-1"


def test_daily_loss_latch_survives_recovery_and_restart(tmp_path: Path) -> None:
    journal, owner, facts = setup(tmp_path)
    facts[0] = replace(inputs(), ledger=replace(inputs().ledger, realized=D(-5)))
    owner.observe("loss", REFS, AT)
    facts[0] = inputs()
    other = RiskJournal(journal, ACCOUNT, lambda refs: facts[0])
    assert reserve(other) == 0
    assert view(other)["daily"]["latched"] is True


def test_terminal_roundtrip_releases_only_owned_slot(tmp_path: Path) -> None:
    _, owner, facts = setup(tmp_path)
    assert reserve(owner) == 5
    facts[0] = replace(inputs(), roundtrip_id="foreign", roundtrip_net=D(0))
    owner.observe("foreign-flat", REFS, AT)
    assert reserve(owner, "still-held") == 0
    facts[0] = replace(inputs(), roundtrip_id="entry-1", roundtrip_net=D(0))
    owner.observe("owned-flat", REFS, AT)
    facts[0] = inputs()
    assert reserve(owner, "new-entry") == 5


def test_no_resolver_cannot_grant_capacity(tmp_path: Path) -> None:
    journal, _, _ = setup(tmp_path)
    owner = RiskJournal(journal, ACCOUNT)
    with pytest.raises(JournalError, match="verifier unavailable"):
        reserve(owner)


def test_positive_fixture_calculation_does_not_qualify_actual_inputs(
    tmp_path: Path,
) -> None:
    _, owner, _ = setup(tmp_path)
    assert reserve(owner) == 5
    assert owner.runtime_qualified is False
    assert owner.qualification_reasons == (
        "ACTUAL_RISK_INPUT_QUALIFIER_UNAVAILABLE",
        "INJECTED_RESOLVER_IS_OFFLINE_FIXTURE_ONLY",
    )
    assert view(owner)["qualification"] == "OFFLINE_FIXTURE_ONLY"
    assert view(owner)["runtime_authorized"] is False


@pytest.mark.parametrize("kind", ["missing", "future", "corrupt"])
def test_reference_checks_precede_fixture_resolver(tmp_path: Path, kind: str) -> None:
    journal, owner, _ = setup(tmp_path)
    refs = REFS
    if kind == "missing":
        refs = (99999,)
    elif kind == "future":
        future = AT + timedelta(seconds=1)
        event = journal.append_next(
            source_id="SYNTHETIC_OFFLINE_RISK_FIXTURE",
            feed="paper",
            event_kind="OFFLINE_RISK_FIXTURE",
            payload=b"future fixture",
            system_first_seen_at=future,
            processed_at=future,
        )
        refs = (event.sequence,)
    else:
        # Deliberate corruption bypasses immutability solely in this fixture.
        journal._db.execute("DROP TRIGGER events_no_update")
        journal._db.execute(
            "UPDATE events SET payload=? WHERE sequence=1", (b"tampered",)
        )
        journal._db.commit()

    def forbidden(_: tuple[int, ...]) -> RiskInputs:
        pytest.fail("invalid references reached the fixture resolver")

    owner.resolve_inputs = forbidden
    with pytest.raises(JournalError):
        owner.observe("invalid-reference", refs, AT)
    assert owner.view() is None


@pytest.mark.parametrize("corruption", ["projection", "dedupe", "orphan"])
def test_cache_corruption_blocks_before_new_evidence(
    tmp_path: Path, corruption: str
) -> None:
    journal, owner, _ = setup(tmp_path)
    owner.observe("initial", REFS, AT)
    count = len(journal.read_strategy_risk_events())
    if corruption == "projection":
        journal._db.execute("DELETE FROM state WHERE key=?", (owner.state_key,))
    elif corruption == "dedupe":
        journal._db.execute(
            "DELETE FROM state WHERE key LIKE 'strategy_risk_evidence:%'"
        )
    else:
        key = (
            "strategy_risk_evidence:"
            + ACCOUNT
            + ":"
            + json.dumps([view(owner)["version"], "orphan"], separators=(",", ":"))
        )
        journal._db.execute(
            "INSERT INTO state(key,value_json) VALUES(?,?)",
            (key, '{"hash":"bad","sequence":1}'),
        )
    journal._db.commit()
    with pytest.raises(JournalError, match="differs"):
        owner.observe("new", REFS, AT)
    assert len(journal.read_strategy_risk_events()) == count


def test_storage_failure_rolls_back_event_and_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, owner, _ = setup(tmp_path)
    original = journal._append_locked

    def fail(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise sqlite3.OperationalError("synthetic disk full")

    monkeypatch.setattr(journal, "_append_locked", fail)
    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        reserve(owner)
    assert owner.view() is None and journal.read_strategy_risk_events() == ()


def test_two_owner_instances_cannot_spend_one_snapshot_twice(tmp_path: Path) -> None:
    journal, first, facts = setup(tmp_path)
    second_journal = Journal.open_private(
        tmp_path / "private" / "risk.sqlite3", tmp_path / "private"
    )
    second = RiskJournal(second_journal, ACCOUNT, lambda refs: facts[0])
    assert reserve(first) == 5
    assert reserve(second, "entry-2", "XYZ") == 0
    assert first.verify_rebuild() and second.verify_rebuild()


def test_stale_clock_latches_and_does_not_rewind(tmp_path: Path) -> None:
    _, owner, facts = setup(tmp_path)
    owner.observe("initial", REFS, AT + timedelta(seconds=1))
    owner.observe("stale", REFS, AT)
    assert view(owner)["at"] == (AT + timedelta(seconds=1)).isoformat()
    assert view(owner)["veto"] is True
    facts[0] = inputs()
    owner.observe("fresh", REFS, AT + timedelta(seconds=2))
    assert view(owner)["veto"] is True


def test_session_rollover_requires_flat_and_preserves_lifetime_roundtrip_ids(
    tmp_path: Path,
) -> None:
    _, owner, facts = setup(tmp_path)
    facts[0] = replace(inputs(), roundtrip_id="completed", roundtrip_net=D(-5))
    owner.observe("loss", REFS, AT)
    future = AT + timedelta(days=3)
    facts[0] = replace(
        inputs(),
        account=replace(inputs().account, session=future.date()),
        ledger=replace(inputs().ledger, session=future.date()),
        observed_at=future,
        presession_equity_at=BASE + timedelta(days=3),
        session_open=inputs().session_open + timedelta(days=3),
        reconciled_flat=False,
    )
    with pytest.raises(ValueError, match="new date"):
        owner.observe("unreconciled-new-day", REFS, future)
    facts[0] = replace(facts[0], reconciled_flat=True)
    owner.observe("new-day", REFS, future)
    assert view(owner)["daily"]["latched"] is False
    assert view(owner)["completed"] == {"completed": "-5"}


@pytest.mark.parametrize(
    "kind", ["foreign", "references", "unverified-baseline", "future", "calendar"]
)
def test_invalid_qualified_facts_never_replace_state(tmp_path: Path, kind: str) -> None:
    _, owner, facts = setup(tmp_path)
    owner.observe("initial", REFS, AT)
    original = owner.view()
    if kind == "foreign":
        facts[0] = replace(
            inputs(), account=replace(inputs().account, account_ref="foreign")
        )
    elif kind == "references":
        facts[0] = replace(inputs(), input_event_refs=(99,))
    elif kind == "unverified-baseline":
        facts[0] = replace(
            inputs(), account=replace(inputs().account, baseline_verified=False)
        )
    elif kind == "future":
        facts[0] = replace(inputs(), observed_at=AT + timedelta(seconds=1))
    else:
        facts[0] = replace(
            inputs(), session_open=inputs().session_open + timedelta(minutes=1)
        )
    with pytest.raises(JournalError):
        owner.observe("invalid", REFS, AT)
    assert owner.view() == original
