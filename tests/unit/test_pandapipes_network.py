"""The pandapipes adapter against a direct engine run and against independent formulas.

Acceptance tests A04, A06 and A13 at adapter level.
"""

from __future__ import annotations

import math
from typing import Any

import pandapipes as pp
import pytest
from pandapipes.component_models.component_toolbox import p_correction_height_air
from tests.conftest import (
    DESIGN_HEAT_W,
    FIXED_FLOW_KG_S,
    hydraulic_case,
    supply_point,
    thermal_case,
)

from r3chain.adapters.pandapipes_network import (
    TESTED_VERSIONS,
    PandapipesNetworkModel,
    ambient_pressure_pa,
    engine_version,
)
from r3chain.domain.records.network import Network
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState
from r3chain.domain.units import PressureReference
from r3chain.ports.network import NetworkResult
from r3chain.reference.hydraulics import (
    darcy_weisbach_pressure_drop_pa,
    friction_factor_swamee_jain,
    hydrostatic_pressure_difference_pa,
    reynolds_number,
)

MODEL = PandapipesNetworkModel()
ENGINE_GRAVITY = 9.81  # pandapipes uses 9.81 m/s2, not the standard 9.80665


@pytest.fixture
def network(scenario: Scenario) -> Network:
    return scenario.networks[0]


def absolute(result: NetworkResult, node_id: str) -> float:
    assert result.pressure_reference is PressureReference.ABSOLUTE
    return result.node(node_id).pressure.value_pa


def water() -> Any:
    return pp.create_empty_network(fluid="water").fluid


def test_the_installed_engine_is_the_tested_release() -> None:
    assert engine_version() in TESTED_VERSIONS
    assert MODEL.capabilities().engine_version == engine_version()


# --- adapter against the engine used directly -----------------------------------------------


def test_adapter_matches_a_direct_engine_run(network: Network) -> None:
    """Build the same network by hand with the engine's own API and compare every result."""
    heights = [160.0, 150.0, 175.0, 195.0, 210.0]
    lengths_m, diameters_mm = [400, 600, 500, 450], [200, 200, 150, 150]
    return_gauge_bar = 7.5 - float(p_correction_height_air(160.0))  # 750 000 Pa absolute at r0
    flow_gauge_bar = return_gauge_bar + 1.5

    net = pp.create_empty_network(fluid="water")
    supply = [pp.create_junction(net, flow_gauge_bar, 353.15, height_m=h) for h in heights]
    back = [pp.create_junction(net, return_gauge_bar, 323.15, height_m=h) for h in heights]
    for index in range(4):
        common = {
            "length_km": lengths_m[index] / 1000,
            "inner_diameter_mm": diameters_mm[index],
            "k_mm": 0.1,
            "u_w_per_m2k": 0.6,
            "text_k": 283.15,
            "sections": 5,
        }
        pp.create_pipe_from_parameters(net, supply[index], supply[index + 1], **common)
        pp.create_pipe_from_parameters(net, back[index + 1], back[index], **common)
    for index, heat in enumerate(DESIGN_HEAT_W.values(), start=1):
        pp.create_heat_consumer(net, supply[index], back[index], qext_w=heat, treturn_k=323.15)
    pp.create_circ_pump_const_pressure(
        net, back[0], supply[0], p_flow_bar=flow_gauge_bar, plift_bar=1.5, t_flow_k=353.15
    )
    pp.pipeflow(
        net,
        mode="bidirectional",
        friction_model=pp.SwameeJain(),
        max_iter_hyd=30,
        max_iter_therm=30,
        max_iter_bidirect=30,
        use_numba=False,
    )

    result = MODEL.solve(network, thermal_case())
    assert result.state is AdapterState.COMPLETED
    for index, height in enumerate(heights):
        for prefix, junctions in (("s", supply), ("r", back)):
            direct = net.res_junction.loc[junctions[index]]
            node = result.node(f"{prefix}{index}")
            direct_absolute = (direct.p_bar + float(p_correction_height_air(height))) * 1e5
            assert node.pressure.value_pa == pytest.approx(direct_absolute, rel=1e-12)
            assert node.temperature_k == pytest.approx(direct.t_k, rel=1e-12)
    # The engine lists supply and return pipes alternately, in the order they were created.
    flows = [float(value) for value in net.res_pipe.mdot_from_kg_per_s]
    assert [pipe.mass_flow_kg_s for pipe in result.pipes if pipe.pipe_id.startswith("supply")] == (
        pytest.approx(flows[0::2], rel=1e-12)
    )
    assert [item.mass_flow_kg_s for item in result.consumers] == pytest.approx(
        [float(value) for value in net.res_heat_consumer.mdot_from_kg_per_s], rel=1e-12
    )
    pump = net.res_circ_pump_pressure.iloc[0]
    assert result.supply_points[0].mass_flow_kg_s == pytest.approx(pump.mdot_from_kg_per_s)
    assert result.supply_points[0].heat_supplied_w == pytest.approx(pump.qext_w)


