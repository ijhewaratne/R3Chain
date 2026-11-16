"""Conformance suite of the source port.

Every source adapter must pass every test here. To put a new engine under the suite, add it to
``SOURCE_ADAPTERS`` with a function that builds a request it can complete. PyDoublet is added
here when its package and reference scenario are supplied (open decision O03).

The suite checks the contract, not the physics. Each adapter has its own tests against reference
results of its engine.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import NamedTuple

import pytest

from r3chain.adapters.source_synthetic import SyntheticSourceModel
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState
from r3chain.domain.units import unit_for_field
from r3chain.ports.source import (
    OutputSemantics,
    SourceModel,
    SourceRequest,
    SourceWell,
)
from tests.conftest import source_request


class Adapter(NamedTuple):
    name: str
    make_model: Callable[[], SourceModel]
    make_request: Callable[[Scenario], SourceRequest]


SOURCE_ADAPTERS = [
    Adapter("synthetic", SyntheticSourceModel, source_request),
]


@pytest.fixture(params=SOURCE_ADAPTERS, ids=lambda adapter: adapter.name)
def adapter(request: pytest.FixtureRequest) -> Adapter:
    return request.param  # type: ignore[no-any-return]


@pytest.fixture
def model(adapter: Adapter) -> SourceModel:
    return adapter.make_model()


@pytest.fixture
def valid(adapter: Adapter, scenario: Scenario) -> SourceRequest:
    return adapter.make_request(scenario)


def test_capabilities_are_declared(model: SourceModel) -> None:
    capabilities = model.capabilities()
    assert capabilities.engine and capabilities.engine_version and capabilities.fidelity
    assert capabilities.model_families and capabilities.well_configurations
    for family in capabilities.model_families:
        assert capabilities.supports(family)
        assert family in capabilities.required_reservoir_parameters
    assert not capabilities.supports("a-family-nobody-supports")
    declared = [
        *capabilities.required_reservoir_parameters.values(),
        *capabilities.optional_reservoir_parameters.values(),
    ]
    for names in declared:
        for name in names:
            assert unit_for_field(name) is not None, f"{name} carries no unit"


@pytest.mark.requirement("W10")
def test_a_completed_result_is_complete_and_states_its_origin(
    model: SourceModel, valid: SourceRequest
) -> None:
    capabilities = model.capabilities()
    result = model.evaluate(valid)
    assert result.state is AdapterState.COMPLETED, result.reason
    # The result says which engine, version, fidelity and model family produced it.
    assert (result.engine, result.engine_version) == (
        capabilities.engine,
        capabilities.engine_version,
    )
    assert result.fidelity == capabilities.fidelity
    assert result.model_family == valid.model_family
    assert result.synthetic == capabilities.synthetic
    # It is tied to exactly the inputs that produced it.
    assert result.source_scenario_id == valid.source_scenario_id
    assert result.sample_id == valid.sample_id
    assert result.input_hash == valid.input_hash()

    performance = result.performance
    assert performance is not None
    numbers = [
        performance.mass_flow_kg_s,
        performance.wellhead_production_temperature_k,
        performance.injection_temperature_k,
        performance.geothermal_extracted_heat_w,
        performance.electrical_pump_power_w,
    ]
    assert all(math.isfinite(number) for number in numbers)
    assert performance.wellhead_production_temperature_k > performance.injection_temperature_k
    assert performance.injection_temperature_k == valid.operating_point.injection_temperature_k
    if performance.mass_flow_semantics is OutputSemantics.REQUESTED_VALUE_ACHIEVED:
        assert performance.mass_flow_kg_s == valid.operating_point.requested_mass_flow_kg_s
    # Upstream economics are either calculated or clearly declared absent.
    if not capabilities.provides_economics:
        assert not result.economics_calculated


def test_the_same_request_always_gives_the_same_result(
    model: SourceModel, valid: SourceRequest
) -> None:
    before = valid.input_hash()
    first = model.evaluate(valid)
    # Other evaluations in between, including failing ones, must leave no trace.
    model.evaluate(valid.at(valid.operating_point.requested_mass_flow_kg_s * 0.5, 320.0))
    model.evaluate(valid.at(-1.0, 320.0))
    model.evaluate(valid.model_copy(update={"model_family": "a-family-nobody-supports"}))
    again = model.evaluate(valid)
    assert again == first
    # A second instance of the adapter gives the same result. Nothing is shared.
    assert type(model)().evaluate(valid) == first
    # The request itself is never modified.
    assert valid.input_hash() == before


@pytest.mark.requirement("W09")
def test_an_unknown_model_family_is_unsupported(model: SourceModel, valid: SourceRequest) -> None:
    result = model.evaluate(valid.model_copy(update={"model_family": "a-family-nobody-supports"}))
    assert result.state is AdapterState.UNSUPPORTED_MODEL
    # Never a zero and never a placeholder that looks like a result.
    assert result.performance is None
    assert result.reason


@pytest.mark.requirement("W09")
def test_an_unsupported_well_configuration_is_unsupported(
    model: SourceModel, valid: SourceRequest
) -> None:
    injection = next(well for well in valid.wells if well.role.value == "injection")
    extra = SourceWell(
        well_id="second-injection",
        role=injection.role,
        vertical_depth_m=injection.vertical_depth_m,
        measured_length_m=injection.measured_length_m,
    )
    request = valid.model_copy(
        update={
            "well_configuration_id": "a-configuration-nobody-supports",
            "wells": (*valid.wells, extra),
        }
    )
    result = model.evaluate(request)
    assert result.state is AdapterState.UNSUPPORTED_MODEL
    assert result.performance is None and result.reason


@pytest.mark.parametrize("mass_flow", [-5.0, 0.0])
def test_a_flow_that_is_not_positive_never_completes(
    model: SourceModel, valid: SourceRequest, mass_flow: float
) -> None:
    result = model.evaluate(valid.at(mass_flow, valid.operating_point.injection_temperature_k))
    assert result.state is not AdapterState.COMPLETED
    assert result.performance is None and result.reason
    if mass_flow < 0.0:
        assert result.state is AdapterState.INVALID_INPUT


def test_a_missing_required_parameter_is_invalid_input(
    model: SourceModel, valid: SourceRequest
) -> None:
    required = model.capabilities().required_reservoir_parameters[valid.model_family]
    for name in required:
        parameters = {
            key: value for key, value in valid.reservoir_parameters.items() if key != name
        }
        result = model.evaluate(valid.model_copy(update={"reservoir_parameters": parameters}))
        assert result.state is AdapterState.INVALID_INPUT, name
        assert name in result.reason
        assert result.performance is None
