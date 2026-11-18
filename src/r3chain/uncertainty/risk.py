"""Risk metrics, pre-drilling expected case, regret, Pareto set, and decision drivers."""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from typing import Literal

import numpy as np
from scipy.stats import spearmanr

from r3chain.domain.base import NonNegativeReal, Real, Record


class CandidateSampleOutcome(Record):
    """Outcome of one candidate in one joint uncertainty sample."""

    sample_index: int
    candidate_id: str
    evaluated: bool
    feasible: bool
    failure_kind: Literal["none", "physical_failure", "unresolved_numerical"] = "none"
    reason: str = ""
    pv_cost_currency: Real | None = None
    baseline_pv_cost_currency: Real | None = None
    lcoh_currency_per_j: Real | None = None
    geothermal_demand_share_fraction: Real | None = None
    pv_heat_j: NonNegativeReal | None = None
    drilling_capex_currency: NonNegativeReal | None = None


class SampleAccount(Record):
    """Explicit separation of unresolved runs vs physical failures and denominators (A14)."""

    total_samples: int
    evaluated_samples: int
    unresolved_samples: int
    feasible_samples: int
    physical_failure_samples: int
    unresolved_fraction: NonNegativeReal
    conditional_service_probability: NonNegativeReal | None = None
    service_probability_denominator: str


class QuantileSummary(Record):
    """Explicit ascending statistical quantiles: p05 <= p50 <= p95 (spec 14.4)."""

    sample_count: int
    p05: Real
    p50: Real
    p95: Real
    mean: Real
    definition: str = "ascending statistical quantiles p05 <= p50 <= p95 over feasible samples"


class PreDrillingInputs(Record):
    """Inputs for coherent conditional vs pre-drilling expected economics (spec 14.2, W02)."""

    success_probability_fraction: NonNegativeReal
    drilling_capex_currency: NonNegativeReal
    failure_drilling_cost_fraction: NonNegativeReal
    abandonment_cost_currency: NonNegativeReal
    conditional_mean_pv_cost_currency: Real
    conditional_mean_pv_heat_j: NonNegativeReal
    baseline_mean_pv_cost_currency: Real
    baseline_mean_pv_heat_j: NonNegativeReal


class PreDrillingExpectedCase(Record):
    """Pre-drilling expected cost integrating failed drilling, abandonment, and fallback heat."""

    p_success: NonNegativeReal
    drilling_capex_currency: NonNegativeReal
    failed_drilling_pv_cost_currency: Real
    expected_pv_cost_currency: Real
    expected_incremental_cost_currency: Real
    conditional_lcoh_currency_per_j: Real | None
    expected_lcoh_currency_per_j: Real | None


class AdoptionStageOutcome(Record):
    """Economic performance under one explicit connection adoption stage (W18, section 14.4)."""

    adoption_fraction: NonNegativeReal
    geothermal_delivered_heat_j: NonNegativeReal
    lcoh_geothermal_delivered_currency_per_j: Real | None
    pv_total_cost_currency: Real
    incremental_cost_vs_baseline_currency: Real


class AdoptionThresholdSummary(Record):
    """Break-even adoption threshold and scenario stages for one candidate."""

    candidate_id: str
    stages: tuple[AdoptionStageOutcome, ...]
    breakeven_adoption_fraction: Real | None = None
    note: str = ""


class RankingStabilityRow(Record):
    """Share of paired samples where one candidate ranks first, with its denominator (V01)."""

    candidate_id: str
    first_rank_count: int
    denominator: int
    first_rank_share: NonNegativeReal


class DecisionDriverRow(Record):
    """Uncertain variable whose variation drives or flips candidate rank order (V01)."""

    variable_id: str
    spearman_rank_correlation: Real
    switches_winner: bool
    affected_candidates: tuple[str, ...]
    effect_summary: str


class DecisionRuleConfig(Record):
    """Subset of ranking policy rules governing M5 comparison and preference checks."""

    decision_metric: str
    decision_rule: Literal["expected", "median", "p95", "minimax_regret"]
    tie_tolerance_fraction: NonNegativeReal
    preferable_cost_margin_fraction: NonNegativeReal
    max_unresolved_fraction: NonNegativeReal
    pareto_objectives: tuple[tuple[str, Literal["min", "max"]], ...]


