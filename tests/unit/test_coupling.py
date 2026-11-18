"""The coupling of source, plant and network on the synthetic study.

The four fixtures of the implementation plan are the coupling cases of the synthetic study and
small changes to them: a passing case, a source that is too cold, a source that is too small,
and an iteration that is not allowed to finish.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import (
    UNKNOWN_INPUT,
    couple,
    known,
    policy_variant,
    repository_policies,
)

from r3chain.adapters.source_synthetic import SyntheticSourceModel
from r3chain.coupling.cases import CaseOutcome, MemoNetworkModel, prepare_coupling
from r3chain.coupling.loop import (
    CouplingResult,
    CouplingSettings,
    FailureOrigin,
    ShortfallCause,
    solve_coupled,
)
from r3chain.domain.records.runs import CheckOutcome
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import (
    AdapterState,
    EvaluationStatus,
    StageStatus,
    TechnicalStatus,
    summarise_stage_statuses,
)

CASE = "coupling_cases[alpha-direct-shoulder]"


def completed(outcome: CaseOutcome) -> CouplingResult:
    assert outcome.result is not None, outcome.reason
    return outcome.result


def outcomes(result: CouplingResult) -> dict[str, str]:
    return {check.check_id: check.outcome.value for check in result.checks}


def fixed_flow(mass_flow_kg_s: float) -> tuple[tuple[str, Any], ...]:
    return (
        (f"{CASE}.flow_control", "fixed"),
        (f"{CASE}.fixed_geothermal_mass_flow_kg_s", known(mass_flow_kg_s)),
    )


# --- the passing case -----------------------------------------------------------------------


@pytest.mark.requirement("W07")
def test_direct_heat_covers_the_shoulder_season(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    result = completed(couple(network_model, scenario_data, "alpha-direct-shoulder"))
    assert result.state is AdapterState.COMPLETED and result.reason == ""
    assert result.converged and not result.idle
    assert result.arrangement == "direct_exchanger"
    assert result.performance_basis == "equipment_rating"
    # The source engine is a test model, so the result can never enter a pilot ranking.
    assert result.synthetic
    assert result.engines == {
        "source": "r3chain-synthetic-source 1.0.0",
        "network": "pandapipes 0.17.0",
    }
    point = result.operating_point
    assert point is not None and point.source_operated
    assert point.plant_heat_w == pytest.approx(point.demand_heat_w)
    assert point.backup_heat_w == 0.0 and point.deficit_heat_w == 0.0
    assert point.plant_outlet_temperature_k == pytest.approx(353.15)
    assert point.required_supply_temperature_k == 353.15
    assert result.limiting_conditions == () and result.shortfall_causes == ()
    # Every check passes or is openly not evaluated.
    assert set(outcomes(result).values()) == {"pass", "not_evaluated"}
    assert "heat loss and pressure loss of the route" in result.not_modelled[0]


@pytest.mark.requirement("A01")
def test_coupled_heat_balance_agrees_with_independent_calculations(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    result = completed(couple(network_model, scenario_data, "alpha-direct-shoulder"))
    point = result.operating_point
    assert point is not None and result.ledger is not None
    tolerance = result.settings.relative_heat_mismatch_tolerance_fraction

    # Network side. The consumers take 1 200 000 W and the pipes lose heat on top of that.
    #   demand = delivered + pipe loss + what the engine does not account for
    network = result.network_result
    assert network is not None and network.energy_balance is not None
    balance = network.energy_balance
    assert balance.heat_delivered_w == pytest.approx(1_200_000.0, rel=1e-9)
    assert point.demand_heat_w == pytest.approx(
        balance.heat_delivered_w + balance.pipe_heat_loss_w + balance.residual_w, rel=1e-12
    )
    # An independent heat capacity calculation for the stream at the connection, with the heat
    # capacity of water from the property table of the engine (J/(kg K) at 313, 323, 353, 363 K).
    table = {313.0: 4179.6, 323.0: 4181.5, 353.0: 4196.9, 363.0: 4205.3}
    back = point.network_return_temperature_k
    assert back is not None and 313.0 < back < 323.0
    at_return = table[313.0] + (table[323.0] - table[313.0]) * (back - 313.0) / 10.0
    at_supply = table[353.0] + (table[363.0] - table[353.0]) * (353.15 - 353.0) / 10.0
    independent = point.network_mass_flow_kg_s * 0.5 * (at_return + at_supply) * (353.15 - back)
    assert point.demand_heat_w == pytest.approx(independent, rel=tolerance)

    # Geothermal side. The test engine uses 4180 J/(kg K), so the extracted heat is
    #   m * 4180 * (wellhead temperature - injection temperature).
    assert point.wellhead_temperature_k is not None and point.injection_temperature_k is not None
    extracted = (
        point.geothermal_mass_flow_kg_s
        * 4180.0
        * (point.wellhead_temperature_k - point.injection_temperature_k)
    )
    assert point.geothermal_extracted_heat_w == pytest.approx(extracted, rel=1e-12)
    # Source, plant and network agree within the numerical policy.
    assert point.geothermal_extracted_heat_w == pytest.approx(point.plant_heat_w, rel=tolerance)
    assert point.plant_heat_w == pytest.approx(point.demand_heat_w, rel=1e-12)

    # The ledger says the same, boundary by boundary.
    assert result.ledger.closed
    assert [item.boundary for item in result.ledger.balances] == [
        "geothermal_circuit",
        "plant",
        "network",
    ]
    assert result.ledger.entry("geothermal_extraction").power_w == pytest.approx(extracted)
    assert result.ledger.entry("consumer_delivery").power_w == pytest.approx(1_200_000.0)
    assert outcomes(result)["energy_ledger_closed"] == "pass"


def test_residuals_are_within_the_numerical_policy_and_recorded(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    result = completed(couple(network_model, scenario_data, "alpha-direct-shoulder"))
    settings, residuals = result.settings, result.residuals
    assert residuals is not None
    assert result.settings_origin == "policy numerical-coupling-default version 3"
    assert settings == CouplingSettings.from_policy(
        repository_policies()["numerical-coupling-default"]
    )
    assert residuals.temperature_delta_k <= settings.temperature_change_tolerance_delta_k
    assert residuals.heat_fraction <= settings.relative_heat_mismatch_tolerance_fraction
    assert residuals.flow_fraction <= settings.relative_flow_mismatch_tolerance_fraction

    history = result.iterations
    assert 2 <= len(history) <= settings.max_iterations_count
    assert [record.iteration for record in history] == list(range(1, len(history) + 1))
    assert [record.within_tolerance for record in history] == [False] * (len(history) - 1) + [True]
    # The iteration starts at the lowest flow of the range and moves towards the demand.
    assert history[0].geothermal_mass_flow_kg_s == 2.0
    assert history[0].residuals.flow_fraction > 0.5
    assert history[-1].residuals == residuals
    assert result.source_evaluations_count == len(history)


def test_the_same_frozen_input_reproduces_the_same_result(
    network_model: MemoNetworkModel, scenario: Scenario
) -> None:
    policies = repository_policies()
    model = SyntheticSourceModel()
    case = scenario.coupling_cases[0]

    def problem() -> Any:
        return prepare_coupling(
            scenario,
            case,
            capabilities=model.capabilities(),
            source_policy=policies["source-envelope-synthetic"],
            numerical_policy=policies["numerical-coupling-default"],
        )

    first = solve_coupled(problem(), model, network_model)
    second = solve_coupled(problem(), model, network_model)
    assert first.input_hash == second.input_hash == problem().input_hash()
    assert first.content_hash() == second.content_hash()
    # A changed input is a different problem.
    looser = policy_variant(policies["numerical-coupling-default"], max_iterations_count=40)
    other = prepare_coupling(
        scenario,
        case,
        capabilities=model.capabilities(),
        source_policy=policies["source-envelope-synthetic"],
        numerical_policy=looser,
    )
    assert other.input_hash() != first.input_hash


def test_damping_reaches_the_same_operating_point_in_more_passes(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    plain = completed(couple(network_model, scenario_data, "alpha-direct-shoulder"))
    damped = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-shoulder",
            numerical={"damping_fraction": 0.5},
        )
    )
    assert damped.state is AdapterState.COMPLETED
    assert len(damped.iterations) > len(plain.iterations)
    first, second = plain.operating_point, damped.operating_point
    assert first is not None and second is not None
    tolerance = plain.settings.relative_flow_mismatch_tolerance_fraction
    assert second.geothermal_mass_flow_kg_s == pytest.approx(
        first.geothermal_mass_flow_kg_s, rel=2 * tolerance
    )
    assert second.plant_heat_w == pytest.approx(first.plant_heat_w)


# --- insufficient temperature ---------------------------------------------------------------


@pytest.mark.requirement("A02")
def test_inadequate_hot_end_approach_fails_direct_heat_with_its_terminal(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    result = completed(couple(network_model, scenario_data, "beta-direct-summer"))
    assert result.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert result.failure_origin is FailureOrigin.PLANT
    # The hot end is the one limiting terminal, and the cause is temperature, not power.
    assert result.limiting_conditions == ("exchanger_hot_end_approach",)
    assert result.shortfall_causes == (ShortfallCause.INSUFFICIENT_TEMPERATURE,)
    assert "insufficient_temperature, limited by exchanger_hot_end_approach" in result.reason

    checks = outcomes(result)
    assert checks["exchanger_hot_end_approach"] == "fail"
    assert checks["exchanger_cold_end_approach"] == "pass"
    assert checks["exchanger_rating"] == "pass"
    point = result.operating_point
    assert point is not None and point.wellhead_temperature_k is not None
    hot_end = result.check("exchanger_hot_end_approach")
    # Observed: wellhead temperature minus required supply temperature. Limit: 5 K approach.
    assert hot_end.observed == pytest.approx(point.wellhead_temperature_k - 353.15)
    assert hot_end.limit == 5.0 and hot_end.margin is not None and hot_end.margin < -10.0
    assert hot_end.threshold_origin == (
        "plants[plant-direct].parameters[exchanger_min_approach_delta_k]"
    )

    # The plant still delivers what the temperature permits, and the rest is a reported deficit.
    assert point.plant_outlet_temperature_k == pytest.approx(point.wellhead_temperature_k - 5.0)
    assert 0.0 < point.plant_heat_w < point.demand_heat_w
    assert point.deficit_heat_w == pytest.approx(point.demand_heat_w - point.plant_heat_w)
    # The source is not driven to its limit for nothing.
    assert not any(name.startswith("source:") for name in result.limiting_conditions)
    assert point.geothermal_mass_flow_kg_s < 6.0


def test_a_warmer_reservoir_removes_the_temperature_failure(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    path = "well_scenarios[source-beta].reservoir_parameters[reservoir_temperature_k]"
    result = completed(
        couple(network_model, scenario_data, "beta-direct-summer", patches=((path, known(378.15)),))
    )
    assert result.state is AdapterState.COMPLETED
    assert outcomes(result)["exchanger_hot_end_approach"] == "pass"


# --- insufficient power ---------------------------------------------------------------------


@pytest.mark.requirement("A05")
def test_undersized_source_leaves_a_reported_deficit(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    result = completed(couple(network_model, scenario_data, "alpha-direct-winter"))
    assert result.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert result.shortfall_causes == (ShortfallCause.INSUFFICIENT_POWER,)
    # The limiting condition is traced to the source: its drawdown limit caps the flow.
    assert result.limiting_conditions == ("exchanger_cold_end_approach", "source:max_drawdown_pa")
    note = next(record.note for record in result.iterations if record.note)
    assert "the source rejected" in note and "max_drawdown_pa" in note

    point = result.operating_point
    assert point is not None and result.ledger is not None
    # Alpha reaches 6 MPa drawdown at about 13.6 kg/s.
    assert point.geothermal_mass_flow_kg_s == pytest.approx(13.64, abs=0.02)
    assert point.demand_heat_w > 3_000_000.0
    assert point.deficit_heat_w == pytest.approx(point.demand_heat_w - point.plant_heat_w)
    assert point.deficit_heat_w > 400_000.0 and point.backup_heat_w == 0.0

    # No hidden heat. The ideal boundary of the network model supplied the whole demand, and the
    # part that no finite source delivers is listed and fails the evaluation.
    slack = result.ledger.slack_exchanges[0]
    assert slack.heat_supplied_w == pytest.approx(point.demand_heat_w)
    assert slack.assigned_heat_w == {"plant:plant-direct": pytest.approx(point.plant_heat_w)}
    assert slack.unassigned_heat_w == pytest.approx(point.deficit_heat_w)
    assert not slack.within_intended_dispatch and slack.net_mass_flow_kg_s == 0.0
    assert result.ledger.entry("unassigned_slack_heat").power_w == pytest.approx(
        point.deficit_heat_w
    )
    unassigned = result.check("unassigned_slack_heat")
    assert unassigned.outcome is CheckOutcome.FAIL
    assert unassigned.observed == pytest.approx(point.deficit_heat_w)
    # The ledger still balances, because the deficit is an entry of its own.
    assert result.ledger.closed


@pytest.mark.requirement("A05")
def test_declared_backup_covers_the_shortfall_explicitly(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    alone = completed(couple(network_model, scenario_data, "alpha-direct-winter"))
    result = completed(couple(network_model, scenario_data, "alpha-direct-winter-backup"))
    assert result.state is AdapterState.COMPLETED and result.backup_id == "boiler-west"
    point, without = result.operating_point, alone.operating_point
    assert point is not None and without is not None and result.ledger is not None
    assert point.backup_heat_w == pytest.approx(without.deficit_heat_w)
    assert point.plant_heat_w == pytest.approx(without.plant_heat_w)
    assert point.deficit_heat_w == 0.0
    assert point.plant_heat_w + point.backup_heat_w == pytest.approx(point.demand_heat_w)
    # The backup stands in series after the plant, so the plant outlet is below the supply.
    assert point.plant_outlet_temperature_k is not None
    assert point.plant_outlet_temperature_k < 353.15

    # The backup heat is its own ledger entry and its own share of the slack heat.
    entry = result.ledger.entry("backup_delivery")
    assert entry.from_boundary == "backup:boiler-west"
    assert entry.power_w == pytest.approx(point.backup_heat_w)
    slack = result.ledger.slack_exchanges[0]
    assert slack.assigned_heat_w["backup:boiler-west"] == pytest.approx(point.backup_heat_w)
    assert slack.unassigned_heat_w == 0.0 and slack.within_intended_dispatch
    # Why the backup was needed stays on record.
    assert result.limiting_conditions == alone.limiting_conditions
    assert result.shortfall_causes == (ShortfallCause.INSUFFICIENT_POWER,)
    assert set(outcomes(result).values()) == {"pass", "not_evaluated"}


@pytest.mark.requirement("A05")
def test_a_backup_that_is_too_small_still_leaves_a_deficit(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    path = "coupling_cases[alpha-direct-winter-backup].backup.max_heat_w"
    result = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-winter-backup",
            patches=((path, known(200_000.0)),),
        )
    )
    assert result.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    point = result.operating_point
    assert point is not None
    assert point.backup_heat_w == pytest.approx(200_000.0)
    assert point.deficit_heat_w == pytest.approx(
        point.demand_heat_w - point.plant_heat_w - 200_000.0
    )
    assert point.deficit_heat_w > 0.0


def test_a_small_exchanger_is_named_as_the_limit(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    path = "plants[plant-direct].parameters[exchanger_ua_w_k]"
    result = completed(
        couple(
            network_model, scenario_data, "alpha-direct-shoulder", patches=((path, known(2.0e4)),)
        )
    )
    assert result.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert result.limiting_conditions[0] == "exchanger_rating"
    assert result.shortfall_causes == (ShortfallCause.INSUFFICIENT_POWER,)


# --- failure categories ---------------------------------------------------------------------


@pytest.mark.requirement("A13")
def test_forced_non_convergence_is_not_reported_as_infeasible(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    outcome = couple(
        network_model, scenario_data, "alpha-direct-shoulder", numerical={"max_iterations_count": 3}
    )
    result = completed(outcome)
    assert outcome.status is StageStatus.NON_CONVERGED
    assert result.state is AdapterState.NON_CONVERGED
    assert result.failure_origin is FailureOrigin.COUPLING
    assert not result.converged and len(result.iterations) == 3
    assert "did not converge within 3 iterations" in result.reason
    assert "Nothing is concluded about feasibility" in result.reason
    # No operating point, no ledger and no feasibility statement are given.
    assert result.operating_point is None and result.plant is None and result.ledger is None
    assert result.limiting_conditions == () and result.shortfall_causes == ()
    # The residuals show how far the iteration was from the tolerance.
    assert result.residuals is not None
    assert (
        result.residuals.flow_fraction > result.settings.relative_flow_mismatch_tolerance_fraction
    )

    # The same case with enough iterations is feasible. A physical failure looks different.
    assert couple(network_model, scenario_data, "alpha-direct-shoulder").status is (
        StageStatus.COMPLETED
    )
    infeasible = couple(network_model, scenario_data, "alpha-direct-winter")
    assert infeasible.status is StageStatus.PHYSICAL_LIMIT_EXCEEDED
    # Downstream, the two never merge: one is an unresolved evaluation, the other a failed case.
    assert summarise_stage_statuses([outcome.status]) == (
        TechnicalStatus.NOT_ESTABLISHED,
        EvaluationStatus.UNRESOLVED_NUMERICAL,
    )
    assert summarise_stage_statuses([infeasible.status]) == (
        TechnicalStatus.FAILS_EVALUATED_CASE,
        EvaluationStatus.VALID,
    )


@pytest.mark.requirement("A13")
def test_failures_keep_their_origin_and_category(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    # A source model that does not converge is a numerical failure of the source.
    numerical = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-shoulder",
            source={"max_iterations_count": 1},
        )
    )
    assert (numerical.state, numerical.failure_origin) == (
        AdapterState.NON_CONVERGED,
        FailureOrigin.SOURCE,
    )
    assert "wellhead temperature iteration did not converge" in numerical.reason
    assert numerical.operating_point is None

    # A source that cannot run at the fixed flow is an infeasible resource.
    resource = completed(
        couple(network_model, scenario_data, "alpha-direct-shoulder", patches=fixed_flow(18.0))
    )
    assert (resource.state, resource.failure_origin) == (
        AdapterState.PHYSICAL_LIMIT_EXCEEDED,
        FailureOrigin.SOURCE,
    )
    assert resource.limiting_conditions == ("source:max_drawdown_pa",)
    assert resource.check("source_max_drawdown_pa").outcome is CheckOutcome.FAIL

    # A network whose static pressure is too low is an infeasible network.
    reference = "networks[syn-network].boundary_conditions[pressure-reference].pressure.value_pa"
    network = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-shoulder",
            patches=((reference, 400_000.0),),
        )
    )
    assert (network.state, network.failure_origin) == (
        AdapterState.PHYSICAL_LIMIT_EXCEEDED,
        FailureOrigin.NETWORK,
    )
    assert network.limiting_conditions == ("network:positive_absolute_pressure",)
    assert network.iterations == ()

    # A heat carrier the network engine does not model is unsupported, not infeasible.
    unsupported = couple(
        network_model,
        scenario_data,
        "alpha-direct-shoulder",
        patches=(
            ("networks[syn-network].heat_carrier", "steam"),
            ("connections[conn-west].heat_carrier", "steam"),
            ("connections[conn-east].heat_carrier", "steam"),
        ),
    )
    assert unsupported.status is StageStatus.UNSUPPORTED_MODEL
    assert completed(unsupported).failure_origin is FailureOrigin.NETWORK
    assert "steam" in unsupported.reason

    # An arrangement that is not registered is unsupported as well, before anything is solved.
    unknown = couple(
        network_model,
        scenario_data,
        "alpha-direct-shoulder",
        patches=(("plants[plant-direct].arrangement", "storage_tank"),),
    )
    assert unknown.status is StageStatus.UNSUPPORTED_MODEL and unknown.result is None
    states = {numerical.state, resource.state, AdapterState.UNSUPPORTED_MODEL}
    assert len(states) == 3


# --- inputs that are not established --------------------------------------------------------


@pytest.mark.requirement("A12")
def test_a_missing_required_input_stops_the_case_and_names_it(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    patches = (
        ("plants[plant-direct].parameters[exchanger_ua_w_k]", UNKNOWN_INPUT),
        (f"{CASE}.network_pressure_lift_pa", UNKNOWN_INPUT),
        ("operating_cases[shoulder-season].ground_temperature_k", UNKNOWN_INPUT),
    )
    outcome = couple(network_model, scenario_data, "alpha-direct-shoulder", patches=patches)
    assert outcome.status is StageStatus.NOT_ESTABLISHED and outcome.result is None
    assert sorted(item.field_path for item in outcome.missing_inputs) == sorted(
        path for path, _ in patches
    )


@pytest.mark.requirement("A12")
def test_unknown_limit_stays_an_unevaluated_check(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    path = "connections[conn-west].limits.max_pressure"
    result = completed(
        couple(
            network_model, scenario_data, "alpha-direct-shoulder", patches=((path, UNKNOWN_INPUT),)
        )
    )
    # The case is still evaluated, and the missing limit does not become a passed check.
    assert result.state is AdapterState.COMPLETED
    check = result.check("connection_max_pressure")
    assert check.outcome is CheckOutcome.NOT_EVALUATED
    assert f"{path} is not established" in check.description
    assert path in [item.field_path for item in result.inputs_not_established]
    assert any(path in warning for warning in result.warnings)
    # The known limits are checked with observed value, limit, margin and origin.
    low = result.check("connection_min_pressure")
    assert low.outcome is CheckOutcome.PASS
    assert (low.limit, low.unit) == (200_000.0, "Pa (absolute)")
    assert low.threshold_origin == "connections[conn-west].limits.min_pressure"
    # The unknown pressure loss of the exchanger stays open in the same way.
    assert outcomes(result)["exchanger_network_side_pressure_loss"] == "not_evaluated"


def test_connection_limits_are_enforced(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    path = "connections[conn-west].limits.max_mass_flow_kg_s"
    result = completed(
        couple(network_model, scenario_data, "alpha-direct-shoulder", patches=((path, known(5.0)),))
    )
    assert result.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert result.failure_origin is FailureOrigin.COUPLING
    assert result.reason == "limit exceeded: connection_max_mass_flow"
    # The demand itself is covered. The failure is the limit, not a deficit.
    point = result.operating_point
    assert point is not None and point.deficit_heat_w == 0.0


# --- flow control ---------------------------------------------------------------------------


def test_a_fixed_flow_gives_only_the_heat_that_is_asked_for(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    follow = completed(couple(network_model, scenario_data, "alpha-direct-shoulder"))
    fixed = completed(
        couple(network_model, scenario_data, "alpha-direct-shoulder", patches=fixed_flow(10.0))
    )
    assert fixed.state is AdapterState.COMPLETED
    first, second = follow.operating_point, fixed.operating_point
    assert first is not None and second is not None
    assert second.geothermal_mass_flow_kg_s == 10.0
    assert second.plant_heat_w == pytest.approx(first.plant_heat_w)
    # More flow than needed is cooled less, so it is injected warmer.
    assert first.injection_temperature_k is not None and second.injection_temperature_k is not None
    assert second.injection_temperature_k > first.injection_temperature_k + 5.0
    assert second.geothermal_mass_flow_kg_s > first.geothermal_mass_flow_kg_s

    short = completed(
        couple(network_model, scenario_data, "alpha-direct-shoulder", patches=fixed_flow(3.0))
    )
    assert short.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert short.limiting_conditions == ("exchanger_cold_end_approach",)


def test_no_demand_means_nothing_is_operated(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    loads = [
        {"consumer_id": f"consumer-{number}", "heat_w": 0.0, "return_temperature_k": 323.15}
        for number in range(1, 5)
    ]
    result = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-shoulder",
            patches=(("operating_cases[shoulder-season].loads", known(loads)),),
        )
    )
    assert result.state is AdapterState.COMPLETED and result.idle
    point = result.operating_point
    assert point is not None and not point.source_operated
    assert point.demand_heat_w == 0.0 and point.geothermal_mass_flow_kg_s == 0.0
    assert result.source_evaluations_count == 0 and result.ledger is None
    assert "not operated" in result.warnings[0]


# --- heat pump and pressure separation ------------------------------------------------------


@pytest.mark.requirement("A03")
def test_heat_pump_assisted_case_balances_in_the_ledger(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    result = completed(couple(network_model, scenario_data, "beta-heat-pump-shoulder"))
    assert result.state is AdapterState.COMPLETED
    assert result.arrangement == "heat_pump_assisted"
    assert result.performance_basis == "performance_map"
    plant, ledger, point = result.plant, result.ledger, result.operating_point
    assert plant is not None and ledger is not None and point is not None
    pump = plant.heat_pump
    assert pump is not None and pump.basis == "performance_map"

    # Condenser heat equals evaporator heat plus compressor input, less the stated 5 percent.
    assert pump.compressor_heat_loss_fraction == 0.05
    assert pump.condenser_heat_w == pytest.approx(
        pump.evaporator_heat_w + 0.95 * pump.compressor_electricity_w, rel=1e-12
    )
    assert outcomes(result)["heat_pump_balance"] == "pass"

    # The ledger keeps the paths apart and balances the plant.
    exchanger = ledger.entry("exchanger_transfer").power_w
    evaporator = ledger.entry("heat_pump_evaporator").power_w
    electricity = ledger.entry("compressor_electricity").power_w
    loss = ledger.entry("compressor_heat_loss").power_w
    delivery = ledger.entry("plant_delivery").power_w
    assert evaporator == pytest.approx(pump.evaporator_heat_w)
    assert electricity == pytest.approx(pump.compressor_electricity_w) and electricity > 0.0
    assert exchanger + evaporator + electricity == pytest.approx(delivery + loss, rel=1e-12)
    assert delivery == pytest.approx(point.demand_heat_w)
    # Condenser heat is not counted as a second delivery next to the evaporator heat.
    assert delivery == pytest.approx(exchanger + pump.condenser_heat_w)
    assert ledger.entry("compressor_electricity").quantity.value == "electricity"
    assert ledger.closed

    # Compressor and well pumps are reported separately.
    assert point.compressor_electricity_w == pytest.approx(electricity)
    assert point.source_pump_electricity_w == pytest.approx(
        ledger.entry("well_pump_electricity").power_w
    )
    # The geothermal stream is cooled far below the network return, but not below its limit.
    assert point.injection_temperature_k is not None
    assert 303.15 - 0.05 <= point.injection_temperature_k < 310.0
    # No authorised electrical import is given, so that check stays open.
    assert outcomes(result)["electrical_import"] == "not_evaluated"


def test_electrical_import_limit_is_checked_when_it_is_given(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    path = "plants[plant-heat-pump].parameters[max_electrical_import_w]"
    result = completed(
        couple(
            network_model,
            scenario_data,
            "beta-heat-pump-shoulder",
            patches=((path, known(250_000.0)),),
        )
    )
    point = result.operating_point
    assert point is not None
    check = result.check("electrical_import")
    # Compressor and well pumps together need more than 250 kW.
    assert check.observed == pytest.approx(
        point.compressor_electricity_w + point.source_pump_electricity_w
    )
    assert check.outcome is CheckOutcome.FAIL
    assert result.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED
    assert result.reason == "limit exceeded: electrical_import"


def test_screening_model_results_are_labelled(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    patches = (
        ("plants[plant-heat-pump].heat_pump_map", None),
        ("plants[plant-heat-pump].parameters[carnot_efficiency_fraction]", known(0.5)),
        (
            "plants[plant-heat-pump].parameters[evaporator_temperature_difference_delta_k]",
            known(10.0),
        ),
        (
            "plants[plant-heat-pump].parameters[condenser_temperature_difference_delta_k]",
            known(5.0),
        ),
    )
    result = completed(
        couple(network_model, scenario_data, "beta-heat-pump-shoulder", patches=patches)
    )
    assert result.performance_basis == "screening_model"
    assert any("screening model" in warning for warning in result.warnings)
    assert outcomes(result)["heat_pump_capacity"] == "not_evaluated"


def test_pressure_separation_costs_temperature(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    direct = completed(couple(network_model, scenario_data, "alpha-direct-shoulder"))
    separated = completed(couple(network_model, scenario_data, "alpha-separated-shoulder"))
    assert separated.state is AdapterState.COMPLETED
    first, second = direct.operating_point, separated.operating_point
    assert first is not None and second is not None
    assert second.plant_heat_w == pytest.approx(first.plant_heat_w)
    # The second exchanger keeps the geothermal stream at least 3 K warmer on its way back,
    # so the same heat needs more flow.
    assert first.injection_temperature_k is not None and second.injection_temperature_k is not None
    assert second.injection_temperature_k >= first.injection_temperature_k + 3.0 - 0.05
    assert second.geothermal_mass_flow_kg_s > first.geothermal_mass_flow_kg_s
    assert separated.plant is not None
    assert set(separated.plant.exchangers) == {"exchanger", "separation_exchanger"}


@pytest.mark.requirement("W06")
@pytest.mark.requirement("W12")
def test_a_plant_at_a_connection_away_from_the_pressure_reference_is_evaluated(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    """The network holds its pressure at r0. A plant at the eastern connection still works."""
    result = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-shoulder",
            patches=((f"{CASE}.connection_id", "conn-east"), (f"{CASE}.route_id", None)),
        )
    )
    assert result.state is AdapterState.COMPLETED
    network = result.network_result
    assert network is not None
    # The reference node keeps its pressure although the supply point is elsewhere.
    assert network.node("r0").pressure.value_pa == pytest.approx(750_000.0, abs=1.0)
    supply = network.supply_points[0]
    assert supply.pressure_lift_pa == pytest.approx(150_000.0, abs=1.0)


def test_connection_max_heat_checks_delivered_heat_minus_route_loss(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    design = {
        "inner_diameter_m": known(0.175),
        "roughness_m": known(0.0001),
        "heat_transfer_coefficient_w_m2_k": known(0.4),
    }
    routed = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-shoulder",
            patches=((f"{CASE}.transmission", design),),
        )
    )
    point = routed.operating_point
    assert point is not None and point.transmission_heat_loss_w is not None
    conn_heat = point.demand_heat_w - point.transmission_heat_loss_w
    # Place connection limit between connection heat (~1.27 MW) and plant heat (~1.30 MW).
    midpoint_limit = conn_heat + 0.5 * point.transmission_heat_loss_w
    checked = completed(
        couple(
            network_model,
            scenario_data,
            "alpha-direct-shoulder",
            patches=(
                (f"{CASE}.transmission", design),
                ("connections[conn-west].limits.max_heat_w", known(midpoint_limit)),
            ),
        )
    )
    assert checked.state is AdapterState.COMPLETED
    assert checked.check("connection_max_heat").outcome.value == "pass"
    assert checked.check("connection_max_heat").observed == pytest.approx(conn_heat, rel=1e-6)
