"""Joint uncertainty sampling, pre-drilling risk, resilience cases, and decision analysis (M5)."""

from r3chain.uncertainty.policies import DecisionRuleSettings, UncertaintySettings
from r3chain.uncertainty.resilience import ResilienceOutcome, evaluate_resilience_cases
from r3chain.uncertainty.risk import (
    AdoptionStageOutcome,
    AdoptionThresholdSummary,
    CandidateRiskSummary,
    CandidateSampleOutcome,
    DecisionDriverRow,
    DecisionRuleConfig,
    PreDrillingExpectedCase,
    PreDrillingInputs,
    QuantileSummary,
    RankingStabilityRow,
    SampleAccount,
    UncertaintyComparisonSummary,
    analyse_candidate_risk,
    compare_candidate_uncertainty,
    compute_pre_drilling_expected_case,
    estimate_breakeven_adoption_fraction,
)
from r3chain.uncertainty.sampling import (
    JointSampleSet,
    ProgressiveAssessment,
    generate_joint_samples,
    generate_scenario_sweep,
    point_estimate_realization,
    run_progressive_allocation,
)

__all__ = [
    "AdoptionStageOutcome",
    "AdoptionThresholdSummary",
    "CandidateRiskSummary",
    "CandidateSampleOutcome",
    "DecisionDriverRow",
    "DecisionRuleConfig",
    "DecisionRuleSettings",
    "JointSampleSet",
    "PreDrillingExpectedCase",
    "PreDrillingInputs",
    "ProgressiveAssessment",
    "QuantileSummary",
    "RankingStabilityRow",
    "ResilienceOutcome",
    "SampleAccount",
    "UncertaintyComparisonSummary",
    "UncertaintySettings",
    "analyse_candidate_risk",
    "compare_candidate_uncertainty",
    "compute_pre_drilling_expected_case",
    "estimate_breakeven_adoption_fraction",
    "evaluate_resilience_cases",
    "generate_joint_samples",
    "generate_scenario_sweep",
    "point_estimate_realization",
    "run_progressive_allocation",
]