# --- a single pipe against textbook formulas ------------------------------------------------


def single_pipe_network(length_m: float = 1000.0, diameter_m: float = 0.1) -> Network:
    """One supply pipe, one consumer and one return pipe on level ground."""
    pipe = {
        "length_m": length_m,
        "inner_diameter_m": diameter_m,
        "roughness_m": 0.0001,
        "heat_transfer_coefficient_w_m2_k": 2.0,
    }
    node = {"elevation_m": 0.0, "zone_id": "zone"}
    return Network.model_validate(
        {
            "network_id": "syn-network",
            "version": "single-pipe",
            "dataset_id": "syn-definitions",
            "heat_carrier": "hot_water",
            "fluid": {"name": "water", "property_model": "incompressible"},
            "vertical_datum": "test",
            "nodes": [
                {"node_id": "s0", "circuit": "supply", **node},
                {"node_id": "s1", "circuit": "supply", **node},
                {"node_id": "r0", "circuit": "return", **node},
                {"node_id": "r1", "circuit": "return", **node},
            ],
            "pipes": [
                {"pipe_id": "supply", "from_node": "s0", "to_node": "s1", **pipe},
                {"pipe_id": "return", "from_node": "r1", "to_node": "r0", **pipe},
            ],
            "consumers": [
                {
                    "consumer_id": "consumer",
                    "supply_node": "s1",
                    "return_node": "r1",
                    "design_heat_w": 500_000.0,
                }
            ],
        }
    )


def test_friction_loss_matches_the_independent_swamee_jain_calculation() -> None:
    # A hydraulic case has one uniform fluid temperature, so the textbook calculation with the
    # engine's own density and viscosity must reproduce the friction loss.
    network = single_pipe_network()
    result = MODEL.solve(network, hydraulic_case({"consumer": 4.0}))
    assert result.state is AdapterState.COMPLETED, result.reason

    fluid = water()
    density = float(fluid.get_density(353.15))
    viscosity = float(fluid.get_viscosity(353.15))
    velocity = 4.0 / (density * math.pi * 0.1**2 / 4.0)
    reynolds = reynolds_number(density, velocity, 0.1, viscosity)
    factor = friction_factor_swamee_jain(reynolds, 0.0001 / 0.1)
    expected = darcy_weisbach_pressure_drop_pa(factor, 1000.0, 0.1, density, velocity)

    supply = result.pipe("supply")
    assert supply.velocity_m_s == pytest.approx(velocity, rel=1e-9)
    assert supply.friction_pressure_drop_pa == pytest.approx(expected, rel=1e-6)
    # On level ground the whole pressure drop is friction.
    assert supply.pressure_drop_pa == pytest.approx(supply.friction_pressure_drop_pa)
    assert absolute(result, "s0") - absolute(result, "s1") == pytest.approx(expected, rel=1e-6)
    # Both pipes are equal, so the consumer sees the lift minus two friction losses.
    assert result.consumer("consumer").differential_pressure_pa == pytest.approx(
        150_000.0 - 2.0 * expected, rel=1e-6
    )