class CandidateRiskSummary(Record):
    """Probabilistic / sweep risk profile of one candidate."""

    candidate_id: str
    sample_account: SampleAccount
    lcoh_quantiles: QuantileSummary | None = None
    pv_cost_quantiles: QuantileSummary | None = None
    incremental_cost_quantiles: QuantileSummary | None = None
    mean_geothermal_share_fraction: NonNegativeReal | None = None
    mean_regret_currency: NonNegativeReal | None = None
    max_regret_currency: NonNegativeReal | None = None
    regret_denominator: int = 0
    decision_score: Real | None = None
    pareto_optimal: bool = False
    pre_drilling: PreDrillingExpectedCase | None = None


class UncertaintyComparisonSummary(Record):
    """Joint multi-candidate uncertainty, regret, Pareto, driver, and preference comparison."""

    has_probabilities: bool
    total_paired_samples: int
    candidates: dict[str, CandidateRiskSummary]
    pareto_candidate_ids: tuple[str, ...]
    ordered_candidate_ids: tuple[str, ...]
    ranking_stability: tuple[RankingStabilityRow, ...]
    decision_drivers: tuple[DecisionDriverRow, ...]
    preference_outcome: Literal[
        "clear_winner", "tie_or_overlap", "no_defensible_preference", "insufficient_evidence"
    ]
    preference_reason: str
    missing_information: tuple[str, ...] = ()


def compute_pre_drilling_expected_case(inputs: PreDrillingInputs) -> PreDrillingExpectedCase:
    """Compute pre-drilling expected PV cost and LCOH without dividing LCOH by p_success."""
    p = float(inputs.success_probability_fraction)
    failed_pv = (
        float(inputs.failure_drilling_cost_fraction) * float(inputs.drilling_capex_currency)
        + float(inputs.abandonment_cost_currency)
        + float(inputs.baseline_mean_pv_cost_currency)
    )
    expected_pv = p * float(inputs.conditional_mean_pv_cost_currency) + (1.0 - p) * failed_pv
    expected_inc = expected_pv - float(inputs.baseline_mean_pv_cost_currency)
    cond_lcoh = (
        float(inputs.conditional_mean_pv_cost_currency) / float(inputs.conditional_mean_pv_heat_j)
        if inputs.conditional_mean_pv_heat_j > 0.0
        else None
    )
    # Full heat service continues under baseline fallback when drilling fails -> full heat denom.
    expected_pv_heat = p * float(inputs.conditional_mean_pv_heat_j) + (1.0 - p) * float(
        inputs.baseline_mean_pv_heat_j
    )
    expected_lcoh = expected_pv / expected_pv_heat if expected_pv_heat > 0.0 else None
    return PreDrillingExpectedCase(
        p_success=p,
        drilling_capex_currency=inputs.drilling_capex_currency,
        failed_drilling_pv_cost_currency=failed_pv,
        expected_pv_cost_currency=expected_pv,
        expected_incremental_cost_currency=expected_inc,
        conditional_lcoh_currency_per_j=cond_lcoh,
        expected_lcoh_currency_per_j=expected_lcoh,
    )


def _quantiles(values: Sequence[float]) -> QuantileSummary | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    p05, p50, p95 = (float(x) for x in np.percentile(arr, [5.0, 50.0, 95.0], method="linear"))
    return QuantileSummary(
        sample_count=len(values),
        p05=p05,
        p50=p50,
        p95=p95,
        mean=float(np.mean(arr)),
    )


