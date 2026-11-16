"""The store and the pipeline runner: immutability, dependency hashing, reuse and replay."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import POLICY_DIRECTORY, SYNTHETIC_SCENARIO, FakeClock, changed, known

from r3chain.domain.evidence import InputReader
from r3chain.domain.records.runs import Run, RunStatus, StageOutput
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import EvaluationStatus, StageStatus, TechnicalStatus
from r3chain.pipeline.runner import (
    PipelineRunner,
    ScenarioNotExecutableError,
    StageContext,
)
from r3chain.pipeline.stages import default_stages
from r3chain.store.store import (
    ImmutabilityError,
    NotFoundError,
    Store,
    load_policies,
    policy_ref,
)

# --- store ----------------------------------------------------------------------------------


@pytest.mark.requirement("W01")
def test_dataset_version_cannot_change(store: Store, scenario: Scenario) -> None:
    dataset = scenario.datasets[0]
    source = SYNTHETIC_SCENARIO.parent / dataset.source_uri
    store.register_dataset(dataset, source)
    store.register_dataset(dataset, source)  # storing the same thing again is harmless
    assert store.get_dataset("syn-definitions", dataset.version) == dataset
    assert store.read_bytes(dataset.checksum) == source.read_bytes()

    # The same identifier and version with different content is refused.
    altered = dataset.model_copy(update={"owner": "someone else"})
    with pytest.raises(ImmutabilityError, match="new version"):
        store.register_dataset(altered)
    # A new version is the way to change a dataset.
    store.register_dataset(altered.model_copy(update={"version": "next"}))
    assert store.get_dataset("syn-definitions", "next").owner == "someone else"

    with pytest.raises(NotFoundError):
        store.get_dataset("syn-definitions", "never-registered")


def test_a_file_must_match_its_recorded_checksum(
    store: Store, scenario: Scenario, tmp_path: Path
) -> None:
    other = tmp_path / "definitions.md"
    other.write_text("edited after registration", encoding="utf-8")
    with pytest.raises(ImmutabilityError, match="differs from the recorded checksum"):
        store.register_dataset(scenario.datasets[0], other)


def test_a_stored_file_that_was_altered_is_detected(store: Store) -> None:
    checksum = store.put_bytes(b"original content")
    assert store.has_blob(checksum)
    assert store.read_bytes(checksum) == b"original content"
    stored = next((store.root / "blobs").rglob("*"), None)
    files = [path for path in (store.root / "blobs").rglob("*") if path.is_file()]
    assert stored is not None and len(files) == 1
    files[0].write_bytes(b"tampered content")
    with pytest.raises(ImmutabilityError, match="no longer matches"):
        store.read_bytes(checksum)


@pytest.mark.requirement("W19")
def test_policy_version_cannot_change(store: Store) -> None:
    policy = load_policies(POLICY_DIRECTORY)[0]
    reference = store.register_policy(policy)
    assert reference == policy_ref(policy)
    assert store.get_policy(policy.policy_id, policy.version) == policy
    # A changed threshold under the same version is refused. It needs a new version.
    loosened = policy.model_copy(
        update={"parameters": {**policy.parameters, "max_iterations_count": 500.0}}
    )
    with pytest.raises(ImmutabilityError):
        store.register_policy(loosened)
    newer = store.register_policy(loosened.model_copy(update={"version": "2"}))
    assert newer.policy_hash != reference.policy_hash


@pytest.mark.requirement("W19")
def test_revision_records_field_level_changes(
    store: Store, scenario: Scenario, scenario_data: dict[str, Any]
) -> None:
    first = store.add_revision(scenario, author="planner", rationale="initial import")
    assert first.revision_id == scenario.revision_id()
    assert first.parent_revision_id is None and first.changes == ()
    assert store.get_scenario(first.revision_id) == scenario

    # Freezing identical content again gives the same revision, not a second one.
    again = store.add_revision(scenario, author="someone", rationale="again")
    assert again == first

    path = "connections[conn-east].limits.max_pressure"
    resolved = Scenario.model_validate(
        changed(scenario_data, path, known({"value_pa": 1_000_000, "reference": "absolute"}))
    )
    second = store.add_revision(
        resolved,
        author="network planner",
        rationale="pressure rating supplied",
        parent_revision_id=first.revision_id,
    )
    assert second.revision_id != first.revision_id
    assert second.parent_revision_id == first.revision_id
    assert [(change.field_path, change.kind.value) for change in second.changes] == [
        (path, "changed")
    ]
    assert second.changes[0].before["state"] == "unknown"
    assert second.changes[0].after["value"] == {"value_pa": 1_000_000.0, "reference": "absolute"}

    # The parent is unchanged and both remain readable.
    assert store.get_scenario(first.revision_id) == scenario
    assert [item.revision_id for item in store.list_revisions("synthetic-study")] == [
        first.revision_id,
        second.revision_id,
    ]
    with pytest.raises(NotFoundError):
        store.get_revision("rev-0000000000000000")


@pytest.mark.requirement("W24")
def test_change_paths_match_the_assumption_register(
    store: Store, scenario: Scenario, scenario_data: dict[str, Any]
) -> None:
    """A change is recorded under the same path as the assumption it changes."""
    from r3chain.domain.assumptions import assumption_register

    paths = [
        "well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]",
        "sites[delta].constraints[housing-setback].outcome",
        "connections[conn-east].limits.max_pressure",
        "economic_scenarios[econ-base].cost_items[route-alpha-west-capex].amount",
    ]
    registered = {row.field_path for row in assumption_register(scenario)}
    assert set(paths) <= registered

    data = scenario_data
    for path, value in zip(
        paths,
        (388.15, "satisfied", {"value_pa": 1_000_000, "reference": "absolute"}, 900_000),
        strict=True,
    ):
        data = changed(data, path, known(value))
    first = store.add_revision(scenario, author="t", rationale="t")
    second = store.add_revision(
        Scenario.model_validate(data),
        author="t",
        rationale="t",
        parent_revision_id=first.revision_id,
    )
    assert sorted(change.field_path for change in second.changes) == sorted(paths)

    # A removed element is named by the path it had in the parent.
    fewer = {**scenario_data, "routes": scenario_data["routes"][:2]}
    third = store.add_revision(
        Scenario.model_validate(fewer),
        author="t",
        rationale="t",
        parent_revision_id=first.revision_id,
    )
    assert [(c.field_path, c.kind.value) for c in third.changes] == [
        ("routes[route-gamma-east]", "removed")
    ]


def test_a_revision_altered_in_the_database_is_detected(store: Store, scenario: Scenario) -> None:
    revision = store.add_revision(scenario, author="planner", rationale="initial import")
    altered = scenario.model_dump_json().replace("6000.0", "9000.0", 1)
    connection = sqlite3.connect(store.root / "r3chain.db")
    connection.execute(
        "UPDATE revisions SET scenario_json = ? WHERE revision_id = ?",
        (altered, revision.revision_id),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ImmutabilityError, match="no longer matches its hash"):
        store.get_scenario(revision.revision_id)


def test_a_run_is_finalized_exactly_once(store: Store, clock: FakeClock) -> None:
    run = Run(
        run_id="run-test",
        scenario_revision="rev-test",
        input_hashes={},
        code_version="test",
        engine_versions={},
        seed=0,
        status=RunStatus.RUNNING,
        created_at=clock(),
    )
    store.create_run(run)
    done = run.model_copy(update={"status": RunStatus.COMPLETED, "finished_at": clock()})
    store.finalize_run(done)
    assert store.get_run("run-test").status is RunStatus.COMPLETED
    with pytest.raises(ImmutabilityError):
        store.finalize_run(done.model_copy(update={"seed": 99}))
    with pytest.raises(ValueError):
        store.create_run(done)


# --- pipeline -------------------------------------------------------------------------------


class TotalPadArea:
    """Adds up the usable pad areas. It needs every one of them to be known."""

    name = "total_pad_area"
    version = "1"
    requires: tuple[str, ...] = ()
    stochastic = False

    def __init__(self) -> None:
        self.calls = 0

    def input_fingerprint(self, context: StageContext) -> Any:
        return {
            site.site_id: site.usable_pad_area_m2.model_dump(mode="json")
            for site in context.scenario.sites
        }

    def run(self, context: StageContext) -> StageOutput:
        self.calls += 1
        reader = InputReader()
        areas = [
            reader.get(site.usable_pad_area_m2, f"sites[{site.site_id}].usable_pad_area_m2")
            for site in context.scenario.sites
        ]
        reader.raise_if_missing()
        return StageOutput(
            stage=self.name,
            stage_version=self.version,
            status=StageStatus.COMPLETED,
            payload={"total_m2": sum(area for area in areas if area is not None)},
        )


class AreaTimesRate:
    """Depends on the stage above and on the discount rate."""

    name = "area_times_rate"
    version = "1"
    requires = ("total_pad_area",)
    stochastic = False

    def __init__(self) -> None:
        self.calls = 0

    def input_fingerprint(self, context: StageContext) -> Any:
        return context.scenario.economic_scenarios[0].discount_rate_per_year.model_dump(mode="json")

    def run(self, context: StageContext) -> StageOutput:
        self.calls += 1
        reader = InputReader()
        rate = reader.get(context.scenario.economic_scenarios[0].discount_rate_per_year, "rate")
        reader.raise_if_missing()
        assert rate is not None
        total = context.upstream["total_pad_area"].payload["total_m2"]
        return StageOutput(
            stage=self.name,
            stage_version=self.version,
            status=StageStatus.COMPLETED,
            payload={"value": total * rate},
        )


class Chain:
    def __init__(self, store: Store, clock: FakeClock) -> None:
        self.first, self.second = TotalPadArea(), AreaTimesRate()
        self.store = store
        self.runner = PipelineRunner(
            store, [self.first, self.second], clock=clock, code_version_text="test"
        )

    def freeze(self, data: dict[str, Any], parent: str | None = None) -> str:
        scenario = Scenario.model_validate(data)
        return self.store.add_revision(
            scenario, author="test", rationale="test", parent_revision_id=parent
        ).revision_id

    def calls(self) -> tuple[int, int]:
        return self.first.calls, self.second.calls


@pytest.fixture
def chain(store: Store, clock: FakeClock) -> Chain:
    return Chain(store, clock)


@pytest.fixture
def complete_data(scenario_data: dict[str, Any]) -> dict[str, Any]:
    """The synthetic scenario with the unknown pad area of site Delta resolved."""
    return changed(scenario_data, "sites[delta].usable_pad_area_m2", known(5400))


@pytest.mark.requirement("A12")
def test_a_missing_input_gives_no_number(chain: Chain, scenario_data: dict[str, Any]) -> None:
    run, result = chain.runner.run(chain.freeze(scenario_data))
    first, second = result.stage_outputs
    assert first.status is StageStatus.NOT_ESTABLISHED
    assert first.payload == {}
    assert [item.field_path for item in first.missing_inputs] == ["sites[delta].usable_pad_area_m2"]
    # The stage after it does not run and does not invent an input.
    assert second.status is StageStatus.NOT_RUN and chain.calls() == (1, 0)
    assert result.technical_status is TechnicalStatus.NOT_ESTABLISHED
    assert result.evaluation_status is EvaluationStatus.NOT_EVALUATED
    assert not result.ranking_eligibility.eligible
    assert run.status is RunStatus.COMPLETED


@pytest.mark.requirement("A15")
def test_changed_input_recomputes_dependent_stages(
    chain: Chain, complete_data: dict[str, Any]
) -> None:
    base = chain.freeze(complete_data)
    first_run, first_result = chain.runner.run(base)
    # 6000 + 5200 + 5600 + 5400 = 22200 m2, times 0.04 = 888.
    assert first_result.stage_outputs[0].payload == {"total_m2": 22200.0}
    assert first_result.stage_outputs[1].payload["value"] == pytest.approx(888.0)
    assert first_result.technical_status is TechnicalStatus.PASSES_EVALUATED_CASES
    assert chain.calls() == (1, 1)

    # The same revision again: nothing is recomputed, and the run says what it reused.
    second_run, _ = chain.runner.run(base)
    assert chain.calls() == (1, 1)
    assert [stage.cache_hit for stage in second_run.stages] == [True, True]
    assert {stage.reused_from_run_id for stage in second_run.stages} == {first_run.run_id}
    assert second_run.parent_run_ids == (first_run.run_id,)
    assert second_run.result_hash == first_run.result_hash

    # A field that neither stage depends on: both stages are validly reused.
    unrelated = chain.freeze(
        changed(complete_data, "routes[route-alpha-west].corridor_length_m", 1250), base
    )
    assert unrelated != base
    assert [stage.cache_hit for stage in chain.runner.run(unrelated)[0].stages] == [True, True]
    assert chain.calls() == (1, 1)

    # The discount rate: only the stage that depends on it is recomputed.
    rate_path = "economic_scenarios[econ-base].discount_rate_per_year"
    rate_run, rate_result = chain.runner.run(
        chain.freeze(changed(complete_data, rate_path, known(0.05)), base)
    )
    assert [stage.cache_hit for stage in rate_run.stages] == [True, False]
    assert rate_result.stage_outputs[1].payload["value"] == pytest.approx(1110.0)
    assert chain.calls() == (1, 2)

    # A pad area: the first stage is recomputed, and so is everything after it. A stale result
    # is never reused.
    area_run, area_result = chain.runner.run(
        chain.freeze(changed(complete_data, "sites[alpha].usable_pad_area_m2", known(7000)), base)
    )
    assert [stage.cache_hit for stage in area_run.stages] == [False, False]
    assert area_result.stage_outputs[0].payload == {"total_m2": 23200.0}
    assert area_result.stage_outputs[1].payload["value"] == pytest.approx(928.0)
    assert chain.calls() == (2, 3)
    assert area_run.result_hash != first_run.result_hash


@pytest.mark.requirement("A17")
def test_replay_is_identical(chain: Chain, complete_data: dict[str, Any]) -> None:
    original, _ = chain.runner.run(chain.freeze(complete_data))
    outcome = chain.runner.replay(original.run_id)
    assert outcome.identical
    assert outcome.original_hash == outcome.replay_hash == original.result_hash
    assert outcome.engine_versions_changed == ()
    # A replay recomputes everything from the frozen inputs. It does not read the cache.
    assert chain.calls() == (2, 2)
    replayed = chain.store.get_run(outcome.replay_run_id)
    assert [stage.cache_hit for stage in replayed.stages] == [False, False]
    assert replayed.parent_run_ids == (original.run_id,)
    assert replayed.run_id != original.run_id


def test_replay_reports_a_changed_stage_version(
    chain: Chain, complete_data: dict[str, Any]
) -> None:
    original, _ = chain.runner.run(chain.freeze(complete_data))
    chain.first.version = "2"
    outcome = chain.runner.replay(original.run_id)
    assert not outcome.identical
    assert outcome.engine_versions_changed == ("stage:total_pad_area",)


def test_a_tool_failure_is_recorded_and_never_cached(
    store: Store, clock: FakeClock, complete_data: dict[str, Any]
) -> None:
    class Broken(TotalPadArea):
        name = "broken"

        def run(self, context: StageContext) -> StageOutput:
            self.calls += 1
            raise RuntimeError("engine crashed")

    stage = Broken()
    runner = PipelineRunner(store, [stage], clock=clock, code_version_text="test")
    revision = store.add_revision(
        Scenario.model_validate(complete_data), author="t", rationale="t"
    ).revision_id
    _, result = runner.run(revision)
    output = result.stage_outputs[0]
    assert output.status is StageStatus.TOOL_FAILURE
    assert output.reason == "RuntimeError: engine crashed"
    assert result.evaluation_status is EvaluationStatus.TOOL_FAILURE
    assert result.technical_status is TechnicalStatus.NOT_ESTABLISHED
    # A failure of the tool says nothing about the inputs, so it is tried again next time.
    runner.run(revision)
    assert stage.calls == 2
    with pytest.raises(RuntimeError):
        runner.run(revision, raise_on_error=True)


def test_only_a_stochastic_stage_depends_on_the_seed(
    store: Store, clock: FakeClock, complete_data: dict[str, Any]
) -> None:
    deterministic, stochastic = TotalPadArea(), TotalPadArea()
    stochastic.name, stochastic.stochastic = "sampled", True
    runner = PipelineRunner(store, [deterministic, stochastic], clock=clock, code_version_text="t")
    revision = store.add_revision(
        Scenario.model_validate(complete_data), author="t", rationale="t"
    ).revision_id
    runner.run(revision, seed=1)
    runner.run(revision, seed=2)
    runner.run(revision, seed=1)
    assert (deterministic.calls, stochastic.calls) == (1, 2)


def test_an_invalid_scenario_is_not_executed(chain: Chain, complete_data: dict[str, Any]) -> None:
    revision = chain.freeze(changed(complete_data, "study.baseline_id", "nothing"))
    with pytest.raises(ScenarioNotExecutableError) as raised:
        chain.runner.run(revision)
    assert raised.value.report.errors[0].code == "unknown_reference"
    assert chain.calls() == (0, 0)


def test_a_policy_must_match_the_hash_in_the_scenario(
    chain: Chain, complete_data: dict[str, Any]
) -> None:
    wrong = "sha256:" + "0" * 64
    data = changed(complete_data, "policies[screening-synthetic].policy_hash", wrong)
    with pytest.raises(ValueError, match="does not match the hash"):
        chain.runner.run(chain.freeze(data))


def test_stage_order_is_checked(store: Store) -> None:
    with pytest.raises(ValueError, match="do not run before it"):
        PipelineRunner(store, [AreaTimesRate(), TotalPadArea()], code_version_text="t")
    with pytest.raises(ValueError, match="appears twice"):
        PipelineRunner(store, [TotalPadArea(), TotalPadArea()], code_version_text="t")


# --- the default chain ----------------------------------------------------------------------


@pytest.mark.requirement("W12")
def test_synthetic_values_label_the_result(
    store: Store, clock: FakeClock, scenario: Scenario, scenario_data: dict[str, Any]
) -> None:
    runner = PipelineRunner(store, default_stages(), clock=clock, code_version_text="test")
    revision = store.add_revision(scenario, author="t", rationale="t").revision_id
    run, result = runner.run(revision)
    assert result.synthetic

    statuses = {output.stage: output.status.value for output in result.stage_outputs}
    assert statuses == {
        "site_evidence": "completed",
        "source": "completed",
        "screen": "not_established",
        "couple": "not_run",
        "operate": "not_run",
        "cost": "not_run",
        "compare": "not_run",
    }
    sites = result.stage_outputs[0].payload["sites"]
    assert {site: summary["evidence_status"] for site, summary in sites.items()} == {
        "alpha": "documented",
        "beta": "documented",
        "gamma": "documented",
        "delta": "conflict",
    }
    assert "sites[delta].access" in sites["delta"]["inputs_not_established"]
    # A placeholder never produces a number and never passes a check.
    screen = next(output for output in result.stage_outputs if output.stage == "screen")
    assert screen.payload == {} and "milestone M3" in screen.reason
    assert result.technical_status is TechnicalStatus.NOT_ESTABLISHED
    assert result.kpis == () and result.checks == ()
    assert set(run.input_hashes) == {"site_evidence", "source", "screen"}
    assert store.get_results(run.run_id) == (result,)

    # Synthetic values inside a study that is not marked synthetic still label the result.
    pilot = Scenario.model_validate(changed(scenario_data, "study.synthetic", False))
    _, pilot_result = runner.run(store.add_revision(pilot, author="t", rationale="t").revision_id)
    assert pilot_result.synthetic
