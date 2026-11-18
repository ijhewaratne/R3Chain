"""KPI registry and route/demand comparison helpers (extension point X10)."""

from __future__ import annotations

from r3chain.domain.base import NonEmptyStr, NonNegativeReal, Real, Record
from r3chain.domain.records.runs import KpiDefinition, KpiValue
from r3chain.economics.cashflow import BaselineComparison, BoundaryEconomics
from r3chain.ports.dispatch import DispatchTotals

KPI_DEFINITIONS: dict[str, KpiDefinition] = {}


def register_kpi(definition: KpiDefinition) -> KpiDefinition:
    """Register one KPI definition with explicit unit and boundary."""
    KPI_DEFINITIONS[definition.kpi_id] = definition
    return definition


def _seed_kpis() -> None:
    specs = (
        (
            "annual_geothermal_delivered_heat_j",
            "Annual geothermal delivered heat",
            "J",
            "geothermal_delivered",
            "Annual useful geothermal heat delivered to the connection after route losses",
        ),
        (
            "annual_geothermal_plant_heat_j",
            "Annual geothermal plant handover heat",
            "J",
            "geothermal_plant",
            "Annual geothermal heat leaving the plant into the transmission route",
        ),
        (
            "annual_geothermal_curtailed_heat_j",
            "Annual curtailed geothermal heat",
            "J",
            "geothermal_delivered",
            "Available geothermal heat not dispatched because of base load or low demand",
        ),
        (
            "annual_base_load_heat_j",
            "Annual existing base load heat",
            "J",
            "system_delivered",
            "Annual useful heat delivered by existing base load units",
        ),
        (
            "annual_backup_heat_j",
            "Annual backup heat",
            "J",
            "system_delivered",
            "Annual heat delivered by backup and peak boilers",
        ),
        (
            "annual_unserved_heat_j",
            "Annual unserved demand",
            "J",
            "system_delivered",
            "Annual heat demand not met by any modelled source",
        ),
        (
            "annual_geothermal_electricity_j",
            "Annual geothermal electricity",
            "J",
            "geothermal_plant",
            "Well pump and heat pump compressor electricity consumed over the year",
        ),
        (
            "equivalent_full_load_s",
            "Geothermal equivalent full load time",
            "s",
            "geothermal_delivered",
            "Integrated useful geothermal heat divided by reference delivered capacity",
        ),
        (
            "geothermal_demand_share_fraction",
            "Geothermal share of delivered demand",
            "1",
            "system_delivered",
            "Useful geothermal delivered heat as a fraction of annual heat demand",
        ),
        (
            "lcoh_plant_currency_per_j",
            "Levelised cost of heat (plant boundary)",
            "currency/J",
            "geothermal_plant",
            "Discounted plant-boundary cost divided by discounted plant handover heat",
        ),
        (
            "lcoh_geothermal_delivered_currency_per_j",
            "Levelised cost of heat (geothermal delivered boundary)",
            "currency/J",
            "geothermal_delivered",
            "Discounted geothermal and route cost divided by discounted geothermal delivered heat",
        ),
        (
            "lcoh_system_delivered_currency_per_j",
            "Levelised cost of heat (system delivered boundary)",
            "currency/J",
            "system_delivered",
            "Discounted whole-system alternative cost divided by discounted system delivered heat",
        ),
        (
            "total_capex_currency",
            "Total geothermal and route capital expenditure",
            "currency",
            "geothermal_delivered",
            "Gross or unsubsidised upfront CAPEX across included source, plant and route assets",
        ),
        (
            "pv_total_cost_currency",
            "Discounted total cost (system delivered boundary)",
            "currency",
            "system_delivered",
            "Present value of CAPEX, replacements, OPEX and energy costs net of residual value",
        ),
        (
            "incremental_discounted_cost_currency",
            "Incremental discounted cost versus baseline",
            "currency",
            "system_delivered",
            "Difference in discounted system cost relative to the declared baseline",
        ),
        (
            "npv_currency",
            "Net present value (system delivered boundary)",
            "currency",
            "system_delivered",
            "Discounted heat sales revenue minus discounted system cost",
        ),
        (
            "lcoh_funded_geothermal_delivered_currency_per_j",
            "Levelised cost of heat (funded geothermal delivered)",
            "currency/J",
            "geothermal_delivered",
            "Discounted geothermal cost net of declared grants over geothermal delivered heat",
        ),
        (
            "incremental_funded_cost_currency",
            "Incremental funded cost versus baseline",
            "currency",
            "system_delivered",
            "Difference in funded discounted system cost relative to the baseline",
        ),
        (
            "conditional_service_probability_fraction",
            "Conditional service probability",
            "1",
            "system_delivered",
            "Share of evaluated joint samples meeting service conditions (unresolved excluded)",
        ),
        (
            "expected_lcoh_pre_drilling_currency_per_j",
            "Pre-drilling expected LCOH",
            "currency/J",
            "system_delivered",
            "Expected discounted system cost over expected delivered heat including drilling "
            "failure and fallback",
        ),
        (
            "expected_pv_cost_pre_drilling_currency",
            "Pre-drilling expected discounted cost",
            "currency",
            "system_delivered",
            "Probability-weighted discounted system cost including drilling failure, "
            "abandonment, and fallback",
        ),
        (
            "max_regret_currency",
            "Maximum regret across paired samples",
            "currency",
            "system_delivered",
            "Worst-case cost excess relative to the best feasible option in each paired sample",
        ),
        (
            "first_rank_share_fraction",
            "First-rank stability share",
            "1",
            "system_delivered",
            "Fraction of paired samples in which the candidate ranks first within tie tolerance",
        ),
        *(
            row
            for case_id, title in (
                ("source_outage", "loss of geothermal source"),
                ("backup_loss", "loss of backup boiler"),
                ("base_load_loss", "loss of existing base load"),
                ("heat_pump_electricity_loss", "loss of heat pump electricity supply"),
            )
            for row in (
                (
                    f"resilience_{case_id}_unserved_heat_j",
                    f"Resilience unserved heat ({title})",
                    "J",
                    "system_delivered",
                    f"Annual unserved heat demand during deterministic {title} scenario",
                ),
                (
                    f"resilience_{case_id}_unserved_hours",
                    f"Resilience unserved hours ({title})",
                    "h",
                    "system_delivered",
                    f"Hours with unserved heat during deterministic {title} scenario",
                ),
                (
                    f"resilience_{case_id}_largest_deficit_w",
                    f"Resilience largest deficit ({title})",
                    "W",
                    "system_delivered",
                    f"Peak unserved power deficit during deterministic {title} scenario",
                ),
            )
        ),
    )
    for kpi_id, name, unit, boundary, description in specs:
        register_kpi(
            KpiDefinition(
                kpi_id=kpi_id,
                name=name,
                unit=unit,
                boundary=boundary,
                description=description,
            )
        )