def estimate_breakeven_adoption_fraction(
    stages: Sequence[AdoptionStageOutcome],
) -> tuple[float | None, str]:
    """Interpolate adoption fraction where incremental cost vs baseline reaches zero (spec 14.4)."""
    ordered = sorted(stages, key=lambda s: s.adoption_fraction)
    if not ordered:
        return None, "no adoption stages evaluated"
    if all(s.incremental_cost_vs_baseline_currency <= 0.0 for s in ordered):
        return (
            float(ordered[0].adoption_fraction),
            "cost-competitive across all evaluated adoption stages",
        )
    if all(s.incremental_cost_vs_baseline_currency > 0.0 for s in ordered):
        return (
            None,
            "incremental cost remains positive across all adoption stages up to "
            f"{ordered[-1].adoption_fraction:.0%}",
        )
    for left, right in itertools.pairwise(ordered):
        l_inc = left.incremental_cost_vs_baseline_currency
        r_inc = right.incremental_cost_vs_baseline_currency
        if (l_inc <= 0.0 <= r_inc) or (l_inc >= 0.0 >= r_inc):
            span = l_inc - r_inc
            if abs(span) <= 1e-12:
                return float(left.adoption_fraction), "constant at threshold"
            t = l_inc / span
            frac = left.adoption_fraction + t * (right.adoption_fraction - left.adoption_fraction)
            return float(frac), f"breaks even vs baseline at {frac:.1%} connected load"
    return None, "no sign crossing detected"


def analyse_candidate_risk(
    candidate_id: str,
    outcomes: Sequence[CandidateSampleOutcome],
    *,
    has_probabilities: bool,
    pre_drilling_template: PreDrillingInputs | None = None,
) -> CandidateRiskSummary:
    """Summarise sample accounting, conditional quantiles, and pre-drilling risk for one option."""
    total = len(outcomes)
    unresolved = sum(1 for item in outcomes if not item.evaluated)
    evaluated = total - unresolved
    feasible_items = [item for item in outcomes if item.evaluated and item.feasible]
    feasible = len(feasible_items)
    physical_failures = evaluated - feasible
    unresolved_frac = unresolved / total if total > 0 else 0.0
    service_prob = (feasible / evaluated) if (has_probabilities and evaluated > 0) else None
    denom_label = (
        f"feasible / evaluated = {feasible} / {evaluated} (unresolved excluded: {unresolved})"
        if has_probabilities
        else f"scenario sweep only (no probabilities; feasible={feasible}, evaluated={evaluated})"
    )
    account = SampleAccount(
        total_samples=total,
        evaluated_samples=evaluated,
        unresolved_samples=unresolved,
        feasible_samples=feasible,
        physical_failure_samples=physical_failures,
        unresolved_fraction=unresolved_frac,
        conditional_service_probability=service_prob,
        service_probability_denominator=denom_label,
    )
    lcohs = [
        float(item.lcoh_currency_per_j)
        for item in feasible_items
        if item.lcoh_currency_per_j is not None
    ]
    pvs = [
        float(item.pv_cost_currency) for item in feasible_items if item.pv_cost_currency is not None
    ]
    incs = [
        float(item.pv_cost_currency - item.baseline_pv_cost_currency)
        for item in feasible_items
        if item.pv_cost_currency is not None and item.baseline_pv_cost_currency is not None
    ]
    shares = [
        float(item.geothermal_demand_share_fraction)
        for item in feasible_items
        if item.geothermal_demand_share_fraction is not None
    ]
    pre_drilling = (
        compute_pre_drilling_expected_case(
            pre_drilling_template.model_copy(
                update={
                    "conditional_mean_pv_cost_currency": float(np.mean(pvs))
                    if pvs
                    else pre_drilling_template.conditional_mean_pv_cost_currency,
                }
            )
        )
        if pre_drilling_template is not None and pvs
        else None
    )
    return CandidateRiskSummary(
        candidate_id=candidate_id,
        sample_account=account,
        lcoh_quantiles=_quantiles(lcohs),
        pv_cost_quantiles=_quantiles(pvs),
        incremental_cost_quantiles=_quantiles(incs),
        mean_geothermal_share_fraction=float(np.mean(shares)) if shares else None,
        pre_drilling=pre_drilling,
    )


