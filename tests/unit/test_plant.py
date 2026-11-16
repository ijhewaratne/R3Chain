"""Plant arrangements, their registry and the plant records."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from tests.conftest import changed, known

from r3chain.coupling import plant as plant_module
from r3chain.coupling.fluids import ConstantHeatCapacity
from r3chain.coupling.heat_pump import CarnotScreeningHeatPump, MapHeatPump
from r3chain.coupling.plant import (
    DEMAND_MET,
    Arrangement,
    GeothermalInlet,
    NetworkDemand,
    PlantInputError,
    PlantPoint,
    PlantSpec,
    arrangement_named,
    arrangements,
    evaluate_plant,
    performance_basis,
    register_arrangement,
    required_geothermal_flow,
    resolve_plant,
)
from r3chain.domain.evidence import NotEstablished
from r3chain.domain.records.plant import CouplingCase, OperatingCase, Plant
from r3chain.domain.records.runs import CheckOutcome
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState
from r3chain.domain.validation import parse_scenario

LIQUID = ConstantHeatCapacity(4000.0, "illustrative value for hand calculations")
UNKNOWN = {"state": "unknown", "reason": "not selected yet", "owner": "plant designer"}

# The network asks for 20 kg/s heated from 320 K to 350 K: 80 000 W/K * 30 K = 2 400 000 W.
DEMAND = NetworkDemand(20.0, 320.0, 350.0, LIQUID)

DIRECT = PlantSpec(
    "direct",
    "direct_exchanger",
    {"exchanger_ua_w_k": 1.0e9, "exchanger_min_approach_delta_k": 5.0},
)
HEAT_PUMP = PlantSpec(
    "pump",
    "heat_pump_assisted",
    {
        "exchanger_ua_w_k": 1.0e9,
        "exchanger_min_approach_delta_k": 5.0,
        "min_geothermal_outlet_temperature_k": 300.0,
        "compressor_heat_loss_fraction": 0.1,
    },
    CarnotScreeningHeatPump(0.5, 10.0, 5.0),
)
SEPARATED = PlantSpec(
    "separated",
    "pressure_separation_exchanger",
    {
        "exchanger_ua_w_k": 1.0e9,
        "exchanger_min_approach_delta_k": 5.0,
        "separation_exchanger_ua_w_k": 400_000.0,
        "separation_exchanger_min_approach_delta_k": 3.0,
    },
)


def inlet(mass_flow_kg_s: float, temperature_k: float = 380.0) -> GeothermalInlet:
    return GeothermalInlet(mass_flow_kg_s, temperature_k, LIQUID)


def outcomes(point: PlantPoint) -> dict[str, str]:
    return {check.check_id: check.outcome.value for check in point.checks}


def plant_record(scenario: Scenario, plant_id: str) -> Plant:
    return next(item for item in scenario.plants if item.plant_id == plant_id)


# --- registry -------------------------------------------------------------------------------


def test_registry_holds_the_three_arrangements_of_the_plan() -> None:
    assert [item.name for item in arrangements()] == [
        "direct_exchanger",
        "heat_pump_assisted",
        "pressure_separation_exchanger",
    ]
    pump = arrangement_named("heat_pump_assisted")
    assert pump is not None and pump.uses_heat_pump
    # The boundaries of the heat pump are stated, as specification 10.3 requires.
    assert "evaporator is on the geothermal stream" in pump.description
    assert "condenser on the network stream" in pump.description
    assert arrangement_named("storage_tank") is None
    with pytest.raises(ValueError, match="already registered"):
        register_arrangement(pump)


def test_a_new_arrangement_is_one_registry_entry(scenario: Scenario) -> None:
    """Extension point X6: a new integration concept needs no change outside its entry."""

    def half_the_demand(
        spec: PlantSpec, source: GeothermalInlet, demand: NetworkDemand
    ) -> PlantPoint:
        heat = spec.parameters["share_fraction"] * demand.heat_w
        return PlantPoint(
            plant_id=spec.plant_id,
            arrangement=spec.arrangement,
            performance_basis="equipment_rating",
            binding_limit="share_of_demand",
            geothermal_mass_flow_kg_s=source.mass_flow_kg_s,
            geothermal_inlet_temperature_k=source.temperature_k,
            geothermal_outlet_temperature_k=source.temperature_k
            - heat / (source.mass_flow_kg_s * 4000.0),
            network_mass_flow_kg_s=demand.mass_flow_kg_s,
            network_inlet_temperature_k=demand.return_temperature_k,
            network_outlet_temperature_k=demand.with_heat_w(heat).supply_temperature_k,
            required_outlet_temperature_k=demand.supply_temperature_k,
            required_heat_w=demand.heat_w,
            heat_to_network_w=heat,
            heat_from_geothermal_w=heat,
            exchangers={},
            balance_residual_w=0.0,
        )

    entry = Arrangement(
        name="test_half_demand",
        description="Delivers a fixed share of the demand. For this test only.",
        required_parameters=("share_fraction",),
        optional_parameters=(),
        uses_heat_pump=False,
        evaluate=half_the_demand,
        coldest_outlet_k=lambda spec, demand: demand.return_temperature_k,
    )
    register_arrangement(entry)
    try:
        record = Plant.model_validate(
            {
                "plant_id": "new-concept",
                "arrangement": "test_half_demand",
                "parameters": {"share_fraction": known(0.5)},
            }
        )
        spec, left_out = resolve_plant(record)
        assert left_out == ()
        point = evaluate_plant(spec, inlet(10.0), DEMAND)
        assert point.heat_to_network_w == pytest.approx(1_200_000.0)
        assert not point.meets_demand
    finally:
        del plant_module._REGISTRY["test_half_demand"]
    assert arrangement_named("test_half_demand") is None


# --- from a record to a plant ---------------------------------------------------------------


def test_synthetic_plants_resolve_with_their_gaps_visible(scenario: Scenario) -> None:
    spec, left_out = resolve_plant(plant_record(scenario, "plant-direct"))
    assert spec.arrangement == "direct_exchanger" and spec.heat_pump is None
    assert spec.parameters["exchanger_ua_w_k"] == 450_000.0
    assert performance_basis(spec) == "equipment_rating"
    # The two unknown optional values are left out and reported. They get no default.
    assert "network_side_rated_pressure_loss_pa" not in spec.parameters
    assert [item.field_path for item in left_out] == [
        "plants[plant-direct].parameters[network_side_rated_pressure_loss_pa]",
        "plants[plant-direct].parameters[network_side_rated_mass_flow_kg_s]",
    ]
    assert spec.origin("exchanger_ua_w_k") == "plants[plant-direct].parameters[exchanger_ua_w_k]"

    pump, _ = resolve_plant(plant_record(scenario, "plant-heat-pump"))
    assert isinstance(pump.heat_pump, MapHeatPump)
    assert performance_basis(pump) == "performance_map"
    assert pump.fingerprint()["heat_pump"]["map"]["map_id"] == "synthetic-heat-pump"


@pytest.mark.requirement("A12")
def test_an_unknown_required_plant_value_is_not_defaulted(scenario_data: dict[str, Any]) -> None:
    path = "plants[plant-direct].parameters[exchanger_ua_w_k]"
    scenario = Scenario.model_validate(changed(scenario_data, path, UNKNOWN))
    with pytest.raises(NotEstablished) as error:
        resolve_plant(plant_record(scenario, "plant-direct"))
    assert [(item.field_path, item.owner) for item in error.value.missing] == [
        (path, "plant designer")
    ]

    map_path = "plants[plant-heat-pump].heat_pump_map"
    scenario = Scenario.model_validate(changed(scenario_data, map_path, UNKNOWN))
    with pytest.raises(NotEstablished) as error:
        resolve_plant(plant_record(scenario, "plant-heat-pump"))
    assert [item.field_path for item in error.value.missing] == [map_path]


def test_a_plant_record_that_does_not_fit_its_arrangement_is_rejected(
    scenario_data: dict[str, Any],
) -> None:
    def resolve(plant_id: str, path: str, value: Any) -> PlantInputError:
        data = changed(scenario_data, f"plants[{plant_id}].{path}", value)
        with pytest.raises(PlantInputError) as error:
            resolve_plant(plant_record(Scenario.model_validate(data), plant_id))
        return error.value

    unsupported = resolve("plant-direct", "arrangement", "storage_tank")
    assert unsupported.state is AdapterState.UNSUPPORTED_MODEL
    assert "direct_exchanger, heat_pump_assisted" in unsupported.reason

    stray = resolve("plant-direct", "parameters[compressor_heat_loss_fraction]", known(0.05))
    assert stray.state is AdapterState.INVALID_INPUT
    assert "is not used by arrangement direct_exchanger" in stray.reason

    negative = resolve("plant-direct", "parameters[exchanger_ua_w_k]", known(-1.0))
    assert "exchanger_ua_w_k must be positive" in negative.reason

    fraction = resolve("plant-heat-pump", "parameters[compressor_heat_loss_fraction]", known(1.0))
    assert "compressor_heat_loss_fraction must lie in 0 to below 1" in fraction.reason

    # A direct exchanger turned into a heat pump arrangement lacks two required values.
    incomplete = resolve("plant-direct", "arrangement", "pressure_separation_exchanger")
    assert "separation_exchanger_ua_w_k is required" in incomplete.reason

    # A heat pump arrangement needs a map or the screening parameters.
    without_map = resolve("plant-heat-pump", "heat_pump_map", None)
    assert "needs a heat pump performance map, or the parameters of the screening model" in (
        without_map.reason
    )


def test_screening_model_is_used_only_when_asked_for_and_is_labelled(
    scenario_data: dict[str, Any],
) -> None:
    data = changed(scenario_data, "plants[plant-heat-pump].heat_pump_map", None)
    for name, value in (
        ("carnot_efficiency_fraction", 0.5),
        ("evaporator_temperature_difference_delta_k", 10.0),
        ("condenser_temperature_difference_delta_k", 5.0),
    ):
        data = changed(data, f"plants[plant-heat-pump].parameters[{name}]", known(value))
    spec, _ = resolve_plant(plant_record(Scenario.model_validate(data), "plant-heat-pump"))
    assert isinstance(spec.heat_pump, CarnotScreeningHeatPump)
    assert performance_basis(spec) == "screening_model"
    point = evaluate_plant(spec, GeothermalInlet(10.0, 345.0, LIQUID), DEMAND)
    assert point.performance_basis == "screening_model"
    assert any("not achieved equipment performance" in note for note in point.warnings)
    # The screening model states no capacity, so that check stays open.
    assert outcomes(point)["heat_pump_capacity"] == "not_evaluated"


def test_plant_and_case_records_reject_inconsistent_content(scenario: Scenario) -> None:
    plant = plant_record(scenario, "plant-direct").to_json_dict()
    with pytest.raises(ValueError, match="no unit suffix"):
        Plant.model_validate({**plant, "parameters": {"exchanger_ua": known(1.0)}})

    case = scenario.coupling_cases[0].to_json_dict()
    with pytest.raises(ValueError, match="needs fixed_geothermal_mass_flow_kg_s"):
        CouplingCase.model_validate({**case, "flow_control": "fixed"})
    with pytest.raises(ValueError, match="only allowed with flow_control 'fixed'"):
        CouplingCase.model_validate({**case, "fixed_geothermal_mass_flow_kg_s": known(5.0)})
    with pytest.raises(ValueError, match="network_pressure_lift_pa must be positive"):
        CouplingCase.model_validate({**case, "network_pressure_lift_pa": known(0.0)})

    operating = scenario.operating_cases[0].to_json_dict()
    loads = operating["loads"]["value"]
    with pytest.raises(ValueError, match="duplicate consumer load"):
        OperatingCase.model_validate(
            {**operating, "loads": {**operating["loads"], "value": [*loads, loads[0]]}}
        )
    assert (
        Plant.model_validate(plant).content_hash()
        == plant_record(scenario, "plant-direct").content_hash()
    )


@pytest.mark.requirement("W07")
def test_coupling_cases_must_fit_the_scenario(scenario_data: dict[str, Any]) -> None:
    def errors(path: str, value: Any) -> list[tuple[str, str]]:
        scenario, report = parse_scenario(changed(scenario_data, path, value))
        assert scenario is not None
        return [(issue.code, issue.field_path) for issue in report.errors]

    case = "coupling_cases[alpha-direct-shoulder]"
    assert errors(f"{case}.plant_id", "no-such-plant") == [
        ("unknown_reference", f"{case}.plant_id")
    ]
    assert errors(f"{case}.operating_case_id", "no-such-case") == [
        ("unknown_reference", f"{case}.operating_case_id")
    ]
    # A route from another site is never combined with this source.
    assert errors(f"{case}.route_id", "route-beta-west") == [("route_mismatch", f"{case}.route_id")]
    assert errors(f"{case}.connection_id", "conn-east") == [("route_mismatch", f"{case}.route_id")]

    loads = scenario_data["operating_cases"][0]["loads"]
    fewer = {**loads, "value": loads["value"][:3]}
    assert errors("operating_cases[winter-design].loads", fewer) == [
        ("missing_load", "operating_cases[winter-design].loads")
    ]
    stranger = {"consumer_id": "consumer-9", "heat_w": 1.0, "return_temperature_k": 323.15}
    more = {**loads, "value": [*loads["value"], stranger]}
    assert errors("operating_cases[winter-design].loads", more) == [
        ("unknown_reference", "operating_cases[winter-design].loads")
    ]


# --- direct exchanger -----------------------------------------------------------------------


def test_direct_exchanger_meets_the_demand_with_the_smallest_flow() -> None:
    # The geothermal stream enters at 380 K and may leave at 320 + 5 = 325 K.
    #   smallest flow = 2 400 000 / (4000 * (380 - 325)) = 10.909 kg/s
    choice = required_geothermal_flow(
        DIRECT,
        inlet_temperature_k=380.0,
        fluid=LIQUID,
        demand=DEMAND,
        min_flow_kg_s=2.0,
        max_flow_kg_s=40.0,
    )
    assert choice.meets_demand and choice.limit_at_largest_flow == DEMAND_MET
    assert choice.flow_kg_s == pytest.approx(10.909091, rel=1e-6)

    point = evaluate_plant(DIRECT, inlet(choice.flow_kg_s), DEMAND)
    assert point.meets_demand
    assert point.heat_to_network_w == pytest.approx(2_400_000.0)
    assert point.heat_from_geothermal_w == pytest.approx(point.heat_to_network_w)
    assert point.geothermal_outlet_temperature_k == pytest.approx(325.0)
    assert point.network_outlet_temperature_k == pytest.approx(350.0)
    assert abs(point.balance_residual_w) < 1e-6
    assert set(outcomes(point).values()) <= {"pass", "not_evaluated"}

    # A demand that the lowest flow already meets is served at the lowest flow.
    small = NetworkDemand(1.0, 320.0, 350.0, LIQUID)
    low = required_geothermal_flow(
        DIRECT,
        inlet_temperature_k=380.0,
        fluid=LIQUID,
        demand=small,
        min_flow_kg_s=2.0,
        max_flow_kg_s=40.0,
    )
    assert low.flow_kg_s == 2.0 and low.meets_demand


@pytest.mark.requirement("A02")
def test_a_source_that_is_too_cold_fails_at_the_hot_end_only() -> None:
    # Inlet 345 K. The network outlet can reach 345 - 5 = 340 K instead of 350 K.
    #   deliverable heat = 80 000 * (340 - 320) = 1 600 000 W
    #   smallest flow for it = 1 600 000 / (4000 * (345 - 325)) = 20 kg/s
    choice = required_geothermal_flow(
        DIRECT,
        inlet_temperature_k=345.0,
        fluid=LIQUID,
        demand=DEMAND,
        min_flow_kg_s=2.0,
        max_flow_kg_s=40.0,
    )
    assert not choice.meets_demand
    assert choice.limit_at_largest_flow == "exchanger_hot_end_approach"
    # The source is not driven to its largest flow, because more flow delivers no more heat.
    assert choice.flow_kg_s == pytest.approx(20.0, rel=1e-6)

    point = evaluate_plant(DIRECT, inlet(choice.flow_kg_s, 345.0), DEMAND)
    assert point.heat_to_network_w == pytest.approx(1_600_000.0, rel=1e-9)
    assert point.network_outlet_temperature_k == pytest.approx(340.0)
    results = outcomes(point)
    assert results["exchanger_hot_end_approach"] == "fail"
    assert results["exchanger_cold_end_approach"] == "pass"
    assert results["exchanger_rating"] == "pass"
    hot_end = next(c for c in point.checks if c.check_id == "exchanger_hot_end_approach")
    assert (hot_end.observed, hot_end.limit, hot_end.margin) == (
        pytest.approx(-5.0),
        5.0,
        pytest.approx(-10.0),
    )
    assert hot_end.threshold_origin == "plants[direct].parameters[exchanger_min_approach_delta_k]"


def test_a_source_with_too_little_flow_fails_at_the_cold_end_only() -> None:
    # 6 kg/s at 380 K can give 24 000 * (380 - 325) = 1 320 000 W of the 2 400 000 W.
    point = evaluate_plant(DIRECT, inlet(6.0), DEMAND)
    assert point.binding_limit == "exchanger_cold_end_approach"
    assert point.heat_to_network_w == pytest.approx(1_320_000.0)
    results = outcomes(point)
    assert results["exchanger_hot_end_approach"] == "pass"
    assert results["exchanger_cold_end_approach"] == "fail"


def test_small_surface_fouling_and_outlet_limit_each_bind_by_name() -> None:
    small = PlantSpec(
        "small",
        "direct_exchanger",
        {
            "exchanger_ua_w_k": 60_000.0,
            "exchanger_min_approach_delta_k": 5.0,
            "exchanger_fouling_allowance_fraction": 0.25,
        },
    )
    point = evaluate_plant(small, inlet(20.0), DEMAND)
    assert point.binding_limit == "exchanger_rating"
    assert outcomes(point)["exchanger_rating"] == "fail"
    # The fouling allowance reduces the conductance in service: 60 000 * 0.75 = 45 000 W/K.
    assert point.exchangers["exchanger"].ua_w_k == pytest.approx(45_000.0)
    # Without an allowance the stated value is used, and the result says so.
    assert any(
        "no fouling allowance" in note
        for note in evaluate_plant(DIRECT, inlet(20.0), DEMAND).warnings
    )

    limited = PlantSpec(
        "limited",
        "direct_exchanger",
        {**DIRECT.parameters, "min_geothermal_outlet_temperature_k": 340.0},
    )
    # 12 kg/s may cool from 380 K to 340 K only: 48 000 * 40 = 1 920 000 W.
    point = evaluate_plant(limited, inlet(12.0), DEMAND)
    assert point.binding_limit == "min_geothermal_outlet_temperature"
    assert point.heat_to_network_w == pytest.approx(1_920_000.0)
    assert point.geothermal_outlet_temperature_k == pytest.approx(340.0)
    assert outcomes(point)["min_geothermal_outlet_temperature"] == "fail"


@pytest.mark.requirement("A12")
def test_pressure_loss_is_reported_checked_or_left_open() -> None:
    def spec(**extra: float) -> PlantSpec:
        return PlantSpec("p", "direct_exchanger", {**DIRECT.parameters, **extra})

    # Nothing given: both sides stay open. They never read as passed.
    point = evaluate_plant(DIRECT, inlet(12.0), DEMAND)
    assert point.geothermal_side_pressure_loss_pa is None
    assert outcomes(point)["exchanger_geothermal_side_pressure_loss"] == "not_evaluated"
    assert outcomes(point)["exchanger_network_side_pressure_loss"] == "not_evaluated"

    rated = {
        "geothermal_side_rated_pressure_loss_pa": 60_000.0,
        "geothermal_side_rated_mass_flow_kg_s": 15.0,
    }
    # 60 000 Pa at 15 kg/s gives 60 000 * (12 / 15)^2 = 38 400 Pa at 12 kg/s.
    point = evaluate_plant(spec(**rated), inlet(12.0), DEMAND)
    assert point.geothermal_side_pressure_loss_pa == pytest.approx(38_400.0)
    # A loss without a limit is a number, not a passed check.
    assert outcomes(point)["exchanger_geothermal_side_pressure_loss"] == "not_evaluated"

    passed = evaluate_plant(
        spec(**rated, max_geothermal_side_pressure_loss_pa=50_000.0), inlet(12.0), DEMAND
    )
    check = next(
        c for c in passed.checks if c.check_id == "exchanger_geothermal_side_pressure_loss"
    )
    assert (check.outcome, check.margin) == (CheckOutcome.PASS, pytest.approx(11_600.0))
    failed = evaluate_plant(
        spec(**rated, max_geothermal_side_pressure_loss_pa=30_000.0), inlet(12.0), DEMAND
    )
    assert outcomes(failed)["exchanger_geothermal_side_pressure_loss"] == "fail"


# --- heat pump assisted ---------------------------------------------------------------------


@pytest.mark.requirement("A03")
def test_heat_pump_assisted_plant_keeps_its_two_heat_paths_apart() -> None:
    # Inlet 345 K at 20 kg/s (80 000 W/K), demand 2 400 000 W.
    # Direct exchanger: network outlet up to 340 K, 1 600 000 W, geothermal stream leaves at 325 K.
    # Heat pump: condenser 800 000 W from 340 K to 350 K, source inlet 325 K.
    #   condensing 355 K, evaporating 315 K, COP = 0.5 * 355 / 40 = 4.4375
    #   W = 800 000 / 4.4375 = 180 282 W, loss = 18 028 W
    #   Q_evaporator = 800 000 - 0.9 * 180 282 = 637 746 W
    #   geothermal outlet = 325 - 637 746 / 80 000 = 317.03 K
    point = evaluate_plant(HEAT_PUMP, inlet(20.0, 345.0), DEMAND)
    assert point.meets_demand
    direct = point.exchangers["direct_exchanger"]
    pump = point.heat_pump
    assert pump is not None
    assert direct.heat_w == pytest.approx(1_600_000.0)
    assert pump.heating_cop_ratio == pytest.approx(4.4375)
    assert pump.condenser_heat_w == pytest.approx(800_000.0)
    assert pump.compressor_electricity_w == pytest.approx(180_281.69, rel=1e-6)
    assert pump.evaporator_heat_w == pytest.approx(637_746.48, rel=1e-6)
    assert point.geothermal_outlet_temperature_k == pytest.approx(317.028, abs=1e-3)

    # The network receives exchanger heat plus condenser heat. The geothermal stream gives
    # exchanger heat plus evaporator heat. Condenser and evaporator heat are never added.
    assert point.heat_to_network_w == pytest.approx(direct.heat_w + pump.condenser_heat_w)
    assert point.heat_from_geothermal_w == pytest.approx(direct.heat_w + pump.evaporator_heat_w)
    assert point.heat_to_network_w == pytest.approx(2_400_000.0)
    assert point.compressor_electricity_w == pytest.approx(pump.compressor_electricity_w)
    assert point.heat_loss_to_surroundings_w == pytest.approx(18_028.17, rel=1e-6)
    # Energy conservation of the whole plant.
    assert point.heat_from_geothermal_w + point.compressor_electricity_w == pytest.approx(
        point.heat_to_network_w + point.heat_loss_to_surroundings_w
    )
    assert abs(point.balance_residual_w) < 1e-6
    assert outcomes(point)["heat_pump_balance"] == "pass"


def test_heat_pump_stays_off_when_direct_heat_is_enough() -> None:
    point = evaluate_plant(HEAT_PUMP, inlet(20.0, 380.0), DEMAND)
    assert point.meets_demand and point.heat_pump is None
    assert point.compressor_electricity_w == 0.0
    assert point.heat_to_network_w == pytest.approx(point.heat_from_geothermal_w)


def test_heat_pump_limits_are_reported_once_each() -> None:
    capped = PlantSpec(
        "capped",
        "heat_pump_assisted",
        HEAT_PUMP.parameters,
        CarnotScreeningHeatPump(0.5, 10.0, 5.0, max_condenser_heat_w=500_000.0),
    )
    point = evaluate_plant(capped, inlet(20.0, 345.0), DEMAND)
    assert point.binding_limit == "heat_pump_capacity"
    assert point.heat_to_network_w == pytest.approx(1_600_000.0 + 500_000.0)
    results = outcomes(point)
    assert results["heat_pump_capacity"] == "fail"
    # The outlet temperature is checked for the duty the capacity permits, so it passes.
    assert results["min_geothermal_outlet_temperature"] == "pass"

    # With 6 kg/s the geothermal stream reaches its lowest temperature before the demand is met.
    point = evaluate_plant(HEAT_PUMP, inlet(6.0, 345.0), DEMAND)
    assert point.binding_limit == "heat_pump_source_heat"
    assert point.geothermal_outlet_temperature_k == pytest.approx(300.0)
    assert outcomes(point)["min_geothermal_outlet_temperature"] == "fail"

    # A source colder than the model covers is outside the performance model.
    point = evaluate_plant(HEAT_PUMP, inlet(20.0, 330.0), NetworkDemand(20.0, 320.0, 700.0, LIQUID))
    assert point.binding_limit in {"heat_pump_map_domain", "heat_pump_source_heat"}


def test_an_operating_point_outside_the_map_is_a_failed_check(scenario: Scenario) -> None:
    spec, _ = resolve_plant(plant_record(scenario, "plant-heat-pump"))
    # The map ends at a sink outlet of 363.15 K. The network asks for 370 K.
    demand = NetworkDemand(10.0, 323.15, 370.0, LIQUID)
    point = evaluate_plant(spec, inlet(10.0, 345.0), demand)
    assert point.binding_limit == "heat_pump_map_domain"
    assert point.heat_pump is None
    check = next(c for c in point.checks if c.check_id == "heat_pump_map_domain")
    assert (check.outcome, check.observed, check.limit) == (CheckOutcome.FAIL, 370.0, 363.15)
    assert "synthetic-heat-pump" in (check.threshold_origin or "")
    # The direct exchanger still delivers what it can.
    assert point.heat_to_network_w == pytest.approx(point.exchangers["direct_exchanger"].heat_w)


# --- pressure separation --------------------------------------------------------------------


def test_pressure_separation_needs_a_hotter_loop_and_more_geothermal_flow() -> None:
    # Separation exchanger with equal heat capacity rates: its two streams differ by
    #   Q / UA = 2 400 000 / 400 000 = 6 K, which is above the minimum approach of 3 K.
    # The loop therefore runs from 326 K to 356 K, and the geothermal stream may leave at 331 K.
    #   smallest flow = 2 400 000 / (4000 * (380 - 331)) = 12.245 kg/s
    choice = required_geothermal_flow(
        SEPARATED,
        inlet_temperature_k=380.0,
        fluid=LIQUID,
        demand=DEMAND,
        min_flow_kg_s=2.0,
        max_flow_kg_s=40.0,
    )
    assert choice.meets_demand
    assert choice.flow_kg_s == pytest.approx(12.244898, rel=1e-6)
    point = evaluate_plant(SEPARATED, inlet(choice.flow_kg_s), DEMAND)
    separation = point.exchangers["separation_exchanger"]
    assert separation.hot_inlet_temperature_k == pytest.approx(356.0)
    assert separation.hot_outlet_temperature_k == pytest.approx(326.0)
    assert separation.cold_outlet_temperature_k == pytest.approx(350.0)
    assert point.geothermal_outlet_temperature_k == pytest.approx(331.0)
    assert point.heat_to_network_w == pytest.approx(2_400_000.0)
    assert any("intermediate loop" in note for note in point.warnings)

    # The same demand without separation needs less flow: 10.909 kg/s.
    direct = required_geothermal_flow(
        DIRECT,
        inlet_temperature_k=380.0,
        fluid=LIQUID,
        demand=DEMAND,
        min_flow_kg_s=2.0,
        max_flow_kg_s=40.0,
    )
    assert direct.flow_kg_s < choice.flow_kg_s


def test_pressure_separation_delivers_what_the_source_temperature_permits() -> None:
    # Inlet 350 K. The loop may reach 345 K. With a heat Q the loop must reach
    #   320 + Q / 80 000 + max(Q / 400 000, 3) K.
    # Setting this to 345 K with the 3 K approach binding gives Q = 80 000 * 22 = 1 760 000 W,
    # and then Q / UA = 4.4 K > 3 K, so the surface binds instead:
    #   Q / 80 000 + Q / 400 000 = 25  ->  Q = 25 / (1/80 000 + 1/400 000) = 1 666 667 W
    point = evaluate_plant(SEPARATED, inlet(40.0, 350.0), DEMAND)
    assert point.binding_limit == "exchanger_hot_end_approach"
    assert point.heat_to_network_w == pytest.approx(1_666_666.7, rel=1e-6)
    assert outcomes(point)["exchanger_hot_end_approach"] == "fail"


@pytest.mark.parametrize("spec", [DIRECT, HEAT_PUMP, SEPARATED], ids=lambda item: item.arrangement)
def test_more_geothermal_flow_never_delivers_less_heat(spec: PlantSpec) -> None:
    for temperature in (335.0, 350.0, 380.0):
        heats = [
            evaluate_plant(spec, inlet(flow, temperature), DEMAND).heat_to_network_w
            for flow in (2.0, 4.0, 8.0, 12.0, 20.0, 40.0)
        ]
        assert all(later >= earlier * (1.0 - 1e-9) for earlier, later in pairwise(heats))
        assert all(heat <= DEMAND.heat_w * (1.0 + 1e-9) for heat in heats)
