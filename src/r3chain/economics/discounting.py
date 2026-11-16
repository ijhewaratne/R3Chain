"""Discounting and levelised cost of heat (specification 13.3).

All cash flows are indexed by year. Index 0 is the start of the horizon, so a value at index t is
discounted by (1 + r) to the power t.

    LCOH = sum over t = 0..N of C_t / (1 + r)^t
           -------------------------------------------
           sum over t = 1..N of E_t / (1 + r)^t

The numerator and the denominator must refer to the same heat boundary. When no useful heat is
delivered the LCOH is undefined. It is never reported as zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


class UndefinedLcohError(ValueError):
    """The discounted delivered heat is zero, so a cost per unit of heat does not exist."""


def discount_factor(rate_per_year: float, year: float) -> float:
    if rate_per_year <= -1.0:
        raise ValueError("the discount rate must be above -1")
    return float((1.0 + rate_per_year) ** (-year))


def present_value(cash_flows: Sequence[float], rate_per_year: float) -> float:
    """Discount a series of yearly values, where index t is t years from the start."""
    return math.fsum(
        value * discount_factor(rate_per_year, year) for year, value in enumerate(cash_flows)
    )


def capital_recovery_factor(rate_per_year: float, years: float) -> float:
    """Annual payment per unit of investment over a number of years.

    For a discount rate of zero the limit is 1 / years.
    """
    if years <= 0.0:
        raise ValueError("the recovery period must be positive")
    if rate_per_year <= -1.0:
        raise ValueError("the discount rate must be above -1")
    if rate_per_year == 0.0:
        return 1.0 / years
    growth = (1.0 + rate_per_year) ** years
    return float(rate_per_year * growth / (growth - 1.0))


def levelised_cost_of_heat(
    costs: Sequence[float], delivered_heat_j: Sequence[float], rate_per_year: float
) -> float:
    """Return the discounted cost per joule of delivered heat.

    ``costs[t]`` and ``delivered_heat_j[t]`` belong to year t, for t = 0..N. Heat is delivered
    from year 1 on, so ``delivered_heat_j[0]`` must be zero.
    """
    if len(costs) != len(delivered_heat_j):
        raise ValueError("costs and delivered heat must cover the same years")
    if not costs:
        raise ValueError("at least one year is required")
    if delivered_heat_j[0] != 0.0:
        raise ValueError("heat is delivered from year 1 on. Year 0 must hold no delivered heat")
    if any(heat < 0.0 for heat in delivered_heat_j):
        raise ValueError("delivered heat cannot be negative")
    discounted_heat = present_value(delivered_heat_j, rate_per_year)
    if discounted_heat == 0.0:
        raise UndefinedLcohError(
            "no useful heat is delivered, so the levelised cost of heat is undefined"
        )
    return present_value(costs, rate_per_year) / discounted_heat
