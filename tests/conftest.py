"""Shared fixtures."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from r3chain.adapters.source_synthetic import SyntheticSourceModel
from r3chain.coupling.cases import CaseOutcome, MemoNetworkModel, evaluate_case
from r3chain.domain.paths import PatchOperation, apply_patch
from r3chain.domain.records.runs import Policy
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.validation import read_scenario_data
from r3chain.ports.network import NetworkCase
from r3chain.ports.source import (
    SourceNumericalSettings,
    SourceOperatingPoint,
    SourceRequest,
    build_source_request,
)
from r3chain.store.store import Store, load_policies

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_SCENARIO = REPO_ROOT / "examples" / "synthetic" / "scenario.yaml"
POLICY_DIRECTORY = REPO_ROOT / "policies"

SYNTHETIC = {"provenance_type": "synthetic", "source_id": "syn-definitions"}


# The two geometry defects that the synthetic study contains on purpose (definitions.md, 9).
IMPORT_WARNINGS = (
    ("geometry_repaired", "spatial_layers[restrictions].features[R1]"),
    ("feature_rejected", "spatial_layers[restrictions].features[R4]"),
)


# An engineering input that is not available yet.
UNKNOWN_INPUT = {"state": "unknown", "reason": "not supplied yet", "owner": "study owner"}


def known(value: Any) -> dict[str, Any]:
    """An engineering input with synthetic provenance, as JSON data."""
    return {"state": "known", "value": value, "provenance": dict(SYNTHETIC)}


def changed(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Return a copy of scenario data with one value replaced."""
    return apply_patch(data, [PatchOperation("set", path, value)])


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def _synthetic_data() -> dict[str, Any]:
    """The synthetic scenario with its GIS layers imported, read once per session."""
    data, problems = read_scenario_data(SYNTHETIC_SCENARIO)
    assert problems == [], problems
    assert isinstance(data, dict)
    return data


@pytest.fixture
def scenario_data(_synthetic_data: dict[str, Any]) -> dict[str, Any]:
    """The synthetic scenario as plain data. Each test gets its own copy."""
    return copy.deepcopy(_synthetic_data)


@pytest.fixture
def scenario(scenario_data: dict[str, Any]) -> Scenario:
    return Scenario.model_validate(scenario_data)


class FakeClock:
    """A clock that advances by one second on every reading, so tests are deterministic."""

    def __init__(self) -> None:
        self._now = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> Iterator[Store]:
    """An empty store with the repository policies registered."""
    instance = Store(tmp_path / "store", clock=clock)
    for policy in load_policies(POLICY_DIRECTORY):
        instance.register_policy(policy)
    yield instance
    instance.close()


# --- helpers for the engine tests -----------------------------------------------------------

DESIGN_HEAT_W = {
    "consumer-1": 800_000.0,
    "consumer-2": 1_200_000.0,
    "consumer-3": 600_000.0,
    "consumer-4": 400_000.0,
}
FIXED_FLOW_KG_S = {"consumer-1": 6.0, "consumer-2": 9.0, "consumer-3": 5.0, "consumer-4": 3.0}
NETWORK_NUMERICAL = {
    "max_iterations_count": 30,
    "pipe_sections_count": 5,
    "friction_model": "swamee_jain",
    "zero_flow_tolerance_kg_s": 1e-6,
}


def supply_point(**changes: Any) -> dict[str, Any]:
    point = {
        "supply_point_id": "west",
        "supply_node": "s0",
        "return_node": "r0",
        "supply_temperature_k": 353.15,
        "return_pressure": {"value_pa": 750_000.0, "reference": "absolute"},
        "pressure_lift_pa": 150_000.0,
    }
    return {**point, **changes}


def thermal_case(heats: dict[str, float] | None = None, **changes: Any) -> NetworkCase:
    """A thermal case of the synthetic network. Consumers return water at 323.15 K."""
    loads = [
        {"consumer_id": consumer, "heat_w": heat, "return_temperature_k": 323.15}
        for consumer, heat in (heats or DESIGN_HEAT_W).items()
    ]
    data = {
        "case_id": "thermal",
        "network_id": "syn-network",
        "calculation": "thermal",
        "loads": loads,
        "supply_points": [supply_point()],
        "ground_temperature_k": 283.15,
        "numerical": dict(NETWORK_NUMERICAL),
    }
    return NetworkCase.model_validate({**data, **changes})