def _metric_value(outcome: CandidateSampleOutcome, metric: str) -> float | None:
    if not (outcome.evaluated and outcome.feasible):
        return None
    if metric == "pv_total_cost_currency":
        return float(outcome.pv_cost_currency) if outcome.pv_cost_currency is not None else None
    if metric == "lcoh_geothermal_delivered_currency_per_j":
        return (
            float(outcome.lcoh_currency_per_j) if outcome.lcoh_currency_per_j is not None else None
        )
    if metric == "incremental_discounted_cost_currency":
        if outcome.pv_cost_currency is None or outcome.baseline_pv_cost_currency is None:
            return None
        return float(outcome.pv_cost_currency - outcome.baseline_pv_cost_currency)
    if metric == "geothermal_demand_share_fraction":
        return (
            float(outcome.geothermal_demand_share_fraction)
            if outcome.geothermal_demand_share_fraction is not None
            else None
        )
    return None


def _is_pareto_dominated(
    cand_vals: Mapping[str, float],
    other_vals: Mapping[str, float],
    objectives: Sequence[tuple[str, Literal["min", "max"]]],
) -> bool:
    at_least_as_good = True
    strictly_better = False
    for metric, direction in objectives:
        c_v = cand_vals.get(metric)
        o_v = other_vals.get(metric)
        if c_v is None or o_v is None:
            return False
        if direction == "min":
            if o_v > c_v + 1e-12:
                at_least_as_good = False
                break
            if o_v < c_v - 1e-12:
                strictly_better = True
        else:
            if o_v < c_v - 1e-12:
                at_least_as_good = False
                break
            if o_v > c_v + 1e-12:
                strictly_better = True
    return at_least_as_good and strictly_better


