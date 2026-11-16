"""The synthetic source test model and the source performance envelope.

Expected values are derived by hand in the comments. They do not call the adapter's own code.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from pydantic import ValidationError
from tests.conftest import changed, source_request

from r3chain.adapters.source_synthetic import SyntheticSourceModel
from r3chain.coupling.envelope import SourceEnvelope, build_envelope, lookup
from r3chain.domain.evidence import NotEstablished
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState
from r3chain.ports.source import (
    SourceNumericalSettings,
    SourceOperatingPoint,
    SourceRequest,
)

MODEL = SyntheticSourceModel()

# Site Alpha of the synthetic study, and the constants of the test model.
T_RESERVOIR = 378.15
LENGTH_PRODUCTION_WELL = 3250.0
PERMEABILITY, THICKNESS, NET_TO_GROSS = 1.0e-13, 40.0, 0.25
CP, DENSITY, VISCOSITY = 4180.0, 1000.0, 3.0e-4
LOSS_PER_METRE, T_SURFACE, PUMP_EFFICIENCY = 2.0, 283.15, 0.6
WELL_RADIUS, DRAINAGE_RADIUS = 0.1, 1000.0


def closed_form_wellhead_temperature(mass_flow: float) -> float:
    """Solve T = T_res - G ((T_res + T) / 2 - T_g) / (m cp) for T by hand.

    With a = G / (2 m cp):   T (1 + a) = T_res (1 - a) + 2 a T_g
    """
    a = LOSS_PER_METRE * LENGTH_PRODUCTION_WELL / (2.0 * mass_flow * CP)
    ground = (T_SURFACE + T_RESERVOIR) / 2.0
    return (T_RESERVOIR * (1.0 - a) + 2.0 * a * ground) / (1.0 + a)


@pytest.fixture
def request_alpha(scenario: Scenario) -> SourceRequest:
    return source_request(scenario)


def with_iterations(request: SourceRequest, count: int) -> SourceRequest:
    settings = SourceNumericalSettings(
        max_iterations_count=count, temperature_tolerance_delta_k=1e-9
    )
    return request.model_copy(update={"numerical": settings})


# --- the test model against hand derived values ---------------------------------------------


def test_performance_matches_the_hand_calculation(request_alpha: SourceRequest) -> None:
    # 10 kg/s, injection at 313.15 K.
    #
    # Wellhead temperature:
    #   G   = 2.0 W/(m K) * 3250 m = 6500 W/K
    #   a   = 6500 / (2 * 10 * 4180) = 0.0777512
    #   T_g = (283.15 + 378.15) / 2 = 330.65 K
    #   T   = (378.15 * 0.9222488 + 0.1555024 * 330.65) / 1.0777512 = 371.2965 K
    # Heat:
    #   Q   = 10 * 4180 * (371.2965 - 313.15) = 2 430 524 W
    # Hydraulics:
    #   PI  = 2 pi * 1e-13 * (40 * 0.25) * 1000 / (3e-4 * ln(10000)) = 2.273961e-6 kg/(s Pa)
    #   dp  = 10 / 2.273961e-6 = 4.39761e6 Pa
    #   P   = 10 * 2 * 4.39761e6 / (1000 * 0.6) = 146 587 W
    result = MODEL.evaluate(request_alpha)
    assert result.state is AdapterState.COMPLETED
    performance = result.performance
    assert performance is not None
    assert performance.wellhead_production_temperature_k == pytest.approx(371.2965, abs=1e-4)
    assert performance.geothermal_extracted_heat_w == pytest.approx(2_430_524, rel=1e-5)
    assert performance.production_drawdown_pa == pytest.approx(4.39761e6, rel=1e-5)
    assert performance.electrical_pump_power_w == pytest.approx(146_587, rel=1e-5)

    # The same numbers from the formulas written out independently in this test.
    wellhead = closed_form_wellhead_temperature(10.0)
    productivity = (
        2
        * math.pi
        * PERMEABILITY
        * THICKNESS
        * NET_TO_GROSS
        * DENSITY
        / (VISCOSITY * math.log(DRAINAGE_RADIUS / WELL_RADIUS))
    )
    assert performance.wellhead_production_temperature_k == pytest.approx(wellhead, abs=1e-8)
    assert performance.geothermal_extracted_heat_w == pytest.approx(
        10.0 * CP * (wellhead - 313.15), rel=1e-10
    )
    assert performance.electrical_pump_power_w == pytest.approx(
        10.0 * 2.0 * (10.0 / productivity) / (DENSITY * PUMP_EFFICIENCY), rel=1e-12
    )
    # A test engine never passes as a real one.
    assert result.synthetic and "Not PyDoublet" in result.fidelity
    assert not result.economics_calculated
    assert "production_wellhead_pressure" in result.not_calculated
    assert result.raw_request == request_alpha.to_json_dict()


def test_lower_flow_loses_more_temperature_in_the_well(request_alpha: SourceRequest) -> None:
    temperatures = []
    for flow in (2.0, 5.0, 10.0, 13.0):
        performance = MODEL.evaluate(request_alpha.at(flow, 313.15)).performance
        assert performance is not None
        assert performance.wellhead_production_temperature_k == pytest.approx(
            closed_form_wellhead_temperature(flow), abs=1e-7
        )
        temperatures.append(performance.wellhead_production_temperature_k)
    assert temperatures == sorted(temperatures)
    assert all(value < T_RESERVOIR for value in temperatures)


@pytest.mark.requirement("A13")
def test_a_numerical_failure_is_not_a_physical_one(request_alpha: SourceRequest) -> None:
    # At 0.5 kg/s the equation still has a perfectly good solution, but the fixed point
    # iteration diverges because its contraction factor a = 6500 / (2 * 0.5 * 4180) = 1.555
    # is above one. That is a failure of the numerical method. It says nothing about the well.
    assert 300.0 < closed_form_wellhead_temperature(0.5) < T_RESERVOIR
    diverged = MODEL.evaluate(request_alpha.at(0.5, 313.15))
    assert diverged.state is AdapterState.NON_CONVERGED
    assert diverged.performance is None and diverged.active_limits == ()
    assert "did not converge" in diverged.reason

    # Too few iterations at a well behaved operating point is the same kind of failure, and
    # more iterations resolve it.
    short = MODEL.evaluate(with_iterations(request_alpha, 3))
    assert short.state is AdapterState.NON_CONVERGED and short.iterations_count == 3
    assert MODEL.evaluate(with_iterations(request_alpha, 50)).state is AdapterState.COMPLETED

    # A physical limit is a different state, with the limit that was hit.
    limited = MODEL.evaluate(request_alpha.at(20.0, 313.15))
    assert limited.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert limited.active_limits == ("max_drawdown_pa",)
    assert {diverged.state, limited.state, AdapterState.COMPLETED} == {
        AdapterState.NON_CONVERGED,
        AdapterState.PHYSICAL_LIMIT_EXCEEDED,
        AdapterState.COMPLETED,
    }


def test_operating_limits_are_reported_with_observed_value_and_margin(
    request_alpha: SourceRequest,
) -> None:
    # Drawdown at 20 kg/s is 20 / 2.273961e-6 = 8.7952e6 Pa, above the limit of 6.0e6 Pa.
    result = MODEL.evaluate(request_alpha.at(20.0, 313.15))
    check = next(item for item in result.checks if item.check_id == "max_drawdown_pa")
    assert check.outcome.value == "fail"
    assert check.observed == pytest.approx(8.7952e6, rel=1e-4)
    assert check.limit == 6.0e6
    assert check.margin == pytest.approx(6.0e6 - 8.7952e6, rel=1e-4)
    assert check.unit == "Pa" and "source-alpha" in (check.threshold_origin or "")

    too_cold = MODEL.evaluate(request_alpha.at(10.0, 295.0))
    assert too_cold.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert too_cold.active_limits == ("min_injection_temperature_k",)

    too_much = MODEL.evaluate(request_alpha.at(30.0, 313.15))
    assert set(too_much.active_limits) == {"max_mass_flow_kg_s", "max_drawdown_pa"}

    # Injecting hotter than the wellhead extracts no heat.
    no_heat = MODEL.evaluate(request_alpha.at(10.0, 375.0))
    assert no_heat.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert no_heat.active_limits == ("heat_extraction",)

    # A completed result lists the limits it passed, with their margins.
    passed = MODEL.evaluate(request_alpha)
    assert {item.check_id: item.outcome.value for item in passed.checks} == {
        "max_mass_flow_kg_s": "pass",
        "min_injection_temperature_k": "pass",
        "max_drawdown_pa": "pass",
        "heat_extraction": "pass",
    }


def test_invalid_parameters_are_rejected(request_alpha: SourceRequest) -> None:
    def state_with(**parameters: float) -> tuple[AdapterState, str]:
        merged = {**request_alpha.reservoir_parameters, **parameters}
        result = MODEL.evaluate(request_alpha.model_copy(update={"reservoir_parameters": merged}))
        return result.state, result.reason

    assert state_with(permeability_m2=0.0) == (
        AdapterState.INVALID_INPUT,
        "permeability_m2 must be positive",
    )
    state, reason = state_with(net_to_gross_fraction=1.2)
    assert state is AdapterState.INVALID_INPUT and "cannot exceed 1" in reason
    cold = MODEL.evaluate(request_alpha.at(10.0, -5.0))
    assert cold.state is AdapterState.INVALID_INPUT and "above 0 K" in cold.reason
    with pytest.raises(ValidationError, match="no unit suffix"):
        SourceRequest.model_validate(
            {**request_alpha.to_json_dict(), "reservoir_parameters": {"porosity": 0.1}}
        )


# --- building a request from a scenario -----------------------------------------------------


def build(scenario: Scenario) -> SourceRequest:
    return source_request(scenario)


def test_a_request_resolves_the_known_inputs(scenario: Scenario) -> None:
    request = build(scenario)
    assert request.source_scenario_id == "source-alpha" and request.site_id == "alpha"
    assert request.reservoir_parameters == {
        "reservoir_temperature_k": 378.15,
        "reservoir_thickness_m": 40.0,
        "net_to_gross_fraction": 0.25,
        "permeability_m2": 1.0e-13,
        "max_mass_flow_kg_s": 25.0,
        "min_injection_temperature_k": 303.15,
        "max_drawdown_pa": 6.0e6,
    }
    assert [(well.role.value, well.measured_length_m) for well in request.wells] == [
        ("production", 3250.0),
        ("injection", 3400.0),
    ]


@pytest.mark.requirement("A12")
def test_an_unknown_required_input_stops_the_request(scenario_data: dict[str, Any]) -> None:
    unknown = {"state": "unknown", "reason": "no core data yet", "owner": "geologist"}
    base = "well_scenarios[source-alpha]"

    path = f"{base}.reservoir_parameters[permeability_m2]"
    with pytest.raises(NotEstablished) as raised:
        build(Scenario.model_validate(changed(scenario_data, path, unknown)))
    assert [item.field_path for item in raised.value.missing] == [path]

    path = f"{base}.wells[alpha-production].measured_length"
    with pytest.raises(NotEstablished) as raised:
        build(Scenario.model_validate(changed(scenario_data, path, unknown)))
    assert [item.field_path for item in raised.value.missing] == [path]

    # An optional limit that is unknown is left out. It is not replaced by a default.
    path = f"{base}.reservoir_parameters[max_drawdown_pa]"
    request = build(Scenario.model_validate(changed(scenario_data, path, unknown)))
    assert "max_drawdown_pa" not in request.reservoir_parameters
    assert MODEL.evaluate(request.at(20.0, 313.15)).state is AdapterState.COMPLETED


# --- performance envelope -------------------------------------------------------------------

FLOWS = [2.0, 6.0, 10.0, 14.0, 18.0]
TEMPERATURES = [303.15, 313.15, 323.15, 333.15]


@pytest.fixture
def envelope(request_alpha: SourceRequest) -> SourceEnvelope:
    return build_envelope(MODEL, request_alpha, FLOWS, TEMPERATURES)


def test_envelope_holds_one_engine_result_per_grid_point(
    envelope: SourceEnvelope, request_alpha: SourceRequest
) -> None:
    assert len(envelope.points) == 20
    assert (envelope.engine, envelope.synthetic) == ("r3chain-synthetic-source", True)
    # The drawdown limit of 6 MPa allows up to 13.6 kg/s, so 14 and 18 kg/s are infeasible.
    assert envelope.state_counts() == {"completed": 12, "physical_limit_exceeded": 8}
    for flow_index, flow in enumerate(FLOWS):
        for temperature_index, temperature in enumerate(TEMPERATURES):
            point = envelope.point(flow_index, temperature_index)
            exact = MODEL.evaluate(request_alpha.at(flow, temperature))
            assert (point.mass_flow_kg_s, point.injection_temperature_k) == (flow, temperature)
            assert point.state is exact.state
            if exact.performance is None:
                assert point.geothermal_extracted_heat_w is None
                assert point.active_limits == ("max_drawdown_pa",)
            else:
                assert (
                    point.geothermal_extracted_heat_w
                    == exact.performance.geothermal_extracted_heat_w
                )
    best = envelope.best_point()
    assert best is not None
    assert (best.mass_flow_kg_s, best.injection_temperature_k) == (10.0, 303.15)


def test_envelope_is_only_valid_where_the_engine_completed(envelope: SourceEnvelope) -> None:
    # Cells between 2, 6 and 10 kg/s are validated. A cell that touches 14 kg/s is not.
    cells = {(cell.flow_index, cell.temperature_index) for cell in envelope.validated_cells}
    assert cells == {(flow, temperature) for flow in (0, 1) for temperature in (0, 1, 2)}
    assert envelope.approximation_error is not None
    assert envelope.approximation_error.cells_checked_count == 6


def test_interpolation_is_exact_at_grid_points_and_bounded_between(
    envelope: SourceEnvelope, request_alpha: SourceRequest
) -> None:
    error = envelope.approximation_error
    assert error is not None

    at_node = envelope.interpolate(6.0, 313.15)
    exact = MODEL.evaluate(request_alpha.at(6.0, 313.15)).performance
    assert at_node is not None and exact is not None
    assert at_node.geothermal_extracted_heat_w == pytest.approx(exact.geothermal_extracted_heat_w)
    assert at_node.approximation == "bilinear_interpolation"

    # At a cell centre the interpolation differs from the engine by at most the recorded error.
    centre = envelope.interpolate(8.0, 318.15)
    exact = MODEL.evaluate(request_alpha.at(8.0, 318.15)).performance
    assert centre is not None and exact is not None
    heat_error = abs(centre.geothermal_extracted_heat_w - exact.geothermal_extracted_heat_w)
    assert 0.0 < heat_error <= error.max_abs_heat_error_w
    assert (
        abs(centre.wellhead_production_temperature_k - exact.wellhead_production_temperature_k)
        <= error.max_abs_temperature_error_delta_k
    )
    assert centre.error == error

    # Pump power grows with the square of the flow. Between 2 and 6 kg/s the midpoint of a
    # straight line is (4 + 36) / 2 = 20 against the exact 16, an error of 25 percent.
    assert error.max_rel_pump_power_error_fraction == pytest.approx(0.25, rel=1e-9)


def test_envelope_never_extrapolates(
    envelope: SourceEnvelope, request_alpha: SourceRequest
) -> None:
    assert envelope.interpolate(1.0, 313.15) is None  # below the grid
    assert envelope.interpolate(8.0, 340.0) is None  # above the grid
    assert envelope.interpolate(12.0, 313.15) is None  # inside the grid, not validated

    inside = lookup(MODEL, envelope, request_alpha, 8.0, 318.15)
    assert inside.origin == "envelope" and inside.result is None

    # Outside the validated domain the question goes back to the engine.
    outside = lookup(MODEL, envelope, request_alpha, 12.0, 313.15)
    assert outside.origin == "engine" and outside.interpolated is None
    assert outside.result is not None and outside.result.state is AdapterState.COMPLETED
    beyond = lookup(MODEL, envelope, request_alpha, 16.0, 313.15)
    assert beyond.result is not None
    assert beyond.result.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED


def test_a_finer_grid_has_a_smaller_recorded_error(request_alpha: SourceRequest) -> None:
    coarse = build_envelope(MODEL, request_alpha, [2.0, 6.0, 10.0], TEMPERATURES)
    fine = build_envelope(MODEL, request_alpha, [2.0, 4.0, 6.0, 8.0, 10.0], TEMPERATURES)
    assert coarse.approximation_error is not None and fine.approximation_error is not None
    assert (
        fine.approximation_error.max_abs_heat_error_w
        < coarse.approximation_error.max_abs_heat_error_w
    )
    assert (
        fine.approximation_error.max_rel_pump_power_error_fraction
        < coarse.approximation_error.max_rel_pump_power_error_fraction
    )


def test_an_envelope_without_a_feasible_cell_offers_no_interpolation(
    request_alpha: SourceRequest,
) -> None:
    infeasible = build_envelope(MODEL, request_alpha, [16.0, 18.0], [303.15, 313.15])
    assert infeasible.state_counts() == {"physical_limit_exceeded": 4}
    assert infeasible.validated_cells == () and infeasible.approximation_error is None
    assert infeasible.best_point() is None
    assert infeasible.interpolate(17.0, 308.15) is None


def test_envelope_grid_must_be_increasing(request_alpha: SourceRequest) -> None:
    with pytest.raises(ValidationError, match="strictly increasing"):
        build_envelope(MODEL, request_alpha, [10.0, 6.0], TEMPERATURES)


def test_operating_point_can_be_any_number_and_the_engine_decides() -> None:
    # The request does not judge the operating point. The engine returns a state for it.
    SourceOperatingPoint(requested_mass_flow_kg_s=-3.0, injection_temperature_k=0.0)
