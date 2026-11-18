"""Candidates: identity, transmission sizing, eligibility and ranking (specification 15)."""

from __future__ import annotations

import math
from typing import Any

import pytest
from tests.conftest import couple, known, repository_policies

from r3chain.coupling.cases import MemoNetworkModel
from r3chain.domain.evidence import Known, Unknown
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import EvaluationStatus, SiteEvidenceStatus, TechnicalStatus
from r3chain.geospatial.screening import ScreeningStatus
from r3chain.search.candidates import (
    Candidate,
    CandidateSettings,
    RankingSettings,
    assess_candidates,
    candidate_identifier,
    transmission_design,
)

CANDIDATE_POLICY = repository_policies()["candidate-synthetic"]
RANKING_POLICY = repository_policies()["ranking-synthetic"]


def candidate(name: str, cost: float | None, site: str = "alpha", **changes: Any) -> Candidate:
    identifier = name.replace(" ", "-")
    data: dict[str, Any] = {
        "candidate_id": f"cand-{identifier}",
        "label": name,
        "site_id": site,
        "source_scenario_id": f"source-{site}",
        "target_id": f"target-{site}",
        "well_configuration_id": "doublet",
        "connection_id": "conn-west",
        "route_id": f"{site}-conn-west-1",
        "plant_id": "plant-direct",
        "arrangement": "direct_exchanger",
        "operating_case_ids": ("shoulder-season",),
        "coupling_case_ids": (f"cand-{identifier}-shoulder-season",),
        "screening_status": ScreeningStatus.PASSED,
        "route_rank": 1,
        "route_bases": ("least_cost",),
        "indicative_route_cost_currency": cost,
        "corridor_length_m": 1000.0,
    }
    return Candidate.model_validate({**data, **changes})


def summary(state: str = "completed", **changes: Any) -> dict[str, Any]:
    return {
        "state": state,
        "reason": "" if state == "completed" else "why",
        "synthetic": True,
        **changes,
    }


def assess(
    candidates: list[Candidate],
    states: dict[str, dict[str, Any]],
    *,
    evidence: SiteEvidenceStatus = SiteEvidenceStatus.DOCUMENTED,
    synthetic_study: bool = True,
) -> Any:
    outcomes = {
        item.candidate_id: {item.coupling_case_ids[0]: states.get(item.label, summary())}
        for item in candidates
    }
    return assess_candidates(
        candidates,
        outcomes,
        site_evidence={item.site_id: evidence for item in candidates},
        settings=RankingSettings.from_policy(RANKING_POLICY),
        study_synthetic=synthetic_study,
        route_policy="policy route-synthetic version 1",
    )


def test_the_identifier_is_a_stable_hash_of_the_identity() -> None:
    identity = {
        "site_id": "alpha",
        "source_scenario_id": "source-alpha",
        "connection_id": "conn-west",
        "route_id": "alpha-conn-west-1",
        "plant_id": "plant-direct",
    }
    first = candidate_identifier(identity)
    assert first == candidate_identifier(dict(reversed(list(identity.items()))))
    assert first.startswith("cand-") and len(first) == 15
    assert candidate_identifier({**identity, "plant_id": "plant-heat-pump"}) != first


def test_route_pipes_are_sized_for_the_largest_flow_of_the_connection(
    scenario: Scenario,
) -> None:
    settings = CandidateSettings.from_policy(CANDIDATE_POLICY)
    west, east = scenario.connections
    # 30 kg/s at 1.5 m/s with 975 kg/m3 needs sqrt(4 x 30 / (975 x pi x 1.5)) = 0.1616 m.
    # The next step of 0.025 m is 0.175 m. The eastern 20 kg/s needs 0.132 m, so 0.15 m.
    assert math.sqrt(4 * 30 / (975 * math.pi * 1.5)) == pytest.approx(0.1616, abs=1e-4)
    design = transmission_design(west, settings)
    assert isinstance(design.inner_diameter_m, Known)
    assert design.inner_diameter_m.value == pytest.approx(0.175)
    assert design.inner_diameter_m.provenance.provenance_type.value == "inferred"
    assert "carries 30 kg/s" in (design.inner_diameter_m.provenance.method or "")
    east_design = transmission_design(east, settings)
    assert isinstance(east_design.inner_diameter_m, Known)
    assert east_design.inner_diameter_m.value == pytest.approx(0.15)

    # Without a known largest flow the diameter is unknown and says why.
    unknown_flow = west.model_copy(
        update={
            "limits": west.limits.model_copy(
                update={"max_mass_flow_kg_s": Unknown(reason="not given", owner="planner")}
            )
        }
    )
    design = transmission_design(unknown_flow, settings)
    assert isinstance(design.inner_diameter_m, Unknown)
    assert "max_mass_flow_kg_s is not established" in design.inner_diameter_m.reason