def compare_candidate_uncertainty(
    outcomes_by_candidate: Mapping[str, Sequence[CandidateSampleOutcome]],
    *,
    sample_inputs: Sequence[Mapping[str, float]],
    has_probabilities: bool,
    rules: DecisionRuleConfig,
    pre_drilling_by_candidate: Mapping[str, PreDrillingInputs] | None = None,
) -> UncertaintyComparisonSummary:
    """Compute paired regret, Pareto frontier, ranking stability, drivers, and preference."""
    pre_map = pre_drilling_by_candidate or {}
    summaries = {
        cid: analyse_candidate_risk(
            cid,
            outcomes,
            has_probabilities=has_probabilities,
            pre_drilling_template=pre_map.get(cid),
        )
        for cid, outcomes in outcomes_by_candidate.items()
    }
    n_samples = len(sample_inputs)

    # 1. Paired regret and per-sample first-rank winners (V01).
    regrets: dict[str, list[float]] = {cid: [] for cid in summaries}
    first_counts: dict[str, int] = {cid: 0 for cid in summaries}
    sample_winners: list[str | None] = []
    sample_metric_maps: list[dict[str, float]] = []
    paired_denom = 0
    for idx in range(n_samples):
        vals_here: dict[str, float] = {}
        for cid, outcomes in outcomes_by_candidate.items():
            if idx < len(outcomes):
                val = _metric_value(outcomes[idx], rules.decision_metric)
                if val is not None:
                    vals_here[cid] = val
        sample_metric_maps.append(vals_here)
        if not vals_here:
            sample_winners.append(None)
            continue
        paired_denom += 1
        best_cid = min(vals_here, key=lambda c: (vals_here[c], c))
        best_val = vals_here[best_cid]
        sample_winners.append(best_cid)
        for cid, val in vals_here.items():
            regrets[cid].append(max(val - best_val, 0.0))
            if abs(val - best_val) <= rules.tie_tolerance_fraction * max(abs(best_val), 1e-12):
                first_counts[cid] += 1

    # 2. Candidate decision scores.
    updated_summaries: dict[str, CandidateRiskSummary] = {}
    mean_objectives: dict[str, dict[str, float]] = {}
    for cid, summ in summaries.items():
        r_list = regrets[cid]
        mean_r = float(np.mean(r_list)) if r_list else None
        max_r = float(np.max(r_list)) if r_list else None
        metric_vals = [m for m in (m_map.get(cid) for m_map in sample_metric_maps) if m is not None]
        q_metric = _quantiles(metric_vals)
        score: float | None = None
        if rules.decision_rule == "expected" and q_metric is not None:
            score = q_metric.mean
        elif rules.decision_rule == "median" and q_metric is not None:
            score = q_metric.p50
        elif rules.decision_rule == "p95" and q_metric is not None:
            score = q_metric.p95
        elif rules.decision_rule == "minimax_regret":
            score = max_r
        obj_means: dict[str, float] = {}
        for obj_name, _ in rules.pareto_objectives:
            vals_o = [
                _metric_value(o, obj_name)
                for o in outcomes_by_candidate[cid]
                if _metric_value(o, obj_name) is not None
            ]
            clean_o = [v for v in vals_o if v is not None]
            if clean_o:
                obj_means[obj_name] = float(np.mean(clean_o))
        mean_objectives[cid] = obj_means
        updated_summaries[cid] = summ.model_copy(
            update={
                "mean_regret_currency": mean_r,
                "max_regret_currency": max_r,
                "regret_denominator": len(r_list),
                "decision_score": score,
            }
        )

    # 3. Pareto frontier (non-dominated candidates across objectives).
    pareto_ids: list[str] = []
    for cid, obj_vals in mean_objectives.items():
        if not obj_vals:
            continue
        dominated = any(
            oid != cid and _is_pareto_dominated(obj_vals, other_vals, rules.pareto_objectives)
            for oid, other_vals in mean_objectives.items()
        )
        if not dominated:
            pareto_ids.append(cid)
            updated_summaries[cid] = updated_summaries[cid].model_copy(
                update={"pareto_optimal": True}
            )

    scored = sorted(
        (cid for cid, s in updated_summaries.items() if s.decision_score is not None),
        key=lambda cid: (float(updated_summaries[cid].decision_score or 0.0), cid),
    )

    # 4. Ranking stability rows (V01).
    stability = tuple(
        RankingStabilityRow(
            candidate_id=cid,
            first_rank_count=first_counts[cid],
            denominator=paired_denom,
            first_rank_share=(first_counts[cid] / paired_denom) if paired_denom > 0 else 0.0,
        )
        for cid in sorted(
            updated_summaries,
            key=lambda c: (
                -first_counts[c],
                float(updated_summaries[c].decision_score or math.inf),
                c,
            ),
        )
    )

    # 5. Decision drivers from paired samples with no extra runs (V01).
    drivers = _compute_decision_drivers(
        sample_inputs=sample_inputs,
        sample_metric_maps=sample_metric_maps,
        sample_winners=sample_winners,
        ordered_candidates=scored,
    )

    # 6. Preference conclusion (spec 14.4 & 15.4).
    outcome, reason, missing_info = _preference_conclusion(
        scored=scored,
        summaries=updated_summaries,
        stability=stability,
        pareto_ids=pareto_ids,
        has_probabilities=has_probabilities,
        rules=rules,
    )
    return UncertaintyComparisonSummary(
        has_probabilities=has_probabilities,
        total_paired_samples=paired_denom,
        candidates=updated_summaries,
        pareto_candidate_ids=tuple(sorted(pareto_ids)),
        ordered_candidate_ids=tuple(scored),
        ranking_stability=stability,
        decision_drivers=drivers,
        preference_outcome=outcome,
        preference_reason=reason,
        missing_information=missing_info,
    )