def hydraulic_case(flows: dict[str, float] | None = None, **changes: Any) -> NetworkCase:
    """A hydraulic case of the synthetic network with fixed consumer mass flows."""
    loads = [
        {"consumer_id": consumer, "heat_w": 0.0, "mass_flow_kg_s": flow}
        for consumer, flow in (flows or FIXED_FLOW_KG_S).items()
    ]
    data = {
        "case_id": "hydraulic",
        "network_id": "syn-network",
        "calculation": "hydraulic",
        "loads": loads,
        "supply_points": [supply_point()],
        "ground_temperature_k": 283.15,
        "numerical": dict(NETWORK_NUMERICAL),
    }
    return NetworkCase.model_validate({**data, **changes})


def source_request(scenario: Scenario, source_scenario_id: str = "source-alpha") -> SourceRequest:
    """A request for the synthetic test model at 10 kg/s and 313.15 K injection."""
    model = SyntheticSourceModel()
    well_scenario = next(
        item for item in scenario.well_scenarios if item.source_scenario_id == source_scenario_id
    )
    return build_source_request(
        study_id=scenario.study.study_id,
        well_scenario=well_scenario,
        operating_point=SourceOperatingPoint(
            requested_mass_flow_kg_s=10.0, injection_temperature_k=313.15
        ),
        numerical=SourceNumericalSettings(
            max_iterations_count=50, temperature_tolerance_delta_k=1e-9
        ),
        capabilities=model.capabilities(),
    )


# --- helpers for the coupling tests ---------------------------------------------------------


@pytest.fixture(scope="session")
def network_model() -> MemoNetworkModel:
    """One network engine for the whole session. Identical network cases are solved once."""
    from r3chain.adapters.pandapipes_network import PandapipesNetworkModel

    return MemoNetworkModel(PandapipesNetworkModel())


@pytest.fixture(scope="session", autouse=True)
def _shared_network_solutions(network_model: MemoNetworkModel) -> Iterator[None]:
    """Let every pipeline in the session share network solutions.

    The engine is deterministic and a solution is keyed by its complete input, so sharing it
    changes no result. It only keeps the test suite fast.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("r3chain.pipeline.stages.default_network_model", lambda: network_model)
        yield


def repository_policies() -> dict[str, Policy]:
    return {policy.policy_id: policy for policy in load_policies(POLICY_DIRECTORY)}


def policy_variant(policy: Policy, **parameters: Any) -> Policy:
    """A policy with some values replaced. It is a new version, as the rules require."""
    data = policy.to_json_dict()
    return Policy.model_validate(
        {**data, "version": "test", "parameters": {**data["parameters"], **parameters}}
    )


def couple(
    network_model: MemoNetworkModel,
    data: dict[str, Any],
    case_id: str,
    *,
    patches: tuple[tuple[str, Any], ...] = (),
    numerical: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> CaseOutcome:
    """Evaluate one coupling case of scenario data, after optional changes.

    ``patches`` are field path and value pairs applied to the scenario. ``numerical`` and
    ``source`` replace values of the two policies.
    """
    if patches:
        data = apply_patch(data, [PatchOperation("set", path, value) for path, value in patches])
    scenario = Scenario.model_validate(data)
    policies = repository_policies()
    numerical_policy = policies["numerical-coupling-default"]
    source_policy = policies["source-envelope-synthetic"]
    if numerical:
        numerical_policy = policy_variant(numerical_policy, **numerical)
    if source:
        source_policy = policy_variant(source_policy, **source)
    case = next(item for item in scenario.coupling_cases if item.coupling_case_id == case_id)
    return evaluate_case(
        scenario,
        case,
        source_models=[SyntheticSourceModel()],
        network_model=network_model,
        source_policy=source_policy,
        numerical_policy=numerical_policy,
    )
