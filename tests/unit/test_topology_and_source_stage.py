"""Network topology checks, the source stage of the pipeline, and schema migrations."""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import FakeClock, changed, known

from r3chain.domain.records.network import Network
from r3chain.domain.records.runs import Policy, Result
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import EvaluationStatus, StageStatus
from r3chain.domain.topology import check_topology
from r3chain.domain.validation import Severity, parse_scenario
from r3chain.pipeline.runner import PipelineRunner
from r3chain.pipeline.stages import SourceEnvelopeStage, default_stages
from r3chain.ports.source import SourceCapabilities, SourceRequest, SourceResult
from r3chain.store.store import Store

PAIRS = [("s0", "r0"), ("s3", "r3")]  # the two connections of the synthetic study

# --- topology -------------------------------------------------------------------------------


def codes(network: Network, pairs: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    issues = check_topology(network, PAIRS if pairs is None else pairs)
    return [(issue.code, issue.severity.value) for issue in issues]


def edited(network: Network, **changes: Any) -> Network:
    return Network.model_validate({**network.to_json_dict(), **changes})


def without(items: list[dict[str, Any]], key: str, *identifiers: str) -> list[dict[str, Any]]:
    return [item for item in items if item[key] not in identifiers]


@pytest.fixture
def network(scenario: Scenario) -> Network:
    return scenario.networks[0]


@pytest.mark.requirement("W06")
def test_the_synthetic_network_has_a_clean_topology(network: Network) -> None:
    assert check_topology(network, PAIRS) == ()


@pytest.mark.requirement("W06")
def test_disconnected_parts_are_found(network: Network) -> None:
    data = network.to_json_dict()
    # Cut both pipes between the second and third node pair. Consumers 3 and 4 with the eastern
    # connection are then a separate part.
    cut = edited(network, pipes=without(data["pipes"], "pipe_id", "supply-1-2", "return-2-1"))
    issues = check_topology(cut, [("s0", "r0")])
    island = next(issue for issue in issues if issue.code == "island")
    assert island.severity.value == "error"
    assert set(island.element_ids) == {"s2", "s3", "s4", "r2", "r3", "r4"}
    # Which part is the main one follows from the pressure reference, not from its size.
    assert "s0" not in island.element_ids

    lonely = {"node_id": "s9", "circuit": "supply", "elevation_m": 160.0, "zone_id": "zone-main"}
    issues = check_topology(edited(network, nodes=[*data["nodes"], lonely]), PAIRS)
    assert [(issue.code, issue.element_ids) for issue in issues] == [("isolated_node", ("s9",))]


def test_a_consumer_without_pipes_is_found(network: Network) -> None:
    data = network.to_json_dict()
    unpiped = edited(network, pipes=without(data["pipes"], "pipe_id", "supply-3-4", "return-4-3"))
    found = check_topology(unpiped, PAIRS)
    issue = next(item for item in found if item.code == "consumer_not_connected")
    assert issue.element_ids == ("consumer-4",) and "s4" in issue.message


def test_duplicates_dead_ends_and_unpaired_pipes_are_warnings(network: Network) -> None:
    data = network.to_json_dict()

    duplicate = {**data["pipes"][0], "pipe_id": "supply-0-1-again"}
    assert codes(edited(network, pipes=[*data["pipes"], duplicate])) == [
        ("duplicate_pipe", "warning")
    ]

    # Without consumer 4 the last pipe pair leads nowhere.
    stub = edited(network, consumers=without(data["consumers"], "consumer_id", "consumer-4"))
    assert codes(stub) == [("dead_end", "warning"), ("dead_end", "warning")]

    # A supply pipe whose return pipe is missing.
    one_sided = edited(network, pipes=without(data["pipes"], "pipe_id", "return-2-1"))
    found = check_topology(one_sided, PAIRS)
    unpaired = next(issue for issue in found if issue.code == "unpaired_pipe")
    assert unpaired.element_ids == ("supply-1-2",) and "return" in unpaired.message


def test_topology_is_part_of_scenario_validation(scenario_data: dict[str, Any]) -> None:
    scenario, report = parse_scenario(scenario_data)
    assert scenario is not None and report.issues == ()

    pipes = without(scenario_data["networks"][0]["pipes"], "pipe_id", "supply-1-2", "return-2-1")
    broken = changed(scenario_data, "networks[syn-network].pipes", pipes)
    scenario, report = parse_scenario(broken)
    assert scenario is not None and not report.executable
    assert [(issue.code, issue.field_path) for issue in report.errors] == [
        ("topology_island", "networks[syn-network]")
    ]

    duplicate = {**scenario_data["networks"][0]["pipes"][0], "pipe_id": "supply-0-1-again"}
    doubled = changed(
        scenario_data,
        "networks[syn-network].pipes",
        [*scenario_data["networks"][0]["pipes"], duplicate],
    )
    scenario, report = parse_scenario(doubled)
    assert scenario is not None and report.executable
    assert [(issue.severity, issue.code) for issue in report.issues] == [
        (Severity.WARNING, "topology_duplicate_pipe")
    ]


# --- the source stage -----------------------------------------------------------------------


class Chain:
    def __init__(self, store: Store, clock: FakeClock) -> None:
        self.store = store
        self.runner = PipelineRunner(store, default_stages(), clock=clock, code_version_text="t")

    def run(self, data: dict[str, Any]) -> tuple[Any, Result]:
        scenario = Scenario.model_validate(data)
        revision = self.store.add_revision(scenario, author="t", rationale="t").revision_id
        return self.runner.run(revision)


@pytest.fixture
def chain(store: Store, clock: FakeClock) -> Chain:
    return Chain(store, clock)


def stage(result: Result, name: str) -> Any:
    return next(output for output in result.stage_outputs if output.stage == name)


def test_source_stage_evaluates_every_well_concept(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    _, result = chain.run(scenario_data)
    source = stage(result, "source")
    assert source.status is StageStatus.COMPLETED
    assert source.payload["policy_id"] == "source-envelope-synthetic"
    sources = source.payload["sources"]
    assert set(sources) == {"source-alpha", "source-beta", "source-gamma"}

    # The grid of the policy is 10 flows by 4 injection temperatures.
    counts = {name: item["summary"]["points"] for name, item in sources.items()}
    assert all(sum(points.values()) == 40 for points in counts.values())
    # Each source reaches its drawdown limit at a different flow:
    # alpha at 13.6 kg/s, beta at 7.6 kg/s, gamma at 22 kg/s, which is beyond the grid.
    assert counts["source-alpha"] == {"completed": 24, "physical_limit_exceeded": 16}
    assert counts["source-beta"] == {"completed": 12, "physical_limit_exceeded": 28}
    assert counts["source-gamma"] == {"completed": 40}

    alpha = sources["source-alpha"]
    assert alpha["engine"] == "r3chain-synthetic-source" and alpha["synthetic"]
    assert alpha["summary"]["at_mass_flow_kg_s"] == pytest.approx(12.0)
    assert alpha["summary"]["at_injection_temperature_k"] == pytest.approx(303.15)
    assert 3.4e6 < alpha["summary"]["largest_heat_w"] < 3.5e6
    assert 0.0 < alpha["summary"]["max_rel_heat_error_fraction"] < 0.02
    assert len(alpha["envelope"]["points"]) == 40


@pytest.mark.requirement("W12")
def test_a_test_engine_labels_the_result_synthetic(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    _, result = chain.run(scenario_data)
    assert stage(result, "source").synthetic
    assert not stage(result, "site_evidence").synthetic
    assert result.synthetic
    # The network and coupling calculations have not run, so the study has no technical result.
    assert stage(result, "screen").status is StageStatus.NOT_ESTABLISHED
    assert stage(result, "couple").status is StageStatus.NOT_RUN
    assert result.evaluation_status is EvaluationStatus.NOT_EVALUATED
    assert result.kpis == ()


@pytest.mark.requirement("W09")
def test_a_model_family_without_an_engine_is_unsupported(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    data = changed(scenario_data, "well_scenarios[source-beta].model_family", "pydoublet-carbonate")
    _, result = chain.run(data)
    source = stage(result, "source")
    assert source.status is StageStatus.UNSUPPORTED_MODEL
    assert "source-beta" in source.reason
    beta = source.payload["sources"]["source-beta"]
    assert beta == {
        "state": "unsupported_model",
        "reason": "no engine supports model family 'pydoublet-carbonate'",
    }
    # No performance is invented for it, and the supported sources are still evaluated.
    assert source.payload["sources"]["source-alpha"]["state"] == "completed"
    assert result.evaluation_status is EvaluationStatus.UNSUPPORTED


@pytest.mark.requirement("A12")
def test_an_unknown_source_input_is_reported_and_not_defaulted(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    path = "well_scenarios[source-gamma].reservoir_parameters[permeability_m2]"
    unknown = {"state": "unknown", "reason": "no core data yet", "owner": "geologist"}
    _, result = chain.run(changed(scenario_data, path, unknown))
    source = stage(result, "source")
    assert source.status is StageStatus.NOT_ESTABLISHED
    assert [(item.field_path, item.owner) for item in source.missing_inputs] == [
        (path, "geologist")
    ]
    assert source.payload["sources"]["source-gamma"]["state"] == "not_established"
    assert "summary" not in source.payload["sources"]["source-gamma"]


def test_source_stage_needs_exactly_one_source_policy(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    policies = [
        item
        for item in scenario_data["policies"]
        if item["policy_id"] != "source-envelope-synthetic"
    ]
    _, result = chain.run({**scenario_data, "policies": policies})
    source = stage(result, "source")
    assert source.status is StageStatus.NOT_ESTABLISHED
    assert "exactly one policy of kind 'source'" in source.reason


@pytest.mark.requirement("A15")
def test_only_the_stage_that_depends_on_a_change_is_recomputed(
    chain: Chain, scenario_data: dict[str, Any]
) -> None:
    first, _ = chain.run(scenario_data)
    hits = {record.stage: record.cache_hit for record in first.stages}
    assert hits["site_evidence"] is False and hits["source"] is False

    # A reservoir parameter changes the source stage only.
    temperature = "well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]"
    second, second_result = chain.run(changed(scenario_data, temperature, known(388.15)))
    hits = {record.stage: record.cache_hit for record in second.stages}
    assert hits["site_evidence"] is True and hits["source"] is False
    warmer = stage(second_result, "source").payload["sources"]["source-alpha"]["summary"]
    assert warmer["largest_heat_w"] > 3.5e6

    # A site input changes the site stage only.
    third, _ = chain.run(changed(scenario_data, "sites[delta].usable_pad_area_m2", known(5400)))
    hits = {record.stage: record.cache_hit for record in third.stages}
    assert hits["site_evidence"] is False and hits["source"] is True


def test_a_new_engine_version_recomputes_the_source_stage(
    store: Store, clock: FakeClock, scenario: Scenario
) -> None:
    class NewerEngine:
        """Stands for the same engine in a later version."""

        def __init__(self) -> None:
            from r3chain.adapters.source_synthetic import SyntheticSourceModel

            self._inner = SyntheticSourceModel()

        def capabilities(self) -> SourceCapabilities:
            return self._inner.capabilities().model_copy(update={"engine_version": "9.9.9"})

        def evaluate(self, request: SourceRequest) -> SourceResult:
            return self._inner.evaluate(request)

    revision = store.add_revision(scenario, author="t", rationale="t").revision_id
    current = PipelineRunner(store, default_stages(), clock=clock, code_version_text="t")
    current.run(revision)
    newer = PipelineRunner(
        store, default_stages([NewerEngine()]), clock=clock, code_version_text="t"
    )
    run, result = newer.run(revision)
    hits = {record.stage: record.cache_hit for record in run.stages}
    # A result of an older engine version is never reused for a newer one.
    assert hits == {
        "site_evidence": True,
        "source": False,
        "screen": True,
        "couple": False,
        "operate": False,
        "cost": False,
        "compare": False,
    }
    versions = {
        item["engine_version"] for item in stage(result, "source").payload["sources"].values()
    }
    assert versions == {"9.9.9"}


def test_source_stage_is_a_stage_like_any_other() -> None:
    engine = default_stages()[1]
    assert isinstance(engine, SourceEnvelopeStage)
    assert (engine.name, engine.requires, engine.stochastic) == ("source", (), False)


# --- schema migrations ----------------------------------------------------------------------


def test_records_written_before_this_milestone_still_load() -> None:
    old_policy = {
        "schema_version": "0.1.0",
        "policy_id": "screening-synthetic",
        "kind": "screening",
        "version": "1",
        "title": "Site screening criteria",
        "origin": "written by milestone M0",
        "status": "unreviewed",
        "parameters": {"min_usable_pad_area_m2": 5000.0},
    }
    policy = Policy.model_validate(old_policy)
    assert policy.schema_version == "0.2.0" and policy.kind.value == "screening"

    old_result = {
        "schema_version": "0.1.0",
        "result_id": "res-old",
        "run_id": "run-old",
        "scenario_revision": "rev-old",
        "subject": "study",
        "synthetic": True,
        "technical_status": "not_established",
        "evaluation_status": "not_evaluated",
        "ranking_eligibility": {"eligible": False, "conditional": False, "reasons": []},
        "stage_outputs": [
            {"stage": "site_evidence", "stage_version": "1.0.0", "status": "completed"}
        ],
    }
    result = Result.model_validate(old_result)
    assert result.schema_version == "0.2.0"
    # A stage output written before the flag existed did not come from a test engine.
    assert result.stage_outputs[0].synthetic is False