_seed_kpis()


def make_kpi(kpi_id: str, value: float | None, derived_from: tuple[str, ...]) -> KpiValue:
    if kpi_id not in KPI_DEFINITIONS:
        raise KeyError(f"KPI {kpi_id!r} is not registered")
    return KpiValue(
        definition=KPI_DEFINITIONS[kpi_id],
        value=value,
        derived_from=derived_from,
    )


def operation_kpis(totals: DispatchTotals, derived_from: tuple[str, ...]) -> tuple[KpiValue, ...]:
    share = (
        totals.geothermal_delivered_heat_j / totals.demand_heat_j
        if totals.demand_heat_j > 0.0
        else 0.0
    )
    return (
        make_kpi(
            "annual_geothermal_delivered_heat_j", totals.geothermal_delivered_heat_j, derived_from
        ),
        make_kpi(
            "annual_geothermal_plant_heat_j", totals.geothermal_plant_handover_heat_j, derived_from
        ),
        make_kpi(
            "annual_geothermal_curtailed_heat_j", totals.geothermal_curtailed_heat_j, derived_from
        ),
        make_kpi("annual_base_load_heat_j", totals.base_load_heat_j, derived_from),
        make_kpi("annual_backup_heat_j", totals.backup_heat_j, derived_from),
        make_kpi("annual_unserved_heat_j", totals.unserved_heat_j, derived_from),
        make_kpi(
            "annual_geothermal_electricity_j",
            totals.source_pump_electricity_j + totals.compressor_electricity_j,
            derived_from,
        ),
        make_kpi("equivalent_full_load_s", totals.equivalent_full_load_s, derived_from),
        make_kpi("geothermal_demand_share_fraction", share, derived_from),
    )


def economic_kpis(
    *,
    plant: BoundaryEconomics,
    geothermal_delivered: BoundaryEconomics,
    system_delivered: BoundaryEconomics,
    comparison: BaselineComparison | None,
    funded_geothermal: BoundaryEconomics | None,
    funded_comparison: BaselineComparison | None,
    derived_from: tuple[str, ...],
) -> tuple[KpiValue, ...]:
    kpis: list[KpiValue] = [
        make_kpi("total_capex_currency", geothermal_delivered.capex_currency, derived_from),
        make_kpi("pv_total_cost_currency", system_delivered.pv_cost_currency, derived_from),
        make_kpi("lcoh_plant_currency_per_j", plant.lcoh_currency_per_j, derived_from),
        make_kpi(
            "lcoh_geothermal_delivered_currency_per_j",
            geothermal_delivered.lcoh_currency_per_j,
            derived_from,
        ),
        make_kpi(
            "lcoh_system_delivered_currency_per_j",
            system_delivered.lcoh_currency_per_j,
            derived_from,
        ),
    ]
    if comparison is not None:
        kpis.append(
            make_kpi(
                "incremental_discounted_cost_currency",
                comparison.incremental_discounted_cost_currency,
                derived_from,
            )
        )
    if system_delivered.npv_currency is not None:
        kpis.append(make_kpi("npv_currency", system_delivered.npv_currency, derived_from))
    if funded_geothermal is not None:
        kpis.append(
            make_kpi(
                "lcoh_funded_geothermal_delivered_currency_per_j",
                funded_geothermal.lcoh_currency_per_j,
                derived_from,
            )
        )
    if funded_comparison is not None:
        kpis.append(
            make_kpi(
                "incremental_funded_cost_currency",
                funded_comparison.incremental_discounted_cost_currency,
                derived_from,
            )
        )
    return tuple(kpis)


class RouteDemandOptionResult(Record):
    """Comparison of one route with and without linked additional customers (W33)."""

    candidate_id: NonEmptyStr
    route_id: NonEmptyStr
    linked_customer_count: int
    additional_peak_heat_w: NonNegativeReal
    base_geothermal_delivered_j: NonNegativeReal
    with_demand_geothermal_delivered_j: NonNegativeReal
    base_lcoh_geothermal_delivered_currency_per_j: Real | None
    with_demand_lcoh_geothermal_delivered_currency_per_j: Real | None
    base_pv_cost_currency: Real
    with_demand_pv_cost_currency: Real
    base_npv_currency: Real | None = None
    with_demand_npv_currency: Real | None = None
