"""Multi-year cash flows, asset lives, LCOH, funded cases and baseline comparison (§13.3-13.4)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from r3chain.domain.base import NonEmptyStr, NonNegativeReal, Real, Record
from r3chain.domain.evidence import InputReader, MissingInput, NotEstablished
from r3chain.domain.records.scenarios import (
    AmountBasis,
    CostCategory,
    EconomicScenario,
    FundingRule,
    PriceConvention,
)
from r3chain.economics.discounting import (
    UndefinedLcohError,
    levelised_cost_of_heat,
    present_value,
)
from r3chain.economics.ledger import CostBoundary, LedgerRow, ReconciliationTable


class AnnualCashFlowEntry(Record):
    year: int
    capex_currency: Real
    replacement_currency: Real
    fixed_opex_currency: Real
    variable_cost_currency: Real
    decommissioning_currency: Real
    residual_value_currency: Real
    grant_currency: Real
    total_cost_currency: Real
    heat_delivered_j: NonNegativeReal
    revenue_currency: Real | None = None


class BoundaryEconomics(Record):
    boundary: CostBoundary
    case_label: Literal["unsubsidised", "funded"]
    currency: NonEmptyStr
    price_base_year: int
    price_convention: PriceConvention
    vat_included: bool
    carbon_monetised: bool
    discount_rate_per_year: Real
    horizon_years: int
    capex_currency: Real
    replacement_pv_currency: Real
    residual_value_pv_currency: Real
    annual_energy_and_opex_currency: Real
    pv_cost_currency: Real
    pv_heat_j: NonNegativeReal
    lcoh_currency_per_j: Real | None
    lcoh_status: Literal["defined", "undefined"]
    lcoh_reason: str = ""
    pv_revenue_currency: Real | None = None
    npv_currency: Real | None = None
    applied_funding_rules: tuple[str, ...] = ()
    yearly: tuple[AnnualCashFlowEntry, ...] = ()
    not_modelled: tuple[str, ...] = (
        "multi-year reservoir thermal decline is not modelled; year-1 operation repeats across "
        "the horizon",
    )


class BaselineComparison(Record):
    baseline_id: NonEmptyStr
    boundary: CostBoundary
    case_label: Literal["unsubsidised", "funded"]
    baseline_pv_cost_currency: Real
    candidate_pv_cost_currency: Real
    incremental_discounted_cost_currency: Real
    baseline_heat_j: NonNegativeReal
    candidate_heat_j: NonNegativeReal
    geothermal_displaced_heat_j: NonNegativeReal
    npv_vs_baseline_currency: Real | None = None


def _applicable_grant_fraction(
    row: LedgerRow,
    rules: Sequence[FundingRule],
    *,
    for_replacement: bool = False,
) -> tuple[float, tuple[str, ...]]:
    """Total bounded grant fraction matching ``row`` across declared funding rules."""
    is_replacement = for_replacement or row.category is CostCategory.REPLACEMENT
    total_fraction = 0.0
    matched: list[str] = []
    for rule in rules:
        if rule.locations and row.location not in rule.locations:
            continue
        cats = rule.categories or (CostCategory.CAPEX, CostCategory.REPLACEMENT)
        if is_replacement:
            if not rule.applies_to_replacements:
                continue
            allowed = CostCategory.REPLACEMENT in cats or (
                for_replacement and CostCategory.CAPEX in cats
            )
            if not allowed:
                continue
        elif row.category not in cats:
            continue
        total_fraction += rule.grant_fraction
        matched.append(f"{rule.rule_id} ({rule.effective_date.isoformat()})")
    return min(total_fraction, 1.0), tuple(matched)


def evaluate_boundary_cashflows(
    economic: EconomicScenario,
    ledger: ReconciliationTable,
    *,
    boundary: CostBoundary,
    annual_heat_j: float,
    annual_electricity_j: float = 0.0,
    annual_revenue_currency: float | None = None,
    residual_value_method: str = "linear_remaining_life",
    funding_rules: Sequence[FundingRule] = (),
    case_label: Literal["unsubsidised", "funded"] = "unsubsidised",
) -> BoundaryEconomics:
    """Compute multi-year discounted cash flows and LCOH on one declared boundary."""
    e_base = f"economic_scenarios[{economic.economic_scenario_id}]"
    reader = InputReader()
    rate = reader.get(economic.discount_rate_per_year, f"{e_base}.discount_rate_per_year")
    horizon_raw = reader.get(economic.horizon_years, f"{e_base}.horizon_years")
    if reader.missing or rate is None or horizon_raw is None:
        raise NotEstablished(reader.missing)
    if annual_heat_j < 0.0:
        raise ValueError("annual_heat_j cannot be negative")
    horizon = round(horizon_raw)
    if horizon <= 0 or abs(horizon - horizon_raw) > 1e-6:
        raise ValueError("horizon_years must be a positive whole number of years for cash flow")

    rows = ledger.rows_for_boundary(boundary)
    capex_y = [0.0] * (horizon + 1)
    repl_y = [0.0] * (horizon + 1)
    fixed_y = [0.0] * (horizon + 1)
    var_y = [0.0] * (horizon + 1)
    decomm_y = [0.0] * (horizon + 1)
    residual_y = [0.0] * (horizon + 1)
    grant_y = [0.0] * (horizon + 1)
    applied_rules: set[str] = set()

    for row in rows:
        grant_frac, names = (
            _applicable_grant_fraction(row, funding_rules) if funding_rules else (0.0, ())
        )
        applied_rules.update(names)

        if row.category is CostCategory.CAPEX and row.amount_basis is AmountBasis.TOTAL:
            gross = row.amount_currency
            grant = gross * grant_frac
            capex_y[0] += gross - grant
            grant_y[0] += grant
            life = row.lifetime_years
            if life is not None:
                if life <= 0.0:
                    raise ValueError(f"asset {row.asset_id!r} lifetime_years must be positive")
                step = round(life)
                if abs(step - life) > 1e-6:
                    raise ValueError(
                        f"asset {row.asset_id!r} lifetime_years ({life}) must be a whole number "
                        "of years for annual cash flows"
                    )
                repl_grant_frac, repl_names = (
                    _applicable_grant_fraction(row, funding_rules, for_replacement=True)
                    if funding_rules
                    else (0.0, ())
                )
                last_install = 0
                year = step
                while year < horizon:
                    r_grant = gross * repl_grant_frac
                    repl_y[year] += gross - r_grant
                    grant_y[year] += r_grant
                    applied_rules.update(repl_names)
                    last_install = year
                    year += step
                if (
                    residual_value_method == "linear_remaining_life"
                    and last_install + step > horizon
                ):
                    remaining_fraction = (last_install + step - horizon) / step
                    end_grant_frac = repl_grant_frac if last_install > 0 else grant_frac
                    residual_y[horizon] -= (gross - gross * end_grant_frac) * remaining_fraction
        elif row.category is CostCategory.REPLACEMENT:
            if row.lifetime_years is None:
                raise NotEstablished(
                    (
                        MissingInput(
                            field_path=f"{e_base}.cost_items[{row.item_id}].lifetime_years",
                            state="unknown",
                            reason=(
                                f"replacement item {row.item_id!r} needs a replacement year "
                                "in lifetime_years"
                            ),
                            owner="economics specialist",
                        ),
                    )
                )
            target_year = round(row.lifetime_years)
            if abs(target_year - row.lifetime_years) > 1e-6 or not (1 <= target_year <= horizon):
                raise ValueError(
                    f"replacement item {row.item_id!r} year ({row.lifetime_years}) must be a "
                    f"whole year in 1..{horizon}"
                )
            grant = row.amount_currency * grant_frac
            repl_y[target_year] += row.amount_currency - grant
            grant_y[target_year] += grant
        elif row.category is CostCategory.DECOMMISSIONING:
            decomm_y[horizon] += row.amount_currency
        elif row.category is CostCategory.RESIDUAL_VALUE:
            if row.amount_currency < 0.0:
                raise ValueError(
                    f"residual_value item {row.item_id!r} cannot be negative; enter positive "
                    "residual value to credit against costs"
                )
            residual_y[horizon] -= row.amount_currency
        elif row.amount_basis is AmountBasis.PER_YEAR:
            target = fixed_y if row.category is CostCategory.FIXED_OPEX else var_y
            for y in range(1, horizon + 1):
                target[y] += row.amount_currency
        elif row.amount_basis is AmountBasis.PER_JOULE_HEAT:
            for y in range(1, horizon + 1):
                var_y[y] += row.amount_currency * annual_heat_j
        elif row.amount_basis is AmountBasis.PER_JOULE_ELECTRICITY:
            for y in range(1, horizon + 1):
                var_y[y] += row.amount_currency * annual_electricity_j
        elif row.amount_basis is AmountBasis.TOTAL:
            capex_y[0] += row.amount_currency

    yearly_entries: list[AnnualCashFlowEntry] = []
    total_costs: list[float] = []
    heat_series: list[float] = [0.0] + [annual_heat_j] * horizon
    revenues: list[float] | None = (
        [0.0] + [annual_revenue_currency] * horizon if annual_revenue_currency is not None else None
    )

    for year in range(horizon + 1):
        tot = (
            capex_y[year]
            + repl_y[year]
            + fixed_y[year]
            + var_y[year]
            + decomm_y[year]
            + residual_y[year]
        )
        total_costs.append(tot)
        yearly_entries.append(
            AnnualCashFlowEntry(
                year=year,
                capex_currency=capex_y[year],
                replacement_currency=repl_y[year],
                fixed_opex_currency=fixed_y[year],
                variable_cost_currency=var_y[year],
                decommissioning_currency=decomm_y[year],
                residual_value_currency=residual_y[year],
                grant_currency=grant_y[year],
                total_cost_currency=tot,
                heat_delivered_j=heat_series[year],
                revenue_currency=revenues[year] if revenues is not None else None,
            )
        )

    pv_cost = present_value(total_costs, rate)
    pv_heat = present_value(heat_series, rate)
    pv_repl = present_value(repl_y, rate)
    pv_res = present_value(residual_y, rate)
    lcoh: float | None
    lcoh_status: Literal["defined", "undefined"]
    lcoh_reason = ""
    try:
        lcoh = levelised_cost_of_heat(total_costs, heat_series, rate)
        lcoh_status = "defined"
    except UndefinedLcohError as error:
        lcoh = None
        lcoh_status = "undefined"
        lcoh_reason = str(error)

    pv_rev = present_value(revenues, rate) if revenues is not None else None
    npv = (pv_rev - pv_cost) if pv_rev is not None else None

    return BoundaryEconomics(
        boundary=boundary,
        case_label=case_label,
        currency=economic.currency,
        price_base_year=economic.price_base_year,
        price_convention=economic.price_convention,
        vat_included=economic.vat_included,
        carbon_monetised=economic.carbon_monetised,
        discount_rate_per_year=rate,
        horizon_years=horizon,
        capex_currency=capex_y[0],
        replacement_pv_currency=pv_repl,
        residual_value_pv_currency=pv_res,
        annual_energy_and_opex_currency=fixed_y[1] + var_y[1] if horizon >= 1 else 0.0,
        pv_cost_currency=pv_cost,
        pv_heat_j=pv_heat,
        lcoh_currency_per_j=lcoh,
        lcoh_status=lcoh_status,
        lcoh_reason=lcoh_reason,
        pv_revenue_currency=pv_rev,
        npv_currency=npv,
        applied_funding_rules=tuple(sorted(applied_rules)),
        yearly=tuple(yearly_entries),
    )


def compare_to_baseline(
    *,
    baseline_id: str,
    baseline: BoundaryEconomics,
    candidate: BoundaryEconomics,
    geothermal_displaced_heat_j: float,
    baseline_annual_heat_j: float,
    candidate_annual_heat_j: float,
) -> BaselineComparison:
    """Compare one alternative against the study baseline on an identical boundary and horizon."""
    if baseline.boundary is not candidate.boundary:
        raise ValueError("baseline and candidate comparison must share one cost boundary")
    if baseline.case_label != candidate.case_label:
        raise ValueError("unsubsidised and funded cases cannot be compared across labels")
    scale = max(abs(baseline_annual_heat_j), abs(candidate_annual_heat_j), 1.0)
    if not math.isclose(
        baseline_annual_heat_j,
        candidate_annual_heat_j,
        rel_tol=1e-6,
        abs_tol=1e-3 * scale,
    ):
        raise ValueError(
            f"baseline_annual_heat_j ({baseline_annual_heat_j:.6g} J) and "
            f"candidate_annual_heat_j ({candidate_annual_heat_j:.6g} J) must match for "
            "baseline cost comparison"
        )
    npv_delta = (
        candidate.npv_currency - baseline.npv_currency
        if candidate.npv_currency is not None and baseline.npv_currency is not None
        else None
    )
    return BaselineComparison(
        baseline_id=baseline_id,
        boundary=candidate.boundary,
        case_label=candidate.case_label,
        baseline_pv_cost_currency=baseline.pv_cost_currency,
        candidate_pv_cost_currency=candidate.pv_cost_currency,
        incremental_discounted_cost_currency=(
            candidate.pv_cost_currency - baseline.pv_cost_currency
        ),
        baseline_heat_j=baseline_annual_heat_j,
        candidate_heat_j=candidate_annual_heat_j,
        geothermal_displaced_heat_j=geothermal_displaced_heat_j,
        npv_vs_baseline_currency=npv_delta,
    )
