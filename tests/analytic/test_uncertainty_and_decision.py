"""Analytic fixtures for M5 uncertainty, conditional vs pre-drilling risk, and V01-V04 (A14)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from r3chain.domain.evidence import Known, Provenance, ProvenanceType
from r3chain.domain.records.scenarios import (
    CorrelationGroupSpec,
    CorrelationPair,
    DistributionSpec,
    SuccessDefinition,
    Support,
    UncertaintyKind,
    UncertaintyModel,
    UncertainVariable,
)
from r3chain.domain.units import JOULES_PER_MWH, currency_per_mwh_to_per_j
from r3chain.operation.dispatch import RuleBasedDispatchStrategy
from r3chain.ports.dispatch import (
    DispatchGeothermalSpec,
    DispatchRequest,
    DispatchUnit,
    ExistingUnitRole,
)
from r3chain.uncertainty.resilience import evaluate_resilience_cases
from r3chain.uncertainty.risk import (
    AdoptionStageOutcome,
    CandidateSampleOutcome,
    DecisionRuleConfig,
    PreDrillingInputs,
    analyse_candidate_risk,
    compare_candidate_uncertainty,
    compute_pre_drilling_expected_case,
    estimate_breakeven_adoption_fraction,
)
from r3chain.uncertainty.sampling import (
    generate_joint_samples,
    point_estimate_realization,
    run_progressive_allocation,
)

PROVENANCE = Provenance(provenance_type=ProvenanceType.SYNTHETIC, source_id="syn-definitions")


def _known(value: float) -> Known[float]:
    return Known(value=value, provenance=PROVENANCE)


@pytest.mark.requirement("A14")
@pytest.mark.requirement("W13")
def test_joint_correlated_sampling_reproduces_identically_and_rejects_non_psd() -> None:
    var_t = UncertainVariable(
        variable_id="temp",
        field_path="well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]",
        unit="K",
        kind=UncertaintyKind.DISTRIBUTION,
        distribution=DistributionSpec(family="normal", parameters={"mean": 378.15, "std": 8.0}),
        support=Support(lower=358.15, upper=398.15),
        evidence=PROVENANCE,
        correlation_group="subsurface",
    )
    var_q = UncertainVariable(
        variable_id="flow",
        field_path="well_scenarios[source-alpha].reference_mass_flow_kg_s",
        unit="kg/s",
        kind=UncertaintyKind.DISTRIBUTION,
        distribution=DistributionSpec(
            family="triangular", parameters={"low": 20.0, "mode": 30.0, "high": 45.0}
        ),
        support=Support(lower=18.0, upper=48.0),
        evidence=PROVENANCE,
        correlation_group="subsurface",
    )
    var_p = UncertainVariable(
        variable_id="elec",
        field_path="economic_scenarios[econ-base].discount_rate_per_year",
        unit="1/a",
        kind=UncertaintyKind.DISTRIBUTION,
        distribution=DistributionSpec(family="uniform", parameters={"low": 0.03, "high": 0.07}),
        support=Support(lower=0.02, upper=0.08),
        evidence=PROVENANCE,
    )
    model = UncertaintyModel(
        uncertainty_model_id="u-corr",
        variables=(var_t, var_q, var_p),
        correlation_groups=(
            CorrelationGroupSpec(
                group_id="subsurface",
                method="rank_correlation",
                pairs=(
                    CorrelationPair(variable_a="temp", variable_b="flow", rank_correlation=0.8),
                ),
            ),
        ),
        success_definition=_known_success(),
    )

    s1 = generate_joint_samples(model, sample_count=128, seed=42, method="latin_hypercube")
    s2 = generate_joint_samples(model, sample_count=128, seed=42, method="latin_hypercube")
    s3 = generate_joint_samples(model, sample_count=128, seed=99, method="latin_hypercube")
    assert s1.values_by_variable == s2.values_by_variable
    assert s1.values_by_variable != s3.values_by_variable

    # All samples stay inside declared Support.
    for v in model.variables:
        vals = s1.values_by_variable[v.variable_id]
        assert all(v.support.lower <= x <= v.support.upper for x in vals)

    # Empirical Spearman correlation on subsurface group tracks target 0.8.
    from scipy.stats import spearmanr

    rho, _ = spearmanr(s1.values_by_variable["temp"], s1.values_by_variable["flow"])
    assert rho == pytest.approx(0.8, abs=0.1)

    # Point estimate picks central medians cleanly.
    pe = point_estimate_realization(model)
    assert pe["temp"] == pytest.approx(378.15, abs=0.2)
    assert pe["elec"] == pytest.approx(0.05, abs=1e-9)

    # Non-PSD correlation matrix is rejected loudly.
    with pytest.raises(ValueError, match="positive semi-definite"):
        CorrelationGroupSpec(
            group_id="bad-psd",
            method="rank_correlation",
            pairs=(
                CorrelationPair(variable_a="a", variable_b="b", rank_correlation=0.9),
                CorrelationPair(variable_a="b", variable_b="c", rank_correlation=0.9),
                CorrelationPair(variable_a="a", variable_b="c", rank_correlation=-0.9),
            ),
        )


def _known_success() -> Known[SuccessDefinition]:
    return Known(
        value=SuccessDefinition(
            event="Commercial flow >= 25 kg/s and WHP within drawdown at >= 365 K",
            criteria=("source_operated", "min_temperature_k"),
            information_time="post_exploration_well_test",
            conditional_on="drilling_and_testing",
        ),
        provenance=PROVENANCE,
    )


@pytest.mark.requirement("A14")
def test_unresolved_samples_stay_separate_from_physical_failures_and_denominators() -> None:
    # 10 total samples: 2 numerically unresolved -> 8 evaluated.
    # Of the 8 evaluated: 6 pass technically (feasible), 2 fail physical temperature limits.
    outcomes = [
        *[
            CandidateSampleOutcome(
                sample_index=i,
                candidate_id="cand-a",
                evaluated=True,
                feasible=True,
                pv_cost_currency=20_000_000.0 + i * 500_000.0,
                baseline_pv_cost_currency=25_000_000.0,
                lcoh_currency_per_j=(20.0 + i) / JOULES_PER_MWH,
                geothermal_demand_share_fraction=0.75,
                pv_heat_j=50_000.0 * JOULES_PER_MWH,
            )
            for i in range(6)
        ],
        CandidateSampleOutcome(
            sample_index=6,
            candidate_id="cand-a",
            evaluated=True,
            feasible=False,
            failure_kind="physical_failure",
            reason="insufficient_temperature",
        ),
        CandidateSampleOutcome(
            sample_index=7,
            candidate_id="cand-a",
            evaluated=True,
            feasible=False,
            failure_kind="physical_failure",
            reason="source_limit",
        ),
        CandidateSampleOutcome(
            sample_index=8,
            candidate_id="cand-a",
            evaluated=False,
            feasible=False,
            failure_kind="unresolved_numerical",
            reason="non_converged",
        ),
        CandidateSampleOutcome(
            sample_index=9,
            candidate_id="cand-a",
            evaluated=False,
            feasible=False,
            failure_kind="unresolved_numerical",
            reason="non_converged",
        ),
    ]
    risk = analyse_candidate_risk("cand-a", outcomes, has_probabilities=True)
    acc = risk.sample_account
    assert acc.total_samples == 10
    assert acc.evaluated_samples == 8
    assert acc.unresolved_samples == 2
    assert acc.feasible_samples == 6
    assert acc.physical_failure_samples == 2
    assert acc.unresolved_fraction == pytest.approx(0.2)
    # Probability denominator is evaluated_samples (8), never including unresolved runs.
    assert acc.conditional_service_probability == pytest.approx(6.0 / 8.0)
    assert "6 / 8" in acc.service_probability_denominator
    # Quantiles are strictly ascending (p05 <= p50 <= p95).
    assert risk.lcoh_quantiles is not None
    assert risk.lcoh_quantiles.p05 <= risk.lcoh_quantiles.p50 <= risk.lcoh_quantiles.p95


@pytest.mark.requirement("W02")
def test_pre_drilling_expected_case_accounts_for_failed_drilling_abandonment_and_fallback() -> None:
    # Hand derivation:
    #   p_success = 0.6
    #   drilling CAPEX = 10 MEUR
    #   failure_drilling_cost_fraction = 0.4, abandonment = 0.5 MEUR -> 4.5 MEUR
    #   baseline fallback PV cost = 25 MEUR -> failed PV cost = 29.5 MEUR
    #   conditional feasible PV cost = 20 MEUR across PV heat = 100,000 MWh_PV
    #   expected PV cost = 0.6 * 20M + 0.4 * 29.5M = 12.0M + 11.8M = 23.8 MEUR
    #   expected LCOH = 23.8 MEUR / (100,000 MWh) = 238 EUR/MWh (vs naive 200/0.6 = 333.33!)
    pv_heat = 100_000.0 * JOULES_PER_MWH
    expected = compute_pre_drilling_expected_case(
        PreDrillingInputs(
            success_probability_fraction=0.6,
            drilling_capex_currency=10_000_000.0,
            failure_drilling_cost_fraction=0.4,
            abandonment_cost_currency=500_000.0,
            conditional_mean_pv_cost_currency=20_000_000.0,
            conditional_mean_pv_heat_j=pv_heat,
            baseline_mean_pv_cost_currency=25_000_000.0,
            baseline_mean_pv_heat_j=pv_heat,
        )
    )
    assert expected.failed_drilling_pv_cost_currency == pytest.approx(29_500_000.0)
    assert expected.expected_pv_cost_currency == pytest.approx(23_800_000.0)
    assert expected.expected_incremental_cost_currency == pytest.approx(-1_200_000.0)
    assert expected.conditional_lcoh_currency_per_j * JOULES_PER_MWH == pytest.approx(200.0)
    assert expected.expected_lcoh_currency_per_j * JOULES_PER_MWH == pytest.approx(238.0)


@pytest.mark.requirement("W13")
@pytest.mark.requirement("W34")
@pytest.mark.requirement("V01")
def test_pareto_regret_decision_drivers_and_no_defensible_preference() -> None:
    # Two candidates A and B across 4 paired samples of variable `power_tariff`:
    # When tariff in [50, 70] (samples 0, 1): A wins by 2M; when [110, 130] (samples 2, 3): B wins.
    sample_inputs = [
        {"power_tariff": 50.0, "other": 1.0},
        {"power_tariff": 70.0, "other": 2.0},
        {"power_tariff": 110.0, "other": 1.0},
        {"power_tariff": 130.0, "other": 2.0},
    ]
    a_costs = [18_000_000.0, 19_000_000.0, 24_000_000.0, 26_000_000.0]
    b_costs = [20_000_000.0, 21_000_000.0, 20_000_000.0, 20_500_000.0]
    outcomes_by_cand = {
        "cand-a": [
            CandidateSampleOutcome(
                sample_index=i,
                candidate_id="cand-a",
                evaluated=True,
                feasible=True,
                pv_cost_currency=a_costs[i],
                baseline_pv_cost_currency=25_000_000.0,
                lcoh_currency_per_j=a_costs[i] / (100_000.0 * JOULES_PER_MWH),
                geothermal_demand_share_fraction=0.80,
                pv_heat_j=100_000.0 * JOULES_PER_MWH,
            )
            for i in range(4)
        ],
        "cand-b": [
            CandidateSampleOutcome(
                sample_index=i,
                candidate_id="cand-b",
                evaluated=True,
                feasible=True,
                pv_cost_currency=b_costs[i],
                baseline_pv_cost_currency=25_000_000.0,
                lcoh_currency_per_j=b_costs[i] / (100_000.0 * JOULES_PER_MWH),
                geothermal_demand_share_fraction=0.65,
                pv_heat_j=100_000.0 * JOULES_PER_MWH,
            )
            for i in range(4)
        ],
    }
    comp = compare_candidate_uncertainty(
        outcomes_by_cand,
        sample_inputs=sample_inputs,
        has_probabilities=True,
        rules=DecisionRuleConfig(
            decision_metric="pv_total_cost_currency",
            decision_rule="expected",
            tie_tolerance_fraction=0.01,
            preferable_cost_margin_fraction=0.05,
            max_unresolved_fraction=0.25,
            pareto_objectives=(
                ("pv_total_cost_currency", "min"),
                ("geothermal_demand_share_fraction", "max"),
            ),
        ),
    )
    # Both A (higher geo share 0.80, mean cost 21.75M) and B (lower mean cost 20.375M, share 0.65)
    # lie on the Pareto frontier.
    assert set(comp.pareto_candidate_ids) == {"cand-a", "cand-b"}
    # Regret for A in samples 2,3 = 4M, 5.5M -> max_regret = 5.5M; B in samples 0,1 -> 2M.
    assert comp.candidates["cand-a"].max_regret_currency == pytest.approx(5_500_000.0)
    assert comp.candidates["cand-b"].max_regret_currency == pytest.approx(2_000_000.0)
    # V01: Both candidates rank first in 2 / 4 = 50% of paired samples -> tie/no clear winner.
    stability = {row.candidate_id: row for row in comp.ranking_stability}
    assert stability["cand-a"].first_rank_count == 2
    assert stability["cand-a"].denominator == 4
    assert stability["cand-a"].first_rank_share == pytest.approx(0.5)
    assert comp.preference_outcome != "clear_winner"
    # V01: power_tariff perfectly correlates with rank flip / margin, while `other` does not.
    assert comp.decision_drivers[0].variable_id == "power_tariff"
    assert comp.decision_drivers[0].switches_winner is True


@pytest.mark.requirement("V02")
def test_resilience_cases_report_unserved_heat_hours_and_largest_deficit_only() -> None:
    starts = (
        datetime(2026, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 2, tzinfo=UTC),
    )
    # Demand: 3 MW, 2 MW, 1 MW over 1-hour intervals.
    # Base load = 0.5 MW (prio 1); Geo HP = 2.0 MW (prio 2); Backup = 1.5 MW (prio 3).
    req = DispatchRequest(
        request_id="resilience",
        operating_scenario_id="op",
        starts_utc=starts,
        durations_s=(3600.0, 3600.0, 3600.0),
        demand_heat_w=(3_000_000.0, 2_000_000.0, 1_000_000.0),
        electricity_price_currency_per_j=(currency_per_mwh_to_per_j(80.0),) * 3,
        existing_units=(
            DispatchUnit(
                unit_id="base",
                label="Waste heat",
                role=ExistingUnitRole.BASE_LOAD,
                priority=1,
                max_heat_w=500_000.0,
            ),
            DispatchUnit(
                unit_id="backup",
                label="Gas boiler",
                role=ExistingUnitRole.PEAK_BACKUP,
                priority=3,
                max_heat_w=1_500_000.0,
            ),
        ),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo-hp",
            max_delivered_heat_w=2_000_000.0,
            max_plant_handover_heat_w=2_000_000.0,
            compressor_electricity_per_plant_heat_ratio=0.25,
        ),
        geothermal_priority=2,
    )
    results = evaluate_resilience_cases(
        req,
        strategy=RuleBasedDispatchStrategy(),
        cases=("source_outage", "backup_loss", "base_load_loss", "heat_pump_electricity_loss"),
        has_heat_pump=True,
    )
    by_case = {r.case_id: r for r in results}
    # source_outage / hp_electricity_loss: base (0.5 MW) + backup (1.5 MW) = 2.0 MW capacity.
    # Hour 0 (3 MW demand) leaves 1.0 MW unserved (3.6e9 J); Hours 1 & 2 covered -> 1.0 hour!
    assert by_case["source_outage"].unserved_heat_j == pytest.approx(1_000_000.0 * 3600.0)
    assert by_case["source_outage"].unserved_hours == pytest.approx(1.0)
    assert by_case["source_outage"].largest_deficit_w == pytest.approx(1_000_000.0)
    assert by_case["heat_pump_electricity_loss"].unserved_heat_j == pytest.approx(3.6e9)
    # backup_loss: base (0.5 MW) + geo (2.0 MW) = 2.5 MW -> Hour 0 leaves 0.5 MW deficit.
    assert by_case["backup_loss"].largest_deficit_w == pytest.approx(500_000.0)
    assert by_case["backup_loss"].unserved_hours == pytest.approx(1.0)
    # base_load_loss: geo (2.0 MW) + backup (1.5 MW) = 3.5 MW -> covers all intervals cleanly.
    assert by_case["base_load_loss"].unserved_heat_j == 0.0
    assert by_case["base_load_loss"].unserved_hours == 0.0


@pytest.mark.requirement("V04")
def test_progressive_allocation_checks_error_against_full_set() -> None:
    # Smooth converging series vs oscillating sequence that changes sharply on the tail.
    smooth = [100.0 + ((-1) ** i) * (0.2 / (i + 1)) for i in range(32)]
    accepted = run_progressive_allocation(
        total_count=32,
        initial_count=16,
        step_count=8,
        max_error_fraction=0.01,
        evaluate_prefix=lambda n: float(np.mean(smooth[:n])),
    )
    assert accepted.accepted is True
    assert accepted.chosen_count < 32
    assert accepted.relative_error_vs_full <= 0.01

    tail_jump = [100.0] * 24 + [140.0] * 8  # mean jumps from 100 to 110 on last 8 samples
    rejected = run_progressive_allocation(
        total_count=32,
        initial_count=16,
        step_count=8,
        max_error_fraction=0.02,
        evaluate_prefix=lambda n: float(np.mean(tail_jump[:n])),
    )
    assert rejected.chosen_count == 32
    assert rejected.relative_error_vs_full > 0.02 or rejected.chosen_count == 32


@pytest.mark.requirement("W18")
def test_adoption_stages_and_breakeven_threshold() -> None:
    # Linear crossing: at 50% adoption incremental cost = +2M, at 100% adoption = -2M -> 75%.
    stages = (
        AdoptionStageOutcome(
            adoption_fraction=0.50,
            geothermal_delivered_heat_j=4_000.0 * JOULES_PER_MWH,
            lcoh_geothermal_delivered_currency_per_j=220.0 / JOULES_PER_MWH,
            pv_total_cost_currency=22_000_000.0,
            incremental_cost_vs_baseline_currency=2_000_000.0,
        ),
        AdoptionStageOutcome(
            adoption_fraction=1.00,
            geothermal_delivered_heat_j=8_000.0 * JOULES_PER_MWH,
            lcoh_geothermal_delivered_currency_per_j=160.0 / JOULES_PER_MWH,
            pv_total_cost_currency=23_000_000.0,
            incremental_cost_vs_baseline_currency=-2_000_000.0,
        ),
    )
    be, note = estimate_breakeven_adoption_fraction(stages)
    assert be == pytest.approx(0.75)
    assert "75.0%" in note