def test_pipe_outlet_temperature_matches_the_analytic_cooling_curve() -> None:
    # Water that flows through a pipe in ground of temperature T_g cools as
    #   T_out = T_g + (T_in - T_g) exp(-U pi D L / (m cp))
    network = single_pipe_network()
    case = thermal_case(
        {"consumer": 500_000.0},
        numerical={
            "max_iterations_count": 30,
            "pipe_sections_count": 20,
            "friction_model": "swamee_jain",
            "zero_flow_tolerance_kg_s": 1e-6,
        },
    )
    result = MODEL.solve(network, case)
    assert result.state is AdapterState.COMPLETED, result.reason

    supply = result.pipe("supply")
    flow = supply.mass_flow_kg_s
    capacity = float(water().get_heat_capacity(352.0))
    exponent = -2.0 * math.pi * 0.1 * 1000.0 / (flow * capacity)
    expected = 283.15 + (353.15 - 283.15) * math.exp(exponent)
    assert supply.inlet_temperature_k == pytest.approx(353.15)
    assert supply.outlet_temperature_k == pytest.approx(expected, abs=0.01)
    assert result.node("s1").temperature_k == pytest.approx(expected, abs=0.01)
    assert supply.heat_loss_w == pytest.approx(flow * capacity * (353.15 - expected), rel=1e-3)
    balance = result.energy_balance
    assert balance is not None
    assert abs(balance.residual_w) < 1e-3 * balance.heat_supplied_w


def test_a_pipe_defined_against_the_flow_has_negative_flow_and_positive_loss() -> None:
    data = single_pipe_network().to_json_dict()
    data["pipes"][0].update({"from_node": "s1", "to_node": "s0"})
    reversed_network = Network.model_validate(data)
    forward = MODEL.solve(single_pipe_network(), thermal_case({"consumer": 500_000.0}))
    backward = MODEL.solve(reversed_network, thermal_case({"consumer": 500_000.0}))
    assert backward.state is AdapterState.COMPLETED
    ahead, behind = forward.pipe("supply"), backward.pipe("supply")
    # Signed quantities follow the direction in which the pipe is defined.
    assert behind.mass_flow_kg_s == pytest.approx(-ahead.mass_flow_kg_s)
    assert behind.velocity_m_s == pytest.approx(-ahead.velocity_m_s)
    assert behind.pressure_drop_pa == pytest.approx(-ahead.pressure_drop_pa)
    # The physics does not depend on the direction of the definition.
    assert behind.heat_loss_w == pytest.approx(ahead.heat_loss_w)
    assert (behind.inlet_temperature_k, behind.outlet_temperature_k) == pytest.approx(
        (ahead.inlet_temperature_k, ahead.outlet_temperature_k)
    )
    assert behind.heat_loss_w is not None and behind.heat_loss_w > 0.0


# --- A04: elevation and pressure ------------------------------------------------------------


@pytest.mark.requirement("A04")
def test_elevation_changes_local_pressure_but_not_the_flows(network: Network) -> None:
    flat = Network.model_validate(
        {
            **network.to_json_dict(),
            "nodes": [{**node.to_json_dict(), "elevation_m": 160.0} for node in network.nodes],
        }
    )
    hilly_result = MODEL.solve(network, hydraulic_case())
    flat_result = MODEL.solve(flat, hydraulic_case())
    assert hilly_result.state is flat_result.state is AdapterState.COMPLETED

    # The flows, the friction losses and the pump duty are identical. Elevation adds nothing to
    # the circulation demand, because every rise on the supply side is a fall on the return side.
    for hilly_pipe, flat_pipe in zip(hilly_result.pipes, flat_result.pipes, strict=True):
        assert hilly_pipe.mass_flow_kg_s == pytest.approx(flat_pipe.mass_flow_kg_s, rel=1e-12)
        assert hilly_pipe.friction_pressure_drop_pa == pytest.approx(
            flat_pipe.friction_pressure_drop_pa, rel=1e-9, abs=1e-6
        )
    hilly_pump, flat_pump = hilly_result.supply_points[0], flat_result.supply_points[0]
    assert hilly_pump.mass_flow_kg_s == pytest.approx(flat_pump.mass_flow_kg_s, rel=1e-12)
    assert hilly_pump.hydraulic_power_w == pytest.approx(flat_pump.hydraulic_power_w, rel=1e-12)
    assert hilly_pump.pressure_lift_pa == pytest.approx(150_000.0)

    # The local pressure does change, by exactly the weight of the water column between the
    # node and the reference height of 160 m. Checked with the independent hydrostatic formula.
    density = float(water().get_density(353.15))
    for node in network.nodes:
        column = hydrostatic_pressure_difference_pa(
            density, node.elevation_m - 160.0, gravity_m_s2=ENGINE_GRAVITY
        )
        assert absolute(hilly_result, node.node_id) == pytest.approx(
            absolute(flat_result, node.node_id) - column, rel=1e-10
        )
    # The highest node is 50 m above the reference: about 4.8 bar less pressure there.
    assert absolute(flat_result, "r4") - absolute(hilly_result, "r4") == pytest.approx(
        density * ENGINE_GRAVITY * 50.0, rel=1e-10
    )
    # In a hilly pipe the pressure drop is friction plus the static height change.
    climbing = hilly_result.pipe("supply-3-4")
    static = density * ENGINE_GRAVITY * (210.0 - 195.0)
    assert climbing.pressure_drop_pa == pytest.approx(
        climbing.friction_pressure_drop_pa + static, rel=1e-10
    )


