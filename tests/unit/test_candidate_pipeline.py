"""The evaluation chain of milestone M3 on the synthetic study, and its command line.

One run of the full chain is shared by the tests that only read it. Tests that change the
scenario run the chain again in their own store.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
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
from r3chain.search.candidates import CandidateAssessment
from r3chain.store.store import Store, load_policies, policy_ref

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


@pytest.fixture(scope="module")
def synthetic_run(
    tmp_path_factory: pytest.TempPathFactory,
    network_model: MemoNetworkModel,
    _synthetic_data: dict[str, Any],
) -> Iterator[tuple[Store, Run, Result]]:
    """One run of the full chain on the synthetic study."""
    store = Store(tmp_path_factory.mktemp("candidates") / "store", clock=FakeClock())
    for policy in load_policies(POLICY_DIRECTORY):
        store.register_policy(policy)
    run, result = Chain(store, FakeClock(), network_model).run(_synthetic_data)
    yield store, run, result
    store.close()


def stage(result: Result, name: str) -> StageOutput:
    return next(output for output in result.stage_outputs if output.stage == name)


def assessments(result: Result) -> list[CandidateAssessment]:
    return [
        CandidateAssessment.model_validate(item)
        for item in stage(result, "assess").payload["assessments"]
    ]


def by_label(result: Result) -> dict[str, CandidateAssessment]:
    return {item.label: item for item in assessments(result)}


def hits(run: Run) -> dict[str, bool]:
    return {record.stage: record.cache_hit for record in run.stages}


# --- the chain ------------------------------------------------------------------------------


def test_every_stage_of_the_chain_runs(synthetic_run: tuple[Store, Run, Result]) -> None:
    _, _, result = synthetic_run
    statuses = {output.stage: output.status for output in result.stage_outputs}
    assert statuses == {
        "site_evidence": StageStatus.COMPLETED,
        "source": StageStatus.COMPLETED,
        "screen": StageStatus.COMPLETED,
        "routes": StageStatus.COMPLETED,
        "candidates": StageStatus.COMPLETED,
        "couple": StageStatus.COMPLETED,
        "assess": StageStatus.COMPLETED,
        "operate": StageStatus.NOT_ESTABLISHED,
        "cost": StageStatus.NOT_RUN,
        "compare": StageStatus.NOT_RUN,
    }
    assert stage(result, "candidates").payload["counts"] == {"candidates": 27, "rejected": 1}
    # 9 routes times 3 plants. Four direct or separated plants at Beta fail physically.
    assert stage(result, "couple").payload["counts"] == {
        "completed": 23,
        "physical_limit_exceeded": 4,
    }
    # Operation and economics do not exist yet, so the study has no technical result.
    assert result.technical_status is TechnicalStatus.NOT_ESTABLISHED


def test_each_candidate_has_its_own_result_record(
    synthetic_run: tuple[Store, Run, Result],
) -> None:
    store, run, result = synthetic_run
    records = {item.subject: item for item in store.get_results(run.run_id)}
    assert set(records) == {"study"} | {item.candidate_id for item in assessments(result)}
    for item in assessments(result):
        record = records[item.candidate_id]
        assert record.technical_status is item.technical_status
        assert record.evaluation_status is item.evaluation_status
        assert record.site_evidence_status is item.site_evidence_status
        assert record.ranking_eligibility == item.ranking_eligibility
        assert record.synthetic and record.stage_outputs == ()
        kpis = {kpi.definition.kpi_id: kpi for kpi in record.kpis}
        cost = kpis["indicative_route_cost_currency"]
        assert (
            cost.definition.unit == "currency" and "Not a cost estimate" in cost.definition.boundary
        )
        assert cost.derived_from[1] == "policy route-synthetic version 1"


@pytest.mark.requirement("A11")
@pytest.mark.requirement("W12")
def test_candidates_are_ranked_jointly_by_site_and_connection(
    synthetic_run: tuple[Store, Run, Result],
) -> None:
    _, _, result = synthetic_run
    ranked = sorted(
        (item for item in assessments(result) if item.rank is not None),
        key=lambda item: (item.rank or 0, item.label),
    )
    assert [(item.rank, item.label) for item in ranked] == [
        (1, "beta to conn-west by route 1, plant-heat-pump"),
        (2, "alpha to conn-west by route 1, plant-direct"),
        (2, "alpha to conn-west by route 1, plant-heat-pump"),
        (2, "alpha to conn-west by route 1, plant-separated"),
        (5, "alpha to conn-west by route 2, plant-direct"),
        (5, "alpha to conn-west by route 2, plant-heat-pump"),
        (5, "alpha to conn-west by route 2, plant-separated"),
        (8, "beta to conn-east by route 1, plant-heat-pump"),
        (9, "alpha to conn-east by route 1, plant-direct"),
        (9, "alpha to conn-east by route 1, plant-heat-pump"),
        (9, "alpha to conn-east by route 1, plant-separated"),
    ]
    candidates = {
        item["candidate_id"]: item for item in stage(result, "candidates").payload["candidates"]
    }
    # Route 2 from Alpha to the west is the shortest by length. It ranks below the longer
    # route 1, which follows field paths and avoids the stream.
    shortest = next(
        candidates[item.candidate_id]
        for item in ranked
        if item.label.startswith("alpha to conn-west by route 2")
    )
    cheapest = next(
        candidates[item.candidate_id]
        for item in ranked
        if item.label.startswith("alpha to conn-west by route 1")
    )
    assert "shortest_length" in shortest["route_bases"]
    assert "shortest_length" not in cheapest["route_bases"]
    assert shortest["corridor_length_m"] < cheapest["corridor_length_m"]
    assert shortest["indicative_route_cost_currency"] > cheapest["indicative_route_cost_currency"]

    ranking = stage(result, "assess").payload["ranking"]
    assert ranking["eligible_count"] == 11 and ranking["evaluated_count"] == 27
    assert "The order covers the evaluated candidates only" in ranking["statement"]
    assert "not a global optimum" in ranking["statement"]


@pytest.mark.requirement("W07")
def test_candidates_never_mix_a_source_with_a_route_from_another_site(
    synthetic_run: tuple[Store, Run, Result],
) -> None:
    _, _, result = synthetic_run
    payload = stage(result, "candidates").payload
    routes = payload["routes"]
    concepts = {"source-alpha": "alpha", "source-beta": "beta", "source-gamma": "gamma"}
    cases = {item["coupling_case_id"]: item for item in payload["coupling_cases"]}
    for candidate in payload["candidates"]:
        route = routes[candidate["route_id"]]
        assert route["from_site_id"] == candidate["site_id"]
        assert route["to_connection_id"] == candidate["connection_id"]
        assert concepts[candidate["source_scenario_id"]] == candidate["site_id"]
        for case_id in candidate["coupling_case_ids"]:
            case = cases[case_id]
            assert case["source_scenario_id"] == candidate["source_scenario_id"]
            assert case["route_id"] == candidate["route_id"]
    # The identifier is a hash of exactly the identity, so it is unique and stable.
    identifiers = [item["candidate_id"] for item in payload["candidates"]]
    assert len(set(identifiers)) == len(identifiers) == 27


def test_failed_and_rejected_options_are_explained(
    synthetic_run: tuple[Store, Run, Result],
) -> None:
    _, _, result = synthetic_run
    rejected = stage(result, "assess").payload["rejected"]
    assert len(rejected) == 1
    delta = rejected[0]
    assert delta["option"] == {"site_id": "delta"} and delta["stage"] == "screen"
    assert "land_status fail: the land is restricted" in delta["details"]
    assert any(line.startswith("access fail") for line in delta["details"])

    found = by_label(result)
    # Direct heat from Beta fails at the hot end. Its heat pump candidate is kept and passes.
    direct = found["beta to conn-west by route 1, plant-direct"]
    assert direct.technical_status is TechnicalStatus.FAILS_EVALUATED_CASE
    assert direct.evaluation_status is EvaluationStatus.VALID
    assert "exchanger_hot_end_approach" in direct.limiting_conditions
    assert any("are not covered" in line for line in direct.explanation)
    assert not direct.ranking_eligibility.eligible
    pump = found["beta to conn-west by route 1, plant-heat-pump"]
    assert pump.technical_status is TechnicalStatus.PASSES_EVALUATED_CASES
    assert pump.rank == 1


@pytest.mark.requirement("A12")
def test_a_conditional_site_is_evaluated_but_not_ranked(
    synthetic_run: tuple[Store, Run, Result],
) -> None:
    _, _, result = synthetic_run
    gamma = [item for item in assessments(result) if item.label.startswith("gamma")]
    assert len(gamma) == 12
    for item in gamma:
        # The power line west of Gamma has no documented setback. That is never a pass.
        assert item.screening_status.value == "conditional"
        assert item.technical_status is TechnicalStatus.PASSES_EVALUATED_CASES
        assert item.ranking_eligibility.conditional
        assert "the site screening is conditional" in item.ranking_eligibility.reasons
        assert item.rank is None


@pytest.mark.requirement("W07")
def test_route_heat_loss_and_lift_are_modelled_for_each_candidate(
    synthetic_run: tuple[Store, Run, Result],
) -> None:
    _, _, result = synthetic_run
    coupled = stage(result, "couple").payload["candidates"]
    found = by_label(result)
    short = found["beta to conn-west by route 1, plant-heat-pump"]
    long = found["alpha to conn-east by route 1, plant-direct"]

    def point(item: CandidateAssessment) -> dict[str, Any]:
        (case,) = coupled[item.candidate_id].values()
        result_record = CouplingResult.model_validate(case["result"])
        assert result_record.transmission_route_id is not None
        assert not any("route" in line for line in result_record.not_modelled)
        assert result_record.operating_point is not None
        return result_record.operating_point.to_json_dict()

    near, far = point(short), point(long)
    for operating in (near, far):
        # The plant feeds hotter water so that the connection receives its 353.15 K.
        assert operating["plant_supply_temperature_k"] > operating["required_supply_temperature_k"]
        # The plant pump covers the route as well as the 150 kPa held at the connection.
        assert operating["plant_pressure_lift_pa"] > 150_000.0
        assert operating["transmission_pressure_loss_pa"] > 0.0
    # A 4.7 km route loses more heat than a 0.4 km one.
    assert far["transmission_heat_loss_w"] > 5 * near["transmission_heat_loss_w"]


# --- changes and replay ---------------------------------------------------------------------


@pytest.mark.requirement("A15")
def test_changing_a_plant_value_recomputes_only_the_coupling(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    first, before = chain.run(scenario_data)
    assert not any(hits(first).values())

    path = "plants[plant-direct].parameters[exchanger_min_approach_delta_k]"
    second, after = chain.run(changed(scenario_data, path, known(8.0)))
    reused = hits(second)
    assert all(
        reused[name] for name in ("site_evidence", "source", "screen", "routes", "candidates")
    )
    assert not reused["couple"] and not reused["assess"]
    old = by_label(before)["alpha to conn-west by route 1, plant-direct"]
    new = by_label(after)["alpha to conn-west by route 1, plant-direct"]
    flow = "geothermal_mass_flow_kg_s-shoulder-season"
    # A wider approach returns the geothermal stream warmer, so the same heat needs more flow.
    assert (new.kpi(flow) or 0.0) > (old.kpi(flow) or 0.0)

    # A corridor rate: the routes and everything after them change. The source does not.
    policy = policy_variant(
        repository_policies()["route-synthetic"], trench_cost_field_path_currency_per_m=2000.0
    )
    reference = chain.store.register_policy(policy)
    policies = [
        reference.to_json_dict() if item["policy_id"] == "route-synthetic" else item
        for item in scenario_data["policies"]
    ]
    third, rerouted = chain.run({**scenario_data, "policies": policies})
    reused = hits(third)
    assert reused["source"] and reused["screen"]
    assert not reused["routes"] and not reused["candidates"] and not reused["assess"]
    # With expensive field paths the street route from Alpha to the west is the cheaper one.
    pairs = stage(rerouted, "routes").payload["pairs"]
    alpha_west = next(
        pair for pair in pairs if (pair["site_id"], pair["connection_id"]) == ("alpha", "conn-west")
    )
    first_route = alpha_west["routes"][0]
    assert first_route["rank"] == 1 and "shortest_length" in first_route["bases"]


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
    strict = policy_variant(
        repository_policies()["numerical-coupling-default"], max_iterations_count=3
    )
    reference = chain.store.register_policy(strict)
    assert reference == policy_ref(strict)
    policies = [
        reference.to_json_dict() if item["policy_id"] == strict.policy_id else item
        for item in scenario_data["policies"]
    ]
    _, result = chain.run({**scenario_data, "policies": policies})
    # One candidate that does not converge never stops the others.
    assert stage(result, "couple").status is StageStatus.COMPLETED
    found = assessments(result)
    unresolved = [
        item for item in found if item.evaluation_status is EvaluationStatus.UNRESOLVED_NUMERICAL
    ]
    assert unresolved
    for item in unresolved:
        # An unresolved calculation is never a technical failure, and never ranked.
        assert item.technical_status is TechnicalStatus.NOT_ESTABLISHED
        assert item.rank is None
        assert any(
            "did not converge" in line or "were not reached" in line for line in item.explanation
        )
    failed = [
        item for item in found if item.technical_status is TechnicalStatus.FAILS_EVALUATED_CASE
    ]
    assert all(item.evaluation_status is EvaluationStatus.VALID for item in failed)


@pytest.mark.requirement("A12")
def test_an_unknown_plant_input_is_reported_by_the_stage(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    path = "plants[plant-separated].parameters[separation_exchanger_ua_w_k]"
    _, result = chain.run(changed(scenario_data, path, UNKNOWN_INPUT))
    couple = stage(result, "couple")
    assert couple.status is StageStatus.COMPLETED
    assert [item.field_path for item in couple.missing_inputs] == [path]
    found = assessments(result)
    separated = [item for item in found if item.label.endswith("plant-separated")]
    assert len(separated) == 9
    for item in separated:
        assert item.evaluation_status is EvaluationStatus.NOT_EVALUATED
        assert item.technical_status is TechnicalStatus.NOT_ESTABLISHED
        assert f"shoulder-season: needs {path}" in item.explanation
        assert item.rank is None
    # The other candidates are evaluated as before.
    assert by_label(result)["beta to conn-west by route 1, plant-heat-pump"].rank == 1


def test_the_chain_needs_its_inputs_and_policies(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    _, result = chain.run({**scenario_data, "plants": [], "coupling_cases": []})
    assert stage(result, "candidates").status is StageStatus.NOT_ESTABLISHED
    assert stage(result, "candidates").reason == "the scenario defines no plants"
    assert stage(result, "couple").status is StageStatus.NOT_RUN

    for policy_id, kind in (
        ("route-synthetic", "route"),
        ("ranking-synthetic", "ranking"),
        ("numerical-coupling-default", "numerical"),
    ):
        policies = [item for item in scenario_data["policies"] if item["policy_id"] != policy_id]
        _, result = chain.run({**scenario_data, "policies": policies})
        statuses = {output.stage: output for output in result.stage_outputs}
        missing = next(
            output for output in statuses.values() if output.status is StageStatus.NOT_ESTABLISHED
        )
        assert f"policy of kind '{kind}'" in missing.reason


def test_older_scenarios_still_load(scenario_data: dict[str, Any]) -> None:
    older = {
        name: value
        for name, value in scenario_data.items()
        if name not in {"plants", "operating_cases", "coupling_cases", "spatial_layers"}
    }
    for version in ("0.1.0", "0.2.0"):
        scenario = Scenario.model_validate({**older, "schema_version": version})
        assert scenario.schema_version == "0.3.0"
        assert scenario.plants == () and scenario.spatial_layers == ()
    with pytest.raises(ValueError, match="no migration"):
        Scenario.model_validate({**older, "schema_version": "0.0.9"})


# --- the command line -----------------------------------------------------------------------


def run_cli(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, str]:
    code = main(
        ["couple", str(SYNTHETIC_SCENARIO), "--policies", str(POLICY_DIRECTORY), *arguments]
    )
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_couple_command_lists_every_reference_case(capsys: pytest.CaptureFixture[str]) -> None:
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
        in output
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


def test_candidates_and_map_layers_of_a_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = ["--store", str(tmp_path / "store")]
    code = main([*store, "run", str(SYNTHETIC_SCENARIO), "--policies", str(POLICY_DIRECTORY)])
    output = capsys.readouterr().out
    assert code == 0 and "assess         completed        computed" in output
    assert "Candidates (27)" in output
    run_id = next(line.split()[1] for line in output.splitlines() if line.startswith("Run "))

    code = main([*store, "show", run_id])
    shown = capsys.readouterr().out
    assert code == 0
    assert "site gamma: conditional, usable pad 5950 m2" in shown
    assert "power_line has no documented setback" in shown
    assert "alpha-conn-west-1" in shown and "least_cost" in shown
    assert "rejected site_id delta: excluded by the site screening" in shown

    code = main([*store, "candidates", run_id])
    tables = capsys.readouterr().out
    assert code == 0
    assert "Rejected before evaluation (1)" in tables
    assert "land_status fail: the land is restricted" in tables
    assert "technical status is fails_evaluated_case" in tables

    code = main([*store, "candidates", run_id, "--json"])
    document = json.loads(capsys.readouterr().out)
    assert len(document["assessments"]) == 27 and document["ranking"]["eligible_count"] == 11

    layers = tmp_path / "layers"
    code = main([*store, "layers", run_id, str(layers)])
    assert code == 0
    sites = json.loads((layers / "sites.geojson").read_text(encoding="utf-8"))
    parcels = {
        feature["properties"]["site_id"]: feature
        for feature in sites["features"]
        if feature["properties"]["part"] == "parcel"
    }
    assert parcels["delta"]["properties"]["screening_status"] == "excluded"
    # WGS84 longitude and latitude, as RFC 7946 requires.
    longitude, latitude = parcels["alpha"]["geometry"]["coordinates"][0][0]
    assert 8.9 < longitude < 9.1 and 51.4 < latitude < 51.5
    routes = json.loads((layers / "routes.geojson").read_text(encoding="utf-8"))
    assert len(routes["features"]) == 9
    candidates = json.loads((layers / "candidates.geojson").read_text(encoding="utf-8"))
    assert len(candidates["features"]) == 27
