"""Candidates: enumeration, coupling cases, assessment and a provisional ranking.

Specification 15.1, 15.2 and 15.4.

Identity
    A candidate is one site, one well concept at that site, one connection, one route from that
    site to that connection, and one plant arrangement. Its identifier is a hash of exactly
    these, so the same combination always has the same identifier, and a source at one site
    can never be paired with a route from another.

Enumeration
    Every combination the scenario and the candidate policy permit is enumerated. Options that
    end before they become a candidate, such as an excluded site or a pair without a route, go
    to the rejection table with their reason. A direct heat candidate and a heat pump candidate
    are separate candidates, so a failure of one never removes the other.

Assessment
    Each candidate keeps its four statuses apart: technical, site evidence, evaluation and
    ranking eligibility. Its limiting conditions explain a failure. A candidate that could not
    be evaluated says why.

Ranking
    Only eligible candidates are ranked, by the primary metric of the ranking policy, with ties
    inside the tolerance sharing a rank. The ranking states that it covers the evaluated
    candidates only.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from r3chain.domain import policies
from r3chain.domain.base import Identifier, NonEmptyStr, NonNegativeReal, Real, Record
from r3chain.domain.evidence import Known, Provenance, ProvenanceType, Unknown
from r3chain.domain.hashing import content_hash, short_hash
from r3chain.domain.records.network import Connection, Route
from r3chain.domain.records.plant import CouplingCase, FlowControl, TransmissionDesign
from r3chain.domain.records.runs import Check, KpiDefinition, KpiValue, Policy
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import (
    EvaluationStatus,
    RankingEligibility,
    SiteEvidenceStatus,
    StageStatus,
    TechnicalStatus,
    ranking_eligibility,
    summarise_stage_statuses,
)
from r3chain.geospatial.routes import RoutePair
from r3chain.geospatial.screening import ScreeningStatus, SiteScreening

# --- records --------------------------------------------------------------------------------


class Candidate(Record):
    candidate_id: Identifier
    label: NonEmptyStr
    site_id: Identifier
    source_scenario_id: Identifier
    target_id: Identifier
    well_configuration_id: Identifier
    connection_id: Identifier
    route_id: Identifier
    plant_id: Identifier
    arrangement: NonEmptyStr
    # Customers assumed to connect. None in this milestone: customers along a route are listed
    # with the route as potential demand.
    customer_ids: tuple[Identifier, ...] = ()
    operating_case_ids: tuple[Identifier, ...]
    coupling_case_ids: tuple[Identifier, ...]
    capacity_case_ids: tuple[Identifier, ...] = ()
    capacity_coupling_case_ids: tuple[Identifier, ...] = ()
    screening_status: ScreeningStatus
    route_rank: int
    route_bases: tuple[str, ...]
    indicative_route_cost_currency: Real | None
    corridor_length_m: Real


class RejectedOption(Record):
    """An option that ended before it became a candidate."""

    option: dict[str, str]
    stage: NonEmptyStr
    reason: NonEmptyStr
    details: tuple[str, ...] = ()


class CapacityCaseOutcome(Record):
    """Summary of one capacity-only coupling case (does not affect technical eligibility)."""

    coupling_case_id: Identifier
    operating_case_id: Identifier
    outcome: Literal["limit_found", "demand_covered", "not_established"]
    delivered_heat_w: NonNegativeReal | None = None
    reason: str = ""


class CandidateAssessment(Record):
    candidate_id: Identifier
    label: NonEmptyStr
    technical_status: TechnicalStatus
    evaluation_status: EvaluationStatus
    site_evidence_status: SiteEvidenceStatus
    screening_status: ScreeningStatus
    ranking_eligibility: RankingEligibility
    rank: int | None = None
    # State of the coupling in each operating case.
    case_states: dict[str, str]
    capacity_cases: tuple[CapacityCaseOutcome, ...] = ()
    limiting_conditions: tuple[str, ...] = ()
    # Why the candidate failed, could not be evaluated or cannot be ranked.
    explanation: tuple[str, ...] = ()
    kpis: tuple[KpiValue, ...] = ()
    checks: tuple[Check, ...] = ()
    synthetic: bool

    def kpi(self, kpi_id: str) -> float | None:
        return next((item.value for item in self.kpis if item.definition.kpi_id == kpi_id), None)


class Ranking(Record):
    policy: NonEmptyStr
    primary_metric: NonEmptyStr
    evaluated_count: int
    eligible_count: int
    # Candidate identifiers in rank order. Candidates that share a rank are listed together.
    order: tuple[tuple[Identifier, int], ...]
    statement: NonEmptyStr


# --- settings -------------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateSettings:
    operating_case_ids: tuple[str, ...]
    include_conditional_sites: bool
    network_pressure_lift_pa: float
    max_velocity_m_s: float
    sizing_density_kg_m3: float
    diameter_step_m: float
    roughness_m: float
    heat_transfer_coefficient_w_m2_k: float
    origin: str
    capacity_case_ids: tuple[str, ...] = ()

    @classmethod
    def from_policy(cls, policy: Policy) -> CandidateSettings:
        capacity = (
            policies.names(policy, "capacity_case_ids")
            if "capacity_case_ids" in policy.parameters
            else ()
        )
        return cls(
            operating_case_ids=policies.names(policy, "operating_case_ids"),
            capacity_case_ids=capacity,
            include_conditional_sites=policies.flag(policy, "include_conditional_sites"),
            network_pressure_lift_pa=policies.number(policy, "network_pressure_lift_pa"),
            max_velocity_m_s=policies.number(policy, "transmission_max_velocity_m_s"),
            sizing_density_kg_m3=policies.number(policy, "transmission_sizing_density_kg_m3"),
            diameter_step_m=policies.number(policy, "transmission_diameter_step_m"),
            roughness_m=policies.number(policy, "transmission_roughness_m"),
            heat_transfer_coefficient_w_m2_k=policies.number(
                policy, "transmission_heat_transfer_coefficient_w_m2_k"
            ),
            origin=policies.origin(policy),
        )


@dataclass(frozen=True)
class RankingSettings:
    primary_metric: str
    tie_tolerance_fraction: float
    rankable_site_evidence: frozenset[SiteEvidenceStatus]
    rank_conditional_screening: bool
    origin: str

    @classmethod
    def from_policy(cls, policy: Policy) -> RankingSettings:
        try:
            statuses = frozenset(
                SiteEvidenceStatus(name)
                for name in policies.names(policy, "rankable_site_evidence")
            )
        except ValueError as error:
            raise policies.PolicyError(f"{policies.origin(policy)}: {error}") from error
        return cls(
            primary_metric=policies.text(policy, "primary_metric"),
            tie_tolerance_fraction=policies.number(policy, "tie_tolerance_fraction"),
            rankable_site_evidence=statuses,
            rank_conditional_screening=policies.flag(policy, "rank_conditional_screening"),
            origin=policies.origin(policy),
        )


# --- enumeration ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Enumeration:
    candidates: tuple[Candidate, ...]
    coupling_cases: tuple[CouplingCase, ...]
    routes: dict[str, Route]
    rejected: tuple[RejectedOption, ...]


def candidate_identifier(identity: Mapping[str, str]) -> str:
    return "cand-" + short_hash(content_hash(dict(identity)), 10)


def _inferred(value: float, method: str) -> Known[float]:
    return Known[float](
        value=value,
        provenance=Provenance(provenance_type=ProvenanceType.INFERRED, method=method),
    )


def transmission_design(connection: Connection, settings: CandidateSettings) -> TransmissionDesign:
    """Size the route pipes for the largest mass flow of the connection.

    The diameter is the smallest step that keeps the velocity at or below the limit of the
    policy. Without a known largest flow, the diameter is unknown and says why.
    """
    flow = connection.limits.max_mass_flow_kg_s
    path = f"connections[{connection.connection_id}].limits.max_mass_flow_kg_s"
    diameter: Known[float] | Unknown
    if isinstance(flow, Known):
        needed = math.sqrt(
            4.0 * flow.value / (settings.sizing_density_kg_m3 * math.pi * settings.max_velocity_m_s)
        )
        steps = math.ceil(needed / settings.diameter_step_m - 1e-9)
        size = steps * settings.diameter_step_m
        diameter = _inferred(
            size,
            f"smallest step of {settings.diameter_step_m:g} m that carries {flow.value:g} kg/s at "
            f"no more than {settings.max_velocity_m_s:g} m/s, by {settings.origin}",
        )
    else:
        diameter = Unknown(
            reason=f"the route cannot be sized: {path} is not established",
            owner="Network planner",
        )
    return TransmissionDesign(
        inner_diameter_m=diameter,
        roughness_m=_inferred(settings.roughness_m, f"taken from {settings.origin}"),
        heat_transfer_coefficient_w_m2_k=_inferred(
            settings.heat_transfer_coefficient_w_m2_k, f"taken from {settings.origin}"
        ),
    )


def enumerate_candidates(
    scenario: Scenario,
    screenings: Mapping[str, SiteScreening],
    pairs: Sequence[RoutePair],
    settings: CandidateSettings,
) -> Enumeration:
    """Every candidate the scenario permits, the cases to couple, and what was rejected."""
    rejected: list[RejectedOption] = []
    candidates: list[Candidate] = []
    cases: list[CouplingCase] = []
    routes: dict[str, Route] = {}
    by_pair = {(pair.site_id, pair.connection_id): pair for pair in pairs}
    known_cases = {item.operating_case_id for item in scenario.operating_cases}
    absent = [
        name
        for name in (*settings.operating_case_ids, *settings.capacity_case_ids)
        if name not in known_cases
    ]
    if absent:
        raise policies.PolicyError(
            f"{settings.origin} names operating cases that are not in the scenario: "
            + ", ".join(absent)
        )
    overlap = [name for name in settings.capacity_case_ids if name in settings.operating_case_ids]
    if overlap:
        raise policies.PolicyError(
            f"{settings.origin} names cases in both operating and capacity lists: "
            + ", ".join(overlap)
        )
    designs = {
        connection.connection_id: transmission_design(connection, settings)
        for connection in scenario.connections
    }
    lift = _inferred(settings.network_pressure_lift_pa, f"taken from {settings.origin}")

    for site in scenario.sites:
        option = {"site_id": site.site_id}
        screening = screenings.get(site.site_id)
        if screening is None:
            rejected.append(RejectedOption(option=option, stage="screen", reason="not screened"))
            continue
        if screening.status is ScreeningStatus.EXCLUDED:
            rejected.append(
                RejectedOption(
                    option=option,
                    stage="screen",
                    reason="excluded by the site screening",
                    details=screening.reasons(),
                )
            )
            continue
        if (
            screening.status is ScreeningStatus.CONDITIONAL
            and not settings.include_conditional_sites
        ):
            rejected.append(
                RejectedOption(
                    option=option,
                    stage="screen",
                    reason="conditional sites are left out by the candidate policy",
                    details=screening.reasons(),
                )
            )
            continue
        concepts = [item for item in scenario.well_scenarios if item.site_id == site.site_id]
        if not concepts:
            rejected.append(
                RejectedOption(
                    option=option,
                    stage="candidates",
                    reason="no subsurface concept is defined for this site",
                )
            )
            continue
        reaches = {reach.source_scenario_id: reach for reach in screening.targets}
        for concept in concepts:
            reach = reaches.get(concept.source_scenario_id)
            if reach is not None and reach.reachable is False:
                rejected.append(
                    RejectedOption(
                        option={**option, "source_scenario_id": concept.source_scenario_id},
                        stage="screen",
                        reason=f"target {concept.target_id} is out of reach",
                        details=(reach.reason,),
                    )
                )
                continue
            for connection in scenario.connections:
                pair = by_pair.get((site.site_id, connection.connection_id))
                pair_option = {**option, "connection_id": connection.connection_id}
                if pair is None or pair.status != "routed":
                    rejected.append(
                        RejectedOption(
                            option=pair_option,
                            stage="routes",
                            reason=pair.reason if pair is not None else "no route was generated",
                        )
                    )
                    continue
                for generated in pair.routes:
                    route = generated.route
                    routes[route.route_id] = route
                    for plant in scenario.plants:
                        identity = {
                            "site_id": site.site_id,
                            "source_scenario_id": concept.source_scenario_id,
                            "connection_id": connection.connection_id,
                            "route_id": route.route_id,
                            "plant_id": plant.plant_id,
                        }
                        identifier = candidate_identifier(identity)
                        label = (
                            f"{site.site_id} to {connection.connection_id} by route "
                            f"{generated.rank}, {plant.plant_id}"
                        )
                        case_ids: list[str] = []
                        for operating_case_id in settings.operating_case_ids:
                            case_id = f"{identifier}-{operating_case_id}"
                            case_ids.append(case_id)
                            cases.append(
                                CouplingCase(
                                    coupling_case_id=case_id,
                                    description=f"{label}, {operating_case_id}",
                                    source_scenario_id=concept.source_scenario_id,
                                    plant_id=plant.plant_id,
                                    connection_id=connection.connection_id,
                                    route_id=route.route_id,
                                    operating_case_id=operating_case_id,
                                    flow_control=FlowControl.FOLLOW_DEMAND,
                                    network_pressure_lift_pa=lift,
                                    transmission=designs[connection.connection_id],
                                )
                            )
                        cap_case_ids: list[str] = []
                        for capacity_case_id in settings.capacity_case_ids:
                            case_id = f"{identifier}-{capacity_case_id}"
                            cap_case_ids.append(case_id)
                            cases.append(
                                CouplingCase(
                                    coupling_case_id=case_id,
                                    description=f"{label}, {capacity_case_id} (capacity)",
                                    source_scenario_id=concept.source_scenario_id,
                                    plant_id=plant.plant_id,
                                    connection_id=connection.connection_id,
                                    route_id=route.route_id,
                                    operating_case_id=capacity_case_id,
                                    flow_control=FlowControl.FOLLOW_DEMAND,
                                    network_pressure_lift_pa=lift,
                                    transmission=designs[connection.connection_id],
                                )
                            )
                        candidates.append(
                            Candidate(
                                candidate_id=identifier,
                                label=label,
                                site_id=site.site_id,
                                source_scenario_id=concept.source_scenario_id,
                                target_id=concept.target_id,
                                well_configuration_id=concept.well_configuration_id,
                                connection_id=connection.connection_id,
                                route_id=route.route_id,
                                plant_id=plant.plant_id,
                                arrangement=plant.arrangement,
                                operating_case_ids=settings.operating_case_ids,
                                coupling_case_ids=tuple(case_ids),
                                capacity_case_ids=settings.capacity_case_ids,
                                capacity_coupling_case_ids=tuple(cap_case_ids),
                                screening_status=screening.status,
                                route_rank=generated.rank,
                                route_bases=generated.bases,
                                indicative_route_cost_currency=generated.cost.total_currency,
                                corridor_length_m=route.corridor_length_m,
                            )
                        )
    return Enumeration(tuple(candidates), tuple(cases), routes, tuple(rejected))


# --- assessment -----------------------------------------------------------------------------

ROUTE_COST = KpiDefinition(
    kpi_id="indicative_route_cost_currency",
    name="Indicative route cost",
    unit="currency",
    boundary=(
        "trench, supply and return pipes and crossings of the route from the pad to the "
        "connection, at the unit rates of the route policy. Not a cost estimate"
    ),
)
CORRIDOR_LENGTH = KpiDefinition(
    kpi_id="corridor_length_m",
    name="Trench length",
    unit="m",
    boundary="corridor from the pad to the connection, including the links to the corridors",
)
_OPERATING_KPIS = (
    ("plant_heat_w", "Heat the plant delivers", "W"),
    ("deficit_heat_w", "Heat not covered", "W"),
    ("geothermal_mass_flow_kg_s", "Geothermal mass flow", "kg/s"),
    ("compressor_electricity_w", "Compressor electricity", "W"),
    ("source_pump_electricity_w", "Well pump electricity", "W"),
    ("transmission_heat_loss_w", "Heat loss of the route", "W"),
)


def _operating_kpis(
    candidate: Candidate, case_id: str, operating_case_id: str, summary: Mapping[str, Any]
) -> list[KpiValue]:
    point = summary.get("operating_point") or {}
    found: list[KpiValue] = []
    for name, title, unit in _OPERATING_KPIS:
        value = point.get(name) if summary.get("state") == "completed" or point else None
        found.append(
            KpiValue(
                definition=KpiDefinition(
                    kpi_id=f"{name}-{operating_case_id}",
                    name=f"{title}, {operating_case_id}",
                    unit=unit,
                    boundary=f"coupled operating point of case {case_id}",
                ),
                value=float(value) if isinstance(value, int | float) else None,
                derived_from=(f"coupling case {case_id}",),
            )
        )
    return found


def _capacity_case_outcome(
    case_id: str, operating_case_id: str, summary: Mapping[str, Any]
) -> CapacityCaseOutcome:
    state = str(summary.get("state", "not_run"))
    point = summary.get("operating_point")
    limits = tuple(str(item) for item in summary.get("limiting_conditions", ()))
    if state not in {"completed", "physical_limit_exceeded"} or point is None:
        base_reason = str(summary.get("reason") or state)
        reason = (
            f"{', '.join(limits)}: {base_reason}"
            if limits and base_reason
            else (", ".join(limits) or base_reason)
        )
        return CapacityCaseOutcome(
            coupling_case_id=case_id,
            operating_case_id=operating_case_id,
            outcome="not_established",
            delivered_heat_w=None,
            reason=reason,
        )
    causes = tuple(str(item) for item in summary.get("shortfall_causes", ()))
    deficit_w = float(point.get("deficit_heat_w") or 0.0)
    plant_w = float(point.get("plant_heat_w") or 0.0)
    loss_w = float(point.get("transmission_heat_loss_w") or 0.0)
    delivered_w = 0.0 if "insufficient_temperature" in causes else max(plant_w - loss_w, 0.0)
    limit_hit = state == "physical_limit_exceeded" or deficit_w > 1.0 or bool(causes)
    reason = ", ".join(limits) if limits else str(summary.get("reason") or "")
    return CapacityCaseOutcome(
        coupling_case_id=case_id,
        operating_case_id=operating_case_id,
        outcome="limit_found" if limit_hit else "demand_covered",
        delivered_heat_w=delivered_w,
        reason=reason,
    )


def assess_candidates(
    candidates: Sequence[Candidate],
    outcomes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    site_evidence: Mapping[str, SiteEvidenceStatus],
    settings: RankingSettings,
    study_synthetic: bool,
    route_policy: str,
) -> tuple[tuple[CandidateAssessment, ...], Ranking]:
    """Statuses, explanations and KPIs of each candidate, and the ranking of the eligible ones.

    ``outcomes`` maps a candidate to its coupling case summaries by coupling case identifier.
    """
    assessments: list[CandidateAssessment] = []
    for candidate in candidates:
        summaries = outcomes.get(candidate.candidate_id, {})
        states: dict[str, str] = {}
        limiting: list[str] = []
        explanation: list[str] = []
        kpis: list[KpiValue] = [
            KpiValue(
                definition=ROUTE_COST,
                value=candidate.indicative_route_cost_currency,
                derived_from=(f"route {candidate.route_id}", route_policy),
            ),
            KpiValue(
                definition=CORRIDOR_LENGTH,
                value=candidate.corridor_length_m,
                derived_from=(f"route {candidate.route_id}",),
            ),
        ]
        synthetic = False
        statuses: list[StageStatus] = []
        for case_id, operating_case_id in zip(
            candidate.coupling_case_ids, candidate.operating_case_ids, strict=True
        ):
            summary = summaries.get(case_id, {"state": "not_run", "reason": "not coupled"})
            state = str(summary.get("state", "not_run"))
            states[operating_case_id] = state
            statuses.append(StageStatus(state))
            synthetic = synthetic or bool(summary.get("synthetic"))
            limiting.extend(str(item) for item in summary.get("limiting_conditions", ()))
            if state != "completed":
                reason = summary.get("reason") or state
                explanation.append(f"{operating_case_id}: {reason}")
                for path in summary.get("inputs_not_established", ()):
                    explanation.append(f"{operating_case_id}: needs {path}")
            kpis.extend(_operating_kpis(candidate, case_id, operating_case_id, summary))

        capacity_outcomes: list[CapacityCaseOutcome] = []
        for cap_case_id, cap_op_id in zip(
            candidate.capacity_coupling_case_ids, candidate.capacity_case_ids, strict=True
        ):
            cap_summary = summaries.get(cap_case_id, {"state": "not_run", "reason": "not coupled"})
            synthetic = synthetic or bool(cap_summary.get("synthetic"))
            capacity_outcomes.append(_capacity_case_outcome(cap_case_id, cap_op_id, cap_summary))

        technical, evaluation = summarise_stage_statuses(statuses)
        evidence = site_evidence.get(candidate.site_id, SiteEvidenceStatus.UNKNOWN)
        metric = next(
            (item.value for item in kpis if item.definition.kpi_id == settings.primary_metric),
            None,
        )
        base = ranking_eligibility(
            technical,
            evidence,
            evaluation,
            comparison_fields_complete=metric is not None,
            rankable_site_statuses=settings.rankable_site_evidence,
        )
        reasons = list(base.reasons)
        if metric is None:
            reasons.append(f"the primary metric {settings.primary_metric} is not established")
        conditional = base.conditional
        if candidate.screening_status is ScreeningStatus.CONDITIONAL:
            conditional = True
            if not settings.rank_conditional_screening:
                reasons.append("the site screening is conditional")
        if synthetic and not study_synthetic:
            reasons.append("a result of a synthetic test engine cannot enter a pilot ranking")
        eligibility = RankingEligibility(
            eligible=not reasons,
            conditional=conditional,
            reasons=tuple(dict.fromkeys(reasons)),
        )
        assessments.append(
            CandidateAssessment(
                candidate_id=candidate.candidate_id,
                label=candidate.label,
                technical_status=technical,
                evaluation_status=evaluation,
                site_evidence_status=evidence,
                screening_status=candidate.screening_status,
                ranking_eligibility=eligibility,
                case_states=states,
                capacity_cases=tuple(capacity_outcomes),
                limiting_conditions=tuple(dict.fromkeys(limiting)),
                explanation=tuple(explanation),
                kpis=tuple(kpis),
                synthetic=synthetic,
            )
        )

    eligible = [
        (item, item.kpi(settings.primary_metric))
        for item in assessments
        if item.ranking_eligibility.eligible
    ]
    eligible.sort(key=lambda pair: (pair[1] or 0.0, pair[0].candidate_id))
    order: list[tuple[str, int]] = []
    ranks: dict[str, int] = {}
    leader: float | None = None
    rank = 0
    for position, (item, value) in enumerate(eligible, start=1):
        assert value is not None
        if leader is None or abs(value - leader) > settings.tie_tolerance_fraction * abs(leader):
            rank, leader = position, value
        ranks[item.candidate_id] = rank
        order.append((item.candidate_id, rank))
    assessments = [
        item.model_copy(update={"rank": ranks.get(item.candidate_id)}) for item in assessments
    ]
    definition = (
        next(
            (
                item.definition
                for item in assessments[0].kpis
                if item.definition.kpi_id == settings.primary_metric
            ),
            None,
        )
        if assessments
        else None
    )
    boundary = f" ({definition.boundary})" if definition is not None else ""
    statement = (
        f"Ranked by {settings.primary_metric}{boundary}, lowest first, among the "
        f"{len(eligible)} eligible of the {len(assessments)} evaluated candidates of this study "
        f"revision under {settings.origin}. The order covers the evaluated candidates only. It "
        "is not a global optimum over all possible sites, connections and routes. Candidates "
        f"within {settings.tie_tolerance_fraction:.0%} of each other share a rank."
    )
    ranking = Ranking(
        policy=settings.origin,
        primary_metric=settings.primary_metric,
        evaluated_count=len(assessments),
        eligible_count=len(eligible),
        order=tuple(order),
        statement=statement,
    )
    return tuple(assessments), ranking
