"""Analytic fixtures for discounting (acceptance test A10 at fixture level).

The 20 year period and 4 percent rate repeat the workshop demonstration. They are a labelled
regression example and not project assumptions.
"""

from __future__ import annotations

import pytest

from r3chain.domain.units import JOULES_PER_MWH
from r3chain.economics.discounting import (
    UndefinedLcohError,
    capital_recovery_factor,
    discount_factor,
    levelised_cost_of_heat,
    present_value,
)


@pytest.mark.requirement("A10")
def test_capital_recovery_factor_matches_annuity_sum() -> None:
    # Independent route: the capital recovery factor is the reciprocal of the annuity factor,
    # and the annuity factor is the plain sum of the yearly discount factors.
    annuity = sum(1.04**-year for year in range(1, 21))
    assert capital_recovery_factor(0.04, 20) == pytest.approx(1.0 / annuity, rel=1e-12)
    # Hand value: 1.04^20 = 2.191123143, so
    #   CRF = 0.04 * 2.191123143 / 1.191123143 = 0.0735818
    assert capital_recovery_factor(0.04, 20) == pytest.approx(0.0735818, abs=2e-7)


@pytest.mark.requirement("A10")
def test_capital_recovery_factor_at_zero_rate() -> None:
    # Without discounting an investment is simply spread evenly over the years.
    assert capital_recovery_factor(0.0, 20) == pytest.approx(0.05)
    # The formula approaches the same limit continuously.
    assert capital_recovery_factor(1e-9, 20) == pytest.approx(0.05, rel=1e-6)


def test_capital_recovery_factor_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        capital_recovery_factor(0.04, 0)
    with pytest.raises(ValueError):
        capital_recovery_factor(-1.0, 20)


def test_present_value_by_hand() -> None:
    # 1000 now, 100 after one year and 100 after two years at 10 percent:
    #   1000 + 100 / 1.1 + 100 / 1.21 = 1000 + 90.909091 + 82.644628 = 1173.553719
    assert present_value([1000.0, 100.0, 100.0], 0.10) == pytest.approx(1173.553719, abs=1e-6)
    assert discount_factor(0.10, 2) == pytest.approx(1 / 1.21)
    assert present_value([5.0, 5.0, 5.0], 0.0) == pytest.approx(15.0)


@pytest.mark.requirement("A10")
def test_variable_cash_flow_lcoh_by_hand() -> None:
    # Costs of 1000, 100 and 100 in years 0, 1 and 2. Heat of 50 MWh in each of years 1 and 2.
    # Discount rate 10 percent.
    #   discounted cost = (1210 + 110 + 100) / 1.21 = 1420 / 1.21
    #   discounted heat = (55 + 50) / 1.21          = 105 / 1.21 MWh
    #   LCOH            = 1420 / 105 = 284 / 21     = 13.523810 per MWh
    costs = [1000.0, 100.0, 100.0]
    heat_j = [0.0, 50.0 * JOULES_PER_MWH, 50.0 * JOULES_PER_MWH]
    per_joule = levelised_cost_of_heat(costs, heat_j, 0.10)
    assert per_joule * JOULES_PER_MWH == pytest.approx(284.0 / 21.0, rel=1e-12)


def test_constant_case_matches_annualised_cost() -> None:
    # For a constant yearly cost O and constant yearly heat E the discounted definition reduces
    # to the annualised form (I * CRF + O) / E. With I = 1000, O = 50, E = 10 MWh, 4 percent
    # and 20 years: (1000 * 0.07358175 + 50) / 10 = 12.358175 per MWh.
    years = 20
    costs = [1000.0] + [50.0] * years
    heat_j = [0.0] + [10.0 * JOULES_PER_MWH] * years
    discounted = levelised_cost_of_heat(costs, heat_j, 0.04) * JOULES_PER_MWH
    annualised = (1000.0 * capital_recovery_factor(0.04, years) + 50.0) / 10.0
    assert discounted == pytest.approx(annualised, rel=1e-12)
    assert discounted == pytest.approx(12.358175, abs=1e-5)


def test_lcoh_at_zero_discount_rate() -> None:
    # Without discounting the LCOH is total cost over total heat: (1000 + 2 * 100) / 100 MWh.
    costs = [1000.0, 100.0, 100.0]
    heat_j = [0.0, 50.0 * JOULES_PER_MWH, 50.0 * JOULES_PER_MWH]
    assert levelised_cost_of_heat(costs, heat_j, 0.0) * JOULES_PER_MWH == pytest.approx(12.0)


@pytest.mark.requirement("A10")
def test_lcoh_is_undefined_without_heat() -> None:
    # A candidate that delivers no useful heat has no cost per unit of heat. It must never be
    # ranked as free.
    with pytest.raises(UndefinedLcohError):
        levelised_cost_of_heat([1000.0, 100.0], [0.0, 0.0], 0.04)


def test_lcoh_rejects_inconsistent_input() -> None:
    with pytest.raises(ValueError, match="same years"):
        levelised_cost_of_heat([1.0, 2.0], [0.0], 0.04)
    with pytest.raises(ValueError, match="Year 0"):
        levelised_cost_of_heat([1.0, 2.0], [5.0, 5.0], 0.04)
    with pytest.raises(ValueError, match="negative"):
        levelised_cost_of_heat([1.0, 2.0], [0.0, -5.0], 0.04)
