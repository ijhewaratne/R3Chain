"""The stages that screen sites, generate routes, enumerate, couple and assess candidates.

    screen       site screening against the screening policy             milestone M3
    routes       route alternatives for every site and connection        milestone M3
    candidates   candidate enumeration and the rejection table           milestone M3
    couple       coupled operating point of every candidate              milestones M2, M3
    assess       statuses, explanations, KPIs and the provisional ranking milestone M3

A candidate that fails physically, or that cannot be evaluated, is an outcome of that candidate.
It does not stop the stage, so one unresolved candidate never hides the results of the others.
A stage ends without a usable output only when it cannot work at all, for example without its
policy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from r3chain.coupling.cases import CaseOutcome, MemoNetworkModel, evaluate_case
from r3chain.coupling.plant import arrangements
from r3chain.domain import policies
from r3chain.domain.evidence import MissingInput
from r3chain.domain.records.network import Route
from r3chain.domain.records.plant import CouplingCase
from r3chain.domain.records.runs import Policy, PolicyKind, StageOutput
from r3chain.domain.records.spatial import LayerKind
from r3chain.domain.status import AdapterState, SiteEvidenceStatus, StageStatus
from r3chain.geospatial.routes import (
    RoutePair,
    RouteSettings,
    build_route_network,
    generate_routes,
)
from r3chain.geospatial.screening import (
    ScreeningSettings,
    ScreeningStatus,
    SiteScreening,
    screen_site,
)
from r3chain.pipeline.runner import StageContext
from r3chain.ports.network import NetworkModel
from r3chain.ports.source import SourceModel
from r3chain.search.candidates import (
    Candidate,
    CandidateSettings,
    RankingSettings,
    assess_candidates,
    enumerate_candidates,
)


def single_policy(policies_by_id: Mapping[str, Policy], kind: PolicyKind) -> Policy | None:
    """The one policy of a kind that a scenario references, or None if there is not exactly one."""
    found = [policy for policy in policies_by_id.values() if policy.kind is kind]
    return found[0] if len(found) == 1 else None


def coupling_case_summary(outcome: CaseOutcome) -> dict[str, Any]:
    """The few numbers of a coupling case that a reader needs first."""
    summary: dict[str, Any] = {"state": outcome.status.value, "reason": outcome.reason}
    result = outcome.result
    if result is None:
        summary["inputs_not_established"] = [item.field_path for item in outcome.missing_inputs]
        return summary
    summary |= {
        "arrangement": result.arrangement,
        "performance_basis": result.performance_basis,
        "synthetic": result.synthetic,
        "failure_origin": result.failure_origin.value if result.failure_origin else None,
        "converged": result.converged,
        "iterations_count": len(result.iterations),
        "limiting_conditions": list(result.limiting_conditions),
        "shortfall_causes": [cause.value for cause in result.shortfall_causes],
        "failed_checks": [
            check.check_id for check in result.checks if check.outcome.value == "fail"
        ],
        "checks_not_evaluated": [
            check.check_id for check in result.checks if check.outcome.value == "not_evaluated"
        ],
        "operating_point": (
            result.operating_point.to_json_dict() if result.operating_point else None
        ),
    }
    return summary


def _layers(context: StageContext, *kinds: LayerKind) -> list[Any]:
    return [
        layer.to_json_dict() for layer in context.scenario.spatial_layers if layer.kind in kinds
    ]


class _Base:
    name = ""
    version = "1.0.0"
    requires: tuple[str, ...] = ()
    stochastic = False

    def _output(self, status: StageStatus, **fields: Any) -> StageOutput:
        return StageOutput(stage=self.name, stage_version=self.version, status=status, **fields)

    def _policy(self, context: StageContext, kind: PolicyKind) -> Policy | None:
        return single_policy(context.policies, kind)

    def _no_policy(self, kind: PolicyKind) -> StageOutput:
        return self._output(
            StageStatus.NOT_ESTABLISHED,
            reason=f"the scenario must reference exactly one policy of kind '{kind.value}'",
        )


# --- screening ------------------------------------------------------------------------------


class ScreeningStage(_Base):
    """Screen every site against the criteria of the screening policy."""

    name = "screen"

    def input_fingerprint(self, context: StageContext) -> Any:
        scenario = context.scenario
        policy = self._policy(context, PolicyKind.SCREENING)
        return {
            "crs": scenario.study.study_area.crs,
            "sites": [item.to_json_dict() for item in scenario.sites],
            "targets": [
                [item.site_id, item.source_scenario_id, item.target_id]
                for item in scenario.well_scenarios
            ],
            "layers": _layers(
                context,
                LayerKind.RESTRICTION,
                LayerKind.CORRIDOR,
                LayerKind.GEOLOGICAL_TARGET,
                LayerKind.JURISDICTION,
            ),
            "policy": policy.to_json_dict() if policy else None,
        }

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        if not scenario.sites:
            return self._output(StageStatus.NOT_ESTABLISHED, reason="the scenario defines no sites")
        policy = self._policy(context, PolicyKind.SCREENING)
        if policy is None:
            return self._no_policy(PolicyKind.SCREENING)
        try:
            settings = ScreeningSettings.from_policy(policy)
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))
        screenings = {
            site.site_id: screen_site(
                site,
                well_scenarios=scenario.well_scenarios,
                layers=scenario.spatial_layers,
                settings=settings,
                study_crs=scenario.study.study_area.crs,
            )
            for site in scenario.sites
        }
        counts: dict[str, int] = {}
        for screening in screenings.values():
            counts[screening.status.value] = counts.get(screening.status.value, 0) + 1
        return self._output(
            StageStatus.COMPLETED,
            payload={
                "sites": {key: value.to_json_dict() for key, value in screenings.items()},
                "counts": counts,
                "policy_id": policy.policy_id,
            },
        )


def screenings_of(output: StageOutput) -> dict[str, SiteScreening]:
    return {
        key: SiteScreening.model_validate(value)
        for key, value in output.payload.get("sites", {}).items()
    }


# --- routes ---------------------------------------------------------------------------------


class RouteStage(_Base):
    """Generate the route alternatives between every screened site and every connection."""

    name = "routes"
    requires: tuple[str, ...] = ("screen",)

    def input_fingerprint(self, context: StageContext) -> Any:
        scenario = context.scenario
        policy = self._policy(context, PolicyKind.ROUTE)
        return {
            "crs": scenario.study.study_area.crs,
            "sites": [[item.site_id, item.elevation.to_json_dict()] for item in scenario.sites],
            "connections": [item.to_json_dict() for item in scenario.connections],
            "networks": [
                [item.network_id, [node.to_json_dict() for node in item.nodes]]
                for item in scenario.networks
            ],
            "layers": _layers(
                context, LayerKind.CORRIDOR, LayerKind.CROSSING_BARRIER, LayerKind.DEMAND_POINT
            ),
            "policy": policy.to_json_dict() if policy else None,
        }

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        policy = self._policy(context, PolicyKind.ROUTE)
        if policy is None:
            return self._no_policy(PolicyKind.ROUTE)
        if not scenario.connections:
            return self._output(
                StageStatus.NOT_ESTABLISHED, reason="the scenario defines no connections"
            )
        try:
            settings = RouteSettings.from_policy(policy)
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))
        network = build_route_network(scenario.spatial_layers, settings)
        screenings = screenings_of(context.upstream["screen"])
        networks = {item.network_id: item for item in scenario.networks}
        pairs: list[RoutePair] = []
        skipped: dict[str, str] = {}
        missing: list[MissingInput] = []
        for site in scenario.sites:
            screening = screenings.get(site.site_id)
            if screening is None or screening.status is ScreeningStatus.EXCLUDED:
                skipped[site.site_id] = "excluded by the site screening"
                continue
            if screening.pad_point is None:
                skipped[site.site_id] = "the site has no usable pad to start a route from"
                continue
            for connection in scenario.connections:
                pair = generate_routes(
                    site,
                    screening.pad_point,
                    connection,
                    networks[connection.network_id],
                    network,
                    settings,
                    scenario.study.study_area.crs,
                )
                missing.extend(pair.missing_inputs)
                pairs.append(pair)
        counts = {
            "pairs": len(pairs),
            "routed_pairs": sum(1 for pair in pairs if pair.status == "routed"),
            "routes": sum(len(pair.routes) for pair in pairs),
        }
        return self._output(
            StageStatus.COMPLETED,
            payload={
                "pairs": [pair.to_json_dict() for pair in pairs],
                "skipped_sites": skipped,
                "graph_issues": [issue.to_json_dict() for issue in network.issues],
                "counts": counts,
                "policy_id": policy.policy_id,
            },
            missing_inputs=tuple(missing),
            diagnostics=tuple(issue.message for issue in network.issues),
        )


# --- candidates -----------------------------------------------------------------------------


class CandidateStage(_Base):
    """Enumerate the candidates and record every option that does not become one."""

    name = "candidates"
    requires: tuple[str, ...] = ("screen", "routes", "source")

    def input_fingerprint(self, context: StageContext) -> Any:
        scenario = context.scenario
        policy = self._policy(context, PolicyKind.CANDIDATE)
        return {
            "sites": [item.site_id for item in scenario.sites],
            "well_scenarios": [
                [item.source_scenario_id, item.site_id, item.target_id, item.well_configuration_id]
                for item in scenario.well_scenarios
            ],
            "connections": [item.to_json_dict() for item in scenario.connections],
            "plants": [[item.plant_id, item.arrangement] for item in scenario.plants],
            "operating_cases": [item.operating_case_id for item in scenario.operating_cases],
            "policy": policy.to_json_dict() if policy else None,
        }

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        policy = self._policy(context, PolicyKind.CANDIDATE)
        if policy is None:
            return self._no_policy(PolicyKind.CANDIDATE)
        if not scenario.plants:
            return self._output(
                StageStatus.NOT_ESTABLISHED, reason="the scenario defines no plants"
            )
        try:
            settings = CandidateSettings.from_policy(policy)
            pairs = [
                RoutePair.model_validate(item)
                for item in context.upstream["routes"].payload.get("pairs", [])
            ]
            enumeration = enumerate_candidates(
                scenario, screenings_of(context.upstream["screen"]), pairs, settings
            )
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))
        return self._output(
            StageStatus.COMPLETED if enumeration.candidates else StageStatus.NOT_ESTABLISHED,
            reason="" if enumeration.candidates else "no option became a candidate",
            payload={
                "candidates": [item.to_json_dict() for item in enumeration.candidates],
                "coupling_cases": [item.to_json_dict() for item in enumeration.coupling_cases],
                "routes": {key: value.to_json_dict() for key, value in enumeration.routes.items()},
                "rejected": [item.to_json_dict() for item in enumeration.rejected],
                "counts": {
                    "candidates": len(enumeration.candidates),
                    "rejected": len(enumeration.rejected),
                },
                "policy_id": policy.policy_id,
            },
        )


def candidates_of(output: StageOutput) -> tuple[Candidate, ...]:
    return tuple(Candidate.model_validate(item) for item in output.payload.get("candidates", []))


# --- coupling -------------------------------------------------------------------------------


class CouplingStage(_Base):
    """Couple source, plant and network for every candidate in every operating case."""

    name = "couple"
    version = "2.0.0"
    requires: tuple[str, ...] = ("source", "candidates")

    def __init__(self, source_models: Sequence[SourceModel], network_model: NetworkModel) -> None:
        self._source_models = tuple(source_models)
        self._network_model = network_model

    def _network_engine(self) -> list[str] | None:
        try:
            capabilities = self._network_model.capabilities()
        except Exception:
            return None
        return [capabilities.engine, capabilities.engine_version]

    def input_fingerprint(self, context: StageContext) -> Any:
        scenario = context.scenario
        numerical = self._policy(context, PolicyKind.NUMERICAL)
        source = self._policy(context, PolicyKind.SOURCE)
        return {
            "study_id": scenario.study.study_id,
            "plants": [item.to_json_dict() for item in scenario.plants],
            "operating_cases": [item.to_json_dict() for item in scenario.operating_cases],
            "well_scenarios": [item.to_json_dict() for item in scenario.well_scenarios],
            "connections": [item.to_json_dict() for item in scenario.connections],
            "networks": [item.to_json_dict() for item in scenario.networks],
            "policies": [
                policy.to_json_dict() if policy else None for policy in (numerical, source)
            ],
            "source_engines": [
                [capabilities.engine, capabilities.engine_version]
                for capabilities in (model.capabilities() for model in self._source_models)
            ],
            "network_engine": self._network_engine(),
            "arrangements": [item.name for item in arrangements()],
        }

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        numerical = self._policy(context, PolicyKind.NUMERICAL)
        source = self._policy(context, PolicyKind.SOURCE)
        if numerical is None or source is None:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason=(
                    "the scenario must reference exactly one policy of kind 'numerical' and "
                    "exactly one of kind 'source'"
                ),
            )
        if self._network_engine() is None:
            return self._output(
                StageStatus.UNSUPPORTED_MODEL,
                reason="no network engine is installed. Install r3chain[network]",
            )
        payload = context.upstream["candidates"].payload
        cases = {
            item["coupling_case_id"]: CouplingCase.model_validate(item)
            for item in payload.get("coupling_cases", [])
        }
        routes = {
            key: Route.model_validate(value) for key, value in payload.get("routes", {}).items()
        }
        sources = context.upstream["source"].payload.get("sources", {})
        network_model = MemoNetworkModel(self._network_model)
        evaluated: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        missing: list[MissingInput] = []
        synthetic = False
        for candidate in candidates_of(context.upstream["candidates"]):
            results: dict[str, Any] = {}
            for role, case_ids in (
                ("operating", candidate.coupling_case_ids),
                ("capacity", candidate.capacity_coupling_case_ids),
            ):
                for case_id in case_ids:
                    case = cases[case_id]
                    upstream = sources.get(case.source_scenario_id, {}).get("state")
                    if upstream is not None and upstream != AdapterState.COMPLETED.value:
                        outcome = CaseOutcome(
                            case_id,
                            StageStatus(upstream),
                            f"the source stage ended as {upstream} for {case.source_scenario_id}",
                        )
                    else:
                        outcome = evaluate_case(
                            scenario,
                            case,
                            source_models=self._source_models,
                            network_model=network_model,
                            source_policy=source,
                            numerical_policy=numerical,
                            routes=routes,
                        )
                    counts[outcome.status.value] = counts.get(outcome.status.value, 0) + 1
                    missing.extend(outcome.missing_inputs)
                    synthetic = synthetic or (
                        outcome.result is not None and outcome.result.synthetic
                    )
                    results[case_id] = {
                        "role": role,
                        "summary": coupling_case_summary(outcome),
                        "result": outcome.result.to_json_dict() if outcome.result else None,
                    }
            evaluated[candidate.candidate_id] = results
        if not evaluated:
            return self._output(StageStatus.NOT_ESTABLISHED, reason="there are no candidates")
        return self._output(
            StageStatus.COMPLETED,
            payload={
                "candidates": evaluated,
                "counts": counts,
                "numerical_policy_id": numerical.policy_id,
                "source_policy_id": source.policy_id,
            },
            missing_inputs=tuple(dict.fromkeys(missing)),
            synthetic=synthetic,
        )


# --- assessment -----------------------------------------------------------------------------


class AssessStage(_Base):
    """Statuses, explanations and KPIs of every candidate, and the provisional ranking."""

    name = "assess"
    requires: tuple[str, ...] = ("site_evidence", "candidates", "couple")

    def input_fingerprint(self, context: StageContext) -> Any:
        policy = self._policy(context, PolicyKind.RANKING)
        route = self._policy(context, PolicyKind.ROUTE)
        return {
            "synthetic": context.scenario.study.synthetic,
            "policy": policy.to_json_dict() if policy else None,
            "route_policy": [route.policy_id, route.version] if route else None,
        }

    def run(self, context: StageContext) -> StageOutput:
        policy = self._policy(context, PolicyKind.RANKING)
        if policy is None:
            return self._no_policy(PolicyKind.RANKING)
        route = self._policy(context, PolicyKind.ROUTE)
        try:
            settings = RankingSettings.from_policy(policy)
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))
        candidates = candidates_of(context.upstream["candidates"])
        coupled = context.upstream["couple"].payload.get("candidates", {})
        outcomes = {
            identifier: {case_id: value["summary"] for case_id, value in cases.items()}
            for identifier, cases in coupled.items()
        }
        evidence = {
            site_id: SiteEvidenceStatus(value["evidence_status"])
            for site_id, value in context.upstream["site_evidence"].payload.get("sites", {}).items()
        }
        assessments, ranking = assess_candidates(
            candidates,
            outcomes,
            site_evidence=evidence,
            settings=settings,
            study_synthetic=context.scenario.study.synthetic,
            route_policy=policies.origin(route) if route else "no route policy",
        )
        counts: dict[str, int] = {}
        for item in assessments:
            key = item.technical_status.value
            counts[key] = counts.get(key, 0) + 1
        subject_results = [
            {
                "subject": item.candidate_id,
                "synthetic": item.synthetic,
                "technical_status": item.technical_status.value,
                "site_evidence_status": item.site_evidence_status.value,
                "evaluation_status": item.evaluation_status.value,
                "ranking_eligibility": item.ranking_eligibility.to_json_dict(),
                "kpis": [kpi.to_json_dict() for kpi in item.kpis],
                "diagnostics": [
                    *item.explanation,
                    *(f"limited by {name}" for name in item.limiting_conditions),
                ],
            }
            for item in assessments
        ]
        return self._output(
            StageStatus.COMPLETED,
            payload={
                "assessments": [item.to_json_dict() for item in assessments],
                "ranking": ranking.to_json_dict(),
                "rejected": context.upstream["candidates"].payload.get("rejected", []),
                "technical_counts": counts,
                "subject_results": subject_results,
                "policy_id": policy.policy_id,
            },
            synthetic=any(item.synthetic for item in assessments),
        )
