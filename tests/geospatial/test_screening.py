"""Site screening against the screening policy (specification 8.2)."""

from __future__ import annotations

from typing import Any

import pytest

from r3chain.domain import policies
from r3chain.domain.records.scenario import Scenario
from r3chain.geospatial.screening import (
    CriterionOutcome,
    ScreeningSettings,
    ScreeningStatus,
    SiteScreening,
    screen_site,
)
from tests.conftest import UNKNOWN_INPUT, changed, known, policy_variant, repository_policies

POLICY = repository_policies()["screening-synthetic"]


def screen(
    scenario: Scenario, site_id: str, policy: Any = POLICY, layers: Any = None
) -> SiteScreening:
    site = next(item for item in scenario.sites if item.site_id == site_id)
    return screen_site(
        site,
        well_scenarios=scenario.well_scenarios,
        layers=scenario.spatial_layers if layers is None else layers,
        settings=ScreeningSettings.from_policy(policy),
        study_crs=scenario.study.study_area.crs,
    )


def outcomes(screening: SiteScreening) -> dict[str, str]:
    return {item.criterion_id: item.outcome.value for item in screening.criteria}


@pytest.mark.requirement("W31")
@pytest.mark.requirement("W32")
def test_alpha_passes_with_the_pad_left_after_the_housing_setback(scenario: Scenario) -> None:
    alpha = screen(scenario, "alpha")
    assert alpha.status is ScreeningStatus.PASSED
    assert set(outcomes(alpha).values()) == {"pass"}
    # The parcel is 100 m by 80 m. Housing begins 20 m north of it and needs 40 m of setback,
    # so the northern 20 m are lost: 100 m by 60 m remain.
    pad = alpha.criterion("usable_pad_area")
    assert pad.observed == pytest.approx(6000.0, abs=0.01)
    assert (pad.limit, pad.unit) == (5000.0, "m2")
    assert "spatial_layers[restrictions].features[R1]" in pad.evidence
    assert alpha.pad_point == pytest.approx((500450.0, 5701030.0), abs=1e-3)
    # The target area lies 240 m north of the pad.
    target = alpha.criterion("geological_target")
    assert target.observed == pytest.approx(240.0, abs=1e-3)
    assert alpha.targets[0].reachable
    access = alpha.criterion("access")
    assert (access.observed, access.limit) == (6.0, 4.0)
    assert alpha.jurisdictions == ("Musterstadt (synthetisch)",)
    assert "not permit ready" in alpha.note
    assert "noise_and_acceptance" in alpha.not_assessed


@pytest.mark.requirement("A12")
@pytest.mark.requirement("W32")
def test_a_setback_that_is_not_documented_makes_the_pad_unknown(scenario: Scenario) -> None:
    gamma = screen(scenario, "gamma")
    assert gamma.status is ScreeningStatus.CONDITIONAL
    pad = gamma.criterion("usable_pad_area")
    assert pad.outcome is CriterionOutcome.UNKNOWN
    assert "R3 of kind power_line has no documented setback" in pad.reason
    # The area is still reported: 85 m by 70 m remain after the water protection zone.
    assert pad.observed == pytest.approx(5950.0, abs=0.01)
    # Gamma lies in the neighbouring municipality. That is recorded and excludes nothing.
    assert gamma.jurisdictions == ("Nachbarort (synthetisch)",)

    # Once the policy gives a setback for power lines, the pad is assessed.
    documented = policy_variant(POLICY, setback_power_line_m=20.0)
    assert screen(scenario, "gamma", documented).status is ScreeningStatus.PASSED


@pytest.mark.requirement("A12")
@pytest.mark.requirement("W31")
def test_missing_evidence_never_becomes_a_pass(
    scenario: Scenario, scenario_data: dict[str, Any]
) -> None:
    delta = screen(scenario, "delta")
    assert delta.status is ScreeningStatus.EXCLUDED
    found = outcomes(delta)
    assert found["land_status"] == "fail"
    assert found["access"] == "fail"
    # Unknown evidence stays unknown, even on a site that is excluded for other reasons.
    assert found["constraint-housing-setback"] == "unknown"
    assert found["geological_target"] == "unknown"
    assert delta.criterion("geological_target").reason == (
        "no subsurface concept is defined for this site"
    )

    unknown_land = Scenario.model_validate(
        changed(scenario_data, "sites[beta].land_status", UNKNOWN_INPUT)
    )
    beta = screen(unknown_land, "beta")
    assert beta.criterion("land_status").outcome is CriterionOutcome.UNKNOWN
    assert beta.status is ScreeningStatus.CONDITIONAL

    # Without the layers a criterion needs, it is unknown, not passed.
    bare = screen(scenario, "beta", layers=())
    assert outcomes(bare) == {
        "geological_target": "unknown",
        "land_status": "pass",
        "constraint-housing-setback": "pass",
        "usable_pad_area": "unknown",
        "access": "unknown",
    }
    assert bare.status is ScreeningStatus.CONDITIONAL


@pytest.mark.requirement("W31")
def test_a_rejected_restriction_near_a_parcel_makes_the_pad_unknown(
    scenario: Scenario,
) -> None:
    # Move the rejected bow tie next to Beta. It could not be imported, so Beta's pad area cannot
    # be known.
    layers = []
    for layer in scenario.spatial_layers:
        if layer.layer_id == "restrictions":
            moved = layer.rejected[0].model_copy(
                update={"bounds": (501300.0, 5700600.0, 501350.0, 5700650.0)}
            )
            layer = layer.model_copy(update={"rejected": (moved,)})
        layers.append(layer)
    beta = screen(scenario, "beta", layers=tuple(layers))
    pad = beta.criterion("usable_pad_area")
    assert pad.outcome is CriterionOutcome.UNKNOWN
    assert "R4 was rejected at import" in pad.reason


@pytest.mark.requirement("W32")
def test_policy_values_change_the_outcome_without_code(
    scenario: Scenario, scenario_data: dict[str, Any]
) -> None:
    larger = policy_variant(POLICY, min_usable_pad_area_m2=6500.0)
    alpha = screen(scenario, "alpha", larger)
    assert alpha.status is ScreeningStatus.EXCLUDED
    assert alpha.criterion("usable_pad_area").reason == "only 6000 m2 remain usable"

    wider = policy_variant(POLICY, min_access_width_m=5.5)
    assert screen(scenario, "beta", wider).criterion("access").outcome is CriterionOutcome.FAIL

    restricted = Scenario.model_validate(
        changed(scenario_data, "sites[delta].land_status", known("restricted"))
    )
    allowed = policy_variant(POLICY, allow_restricted_land=True)
    assert screen(restricted, "delta", allowed).criterion("land_status").outcome is (
        CriterionOutcome.PASS
    )

    reach = policy_variant(POLICY, max_horizontal_reach_m=200.0)
    alpha = screen(scenario, "alpha", reach)
    assert alpha.criterion("geological_target").outcome is CriterionOutcome.FAIL
    assert alpha.targets[0].reason == "the target lies 240 m from the pad"


@pytest.mark.requirement("W32")
def test_a_policy_without_a_needed_value_is_refused() -> None:
    incomplete = policy_variant(POLICY)
    parameters = {
        name: value for name, value in incomplete.parameters.items() if name != "min_access_width_m"
    }
    with pytest.raises(policies.PolicyError, match="lacks the number min_access_width_m"):
        ScreeningSettings.from_policy(incomplete.model_copy(update={"parameters": parameters}))
