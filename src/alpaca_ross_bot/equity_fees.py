"""Versioned ordinary-retail fee mathematics, not account applicability or authority."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

POLICY_SHA256 = "7560946c91f95cd56188c379c83c866e22b2f61d2a1c3e77c698de0640181ad2"
SEC = Fraction(206, 10_000_000)
CAT = Fraction(3, 1_000_000)


def verify_fee_policy(path: Path) -> str:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
        raise ValueError("fee policy differs from reviewed source version")
    data = json.loads(raw)
    return str(data["document_sha256"])


def _positive(value: Fraction) -> None:
    if type(value) is not Fraction or value <= 0:
        raise ValueError("positive exact Fraction required")


def _cent_ceiling(value: Fraction) -> Fraction:
    return Fraction(-(-value.numerator * 100 // value.denominator), 100)


def _decimal_cents(value: Fraction) -> Decimal:
    cents = value * 100
    if cents.denominator != 1 or cents < 0:
        raise ValueError("exact nonnegative cents required")
    amount = cents.numerator
    return Decimal(f"{amount // 100}.{amount % 100:02d}")


@dataclass(frozen=True)
class ExecutedFeeLot:
    execution_id: str
    side: str
    quantity: Fraction
    price: Fraction


@dataclass(frozen=True)
class FeeTotals:
    sec: Fraction
    taf: Fraction
    cat: Fraction
    policy_sha256: str = POLICY_SHA256
    runtime_authorized: bool = False

    @property
    def total(self) -> Fraction:
        return self.sec + self.taf + self.cat


def daily_fees(policy: Path, lots: tuple[ExecutedFeeLot, ...]) -> FeeTotals:
    """Aggregate distinct executions, then ceil each fee-type account/day total.

    Caller supplies one complete account/day execution census. This function does
    not establish completeness, actual broker charges or retail classification.
    Paper's raw regulatory charges remain a separate broker ledger.
    """
    verify_fee_policy(policy)
    if type(lots) is not tuple:
        raise ValueError("immutable execution census required")
    seen: dict[str, ExecutedFeeLot] = {}
    sec = cat = Fraction(0)
    for lot in lots:
        if type(lot.execution_id) is not str or not lot.execution_id.strip():
            raise ValueError("execution identity required")
        if lot.side not in ("buy", "sell"):
            raise ValueError("unsupported fee side")
        _positive(lot.quantity)
        _positive(lot.price)
        prior = seen.get(lot.execution_id)
        if prior is not None:
            if prior != lot:
                raise ValueError("execution identity conflict")
            continue
        seen[lot.execution_id] = lot
        cat += CAT * lot.quantity
        if lot.side == "sell":
            sec += SEC * lot.quantity * lot.price
    return FeeTotals(_cent_ceiling(sec), Fraction(0), _cent_ceiling(cat))


def planned_charge_bounds(
    policy: Path, quantity: int, stop_price: Fraction
) -> tuple[Decimal, Decimal]:
    """Bound daily fee increments for one entry and modeled stop exit at S.

    ceil(a+b)-ceil(a) <= ceil(b) per nonnegative fee type. Thus separate
    per-leg ceilings are conservative bounds, NOT actual per-order minimums or
    the broker's daily posting algorithm. Slippage/other exit prices are outside
    this planned-S model. Account applicability and paper cost policy must be
    separately accepted; no FeeBounds.verified or runtime grant is produced.
    """
    verify_fee_policy(policy)
    if type(quantity) is not int or quantity <= 0:
        raise ValueError("positive whole-share quantity required")
    _positive(stop_price)
    entry = _cent_ceiling(CAT * quantity)
    stop = _cent_ceiling(SEC * quantity * stop_price) + _cent_ceiling(CAT * quantity)
    return _decimal_cents(entry), _decimal_cents(stop)
