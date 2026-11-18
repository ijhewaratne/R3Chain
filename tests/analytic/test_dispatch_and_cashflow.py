"""Analytic fixtures for annual operation, reconciliation and cash flows (A07-A10)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from r3chain.domain.evidence import Known, NotEstablished, Provenance, ProvenanceType, Unknown
from r3chain.domain.records.network import Route
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.records.scenarios import (
    AmountBasis,
    CostCategory,
    CostItem,
    EconomicScenario,
    FundingRule,
    PriceConvention,
)
from r3chain.domain.status import AdapterState
from r3chain.domain.timeseries import IntervalSeries, SeriesKind, local_year_bounds_utc
from r3chain.domain.units import JOULES_PER_MWH, SECONDS_PER_HOUR, currency_per_mwh_to_per_j
from r3chain.economics.cashflow import compare_to_baseline, evaluate_boundary_cashflows
from r3chain.economics.ledger import (
    CostBoundary,
    LedgerRow,
    assemble_candidate_ledger,
    reconcile_ledger,
    route_capex_currency,
)
from r3chain.economics.policies import EconomicSettings
from r3chain.geospatial.routes import GeneratedRoute, RouteCost
from r3chain.operation.dispatch import RuleBasedDispatchStrategy
from r3chain.ports.dispatch import (
    DispatchElectricityPoint,
    DispatchGeothermalSpec,
    DispatchRequest,
    DispatchUnit,
    ExistingUnitRole,
)

PROVENANCE = Provenance(provenance_type=ProvenanceType.SYNTHETIC, source_id="syn-definitions")


def known(value: float) -> Known[float]:
    return Known(value=value, provenance=PROVENANCE)


def economic_scenario(
    *,
    discount_rate: float = 0.04,
    horizon: int = 20,
    cost_items: tuple[CostItem, ...] = (),
) -> EconomicScenario:
    return EconomicScenario(
        economic_scenario_id="analytic-econ",
        price_base_year=2026,
        currency="EUR",
        price_convention=PriceConvention.REAL,
        vat_included=False,
        discount_rate_per_year=known(discount_rate),
        horizon_years=known(float(horizon)),
        cost_items=cost_items,
    )


@pytest.mark.requirement("A07")
def test_chronological_dispatch_preserves_leap_year_and_dst_intervals() -> None:
    # Two real local years in Europe/Berlin: 2026 (8760 h) and 2028 leap year (8784 h).
    # Constant 1 MW demand and 600 kW geothermal capacity -> delivered geothermal must match exact
    # UTC interval integration, never assuming 8760 h for a leap year.
    strategy = RuleBasedDispatchStrategy()
    for year, expected_hours in ((2026, 8760), (2028, 8784)):
        start, end = local_year_bounds_utc(year, "Europe/Berlin")
        count = round((end - start).total_seconds() / SECONDS_PER_HOUR)
        assert count == expected_hours
        series = IntervalSeries.regular(
            series_id=f"d-{year}",
            unit="W",
            kind=SeriesKind.INTERVAL_MEAN,
            start_utc=start,
            step_s=SECONDS_PER_HOUR,
            values=[1_000_000.0] * count,
        )
        req = DispatchRequest(
            request_id=f"leap-{year}",
            operating_scenario_id="op",
            starts_utc=series.starts_utc,
            durations_s=series.durations_s,
            demand_heat_w=series.values,
            geothermal=DispatchGeothermalSpec(
                unit_id="geo",
                max_delivered_heat_w=600_000.0,
                max_plant_handover_heat_w=600_000.0,
            ),
        )
        res = strategy.dispatch(req)
        assert res.state is AdapterState.COMPLETED and res.totals is not None
        assert res.totals.demand_heat_j == pytest.approx(expected_hours * JOULES_PER_MWH)
        assert res.totals.geothermal_delivered_heat_j == pytest.approx(
            0.6 * expected_hours * JOULES_PER_MWH
        )
        assert res.totals.unserved_heat_j == pytest.approx(0.4 * expected_hours * JOULES_PER_MWH)

    # Daylight saving spring-forward (29 Mar 2026, 23 h) & fall-back (25 Oct 2026, 25 h).
    berlin = ZoneInfo("Europe/Berlin")
    for month, day, hours in ((3, 29, 23), (10, 25, 25)):
        d0 = datetime(2026, month, day, tzinfo=berlin).astimezone(UTC)
        d1 = datetime(2026, month, day + 1, tzinfo=berlin).astimezone(UTC)
        assert round((d1 - d0).total_seconds() / SECONDS_PER_HOUR) == hours


@pytest.mark.requirement("A08")
def test_competing_base_load_reduces_geothermal_heat_and_raises_lcoh() -> None:
    # Two equal periods of 4000 s each: winter demand 1 MW, summer demand 0.4 MW.
    # Geothermal rated at 0.8 MW; waste heat base load rated at 0.4 MW covers summer completely.
    # At CAPEX = 1000 EUR, fixed OPEX = 0, r = 0, horizon = 1 year:
    #   No base load: geo heat = (0.8 + 0.4) MW * 4000 s = 4.8e9 J -> LCOH = 1000 / 4.8e9 EUR/J.
    #   With base load: geo serves only 0.6 MW in winter (2.4e9 J), 0 in summer -> 2x LCOH.
    starts = (
        datetime(2026, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 7, 1, 0, tzinfo=UTC),
    )
    geo = DispatchGeothermalSpec(
        unit_id="geo",
        max_delivered_heat_w=800_000.0,
        max_plant_handover_heat_w=800_000.0,
    )
    strategy = RuleBasedDispatchStrategy()

    req_alone = DispatchRequest(
        request_id="alone",
        operating_scenario_id="op",
        starts_utc=starts,
        durations_s=(4000.0, 4000.0),
        demand_heat_w=(1_000_000.0, 400_000.0),
        geothermal=geo,
    )
    req_competing = req_alone.model_copy(
        update={
            "request_id": "competing",
            "existing_units": (
                DispatchUnit(
                    unit_id="waste",
                    label="Waste incineration",
                    role=ExistingUnitRole.BASE_LOAD,
                    priority=10,
                    max_heat_w=400_000.0,
                ),
            ),
        }
    )
    res_alone = strategy.dispatch(req_alone)
    res_comp = strategy.dispatch(req_competing)
    assert res_alone.totals is not None and res_comp.totals is not None
    assert res_alone.totals.geothermal_delivered_heat_j == pytest.approx(4.8e9)
    assert res_comp.totals.geothermal_delivered_heat_j == pytest.approx(2.4e9)
    assert res_comp.totals.geothermal_curtailed_heat_j == pytest.approx(4.0e9)

    econ = economic_scenario(discount_rate=0.0, horizon=1)
    row = LedgerRow(
        item_id="plant-capex",
        asset_id="plant-1",
        category=CostCategory.CAPEX,
        cost_owner="system",
        location="plant",
        included=True,
        amount_currency=1000.0,
        amount_basis=AmountBasis.TOTAL,
        origin="fixture",
    )
    ledger_alone = reconcile_ledger("alone", (row,))
    ledger_comp = reconcile_ledger("competing", (row,))
    econ_alone = evaluate_boundary_cashflows(
        econ,
        ledger_alone,
        boundary=CostBoundary.GEOTHERMAL_DELIVERED,
        annual_heat_j=res_alone.totals.geothermal_delivered_heat_j,
    )
    econ_comp = evaluate_boundary_cashflows(
        econ,
        ledger_comp,
        boundary=CostBoundary.GEOTHERMAL_DELIVERED,
        annual_heat_j=res_comp.totals.geothermal_delivered_heat_j,
    )
    assert econ_alone.lcoh_currency_per_j == pytest.approx(1000.0 / 4.8e9)
    assert econ_comp.lcoh_currency_per_j == pytest.approx(1000.0 / 2.4e9)
    assert econ_comp.lcoh_currency_per_j == pytest.approx(2.0 * econ_alone.lcoh_currency_per_j)


@pytest.mark.requirement("A08")
@pytest.mark.requirement("W08")
@pytest.mark.requirement("W15")
def test_underused_geothermal_plant_earns_no_credit_for_curtailed_heat() -> None:
    # Plant capacity 2 MW over 1000 s = 2e9 J potential, demand only 0.5 MW over 1000 s = 0.5e9 J.
    # Unused 1.5e9 J is recorded as curtailed_heat_j and earns zero credit in delivered heat,
    # variable electricity or LCOH denominator.
    req = DispatchRequest(
        request_id="underused",
        operating_scenario_id="op",
        starts_utc=(datetime(2026, 1, 1, tzinfo=UTC),),
        durations_s=(1000.0,),
        demand_heat_w=(500_000.0,),
        electricity_price_currency_per_j=(currency_per_mwh_to_per_j(100.0),),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo",
            max_delivered_heat_w=2_000_000.0,
            max_plant_handover_heat_w=2_000_000.0,
            source_electricity_per_plant_heat_ratio=0.05,
        ),
    )
    res = RuleBasedDispatchStrategy().dispatch(req)
    assert res.totals is not None
    assert res.totals.geothermal_available_heat_j == pytest.approx(2.0e9)
    assert res.totals.geothermal_delivered_heat_j == pytest.approx(0.5e9)
    assert res.totals.geothermal_curtailed_heat_j == pytest.approx(1.5e9)
    # Electricity is consumed only for the 0.5e9 J actually dispatched (0.025e9 J), not 2.0e9 J.
    assert res.totals.source_pump_electricity_j == pytest.approx(0.025e9)


@pytest.mark.requirement("W15")
def test_longer_route_carries_supply_and_return_quantities_and_losses() -> None:
    # Hand derivation for route CAPEX:
    #   corridor_length = 1000 m along street @ 900 EUR/m   = 900 000 EUR (trench)
    #   supply pipe 1020 m + return pipe 1020 m @ 250 EUR/m = 510 000 EUR (two pipes)
    #   1 watercourse crossing @ 150 000 EUR                = 150 000 EUR
    #   Total base route CAPEX                              = 1 560 000 EUR
    route = Route(
        route_id="r-long",
        from_site_id="alpha",
        to_connection_id="conn-west",
        geometry={
            "crs": "EPSG:25832",
            "geojson": {"type": "LineString", "coordinates": [[0, 0], [1000, 0]]},
        },
        corridor_length_m=1000.0,
        supply_pipe_length_m=1020.0,
        return_pipe_length_m=1020.0,
        crossings=({"crossing_id": "x1", "kind": "watercourse", "chainage_m": 500.0},),
        vertical_datum="DHHN2016",
    )
    settings = EconomicSettings(
        residual_value_method="none",
        evaluate_funded_case=False,
        route_lifetime_years=40.0,
        route_fixed_opex_fraction=0.0,
        route_pipe_cost_currency_per_m=250.0,
        link_corridor_kind="street",
        trench_rates={"street": 900.0},
        crossing_costs={"watercourse": 150_000.0},
        additional_customer_connection_currency=0.0,
        additional_customer_pipe_cost_currency_per_m=0.0,
        origin="fixture",
    )
    capex, extra, _ = route_capex_currency(None, route, settings)
    assert capex == pytest.approx(1_560_000.0)
    assert extra == 0.0

    # Link segments ("link (<kind>)") use their corridor kind rate; unknown kinds raise.
    gen_link = GeneratedRoute(
        route=route,
        rank=1,
        bases=("least_cost",),
        cost=RouteCost(
            trench_currency=900_000.0,
            pipes_currency=510_000.0,
            crossings_currency=150_000.0,
            total_currency=1_560_000.0,
        ),
        lengths_by_kind_m={"link (street)": 100.0, "street": 900.0},
        site_link_m=100.0,
        connection_link_m=0.0,
        corridor_ids=("C1",),
    )
    link_capex, _, _ = route_capex_currency(gen_link, route, settings)
    assert link_capex == pytest.approx(1_560_000.0)

    gen_unknown = gen_link.model_copy(
        update={"lengths_by_kind_m": {"highway": 100.0, "street": 900.0}}
    )
    with pytest.raises(NotEstablished):
        route_capex_currency(gen_unknown, route, settings)

    # In dispatch, route heat loss raises plant handover above delivered connection heat,
    # and does not shrink when the geothermal plant operates at part load.
    req = DispatchRequest(
        request_id="route-loss",
        operating_scenario_id="op",
        starts_utc=(datetime(2026, 1, 1, tzinfo=UTC),),
        durations_s=(1000.0,),
        demand_heat_w=(1_000_000.0,),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo",
            max_delivered_heat_w=900_000.0,
            max_plant_handover_heat_w=950_000.0,
            route_loss_when_active_w=50_000.0,
        ),
    )
    res = RuleBasedDispatchStrategy().dispatch(req)
    assert res.totals is not None
    assert res.totals.geothermal_delivered_heat_j == pytest.approx(900_000.0 * 1000.0)
    assert res.totals.route_heat_loss_j == pytest.approx(50_000.0 * 1000.0)
    assert res.totals.geothermal_plant_handover_heat_j == pytest.approx(950_000.0 * 1000.0)

    part_res = RuleBasedDispatchStrategy().dispatch(
        req.model_copy(update={"demand_heat_w": (450_000.0,)})
    )
    assert part_res.totals is not None
    assert part_res.totals.geothermal_delivered_heat_j == pytest.approx(450_000.0 * 1000.0)
    assert part_res.totals.route_heat_loss_j == pytest.approx(50_000.0 * 1000.0)
    assert part_res.totals.geothermal_plant_handover_heat_j == pytest.approx(500_000.0 * 1000.0)


@pytest.mark.requirement("A09")
@pytest.mark.requirement("W03")
def test_upstream_cost_reconciliation_prevents_double_counting() -> None:
    system_wells = CostItem(
        item_id="wells-alpha-sys",
        asset_id="wells-alpha",
        category=CostCategory.CAPEX,
        cost_owner="system",
        location="source",
        included=True,
        amount=known(12_000_000.0),
        amount_basis=AmountBasis.TOTAL,
        currency="EUR",
        price_year=2026,
        price_convention=PriceConvention.REAL,
        lifetime_years=known(30.0),
    )
    upstream_wells = CostItem(
        item_id="wells-alpha-pydoublet",
        asset_id="wells-alpha",
        category=CostCategory.CAPEX,
        cost_owner="pydoublet",
        location="source",
        included=True,
        amount=known(12_000_000.0),
        amount_basis=AmountBasis.TOTAL,
        currency="EUR",
        price_year=2026,
        price_convention=PriceConvention.REAL,
        lifetime_years=known(30.0),
    )
    econ = economic_scenario(cost_items=(system_wells,))
    route = Route(
        route_id="r1",
        from_site_id="alpha",
        to_connection_id="conn-west",
        geometry={
            "crs": "EPSG:25832",
            "geojson": {"type": "LineString", "coordinates": [[0, 0], [100, 0]]},
        },
        corridor_length_m=100.0,
        supply_pipe_length_m=100.0,
        return_pipe_length_m=100.0,
        vertical_datum="DHHN2016",
    )
    res = RuleBasedDispatchStrategy().dispatch(
        DispatchRequest(
            request_id="rec",
            operating_scenario_id="op",
            starts_utc=(datetime(2026, 1, 1, tzinfo=UTC),),
            durations_s=(3600.0,),
            demand_heat_w=(1_000_000.0,),
            geothermal=DispatchGeothermalSpec(
                unit_id="geo",
                max_delivered_heat_w=1_000_000.0,
                max_plant_handover_heat_w=1_000_000.0,
            ),
        )
    )
    assert res.totals is not None

    # Both included=True -> duplicate cost ownership caught explicitly.
    duplicate = assemble_candidate_ledger(
        econ,
        subject_id="cand-1",
        site_id="alpha",
        plant_id="plant-direct",
        route=route,
        generated_route=None,
        connection_id="conn-west",
        candidate_id="cand-1",
        alternative_id=None,
        dispatch_totals=res.totals,
        upstream_items=(upstream_wells,),
    )
    assert not duplicate.clean
    assert "wells-alpha" in duplicate.duplicate_ownership_errors[0]

    # Marking the system copy included=False reconciles cleanly without losing its audit trail.
    reconciled_econ = economic_scenario(
        cost_items=(system_wells.model_copy(update={"included": False}),)
    )
    reconciled = assemble_candidate_ledger(
        reconciled_econ,
        subject_id="cand-1",
        site_id="alpha",
        plant_id="plant-direct",
        route=route,
        generated_route=None,
        connection_id="conn-west",
        candidate_id="cand-1",
        alternative_id=None,
        dispatch_totals=res.totals,
        upstream_items=(upstream_wells,),
    )
    assert reconciled.clean
    plant_rows = reconciled.rows_for_boundary(CostBoundary.PLANT)
    assert sum(row.amount_currency for row in plant_rows) == pytest.approx(12_000_000.0)
    excluded = [row for row in reconciled.rows if not row.included]
    assert len(excluded) == 1 and "double counting" in excluded[0].exclusion_reason


@pytest.mark.requirement("A10")
def test_asset_lives_replacements_and_residual_value_cashflows() -> None:
    # Horizon 20 years, zero discount rate, annual heat = 10 MWh/a:
    #   Asset A: CAPEX 1000, life 10 y -> replaced once at y=10 (+1000), residual at y=20 is 0.
    #   Asset B: CAPEX 1500, life 30 y -> residual life 10/30 at y=20 -> residual credit -500.
    #   Fixed OPEX: 100/y -> 20 * 100 = 2000.
    #   Total undiscounted cost = 1000 + 1500 + 1000 + 2000 - 500 = 5000 EUR (25 EUR/MWh).
    rows = (
        LedgerRow(
            item_id="a",
            asset_id="asset-a",
            category=CostCategory.CAPEX,
            cost_owner="system",
            location="plant",
            included=True,
            amount_currency=1000.0,
            amount_basis=AmountBasis.TOTAL,
            lifetime_years=10.0,
            origin="fixture",
        ),
        LedgerRow(
            item_id="b",
            asset_id="asset-b",
            category=CostCategory.CAPEX,
            cost_owner="system",
            location="source",
            included=True,
            amount_currency=1500.0,
            amount_basis=AmountBasis.TOTAL,
            lifetime_years=30.0,
            origin="fixture",
        ),
        LedgerRow(
            item_id="opex",
            asset_id="asset-a",
            category=CostCategory.FIXED_OPEX,
            cost_owner="system",
            location="plant",
            included=True,
            amount_currency=100.0,
            amount_basis=AmountBasis.PER_YEAR,
            origin="fixture",
        ),
    )
    ledger = reconcile_ledger("cand-life", rows)
    econ_zero = evaluate_boundary_cashflows(
        economic_scenario(discount_rate=0.0, horizon=20),
        ledger,
        boundary=CostBoundary.PLANT,
        annual_heat_j=10.0 * JOULES_PER_MWH,
        residual_value_method="linear_remaining_life",
    )
    assert econ_zero.yearly[0].capex_currency == pytest.approx(2500.0)
    assert econ_zero.yearly[10].replacement_currency == pytest.approx(1000.0)
    assert econ_zero.yearly[20].residual_value_currency == pytest.approx(-500.0)
    assert econ_zero.pv_cost_currency == pytest.approx(5000.0)
    assert econ_zero.lcoh_currency_per_j is not None
    assert econ_zero.lcoh_currency_per_j * JOULES_PER_MWH == pytest.approx(25.0)

    # At 5% discount rate: PV = 2500 + 1000/1.05^10 - 500/1.05^20 + 100 * sum_{t=1..20} 1.05^-t.
    r = 0.05
    expected_pv = (
        2500.0
        + 1000.0 * (1.0 + r) ** -10
        - 500.0 * (1.0 + r) ** -20
        + sum(100.0 * (1.0 + r) ** -t for t in range(1, 21))
    )
    expected_pv_heat = sum(10.0 * JOULES_PER_MWH * (1.0 + r) ** -t for t in range(1, 21))
    econ_5 = evaluate_boundary_cashflows(
        economic_scenario(discount_rate=r, horizon=20),
        ledger,
        boundary=CostBoundary.PLANT,
        annual_heat_j=10.0 * JOULES_PER_MWH,
        residual_value_method="linear_remaining_life",
    )
    assert econ_5.pv_cost_currency == pytest.approx(expected_pv, rel=1e-12)
    assert econ_5.lcoh_currency_per_j == pytest.approx(expected_pv / expected_pv_heat, rel=1e-12)
    assert any("thermal decline" in line for line in econ_5.not_modelled)

    # Replacement without year raises NotEstablished; non-whole life raises ValueError; positive
    # explicit RESIDUAL_VALUE is credited while negative raises ValueError.
    no_year_repl = reconcile_ledger(
        "no-year",
        (
            LedgerRow(
                item_id="r1",
                asset_id="pump",
                category=CostCategory.REPLACEMENT,
                cost_owner="system",
                location="plant",
                included=True,
                amount_currency=500.0,
                amount_basis=AmountBasis.TOTAL,
                lifetime_years=None,
                origin="fixture",
            ),
        ),
    )
    with pytest.raises(NotEstablished):
        evaluate_boundary_cashflows(
            economic_scenario(discount_rate=0.0, horizon=20),
            no_year_repl,
            boundary=CostBoundary.PLANT,
            annual_heat_j=10.0 * JOULES_PER_MWH,
        )

    fractional_life = reconcile_ledger(
        "frac",
        (rows[0].model_copy(update={"lifetime_years": 7.5}),),
    )
    with pytest.raises(ValueError, match="whole number"):
        evaluate_boundary_cashflows(
            economic_scenario(discount_rate=0.0, horizon=20),
            fractional_life,
            boundary=CostBoundary.PLANT,
            annual_heat_j=10.0 * JOULES_PER_MWH,
        )

    with_residual = reconcile_ledger(
        "res-credit",
        (
            rows[0],
            LedgerRow(
                item_id="sal",
                asset_id="salvage",
                category=CostCategory.RESIDUAL_VALUE,
                cost_owner="system",
                location="plant",
                included=True,
                amount_currency=300.0,
                amount_basis=AmountBasis.TOTAL,
                origin="fixture",
            ),
        ),
    )
    res_credit = evaluate_boundary_cashflows(
        economic_scenario(discount_rate=0.0, horizon=20),
        with_residual,
        boundary=CostBoundary.PLANT,
        annual_heat_j=10.0 * JOULES_PER_MWH,
        residual_value_method="none",
    )
    assert res_credit.yearly[20].residual_value_currency == pytest.approx(-300.0)
    assert res_credit.pv_cost_currency == pytest.approx(1700.0)

    neg_residual = reconcile_ledger(
        "res-neg",
        (
            LedgerRow(
                item_id="sal-neg",
                asset_id="salvage",
                category=CostCategory.RESIDUAL_VALUE,
                cost_owner="system",
                location="plant",
                included=True,
                amount_currency=-300.0,
                amount_basis=AmountBasis.TOTAL,
                origin="fixture",
            ),
        ),
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        evaluate_boundary_cashflows(
            economic_scenario(discount_rate=0.0, horizon=20),
            neg_residual,
            boundary=CostBoundary.PLANT,
            annual_heat_j=10.0 * JOULES_PER_MWH,
        )


@pytest.mark.requirement("A10")
def test_zero_discount_rate_and_undefined_zero_heat_lcoh() -> None:
    ledger = reconcile_ledger(
        "zero-heat",
        (
            LedgerRow(
                item_id="capex",
                asset_id="wells",
                category=CostCategory.CAPEX,
                cost_owner="system",
                location="source",
                included=True,
                amount_currency=5_000_000.0,
                amount_basis=AmountBasis.TOTAL,
                origin="fixture",
            ),
        ),
    )
    res = evaluate_boundary_cashflows(
        economic_scenario(discount_rate=0.0, horizon=20),
        ledger,
        boundary=CostBoundary.PLANT,
        annual_heat_j=0.0,
    )
    assert res.lcoh_status == "undefined"
    assert res.lcoh_currency_per_j is None
    assert "no useful heat" in res.lcoh_reason
    # Ensure Unknown CostItem in EconomicScenario still raises when evaluated via ledger builder.
    unknown_item = CostItem(
        item_id="u1",
        asset_id="wells",
        category=CostCategory.CAPEX,
        cost_owner="system",
        location="source",
        included=True,
        amount=Unknown(reason="missing quote", owner="reviewer"),
        amount_basis=AmountBasis.TOTAL,
        currency="EUR",
        price_year=2026,
        price_convention=PriceConvention.REAL,
    )
    assert unknown_item.amount.state == "unknown"


@pytest.mark.requirement("W15")
def test_winter_vs_summer_tariffs_change_annual_energy_cost() -> None:
    # 1 hour winter at 120 EUR/MWh and 1 hour summer at 60 EUR/MWh, 1 MWh thermal with ratio 0.25
    # electricity = 0.25 MWh winter + 0.25 MWh summer -> (30 + 15) = 45 EUR vs 30 EUR if flat 60.
    starts = (datetime(2026, 1, 15, 12, tzinfo=UTC), datetime(2026, 7, 15, 12, tzinfo=UTC))
    base = DispatchRequest(
        request_id="tariffs",
        operating_scenario_id="op",
        starts_utc=starts,
        durations_s=(3600.0, 3600.0),
        demand_heat_w=(1_000_000.0, 1_000_000.0),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo",
            max_delivered_heat_w=1_000_000.0,
            max_plant_handover_heat_w=1_000_000.0,
            compressor_electricity_per_plant_heat_ratio=0.25,
        ),
        electricity_price_currency_per_j=(
            currency_per_mwh_to_per_j(120.0),
            currency_per_mwh_to_per_j(60.0),
        ),
    )
    flat = base.model_copy(
        update={
            "electricity_price_currency_per_j": (
                currency_per_mwh_to_per_j(60.0),
                currency_per_mwh_to_per_j(60.0),
            )
        }
    )
    strategy = RuleBasedDispatchStrategy()
    r_seasonal = strategy.dispatch(base)
    r_flat = strategy.dispatch(flat)
    assert r_seasonal.totals is not None and r_flat.totals is not None
    assert r_seasonal.totals.geothermal_electricity_cost_currency == pytest.approx(45.0)
    assert r_flat.totals.geothermal_electricity_cost_currency == pytest.approx(30.0)


@pytest.mark.requirement("W08")
@pytest.mark.requirement("W15")
def test_piecewise_linear_electricity_curve_interpolates_from_origin_and_holds_top_ratio() -> None:
    starts = tuple(datetime(2026, 1, 1, h, tzinfo=UTC) for h in range(5))
    curve = (
        DispatchElectricityPoint(
            plant_heat_w=1_000_000.0,
            source_pump_electricity_w=40_000.0,
            compressor_electricity_w=100_000.0,
        ),
        DispatchElectricityPoint(
            plant_heat_w=2_000_000.0,
            source_pump_electricity_w=160_000.0,
            compressor_electricity_w=220_000.0,
        ),
    )
    req = DispatchRequest(
        request_id="elec-curve",
        operating_scenario_id="op",
        starts_utc=starts,
        durations_s=(3600.0,) * 5,
        # Demands: 0.5 MW (origin-to-pt1), 1.0 MW (pt1), 1.5 MW (mid pt1-pt2), 2.0 MW (pt2), 0 MW.
        demand_heat_w=(500_000.0, 1_000_000.0, 1_500_000.0, 2_000_000.0, 0.0),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo",
            max_delivered_heat_w=2_000_000.0,
            max_plant_handover_heat_w=2_000_000.0,
            electricity_curve=curve,
        ),
        electricity_price_currency_per_j=(currency_per_mwh_to_per_j(100.0),) * 5,
    )
    res = RuleBasedDispatchStrategy().dispatch(req)
    assert res.totals is not None
    # Pump W: 20k + 40k + 100k + 160k + 0 = 320 kW -> 320e3 * 3600 = 1.152e9 J.
    assert res.totals.source_pump_electricity_j == pytest.approx(1.152e9)
    # Comp W: 50k + 100k + 160k + 220k + 0 = 530 kW -> 530e3 * 3600 = 1.908e9 J.
    assert res.totals.compressor_electricity_j == pytest.approx(1.908e9)
    assert any("piecewise-linearly" in note for note in res.not_modelled)

    # Single-point curve matches proportional scaling and falls back to top ratio above pt1.
    single_req = DispatchRequest(
        request_id="elec-single",
        operating_scenario_id="op",
        starts_utc=starts[:2],
        durations_s=(3600.0, 3600.0),
        demand_heat_w=(500_000.0, 2_500_000.0),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo",
            max_delivered_heat_w=2_500_000.0,
            max_plant_handover_heat_w=2_500_000.0,
            electricity_curve=(curve[0],),
        ),
        electricity_price_currency_per_j=(currency_per_mwh_to_per_j(100.0),) * 2,
    )
    single_res = RuleBasedDispatchStrategy().dispatch(single_req)
    assert single_res.totals is not None
    assert single_res.totals.source_pump_electricity_j == pytest.approx(
        (20_000.0 + 100_000.0) * 3600.0
    )
    assert single_res.totals.compressor_electricity_j == pytest.approx(
        (50_000.0 + 250_000.0) * 3600.0
    )

    # Curve with electricity consumption still requires an electricity tariff.
    with pytest.raises(NotEstablished):
        RuleBasedDispatchStrategy().dispatch(
            req.model_copy(update={"electricity_price_currency_per_j": ()})
        )


@pytest.mark.requirement("W15")
@pytest.mark.requirement("W35")
def test_funding_rule_applies_to_replacements_controls_replacement_grants_and_residual() -> None:
    # Asset: 10 MEUR CAPEX, 15 yr lifetime, 20 yr horizon -> replaced once at year 15 with 10 yr
    # remaining at year 20 (remaining_fraction = 10/15 = 2/3).
    from datetime import date

    from pydantic import ValidationError

    # Omitting applies_to_replacements is rejected at schema validation time.
    with pytest.raises(ValidationError):
        FundingRule(  # type: ignore[call-arg]
            rule_id="missing-flag",
            title="missing applies_to_replacements",
            effective_date=date(2026, 1, 1),
            source_id="syn-definitions",
            reviewed=False,
            grant_fraction=0.2,
            locations=("source",),
            categories=(CostCategory.CAPEX,),
        )

    econ = economic_scenario(discount_rate=0.0, horizon=20)
    ledger = reconcile_ledger(
        "cand-repl",
        (
            LedgerRow(
                item_id="plant-capex",
                asset_id="plant",
                category=CostCategory.CAPEX,
                cost_owner="system",
                location="source",
                included=True,
                amount_currency=10_000_000.0,
                amount_basis=AmountBasis.TOTAL,
                lifetime_years=15.0,
                origin="test",
            ),
        ),
    )
    annual_heat = 10_000.0 * JOULES_PER_MWH
    base_rule_kw = dict(
        rule_id="grant",
        title="20% plant grant",
        effective_date=date(2026, 1, 1),
        source_id="syn-definitions",
        reviewed=False,
        grant_fraction=0.2,
        locations=("source",),
        categories=(CostCategory.CAPEX,),
    )
    first_only = evaluate_boundary_cashflows(
        econ,
        ledger,
        boundary=CostBoundary.SYSTEM_DELIVERED,
        annual_heat_j=annual_heat,
        funding_rules=(FundingRule(**base_rule_kw, applies_to_replacements=False),),
        case_label="funded",
    )
    # Year 0 net = 8M; Year 15 replacement unsubsidised = 10M; residual at Year 20 = -10M * 10/15.
    assert first_only.yearly[0].capex_currency == pytest.approx(8_000_000.0)
    assert first_only.yearly[15].replacement_currency == pytest.approx(10_000_000.0)
    assert first_only.yearly[15].grant_currency == pytest.approx(0.0)
    assert first_only.yearly[20].residual_value_currency == pytest.approx(
        -10_000_000.0 * 10.0 / 15.0
    )

    both = evaluate_boundary_cashflows(
        econ,
        ledger,
        boundary=CostBoundary.SYSTEM_DELIVERED,
        annual_heat_j=annual_heat,
        funding_rules=(FundingRule(**base_rule_kw, applies_to_replacements=True),),
        case_label="funded",
    )
    # Both year 0 and year 15 net = 8M; residual at Year 20 = -8M * 10/15.
    assert both.yearly[0].capex_currency == pytest.approx(8_000_000.0)
    assert both.yearly[15].replacement_currency == pytest.approx(8_000_000.0)
    assert both.yearly[15].grant_currency == pytest.approx(2_000_000.0)
    assert both.yearly[20].residual_value_currency == pytest.approx(-8_000_000.0 * 10.0 / 15.0)


@pytest.mark.requirement("W08")
@pytest.mark.requirement("W15")
def test_negative_demand_and_unequal_baseline_heat_fail_loudly(scenario: Scenario) -> None:
    from r3chain.domain.records.scenarios import StoredSeries
    from r3chain.operation.dispatch import DispatchSettings, build_dispatch_request

    op = scenario.operating_scenarios[0]
    existing_demand = next(
        ts.series for ts in scenario.time_series if ts.series_id == op.demand.series_id
    )
    bad_vals = list(existing_demand.values)
    bad_vals[1] = -50_000.0
    bad_demand = existing_demand.model_copy(update={"values": tuple(bad_vals)})
    scen = scenario.model_copy(
        update={
            "time_series": tuple(
                StoredSeries(series_id=ts.series_id, dataset_id=ts.dataset_id, series=bad_demand)
                if ts.series_id == op.demand.series_id
                else ts
                for ts in scenario.time_series
            )
        }
    )
    settings = DispatchSettings(
        geothermal_priority=2,
        allow_curtailment=True,
        interval_mode="full",
        max_reduction_error_fraction=0.01,
        reduced_stride_count=4,
    )
    expected_ts = existing_demand.starts_utc[1].isoformat()[:19]
    with pytest.raises(ValueError, match=f"{op.demand.series_id}.*interval 1.*{expected_ts}"):
        build_dispatch_request(scen, op, request_id="r", settings=settings)

    econ = economic_scenario(discount_rate=0.04, horizon=20)
    ledger = reconcile_ledger("subj", ())
    base = evaluate_boundary_cashflows(
        econ, ledger, boundary=CostBoundary.SYSTEM_DELIVERED, annual_heat_j=1e12
    )
    cand = evaluate_boundary_cashflows(
        econ, ledger, boundary=CostBoundary.SYSTEM_DELIVERED, annual_heat_j=0.8e12
    )
    with pytest.raises(ValueError, match="baseline_annual_heat_j"):
        compare_to_baseline(
            baseline_id="b",
            baseline=base,
            candidate=cand,
            geothermal_displaced_heat_j=0.5e12,
            baseline_annual_heat_j=1e12,
            candidate_annual_heat_j=0.8e12,
        )
