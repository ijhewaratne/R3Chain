"""The coupling stage of the pipeline and the ``couple`` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import (
    POLICY_DIRECTORY,
    SYNTHETIC_SCENARIO,
    UNKNOWN_INPUT,
    FakeClock,
    changed,
    known,
    policy_variant,
    repository_policies,
)

from r3chain.cli import main
from r3chain.coupling.cases import MemoNetworkModel
from r3chain.coupling.loop import CouplingResult
from r3chain.domain.records.runs import Result, Run, StageOutput
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import EvaluationStatus, StageStatus, TechnicalStatus
from r3chain.pipeline.runner import PipelineRunner
from r3chain.pipeline.stages import default_stages
from r3chain.store.store import Store, policy_ref

CASES = [
    "alpha-direct-shoulder",
    "alpha-direct-winter",
    "alpha-direct-winter-backup",
    "beta-direct-summer",
    "beta-heat-pump-shoulder",
    "alpha-separated-shoulder",
]


class Chain:
    def __init__(self, store: Store, clock: FakeClock, network_model: MemoNetworkModel) -> None:
        self.store = store
        self.runner = PipelineRunner(
            store,
            default_stages(network_model=network_model),
            clock=clock,
            code_version_text="t",
        )

    def run(self, data: dict[str, Any]) -> tuple[Run, Result]:
        scenario = Scenario.model_validate(data)
        revision = self.store.add_revision(scenario, author="t", rationale="t").revision_id
        return self.runner.run(revision, raise_on_error=True)


@pytest.fixture
def chain(store: Store, clock: FakeClock, network_model: MemoNetworkModel) -> Chain:
    return Chain(store, clock, network_model)


def stage(result: Result, name: str) -> StageOutput:
    return next(output for output in result.stage_outputs if output.stage == name)


def hits(run: Run) -> dict[str, bool]:
    return {record.stage: record.cache_hit for record in run.stages}


# --- the stage ------------------------------------------------------------------------------


def test_coupling_stage_evaluates_every_case(chain: Chain, scenario_data: dict[str, Any]) -> None:
    _, result = chain.run(scenario_data)
    couple = stage(result, "couple")
    # A case that fails physically is a valid evaluation, so the stage itself is complete.
    assert couple.status is StageStatus.COMPLETED and couple.reason == ""
    assert couple.synthetic and couple.missing_inputs == ()
    payload = couple.payload
    assert list(payload["cases"]) == CASES
    assert payload["counts"] == {"completed": 4, "physical_limit_exceeded": 2}
    assert payload["numerical_policy_id"] == "numerical-coupling-default"
    assert payload["source_policy_id"] == "source-envelope-synthetic"

    states = {name: case["summary"]["state"] for name, case in payload["cases"].items()}
    assert states == {
        "alpha-direct-shoulder": "completed",
        "alpha-direct-winter": "physical_limit_exceeded",
        "alpha-direct-winter-backup": "completed",
        "beta-direct-summer": "physical_limit_exceeded",
        "beta-heat-pump-shoulder": "completed",
        "alpha-separated-shoulder": "completed",
    }
    winter = payload["cases"]["alpha-direct-winter"]["summary"]
    assert winter["shortfall_causes"] == ["insufficient_power"]
    assert winter["failed_checks"] == ["exchanger_cold_end_approach", "unassigned_slack_heat"]
    assert winter["operating_point"]["deficit_heat_w"] > 400_000.0
    summer = payload["cases"]["beta-direct-summer"]["summary"]
    assert summer["limiting_conditions"] == ["exchanger_hot_end_approach"]
    assert "exchanger_network_side_pressure_loss" in summer["checks_not_evaluated"]

    # The full result of every case is stored and reads back as a record.
    stored = CouplingResult.model_validate(payload["cases"]["beta-heat-pump-shoulder"]["result"])
    assert stored.ledger is not None and stored.ledger.closed
    assert stored.schema_version == "0.1.0"

    # The stages after the coupling do not exist yet, so the study has no technical result.
    assert stage(result, "operate").status is StageStatus.NOT_ESTABLISHED
    assert result.technical_status is TechnicalStatus.NOT_ESTABLISHED
    assert result.synthetic


@pytest.mark.requirement("A15")
def test_changing_a_plant_value_recomputes_only_the_coupling(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    first, before = chain.run(scenario_data)
    assert not any(hits(first).values())

    # A plant value: the source envelopes do not depend on it.
    path = "plants[plant-direct].parameters[exchanger_min_approach_delta_k]"
    second, after = chain.run(changed(scenario_data, path, known(8.0)))
    assert hits(second)["source"] and hits(second)["site_evidence"]
    assert not hits(second)["couple"]
    old = stage(before, "couple").payload["cases"]["alpha-direct-shoulder"]["summary"]
    new = stage(after, "couple").payload["cases"]["alpha-direct-shoulder"]["summary"]
    # A wider approach returns the geothermal stream warmer, so the same heat needs more flow.
    assert (
        new["operating_point"]["geothermal_mass_flow_kg_s"]
        > old["operating_point"]["geothermal_mass_flow_kg_s"]
    )
    assert second.scenario_revision != first.scenario_revision

    # A reservoir value: both the source stage and the coupling depend on it.
    reservoir = "well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]"
    third, _ = chain.run(changed(scenario_data, reservoir, known(388.15)))
    assert not hits(third)["source"] and not hits(third)["couple"]
    assert hits(third)["site_evidence"]

    # The unchanged scenario again reuses everything.
    fourth, again = chain.run(scenario_data)
    assert hits(fourth)["source"] and hits(fourth)["couple"]
    assert stage(again, "couple") == stage(before, "couple")


@pytest.mark.requirement("A17")
def test_a_run_with_coupling_replays_to_the_identical_result(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    run, _ = chain.run(scenario_data)
    outcome = chain.runner.replay(run.run_id)
    assert outcome.identical and outcome.original_hash == outcome.replay_hash
    assert outcome.engine_versions_changed == ()


@pytest.mark.requirement("A13")
def test_a_case_that_does_not_converge_leaves_the_study_unresolved(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    # A new policy version that allows three iterations only.
    strict = policy_variant(
        repository_policies()["numerical-coupling-default"], max_iterations_count=3
    )
    reference = chain.store.register_policy(strict)
    policies = [
        reference.to_json_dict() if item["policy_id"] == strict.policy_id else item
        for item in scenario_data["policies"]
    ]
    assert reference == policy_ref(strict)
    _, result = chain.run({**scenario_data, "policies": policies})
    couple = stage(result, "couple")
    assert couple.status is StageStatus.NON_CONVERGED
    states = {name: case["summary"]["state"] for name, case in couple.payload["cases"].items()}
    # Cases that settle within three passes keep their results. The others are unresolved.
    assert states["alpha-direct-winter"] == "physical_limit_exceeded"
    assert states["beta-direct-summer"] == "physical_limit_exceeded"
    assert states["alpha-direct-shoulder"] == "non_converged"
    assert "alpha-direct-shoulder" in couple.reason
    unresolved = couple.payload["cases"]["alpha-direct-shoulder"]["summary"]
    assert unresolved["operating_point"] is None and not unresolved["converged"]
    # An unresolved calculation is never a technical failure.
    assert result.evaluation_status is EvaluationStatus.UNRESOLVED_NUMERICAL
    assert result.technical_status is TechnicalStatus.NOT_ESTABLISHED
    assert stage(result, "operate").status is StageStatus.NOT_RUN


@pytest.mark.requirement("A12")
def test_an_unknown_plant_input_is_reported_by_the_stage(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    path = "plants[plant-separated].parameters[separation_exchanger_ua_w_k]"
    _, result = chain.run(changed(scenario_data, path, UNKNOWN_INPUT))
    couple = stage(result, "couple")
    assert couple.status is StageStatus.NOT_ESTABLISHED
    assert [(item.field_path, item.owner) for item in couple.missing_inputs] == [
        (path, "study owner")
    ]
    separated = couple.payload["cases"]["alpha-separated-shoulder"]
    assert separated["result"] is None
    assert separated["summary"] == {
        "state": "not_established",
        "reason": "required inputs are not established",
        "inputs_not_established": [path],
    }
    # The other cases are evaluated as before.
    assert couple.payload["cases"]["alpha-direct-shoulder"]["summary"]["state"] == "completed"
    assert result.evaluation_status is EvaluationStatus.NOT_EVALUATED


def test_coupling_stage_needs_cases_and_policies(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    _, result = chain.run({**scenario_data, "coupling_cases": []})
    couple = stage(result, "couple")
    assert couple.status is StageStatus.NOT_ESTABLISHED
    assert couple.reason == "the scenario defines no coupling cases"

    policies = [
        item
        for item in scenario_data["policies"]
        if item["policy_id"] != "numerical-coupling-default"
    ]
    _, result = chain.run({**scenario_data, "policies": policies})
    couple = stage(result, "couple")
    assert couple.status is StageStatus.NOT_ESTABLISHED
    assert "exactly one policy of kind 'numerical'" in couple.reason


def test_an_older_scenario_without_plants_still_loads(scenario_data: dict[str, Any]) -> None:
    older = {
        name: value
        for name, value in scenario_data.items()
        if name not in {"plants", "operating_cases", "coupling_cases"}
    }
    scenario = Scenario.model_validate({**older, "schema_version": "0.1.0"})
    assert scenario.schema_version == "0.2.0"
    assert scenario.plants == () and scenario.coupling_cases == ()
    with pytest.raises(ValueError, match="no migration"):
        Scenario.model_validate({**older, "schema_version": "0.0.9"})


# --- the command line -----------------------------------------------------------------------


def run_cli(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, str]:
    code = main(
        ["couple", str(SYNTHETIC_SCENARIO), "--policies", str(POLICY_DIRECTORY), *arguments]
    )
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_couple_command_lists_every_case(capsys: pytest.CaptureFixture[str]) -> None:
    code, output = run_cli(capsys)
    # Two cases fail on purpose, so the command reports a failure.
    assert code == 1
    lines = output.splitlines()
    assert [line.split(":")[0] for line in lines[:6]] == CASES
    assert "alpha-direct-shoulder: completed. Plant 1.27 of 1.27 MW" in output
    assert (
        "deficit 0.50 MW, limited by exchanger_cold_end_approach, source:max_drawdown_pa" in output
    )
    assert "backup 0.50 MW" in output
    assert "limited by exchanger_hot_end_approach" in output


def test_couple_command_shows_one_case_in_full(capsys: pytest.CaptureFixture[str]) -> None:
    code, output = run_cli(capsys, "--case", "beta-heat-pump-shoulder")
    assert code == 0
    assert "Case      beta-heat-pump-shoulder, arrangement heat_pump_assisted" in output
    assert "State     completed" in output
    assert "r3chain-synthetic-source 1.0.0, a synthetic test engine; pandapipes 0.17.0" in output
    assert "required supply 80.00 degC" in output
    for expected in (
        "Heat at the connection",
        "compressor",
        "heat_pump_evaporator",
        "balance of plant: residual 0.000 kW, closed",
        "open  electrical_import",
        "pass  heat_pump_balance",
        "Not modelled: heat loss and pressure loss of the route",
    ):
        assert expected in output, expected

    code, output = run_cli(capsys, "--case", "alpha-direct-winter")
    assert code == 1
    assert "State     physical_limit_exceeded" in output
    assert (
        "Limited by  exchanger_cold_end_approach, source:max_drawdown_pa (insufficient_power)"
        in (output)
    )
    assert "fail  unassigned_slack_heat" in output
    assert "the source rejected" in output


def test_couple_command_writes_json_and_rejects_unknown_cases(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output = run_cli(capsys, "--case", "alpha-direct-shoulder", "--json")
    assert code == 0
    document = json.loads(output)
    assert list(document) == ["alpha-direct-shoulder"]
    result = CouplingResult.model_validate(document["alpha-direct-shoulder"]["result"])
    assert result.state.value == "completed"
    assert document["alpha-direct-shoulder"]["summary"]["iterations_count"] == len(
        result.iterations
    )

    code, output = run_cli(capsys, "--case", "no-such-case")
    assert code == 1 and "coupling case no-such-case is not in the scenario" in output


def test_show_lists_the_coupling_cases_of_a_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = ["--store", str(tmp_path / "store")]
    code = main([*store, "run", str(SYNTHETIC_SCENARIO), "--policies", str(POLICY_DIRECTORY)])
    output = capsys.readouterr().out
    assert code == 0 and "couple         completed        computed" in output
    run_id = next(line.split()[1] for line in output.splitlines() if line.startswith("Run "))
    code = main([*store, "show", run_id])
    shown = capsys.readouterr().out
    assert code == 0
    assert "case alpha-direct-shoulder: completed. Plant 1.27 of 1.27 MW" in shown
    assert "case beta-direct-summer: physical_limit_exceeded" in shown
