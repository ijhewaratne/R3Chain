"""Conformance suite of the network port.

Every network adapter must pass every test here. To put a new engine under the suite, add it to
``NETWORK_ADAPTERS``. The suite uses the synthetic four consumer network and checks the contract
and the conservation laws. Each adapter has its own tests against reference results.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest

from r3chain.adapters.pandapipes_network import PandapipesNetworkModel
from r3chain.domain.records.network import HeatCarrier, Network
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState
from r3chain.domain.units import PressureReference
from r3chain.ports.network import CalculationMode, NetworkModel
from tests.conftest import DESIGN_HEAT_W, hydraulic_case, supply_point, thermal_case


class Adapter(NamedTuple):
    name: str
    make_model: Callable[[], NetworkModel]


NETWORK_ADAPTERS = [
    Adapter("pandapipes", PandapipesNetworkModel),
]


@pytest.fixture(params=NETWORK_ADAPTERS, ids=lambda adapter: adapter.name)
def model(request: pytest.FixtureRequest) -> NetworkModel:
    return request.param.make_model()  # type: ignore[no-any-return]


@pytest.fixture
def network(scenario: Scenario) -> Network:
    return scenario.networks[0]


def test_capabilities_are_declared(model: NetworkModel) -> None:
    capabilities = model.capabilities()
    assert capabilities.engine and capabilities.engine_version and capabilities.fluid_model
    assert HeatCarrier.HOT_WATER in capabilities.heat_carriers
    assert set(capabilities.calculations) >= {CalculationMode.HYDRAULIC, CalculationMode.THERMAL}
    assert capabilities.friction_models and capabilities.max_supply_points >= 1


def test_a_thermal_case_conserves_mass_and_energy(model: NetworkModel, network: Network) -> None:
    case = thermal_case()
    result = model.solve(network, case)
    assert result.state is AdapterState.COMPLETED, result.reason
    assert (result.network_id, result.case_id) == (network.network_id, case.case_id)
    assert result.calculation is CalculationMode.THERMAL
    # Every node has a finite pressure in the reference the case used.
    assert {node.node_id for node in result.nodes} == network.node_ids()
    assert result.pressure_reference is PressureReference.ABSOLUTE
    for node in result.nodes:
        assert node.pressure.reference is PressureReference.ABSOLUTE
        assert math.isfinite(node.pressure.value_pa) and node.temperature_k is not None

    # Mass is conserved at every node.
    assert result.mass_balance_residual_kg_s is not None
    assert result.mass_balance_residual_kg_s < 1e-9
    supplied_flow = result.supply_points[0].mass_flow_kg_s
    assert sum(item.mass_flow_kg_s for item in result.consumers) == pytest.approx(supplied_flow)

    # Every consumer receives what the case asked for.
    for consumer in result.consumers:
        assert not consumer.closed
        assert consumer.delivered_heat_w == pytest.approx(DESIGN_HEAT_W[consumer.consumer_id])
        assert consumer.return_temperature_k == pytest.approx(323.15)
        assert consumer.supply_temperature_k is not None
        assert 323.15 < consumer.supply_temperature_k < 353.15
        assert consumer.differential_pressure_pa > 0.0

    # Energy is conserved: the heat fed in is the heat delivered plus the pipe losses. The heat
    # of the ideal supply point is reported, so nothing is hidden.
    balance = result.energy_balance
    assert balance is not None
    assert balance.heat_delivered_w == pytest.approx(sum(DESIGN_HEAT_W.values()))
    assert balance.heat_supplied_w == result.supply_points[0].heat_supplied_w
    assert balance.pipe_heat_loss_w > 0.0
    assert balance.heat_supplied_w > balance.heat_delivered_w
    assert abs(balance.residual_w) < 1e-3 * balance.heat_supplied_w


def test_a_hydraulic_case_reports_no_temperatures(model: NetworkModel, network: Network) -> None:
    result = model.solve(network, hydraulic_case())
    assert result.state is AdapterState.COMPLETED, result.reason
    assert result.energy_balance is None
    assert result.supply_points[0].heat_supplied_w is None
    assert all(node.temperature_k is None for node in result.nodes)
    assert all(pipe.heat_loss_w is None for pipe in result.pipes)
    assert result.supply_points[0].mass_flow_kg_s == pytest.approx(23.0)
    assert result.mass_balance_residual_kg_s is not None
    assert result.mass_balance_residual_kg_s < 1e-9


def test_the_same_case_always_gives_the_same_result(model: NetworkModel, network: Network) -> None:
    case = thermal_case()
    first = model.solve(network, case)
    # Other calculations in between, including failing ones, must leave no trace.
    model.solve(network, hydraulic_case())
    model.solve(network, thermal_case({**DESIGN_HEAT_W, "consumer-1": -1.0}))
    assert model.solve(network, case) == first
    assert type(model)().solve(network, case) == first
    assert first.input_hash == model.solve(network, case).input_hash
    assert first.input_hash != model.solve(network, hydraulic_case()).input_hash


def unsupported(model: NetworkModel, network: Network, **case_changes: Any) -> str:
    result = model.solve(network, thermal_case(**case_changes))
    assert result.state is AdapterState.UNSUPPORTED_MODEL
    assert not result.nodes and not result.pipes and result.energy_balance is None
    return result.reason


def test_what_is_not_modelled_is_unsupported(model: NetworkModel, network: Network) -> None:
    capabilities = model.capabilities()
    if HeatCarrier.STEAM not in capabilities.heat_carriers:
        steam = network.model_copy(update={"heat_carrier": HeatCarrier.STEAM})
        assert "steam" in unsupported(model, steam)
    if not capabilities.supports_equipment:
        pump = {"equipment_id": "booster", "kind": "pump", "node_ids": ["s2", "s3"]}
        with_pump = Network.model_validate({**network.to_json_dict(), "equipment": [pump]})
        assert "pump" in unsupported(model, with_pump)
    if capabilities.max_supply_points == 1:
        second = supply_point(supply_point_id="east", supply_node="s3", return_node="r3")
        reason = unsupported(model, network, supply_points=[supply_point(), second])
        assert "supply point" in reason


def invalid(model: NetworkModel, network: Network, case: Any) -> str:
    result = model.solve(network, case)
    assert result.state is AdapterState.INVALID_INPUT, result.state
    assert not result.nodes and not result.consumers
    return result.reason


def test_invalid_cases_are_rejected_with_a_reason(model: NetworkModel, network: Network) -> None:
    partial = {name: heat for name, heat in DESIGN_HEAT_W.items() if name != "consumer-4"}
    assert "consumer-4" in invalid(model, network, thermal_case(partial))
    assert "unknown consumer" in invalid(
        model, network, thermal_case({**DESIGN_HEAT_W, "consumer-9": 1.0})
    )
    assert "syn-other" in invalid(model, network, thermal_case(network_id="syn-other"))
    # Return temperature control needs a thermal calculation.
    loads = [
        {"consumer_id": name, "heat_w": heat, "return_temperature_k": 323.15}
        for name, heat in DESIGN_HEAT_W.items()
    ]
    assert "thermal calculation" in invalid(model, network, hydraulic_case(loads=loads))
    assert "pressure lift" in invalid(
        model, network, thermal_case(supply_points=[supply_point(pressure_lift_pa=0.0)])
    )
    assert "not in the network" in invalid(
        model, network, thermal_case(supply_points=[supply_point(supply_node="nowhere")])
    )
    assert "return circuit" in invalid(
        model, network, thermal_case(supply_points=[supply_point(return_node="s1")])
    )
