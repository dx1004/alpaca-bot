"""Parallel, non-executable measurements of Ross source-scoped screens."""

from decimal import Decimal
from typing import Literal


def measure_ross_screening_profiles(
    *,
    price: Decimal,
    rvol_30d: Decimal | None,
    float_shares: int | None,
    float_status: str,
) -> list[dict[str, object]]:
    """Report separate observed comparisons; never return candidate eligibility.

    Thresholds below are transcribed from distinct 2025 toolkit contexts.
    Relative-volume and float comparisons are descriptive because their source
    applicability and point-in-time/data semantics are not accepted.
    """
    return [
        _general_toolkit_measurements(
            price=price,
            rvol_30d=rvol_30d,
            float_shares=float_shares,
            float_status=float_status,
        ),
        _challenge_worksheet_measurements(
            price=price,
            float_shares=float_shares,
            float_status=float_status,
        ),
    ]


def _general_toolkit_measurements(
    *,
    price: Decimal,
    rvol_30d: Decimal | None,
    float_shares: int | None,
    float_status: str,
) -> dict[str, object]:
    return {
        "profile_id": "ROSS_TOOLKIT_GENERAL_2025_SCREEN",
        "source_ids": ["ROSS_TOOLKIT_GENERAL_2025", "ROSS_TOOLKIT_RVOL_2025"],
        "classification": "ROSS_DIRECT_SOURCE_MEASUREMENT",
        "measurements": {
            "minimum_price_1_usd": _threshold(price, ">=", Decimal("1")),
            "under_20_usd_preference": _threshold(price, "<", Decimal("20")),
            "rvol_30d_at_least_5x": _threshold(rvol_30d, ">=", Decimal("5")),
            "float_under_20m": _float_threshold(
                float_shares,
                float_status,
                "<",
                20_000_000,
            ),
            "news_preference": "UNAVAILABLE_HUMAN_REVIEW_NOT_A_HARD_FILTER",
        },
        "assessment_status": "BLOCKED_RESEARCH",
        "strategy_eligibility": "BLOCKED_RESEARCH",
    }


def _challenge_worksheet_measurements(
    *,
    price: Decimal,
    float_shares: int | None,
    float_status: str,
) -> dict[str, object]:
    return {
        "profile_id": "ROSS_TOOLKIT_WORKSHEET_2025_SCREEN",
        "source_ids": ["ROSS_TOOLKIT_WORKSHEET_2025"],
        "classification": "ROSS_DIRECT_SOURCE_MEASUREMENT",
        "measurements": {
            "reported_price_range_5_to_10_usd": _reported_range(
                price, Decimal("5"), Decimal("10")
            ),
            "top_five_premarket_gapper": "UNAVAILABLE_PREMARKET_RANK_MISSING",
            "positive_catalyst": "UNAVAILABLE_NEWS_SEMANTICS_BLOCKED",
            "hot_market_float_under_20m": _float_threshold(
                float_shares,
                float_status,
                "<",
                20_000_000,
            ),
            "cold_market_float_under_10m": _float_threshold(
                float_shares,
                float_status,
                "<",
                10_000_000,
            ),
            "hot_cold_market_state": "UNAVAILABLE_REGIME_NOT_CLASSIFIED",
        },
        "assessment_status": "BLOCKED_RESEARCH",
        "strategy_eligibility": "BLOCKED_RESEARCH",
    }


def _threshold(
    value: Decimal | None, operator: Literal[">=", "<"], threshold: Decimal
) -> dict[str, object]:
    if value is None:
        return {
            "result": "UNAVAILABLE",
            "value": None,
            "operator": operator,
            "threshold": str(threshold),
        }
    matched = value >= threshold if operator == ">=" else value < threshold
    return {
        "result": (
            "MEASURED_THRESHOLD_MET" if matched else "MEASURED_THRESHOLD_NOT_MET"
        ),
        "value": str(value),
        "operator": operator,
        "threshold": str(threshold),
    }


def _float_threshold(
    value: int | None,
    status: str,
    operator: Literal["<"],
    threshold: int,
) -> dict[str, object]:
    comparison = _threshold(
        None if value is None else Decimal(value), operator, Decimal(threshold)
    )
    return {
        **comparison,
        "numeric_comparison": comparison["result"],
        "result": (
            "UNAVAILABLE" if value is None else "MEASUREMENT_ONLY_SOURCE_UNVERIFIED"
        ),
        "source_status": status,
    }


def _reported_range(
    value: Decimal, minimum: Decimal, maximum: Decimal
) -> dict[str, object]:
    if value in (minimum, maximum):
        result = "BOUNDARY_SEMANTICS_UNRESOLVED"
    elif minimum < value < maximum:
        result = "MEASURED_WITHIN_REPORTED_RANGE"
    else:
        result = "MEASURED_OUTSIDE_REPORTED_RANGE"
    return {
        "result": result,
        "value": str(value),
        "reported_minimum": str(minimum),
        "reported_maximum": str(maximum),
    }