@pytest.mark.requirement("A11")
def test_only_eligible_candidates_are_ranked_and_ties_share_a_rank() -> None:
    candidates = [
        candidate("cheap", 1_000_000.0),
        candidate("tied", 1_005_000.0),
        candidate("dear", 2_000_000.0),
        candidate("failed", 500_000.0),
        candidate("unresolved", 400_000.0),
        candidate("open", 300_000.0),
        candidate("conditional", 200_000.0, screening_status=ScreeningStatus.CONDITIONAL),
        candidate("no cost", None),
    ]
    states = {
        "failed": summary("physical_limit_exceeded", limiting_conditions=["exchanger_rating"]),
        "unresolved": summary("non_converged"),
        "open": summary("not_established", inputs_not_established=["plants[p].parameters[x]"]),
    }
    found, ranking = assess(candidates, states)
    result = {item.label: item for item in found}
    # Within 1 % of each other: the same rank. The next one skips the shared places.
    assert [(name, result[name].rank) for name in ("cheap", "tied", "dear")] == [
        ("cheap", 1),
        ("tied", 1),
        ("dear", 3),
    ]
    assert ranking.eligible_count == 3 and ranking.evaluated_count == 8

    failed = result["failed"]
    assert failed.technical_status is TechnicalStatus.FAILS_EVALUATED_CASE
    assert failed.evaluation_status is EvaluationStatus.VALID
    assert failed.limiting_conditions == ("exchanger_rating",) and failed.rank is None

    unresolved = result["unresolved"]
    assert unresolved.evaluation_status is EvaluationStatus.UNRESOLVED_NUMERICAL
    assert unresolved.technical_status is TechnicalStatus.NOT_ESTABLISHED

    open_case = result["open"]
    assert open_case.evaluation_status is EvaluationStatus.NOT_EVALUATED
    assert "shoulder-season: needs plants[p].parameters[x]" in open_case.explanation

    conditional = result["conditional"]
    assert conditional.ranking_eligibility.conditional
    assert conditional.ranking_eligibility.reasons == ("the site screening is conditional",)

    no_cost = result["no cost"]
    assert "the primary metric indicative_route_cost_currency is not established" in (
        no_cost.ranking_eligibility.reasons
    )
    assert "among the 3 eligible of the 8 evaluated candidates" in ranking.statement


@pytest.mark.requirement("W12")
def test_site_evidence_and_synthetic_engines_limit_eligibility() -> None:
    candidates = [candidate("a", 1.0)]
    found, _ = assess(candidates, {}, evidence=SiteEvidenceStatus.CONDITIONAL)
    assert found[0].ranking_eligibility.reasons == ("site evidence is conditional",)
    found, _ = assess(candidates, {}, synthetic_study=False)
    assert found[0].ranking_eligibility.reasons == (
        "a result of a synthetic test engine cannot enter a pilot ranking",
    )


@pytest.mark.requirement("W07")
def test_a_route_modelled_in_the_coupling_is_visible_in_the_result(
    network_model: MemoNetworkModel, scenario_data: dict[str, Any]
) -> None:
    """The reference case of the scenario, with its route modelled as transmission pipes."""
    design = {
        "inner_diameter_m": known(0.175),
        "roughness_m": known(0.0001),
        "heat_transfer_coefficient_w_m2_k": known(0.4),
    }
    case = "coupling_cases[alpha-direct-shoulder]"
    plain = couple(network_model, scenario_data, "alpha-direct-shoulder").result
    routed = couple(
        network_model,
        scenario_data,
        "alpha-direct-shoulder",
        patches=((f"{case}.transmission", design),),
    ).result
    assert plain is not None and routed is not None
    assert routed.state.value == "completed" and routed.transmission_route_id == "route-alpha-west"
    point, before = routed.operating_point, plain.operating_point
    assert point is not None and before is not None
    settings = routed.settings
    network = routed.network_result
    assert network is not None
    # The connection receives its required supply temperature and pressure difference.
    reached = network.node("s0").temperature_k
    assert reached == pytest.approx(353.15, abs=settings.temperature_change_tolerance_delta_k)
    difference = network.node("s0").pressure.value_pa - network.node("r0").pressure.value_pa
    assert difference == pytest.approx(150_000.0, abs=settings.pressure_difference_tolerance_pa)
    # The plant must therefore deliver hotter water and more heat than at the connection.
    assert point.plant_supply_temperature_k is not None
    assert point.plant_supply_temperature_k > 353.15
    assert point.transmission_heat_loss_w is not None and point.transmission_heat_loss_w > 0.0
    assert point.demand_heat_w == pytest.approx(
        before.demand_heat_w + point.transmission_heat_loss_w, rel=0.01
    )
    assert point.transmission_pressure_loss_pa is not None
    assert point.transmission_pressure_loss_pa > 0.0
    # The ledger separates the route's heat loss from the network's and still closes.
    assert routed.ledger is not None and routed.ledger.closed
    assert routed.ledger.entry("transmission_heat_loss").power_w == pytest.approx(
        point.transmission_heat_loss_w
    )
    # The route lies in the connection's pressure zone, so its limits hold along the route.
    assert "and along the route" in routed.check("connection_min_pressure").description