def test_pressures_are_reported_in_the_reference_of_the_case(network: Network) -> None:
    in_absolute = MODEL.solve(network, hydraulic_case())
    # The same physical state, stated as a gauge pressure at the return node (160 m).
    gauge_value = 750_000.0 - ambient_pressure_pa(160.0)
    gauge_point = supply_point(return_pressure={"value_pa": gauge_value, "reference": "gauge"})
    # The network record states its pressure reference as an absolute pressure. A case in
    # another reference contradicts it and is refused instead of being converted silently.
    refused = MODEL.solve(network, hydraulic_case(supply_points=[gauge_point]))
    assert refused.state is AdapterState.INVALID_INPUT
    free = Network.model_validate({**network.to_json_dict(), "boundary_conditions": []})
    in_gauge = MODEL.solve(free, hydraulic_case(supply_points=[gauge_point]))
    assert in_gauge.pressure_reference is PressureReference.GAUGE
    for node in network.nodes:
        reported = in_gauge.node(node.node_id).pressure
        assert reported.reference is PressureReference.GAUGE
        assert reported.value_pa + ambient_pressure_pa(node.elevation_m) == pytest.approx(
            absolute(in_absolute, node.node_id), rel=1e-10
        )
    assert [pipe.mass_flow_kg_s for pipe in in_gauge.pipes] == pytest.approx(
        [pipe.mass_flow_kg_s for pipe in in_absolute.pipes]
    )
    # Gauge means relative to the air pressure at the height of the node, about 0.994 bar at
    # 160 m and 0.988 bar at 210 m.
    assert ambient_pressure_pa(0.0) == pytest.approx(101_325.0)
    assert ambient_pressure_pa(210.0) < ambient_pressure_pa(160.0) < 101_325.0


def test_a_higher_static_pressure_shifts_every_node_equally(network: Network) -> None:
    base = MODEL.solve(network, hydraulic_case())
    raised_point = supply_point(return_pressure={"value_pa": 1_050_000.0, "reference": "absolute"})
    raised = MODEL.solve(network, hydraulic_case(supply_points=[raised_point]))
    assert raised.state is AdapterState.INVALID_INPUT
    assert "contradicts the network pressure reference" in raised.reason

    # Without a pressure reference in the network record the case alone sets the level.
    free = Network.model_validate({**network.to_json_dict(), "boundary_conditions": []})
    raised = MODEL.solve(free, hydraulic_case(supply_points=[raised_point]))
    assert raised.state is AdapterState.COMPLETED
    for node in network.nodes:
        shift = absolute(raised, node.node_id) - absolute(base, node.node_id)
        assert shift == pytest.approx(300_000.0, rel=1e-9)
    assert [pipe.mass_flow_kg_s for pipe in raised.pipes] == pytest.approx(
        [pipe.mass_flow_kg_s for pipe in base.pipes]
    )


# --- A13: failure categories ----------------------------------------------------------------