def _compute_decision_drivers(
    *,
    sample_inputs: Sequence[Mapping[str, float]],
    sample_metric_maps: Sequence[Mapping[str, float]],
    sample_winners: Sequence[str | None],
    ordered_candidates: Sequence[str],
) -> tuple[DecisionDriverRow, ...]:
    if not sample_inputs or len(ordered_candidates) < 2:
        return ()
    lead_a, lead_b = ordered_candidates[0], ordered_candidates[1]
    var_ids = list(sample_inputs[0].keys())
    drivers: list[DecisionDriverRow] = []
    for vid in var_ids:
        xs: list[float] = []
        margins: list[float] = []
        winners_here: list[str] = []
        for idx, row in enumerate(sample_inputs):
            m_map = sample_metric_maps[idx]
            if lead_a in m_map and lead_b in m_map:
                xs.append(float(row[vid]))
                margins.append(m_map[lead_a] - m_map[lead_b])
                w = sample_winners[idx]
                if w is not None:
                    winners_here.append(w)
        if len(xs) < 3 or len(set(xs)) < 2:
            rho = 0.0
            switches = False
        else:
            raw_rho, _ = spearmanr(xs, margins)
            rho = 0.0 if (raw_rho is None or math.isnan(float(raw_rho))) else float(raw_rho)
            med = float(np.median(xs))
            low_wins = {w for x, w in zip(xs, winners_here, strict=False) if x <= med}
            high_wins = {w for x, w in zip(xs, winners_here, strict=False) if x > med}
            if not high_wins:
                low_wins = {w for x, w in zip(xs, winners_here, strict=False) if x < med}
                high_wins = {w for x, w in zip(xs, winners_here, strict=False) if x >= med}
            switches = bool(low_wins and high_wins and low_wins != high_wins)
        drivers.append(
            DecisionDriverRow(
                variable_id=vid,
                spearman_rank_correlation=rho,
                switches_winner=switches,
                affected_candidates=(lead_a, lead_b),
                effect_summary=(
                    f"rank order between {lead_a} and {lead_b} flips across {vid} "
                    f"(|rho|={abs(rho):.2f})"
                    if switches
                    else f"monotone margin correlation |rho|={abs(rho):.2f} with no rank flip"
                ),
            )
        )
    drivers.sort(
        key=lambda d: (
            0 if d.switches_winner else 1,
            -abs(d.spearman_rank_correlation),
            d.variable_id,
        )
    )
    return tuple(drivers)


def _preference_conclusion(
    *,
    scored: Sequence[str],
    summaries: Mapping[str, CandidateRiskSummary],
    stability: Sequence[RankingStabilityRow],
    pareto_ids: Sequence[str],
    has_probabilities: bool,
    rules: DecisionRuleConfig,
) -> tuple[
    Literal["clear_winner", "tie_or_overlap", "no_defensible_preference", "insufficient_evidence"],
    str,
    tuple[str, ...],
]:
    if not scored:
        return (
            "insufficient_evidence",
            "no eligible candidates have finite uncertainty evaluations",
            ("eligible evaluated candidates",),
        )
    worst_unresolved = max(s.sample_account.unresolved_fraction for s in summaries.values())
    if worst_unresolved > rules.max_unresolved_fraction:
        return (
            "insufficient_evidence",
            f"unresolved numerical fraction {worst_unresolved:.2f} exceeds "
            f"{rules.max_unresolved_fraction:.2f}",
            ("resolve numerical convergence before selecting a winner",),
        )
    if len(scored) == 1:
        return "clear_winner", f"{scored[0]} is the sole evaluated contender", ()
    top1, top2 = scored[0], scored[1]
    s1 = float(summaries[top1].decision_score or 0.0)
    s2 = float(summaries[top2].decision_score or 0.0)
    margin = abs(s2 - s1) / max(abs(s1), 1e-12)
    q1 = summaries[top1].pv_cost_quantiles
    q2 = summaries[top2].pv_cost_quantiles
    overlaps = q1 is not None and q2 is not None and not (q1.p95 < q2.p05 or q2.p95 < q1.p05)
    top_share = stability[0].first_rank_share if stability else 0.0
    if len(pareto_ids) > 1 and (
        margin <= rules.preferable_cost_margin_fraction or top_share < 0.75
    ):
        missing = (
            (
                "reviewed probability distributions and success definition (O05)",
                "risk preference policy (O14)",
            )
            if not has_probabilities
            else ("risk preference tradeoff weights across cost vs geothermal share (O14)",)
        )
        return (
            "no_defensible_preference",
            f"no single candidate is defensibly preferable: {top1} ({s1:.4g}) and {top2} "
            f"({s2:.4g}) trade off on the Pareto frontier with first-rank share {top_share:.0%}",
            missing,
        )
    if margin <= rules.preferable_cost_margin_fraction or (overlaps and top_share < 0.75):
        return (
            "tie_or_overlap",
            f"difference between {top1} and {top2} ({margin:.1%}) lies inside decision margin "
            f"{rules.preferable_cost_margin_fraction:.1%} with overlapping uncertainty ranges",
            ("larger sample count or narrower key parameter distributions",),
        )
    return (
        "clear_winner",
        f"{top1} outperforms {top2} by {margin:.1%} with first-rank share {top_share:.0%}",
        (),
    )