@pytest.mark.requirement("A11")
def test_capacity_cases_stay_separate_from_operating_eligibility(
    scenario: Scenario,
) -> None:
    # A policy without capacity_case_ids continues to enumerate without capacity cases.
    raw = CANDIDATE_POLICY.to_json_dict()
    params_v1 = {k: v for k, v in raw["parameters"].items() if k != "capacity_case_ids"}
    v1_policy = CANDIDATE_POLICY.model_copy(update={"version": "1", "parameters": params_v1})
    s1 = CandidateSettings.from_policy(v1_policy)
    assert s1.capacity_case_ids == ()

    # Overlapping operating and capacity cases are rejected explicitly.
    overlap_policy = CANDIDATE_POLICY.model_copy(
        update={
            "version": "test-overlap",
            "parameters": {
                **raw["parameters"],
                "operating_case_ids": "shoulder-season",
                "capacity_case_ids": "shoulder-season",
            },
        }
    )
    overlap_settings = CandidateSettings.from_policy(overlap_policy)
    from r3chain.domain import policies
    from r3chain.geospatial.screening import SiteScreening
    from r3chain.search.candidates import enumerate_candidates

    with pytest.raises(policies.PolicyError, match="both operating and capacity"):
        enumerate_candidates(scenario, {}, (), overlap_settings)

    # Deficits or failed limits in a capacity case never mark the candidate technically failed.
    cand_ok = candidate(
        "cap limit",
        1_000_000.0,
        capacity_case_ids=("winter-design",),
        capacity_coupling_case_ids=("cand-cap-limit-winter-design",),
    )
    cand_net = candidate(
        "cap unresolved",
        2_000_000.0,
        capacity_case_ids=("winter-design",),
        capacity_coupling_case_ids=("cand-cap-unresolved-winter-design",),
    )
    outcomes = {
        cand_ok.candidate_id: {
            cand_ok.coupling_case_ids[0]: summary("completed"),
            cand_ok.capacity_coupling_case_ids[0]: summary(
                "physical_limit_exceeded",
                reason="exchanger cold end approach reached",
                limiting_conditions=["exchanger_cold_end_approach", "source:max_drawdown_pa"],
                shortfall_causes=["insufficient_power"],
                operating_point={
                    "plant_heat_w": 2_580_000.0,
                    "transmission_heat_loss_w": 30_000.0,
                    "deficit_heat_w": 520_000.0,
                },
            ),
        },
        cand_net.candidate_id: {
            cand_net.coupling_case_ids[0]: summary("completed"),
            cand_net.capacity_coupling_case_ids[0]: summary(
                "physical_limit_exceeded",
                reason=(
                    "the network case fails: pressure limit exceeded: positive_absolute_pressure"
                ),
                limiting_conditions=["network:positive_absolute_pressure"],
                operating_point=None,
            ),
        },
    }
    screen_ok = SiteScreening(
        site_id="alpha",
        status=ScreeningStatus.PASSED,
        criteria=(),
    )
    assert isinstance(screen_ok, SiteScreening)
    found, ranking = assess_candidates(
        [cand_ok, cand_net],
        outcomes,
        site_evidence={"alpha": SiteEvidenceStatus.DOCUMENTED},
        settings=RankingSettings.from_policy(RANKING_POLICY),
        study_synthetic=True,
        route_policy="policy route-synthetic version 1",
    )
    by_name = {item.label: item for item in found}
    assert by_name["cap limit"].technical_status is TechnicalStatus.PASSES_EVALUATED_CASES
    assert by_name["cap limit"].ranking_eligibility.eligible
    assert by_name["cap limit"].rank == 1
    assert by_name["cap limit"].capacity_cases[0].outcome == "limit_found"
    assert by_name["cap limit"].capacity_cases[0].delivered_heat_w == pytest.approx(2_550_000.0)

    assert by_name["cap unresolved"].technical_status is TechnicalStatus.PASSES_EVALUATED_CASES
    assert by_name["cap unresolved"].ranking_eligibility.eligible
    assert by_name["cap unresolved"].rank == 2
    assert by_name["cap unresolved"].capacity_cases[0].outcome == "not_established"
    assert "positive_absolute_pressure" in by_name["cap unresolved"].capacity_cases[0].reason
    assert ranking.eligible_count == 2