@pytest.mark.requirement("A13")
def test_non_convergence_and_a_pressure_limit_are_different_outcomes(network: Network) -> None:
    # One iteration is not enough for the thermal calculation. That is a numerical failure and
    # the result carries no numbers at all.
    numerical = {
        "max_iterations_count": 1,
        "pipe_sections_count": 5,
        "friction_model": "swamee_jain",
        "zero_flow_tolerance_kg_s": 1e-6,
    }
    unresolved = MODEL.solve(network, thermal_case(numerical=numerical))
    assert unresolved.state is AdapterState.NON_CONVERGED
    assert "did not converge" in unresolved.reason
    assert not unresolved.nodes and not unresolved.checks and unresolved.energy_balance is None

    # With enough iterations the same case completes.
    assert MODEL.solve(network, thermal_case()).state is AdapterState.COMPLETED

    # A static pressure that is too low for the high points is a physical failure. With a
    # return pressure of 4 bar absolute the engine's solution has an absolute pressure below
    # zero at the highest node, 50 m above the reference. Such a state cannot exist, so no node
    # pressures are reported. The check names the node and the value.
    free = Network.model_validate({**network.to_json_dict(), "boundary_conditions": []})
    low = supply_point(return_pressure={"value_pa": 400_000.0, "reference": "absolute"})
    infeasible = MODEL.solve(free, thermal_case(supply_points=[low]))
    assert infeasible.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert infeasible.active_limits == ("positive_absolute_pressure",)
    assert infeasible.nodes == () and infeasible.pipes == ()
    (check,) = infeasible.checks
    assert (check.outcome.value, check.limit, check.unit) == ("fail", 0.0, "Pa (absolute)")
    assert "r4" in check.description and "r4" in infeasible.reason
    # 4 bar less than in the feasible case, where r4 is at about 2.94 bar.
    feasible = MODEL.solve(network, thermal_case())
    assert check.observed == pytest.approx(absolute(feasible, "r4") - 350_000.0, rel=1e-6)
    assert check.observed is not None and check.observed < 0.0
    # The engine's own warning about this is kept.
    assert any("pressure is negative" in warning for warning in infeasible.warnings)
    assert {unresolved.state, infeasible.state, feasible.state} == {
        AdapterState.NON_CONVERGED,
        AdapterState.PHYSICAL_LIMIT_EXCEEDED,
        AdapterState.COMPLETED,
    }


def test_node_pressure_limits_are_checked_with_their_origin(network: Network) -> None:
    limits = {
        "min_node_pressure": {"value_pa": 200_000.0, "reference": "absolute"},
        "max_node_pressure": {"value_pa": 1_600_000.0, "reference": "absolute"},
        "pressure_limit_origin": "invented limits of the synthetic study",
    }
    passing = MODEL.solve(network, thermal_case(**limits))
    assert passing.state is AdapterState.COMPLETED
    low, high, _ = passing.checks
    assert (low.check_id, low.outcome.value) == ("min_node_pressure", "pass")
    assert low.observed == absolute(passing, "r4") and "r4" in low.description
    assert low.margin == pytest.approx(low.observed - 200_000.0)
    assert (high.check_id, high.outcome.value) == ("max_node_pressure", "pass")
    assert high.observed == absolute(passing, "s1") and "s1" in high.description
    assert low.threshold_origin == high.threshold_origin == limits["pressure_limit_origin"]
    assert low.unit == "Pa (absolute)"

    # A stricter minimum is violated at the highest node, and the result says where.
    strict = {**limits, "min_node_pressure": {"value_pa": 350_000.0, "reference": "absolute"}}
    failing = MODEL.solve(network, thermal_case(**strict))
    assert failing.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert failing.active_limits == ("min_node_pressure",)
    assert failing.checks[0].margin == pytest.approx(absolute(failing, "r4") - 350_000.0)
    assert failing.nodes and failing.energy_balance is not None

    # A limit in another reference than the case is refused, not converted silently.
    mixed = {**limits, "min_node_pressure": {"value_pa": 100_000.0, "reference": "gauge"}}
    refused = MODEL.solve(network, thermal_case(**mixed))
    assert refused.state is AdapterState.INVALID_INPUT
    assert "inconsistent pressure references" in refused.reason


# --- A06: zero and reversed flow ------------------------------------------------------------


@pytest.mark.requirement("A06")
def test_a_branch_without_flow_has_no_temperature(network: Network) -> None:
    # Consumer 4 takes no heat. The pipes to it carry no water, and a steady state model has no
    # temperature for standing water. The engine would report its default of 293.15 K there.
    heats = {**DESIGN_HEAT_W, "consumer-4": 0.0}
    result = MODEL.solve(network, thermal_case(heats))
    assert result.state is AdapterState.COMPLETED, result.reason

    closed = result.consumer("consumer-4")
    assert closed.closed and closed.mass_flow_kg_s == 0.0 and closed.delivered_heat_w == 0.0
    assert closed.supply_temperature_k is None and closed.return_temperature_k is None
    for pipe_id in ("supply-3-4", "return-4-3"):
        pipe = result.pipe(pipe_id)
        assert pipe.stagnant and abs(pipe.mass_flow_kg_s) <= 1e-6
        assert pipe.inlet_temperature_k is None and pipe.outlet_temperature_k is None
        assert pipe.heat_loss_w is None
    assert result.node("s4").temperature_k is None and result.node("r4").temperature_k is None

    # No number in the result is the engine default, and none is missing where water flows.
    every_temperature = [node.temperature_k for node in result.nodes]
    every_temperature += [pipe.outlet_temperature_k for pipe in result.pipes]
    assert 293.15 not in [value for value in every_temperature if value is not None]
    for node_id in ("s0", "s1", "s2", "s3", "r0", "r1", "r2", "r3"):
        assert result.node(node_id).temperature_k is not None
    assert any("stagnant" in warning for warning in result.warnings)

    # The rest of the network still balances.
    balance = result.energy_balance
    assert balance is not None
    assert balance.heat_delivered_w == pytest.approx(2_600_000.0)
    assert abs(balance.residual_w) < 1e-3 * balance.heat_supplied_w
    # The pressure at the dead end is still defined, by the water column.
    assert math.isfinite(absolute(result, "r4"))


@pytest.mark.requirement("A06")
def test_a_network_without_any_flow_is_reported_as_such(network: Network) -> None:
    result = MODEL.solve(network, thermal_case(dict.fromkeys(DESIGN_HEAT_W, 0.0)))
    assert result.state is AdapterState.COMPLETED, result.reason
    assert all(consumer.closed for consumer in result.consumers)
    assert all(pipe.stagnant for pipe in result.pipes)
    assert all(node.temperature_k is None for node in result.nodes)
    point = result.supply_points[0]
    assert abs(point.mass_flow_kg_s) <= 1e-6
    assert point.heat_supplied_w is None and point.return_temperature_k is None
    assert result.energy_balance is not None
    assert result.energy_balance.heat_supplied_w == 0.0
    # Pressures are purely hydrostatic and still finite.
    assert all(math.isfinite(node.pressure.value_pa) for node in result.nodes)

    hydraulic = MODEL.solve(network, hydraulic_case(dict.fromkeys(FIXED_FLOW_KG_S, 0.0)))
    assert hydraulic.state is AdapterState.COMPLETED
    assert all(pipe.stagnant and pipe.velocity_m_s == 0.0 for pipe in hydraulic.pipes)


@pytest.mark.requirement("A06")
def test_reversed_flow_through_a_consumer_is_refused(network: Network) -> None:
    feeding = MODEL.solve(network, thermal_case({**DESIGN_HEAT_W, "consumer-2": -1_200_000.0}))
    assert feeding.state is AdapterState.INVALID_INPUT
    assert "feeding heat in through a consumer" in feeding.reason and not feeding.nodes

    backwards = MODEL.solve(network, hydraulic_case({**FIXED_FLOW_KG_S, "consumer-2": -9.0}))
    assert backwards.state is AdapterState.INVALID_INPUT
    assert "reversed flow through a consumer" in backwards.reason

    loads = [
        {"consumer_id": name, "heat_w": 100.0, "mass_flow_kg_s": 0.0} for name in DESIGN_HEAT_W
    ]
    no_flow = MODEL.solve(network, thermal_case(loads=loads))
    assert no_flow.state is AdapterState.INVALID_INPUT
    assert "heat cannot be delivered without flow" in no_flow.reason


def test_engine_warnings_are_kept(network: Network) -> None:
    # A return pressure of 5 bar absolute leaves the highest node below air pressure but above
    # zero. The engine warns about the negative gauge pressure, and the warning is passed on.
    free = Network.model_validate({**network.to_json_dict(), "boundary_conditions": []})
    low = supply_point(return_pressure={"value_pa": 500_000.0, "reference": "absolute"})
    result = MODEL.solve(free, thermal_case(supply_points=[low]))
    assert result.state is AdapterState.COMPLETED
    assert 0.0 < absolute(result, "r4") < ambient_pressure_pa(210.0)
    assert any("pressure is negative" in warning for warning in result.warnings)
    assert MODEL.solve(network, thermal_case()).warnings == ()
